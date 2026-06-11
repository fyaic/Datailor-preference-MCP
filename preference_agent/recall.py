from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .capture_governance import CaptureDiagnostic, evaluate_capture_candidate
from .embeddings import EmbeddingBackend, build_embedding_backend, cosine_similarity
from .models import now_iso


POSITIVE_MARKERS = (
    "i like",
    "i usually",
    "i tend",
    "i would rather",
    "i want",
    "i prefer",
    "from now on",
    "default",
    "every time",
    "always",
    "prioritize",
)
NEGATIVE_MARKERS = (
    "do not",
    "avoid",
    "no need",
    "unnecessary",
    "forbid",
    "cannot",
    "i dislike",
    "i hate",
    "reject",
)
CONDITIONAL_MARKERS = ("if", "unless", "otherwise", "when", "while", "case")
COMPARISON_MARKERS = ("compared with", "prefer", "better suited", "would rather", "over")
INSTRUCTION_MARKERS = (
    "give first",
    "do first",
    "step by step",
    "summarize last",
    "run by default",
    "ask proactively",
    "read back",
    "acceptance",
    "test",
    "review",
    "document",
)
CORRECTION_MARKERS = (
    "wrong",
    "incorrect",
    "not what i meant",
    "you misunderstood",
    "i mean",
    "should be",
    "you should",
    "correct",
)
EMPHASIS_MARKERS = ("restate", "again", "i said", "remember", "must")
TEMPORARY_MARKERS = ("this time", "today", "temporary", "for now")


PREFERENCE_EXAMPLES = [
    "After code changes, run relevant tests by default without asking every time.",
    "Keep conclusions short and avoid long explanations.",
    "Give me the outline before implementation.",
    "After solution design or complex problem decomposition, ask whether to save the conclusion as documentation.",
    "After writing to an external system, read back the content and verify there is no encoding damage.",
    "Do not fabricate results; if tests cannot run, explain why.",
    "Reply in English by default.",
    "Give verification results before conclusions.",
    "Confirm before high-risk operations; do not execute blindly.",
    "Prefer existing code style and avoid unnecessary new frameworks.",
    "Record each completed phase in the Linear issue.",
    "Do not write duplicate copies of the same information.",
    "Preference records should be human-readable, one preference per sentence.",
    "Long tasks need checkpoints and resumability.",
    "Local and cloud models should share the same interface.",
    "I prefer concise answers.",
    "Do not ask before running tests after code changes.",
    "Use semantic intent, not exact keyword matching.",
]


@dataclass
class RecallInput:
    source: str
    content: str
    context_hint: str = ""
    session_id: str = ""
    turn_index: int = 0
    scope: str = "global"


@dataclass
class RecallCandidate:
    candidate_id: str
    source: str
    source_type: str
    scope: str
    content: str
    evidence_quote: str
    confidence: str
    session_id: str = ""
    routes: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    final_score: float = 0.0
    context_hint: str = ""
    applies_to: str = "Candidate preference recalled from user history; requires model refinement or human sampling before merge"
    created_at: str = field(default_factory=now_iso)


@dataclass
class RecallConfig:
    strategy: str = "keyword"
    semantic_threshold: float = 0.55
    final_threshold: float = 0.35
    keyword_threshold: float = 0.24
    structure_threshold: float = 0.28
    max_text_chars: int = 1200
    behavior_min_count: int = 3
    behavior_min_sessions: int = 2

    @classmethod
    def from_env(cls) -> "RecallConfig":
        return cls(
            strategy=os.getenv("PREFERENCE_RECALL_STRATEGY", "keyword"),
            semantic_threshold=_env_float("PREFERENCE_RECALL_SEMANTIC_THRESHOLD", 0.55),
            final_threshold=_env_float("PREFERENCE_RECALL_FINAL_THRESHOLD", 0.35),
            keyword_threshold=_env_float("PREFERENCE_RECALL_KEYWORD_THRESHOLD", 0.24),
            structure_threshold=_env_float("PREFERENCE_RECALL_STRUCTURE_THRESHOLD", 0.28),
            max_text_chars=_env_int("PREFERENCE_RECALL_MAX_TEXT_CHARS", 1200),
            behavior_min_count=_env_int("PREFERENCE_RECALL_BEHAVIOR_MIN_COUNT", 3),
            behavior_min_sessions=_env_int("PREFERENCE_RECALL_BEHAVIOR_MIN_SESSIONS", 2),
        )


