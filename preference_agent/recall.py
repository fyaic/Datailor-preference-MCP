from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .embeddings import EmbeddingBackend, build_embedding_backend, cosine_similarity
from .models import now_iso
from .quality import should_recall_user_text


POSITIVE_MARKERS = (
    "我喜欢",
    "我习惯",
    "我倾向",
    "我更愿意",
    "我希望",
    "我偏好",
    "以后",
    "默认",
    "每次",
    "总是",
    "优先",
)
NEGATIVE_MARKERS = (
    "不要",
    "别",
    "不用",
    "不必",
    "无需",
    "避免",
    "禁止",
    "不能",
    "我不喜欢",
    "我讨厌",
    "拒绝",
)
CONDITIONAL_MARKERS = ("如果", "除非", "否则", "当", "在", "情况下")
COMPARISON_MARKERS = ("比起", "相比", "更喜欢", "更适合", "宁愿", "也不", "优于")
INSTRUCTION_MARKERS = (
    "先给",
    "先做",
    "分步骤",
    "最后总结",
    "默认跑",
    "主动问",
    "回读",
    "验收",
    "测试",
    "review",
    "沉淀",
    "文档",
)
CORRECTION_MARKERS = (
    "不对",
    "错了",
    "不是这个意思",
    "你理解错了",
    "我的意思是",
    "应该是",
    "你应该",
    "纠正",
)
EMPHASIS_MARKERS = ("重申", "再次强调", "我说过", "记住", "一定要", "必须")
TEMPORARY_MARKERS = ("这次", "今天", "临时", "先暂时", "本次")


PREFERENCE_EXAMPLES = [
    "以后代码修改完成后默认运行相关测试，不要每次询问。",
    "把结论写短一点，不要长篇解释。",
    "先给我大纲，再开始实现。",
    "方案设计和复杂问题拆解之后，主动询问是否沉淀成文档。",
    "写入包含中文的外部系统后，必须回读确认中文没有乱码。",
    "不要伪造结果，测试跑不了就说明原因。",
    "默认用中文回复。",
    "回复先给验证结果，再给结论。",
    "遇到高风险操作先确认，不要直接执行。",
    "优先沿用现有代码风格，不要引入不必要的新框架。",
    "每个阶段完成后记录到 Linear issue。",
    "不要把同一份信息写成多份重复文件。",
    "偏好记录要人类可读，一条偏好一句话。",
    "长任务要有 checkpoint，可以中断恢复。",
    "本地模型和云模型要使用同一套接口。",
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
    routes: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    final_score: float = 0.0
    context_hint: str = ""
    applies_to: str = "从用户历史输入召回的候选偏好，需模型精提或人工抽检后合并"
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
        eligible = [item for item in inputs if should_recall_user_text(item.content)]
        if not eligible:
            return []
        semantic_scores = self._semantic_scores(eligible)
        candidates: list[RecallCandidate] = []
        for item, semantic_score in zip(eligible, semantic_scores):
            self._observe_behavior(item)
            keyword_score = keyword_recall_score(item.content)
            structure_score = structure_recall_score(item.content, item.context_hint)
            behavior_score = self._behavior_score(item)
            final_score = (
                0.40 * semantic_score
                + 0.30 * keyword_score
                + 0.20 * behavior_score
                + 0.10 * structure_score
            )
            routes = []
            scores = {
                "semantic": round(semantic_score, 4),
                "keyword": round(keyword_score, 4),
                "behavior": round(behavior_score, 4),
                "structure": round(structure_score, 4),
            }
            if semantic_score >= self.config.semantic_threshold:
                routes.append("semantic")
            if keyword_score >= self.config.keyword_threshold:
                routes.append("keyword")
            if behavior_score > 0:
                routes.append("behavior")
            if structure_score >= self.config.structure_threshold:
                routes.append("structure")
            if not self._passes(final_score, semantic_score, keyword_score, structure_score):
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
                    content=f"用户多次提出类似请求：{_truncate(sample.content, self.config.max_text_chars)}",
                    evidence_quote=sample.content,
                    confidence=_confidence(score),
                    routes=["behavior"],
                    scores={"behavior": round(score, 4)},
                    final_score=score,
                    context_hint=sample.context_hint,
                    applies_to="当 agent 处理同类任务或回复结构时，参考用户反复提出的行为习惯",
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
    ) -> bool:
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
    if "但是" in text or "而不是" in text:
        score += 0.18
    return min(1.0, score)


def behavior_template_key(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text.casefold())
    if not cleaned or len(cleaned) < 8:
        return ""
    patterns = [
        (r"先给我.{0,12}", "先给我"),
        (r"分步骤.{0,12}", "分步骤"),
        (r".{0,8}测试.{0,12}", "测试"),
        (r".{0,8}验证.{0,12}", "验证"),
        (r".{0,8}回读.{0,12}", "回读"),
        (r".{0,8}linear.{0,12}", "linear"),
        (r".{0,8}沉淀.{0,12}", "沉淀"),
        (r".{0,8}文档.{0,12}", "文档"),
        (r"不要.{0,12}", "不要"),
        (r"别.{0,12}", "别"),
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
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