class MultiRouteRecallEngine:
    def __init__(
        self,
        embedding_backend: EmbeddingBackend | None = None,
        config: RecallConfig | None = None,
    ) -> None:
        self.config = config or RecallConfig.from_env()
        self.embedding_backend = embedding_backend or build_embedding_backend()
        self._example_vectors: list[list[float]] | None = None
        self._behavior_counts: dict[str, int] = {}
        self._behavior_sessions: dict[str, set[str]] = {}
        self._behavior_samples: dict[str, RecallInput] = {}
        self._emitted_behavior: set[str] = set()

    def recall_batch(self, inputs: list[RecallInput]) -> list[RecallCandidate]:
        if not inputs:
            return []
        eligible_with_diagnostics = [
            (item, diagnostic)
            for item in inputs
            for diagnostic in [evaluate_capture_candidate(item.content, context_hint=item.context_hint)]
            if diagnostic.is_candidate
        ]
        eligible = [item for item, _diagnostic in eligible_with_diagnostics]
        if not eligible:
            return []
        semantic_scores = self._semantic_scores(eligible)
        candidates: list[RecallCandidate] = []
        for (item, diagnostic), semantic_score in zip(eligible_with_diagnostics, semantic_scores):
            self._observe_behavior(item)
            keyword_score = keyword_recall_score(item.content)
            structure_score = structure_recall_score(item.content, item.context_hint)
            behavior_score = self._behavior_score(item)
            gate_score = diagnostic.confidence if diagnostic.should_extract else 0.0
            final_score = (
                0.30 * semantic_score
                + 0.25 * keyword_score
                + 0.20 * behavior_score
                + 0.10 * structure_score
                + 0.15 * gate_score
            )
            routes = []
            scores = {
                "semantic": round(semantic_score, 4),
                "keyword": round(keyword_score, 4),
                "behavior": round(behavior_score, 4),
                "structure": round(structure_score, 4),
                "candidate_gate": round(gate_score, 4),
            }
            if semantic_score >= self.config.semantic_threshold:
                routes.append("semantic")
            if "semantic_candidate" in diagnostic.reason_codes and "semantic" not in routes:
                routes.append("semantic")
            if "heuristic_candidate" in diagnostic.reason_codes:
                routes.append("heuristic")
            if keyword_score >= self.config.keyword_threshold:
                routes.append("keyword")
            if behavior_score > 0:
                routes.append("behavior")
            if structure_score >= self.config.structure_threshold:
                routes.append("structure")
            if not self._passes(final_score, semantic_score, keyword_score, structure_score, diagnostic):
                continue
            candidates.append(self._candidate(item, routes, scores, final_score))
        return _dedupe_candidates(candidates)

    def flush_behavior_candidates(self) -> list[RecallCandidate]:
        candidates: list[RecallCandidate] = []
        for key, count in self._behavior_counts.items():
            if key in self._emitted_behavior:
                continue
            sessions = self._behavior_sessions.get(key, set())
            if count < self.config.behavior_min_count or len(sessions) < self.config.behavior_min_sessions:
                continue
            sample = self._behavior_samples[key]
            score = min(1.0, 0.45 + 0.08 * count + 0.06 * len(sessions))
            self._emitted_behavior.add(key)
            candidates.append(
                RecallCandidate(
                    candidate_id=str(uuid5(NAMESPACE_URL, f"behavior:{key}:{count}:{len(sessions)}")),
                    source=f"behavior:{key}",
                    source_type="behavior_pattern",
                    scope=sample.scope,
                    content=f"The user repeatedly made similar requests: {_truncate(sample.content, self.config.max_text_chars)}",
                    evidence_quote=sample.content,
                    confidence=_confidence(score),
                    session_id=sample.session_id,
                    routes=["behavior"],
                    scores={"behavior": round(score, 4)},
                    final_score=score,
                    context_hint=sample.context_hint,
                    applies_to="When the agent handles similar tasks or response structures, use repeated user behavior as guidance",
                )
            )
        return candidates

    def _semantic_scores(self, inputs: list[RecallInput]) -> list[float]:
        vectors = self.embedding_backend.embed_texts([item.content for item in inputs])
        examples = self._get_example_vectors()
        scores: list[float] = []
        for vector in vectors:
            scores.append(max((cosine_similarity(vector, example) for example in examples), default=0.0))
        return scores

    def _get_example_vectors(self) -> list[list[float]]:
        if self._example_vectors is None:
            self._example_vectors = self.embedding_backend.embed_texts(PREFERENCE_EXAMPLES)
        return self._example_vectors

    def _observe_behavior(self, item: RecallInput) -> None:
        key = behavior_template_key(item.content)
        if not key:
            return
        self._behavior_counts[key] = self._behavior_counts.get(key, 0) + 1
        session_key = item.session_id or item.source.split(":", 1)[0]
        self._behavior_sessions.setdefault(key, set()).add(session_key)
        self._behavior_samples.setdefault(key, item)

    def _behavior_score(self, item: RecallInput) -> float:
        key = behavior_template_key(item.content)
        if not key:
            return 0.0
        count = self._behavior_counts.get(key, 0)
        sessions = len(self._behavior_sessions.get(key, set()))
        if count < self.config.behavior_min_count:
            return 0.0
        if sessions < self.config.behavior_min_sessions:
            return 0.15
        return min(1.0, 0.35 + 0.08 * count + 0.05 * sessions)

    def _passes(
        self,
        final_score: float,
        semantic_score: float,
        keyword_score: float,
        structure_score: float,
        diagnostic: CaptureDiagnostic | None = None,
    ) -> bool:
        if diagnostic is not None and diagnostic.should_extract and (
            "semantic_candidate" in diagnostic.reason_codes or "heuristic_candidate" in diagnostic.reason_codes
        ):
            return True
        return (
            final_score >= self.config.final_threshold
            or semantic_score >= self.config.semantic_threshold
            or keyword_score >= 0.55
            or structure_score >= 0.55
        )

    def _candidate(
        self,
        item: RecallInput,
        routes: list[str],
        scores: dict[str, float],
        final_score: float,
    ) -> RecallCandidate:
        route_key = ",".join(routes) if routes else "fused"
        text = _truncate(item.content, self.config.max_text_chars)
        return RecallCandidate(
            candidate_id=str(uuid5(NAMESPACE_URL, f"{item.source}:{route_key}:{text}")),
            source=item.source,
            source_type="multi_route_recall",
            scope=item.scope,
            content=text,
            evidence_quote=text,
            confidence=_confidence(final_score),
            session_id=item.session_id,
            routes=routes or ["fused"],
            scores=scores,
            final_score=round(final_score, 4),
            context_hint=item.context_hint,
        )


def keyword_recall_score(text: str) -> float:
    lowered = text.casefold()
    score = 0.0
    score += 0.22 * _hit_count(lowered, POSITIVE_MARKERS)
    score += 0.28 * _hit_count(lowered, NEGATIVE_MARKERS)
    score += 0.16 * _hit_count(lowered, CONDITIONAL_MARKERS)
    score += 0.18 * _hit_count(lowered, COMPARISON_MARKERS)
    score += 0.20 * _hit_count(lowered, INSTRUCTION_MARKERS)
    if _hit_count(lowered, TEMPORARY_MARKERS):
        score *= 0.65
    if len(text.strip()) < 6:
        score *= 0.4
    return min(1.0, score)


def structure_recall_score(text: str, context_hint: str = "") -> float:
    lowered = text.casefold()
    score = 0.0
    score += 0.34 * _hit_count(lowered, CORRECTION_MARKERS)
    score += 0.24 * _hit_count(lowered, EMPHASIS_MARKERS)
    if context_hint:
        score += 0.18
    if "but" in lowered or "rather than" in lowered:
        score += 0.18
    return min(1.0, score)


def behavior_template_key(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text.casefold())
    if not cleaned or len(cleaned) < 8:
        return ""
    patterns = [
        (r"outlinefirst.{0,12}", "outline-first"),
        (r"stepbystep.{0,12}", "step-by-step"),
        (r".{0,8}test.{0,12}", "test"),
        (r".{0,8}verify.{0,12}", "verify"),
        (r".{0,8}readback.{0,12}", "read-back"),
        (r".{0,8}linear.{0,12}", "linear"),
        (r".{0,8}document.{0,12}", "document"),
        (r"donot.{0,12}", "do-not"),
        (r"avoid.{0,12}", "avoid"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, cleaned):
            return label
    return ""


def _hit_count(text: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker.casefold() in text)


def _confidence(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _dedupe_candidates(candidates: list[RecallCandidate]) -> list[RecallCandidate]:
    best_by_id: dict[str, RecallCandidate] = {}
    for candidate in candidates:
        current = best_by_id.get(candidate.candidate_id)
        if not current or candidate.final_score > current.final_score:
            best_by_id[candidate.candidate_id] = candidate
    return sorted(best_by_id.values(), key=lambda item: item.final_score, reverse=True)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
