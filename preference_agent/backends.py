from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from typing import Any

from .capture_governance import TAXONOMY, evaluate_capture_candidate
from .live_confidence import LiveConfidence, live_confidence, should_inject_live_confidence
from .models import Evidence, PreferenceRecord, Session, unique_strings
from .quality import looks_like_one_off_task, looks_like_raw_user_fragment, should_recall_user_text


CONTEXTUAL_RELEVANCE_MIN = 0.30
GUARDRAIL_RELEVANCE_MIN = 0.24
WORKFLOW_RELEVANCE_MIN = 0.24
STRONG_SEMANTIC_RELEVANCE_MIN = 0.38
LLM_DECISION_CANDIDATE_LIMIT = 8


class PreferenceModelBackend(ABC):
    @abstractmethod
    def extract_preferences(self, session: Session) -> list[PreferenceRecord]:
        raise NotImplementedError

    @abstractmethod
    def merge_decision(
        self, candidate: PreferenceRecord, existing: list[PreferenceRecord]
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def decide(
        self,
        task: str,
        context: dict[str, Any],
        records: list[PreferenceRecord],
        agent: str = "agent",
    ) -> dict[str, Any]:
        raise NotImplementedError

    def classify_preference_candidate(self, text: str, context_hint: str = "") -> dict[str, Any]:
        raise NotImplementedError


class ModelRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "message": str(self),
            "status_code": self.status_code,
            "response_body": self.response_body,
        }


def build_backend(name: str | None = None) -> PreferenceModelBackend:
    backend = (name or os.getenv("PREFERENCE_MODEL_BACKEND") or "auto").strip().lower()
    if backend in {"openai", "openai-compatible", "llm", "local"}:
        return OpenAICompatibleBackend()
    if backend in {"auto"}:
        if _api_configuration_present():
            return OpenAICompatibleBackend()
        return HeuristicBackend()
    return HeuristicBackend()


def backend_status(name: str | None = None) -> dict[str, Any]:
    configured = (name or os.getenv("PREFERENCE_MODEL_BACKEND") or "auto").strip().lower() or "auto"
    api_configured = _api_configuration_present()
    if configured == "auto":
        effective = "openai-compatible" if api_configured else "heuristic"
        reason = "api_configuration_detected" if api_configured else "no_api_configuration_detected"
    elif configured in {"openai", "openai-compatible", "llm", "local"}:
        effective = "openai-compatible"
        reason = "explicit_model_backend"
    else:
        effective = "heuristic"
        reason = "explicit_heuristic_backend" if configured == "heuristic" else "unknown_backend_defaulted_to_heuristic"
    return {
        "configured": configured,
        "effective": effective,
        "api_configured": api_configured,
        "model_semantics_active": effective == "openai-compatible",
        "forced_heuristic_with_api_config": configured == "heuristic" and api_configured,
        "reason": reason,
    }


def _api_configuration_present() -> bool:
    return bool(
        os.getenv("PREFERENCE_MODEL_BASE_URL")
        or os.getenv("PREFERENCE_MODEL_API_KEY")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_KEY")
    )


class HeuristicBackend(PreferenceModelBackend):
    """Offline backend for deterministic POC tests.

    This is deliberately not the target intelligence layer. The production-quality
    semantic behavior is expected from OpenAI-compatible cloud/local models.
    """

    preference_markers = (
        "from now on",
        "default",
        "every time",
        "always",
        "must",
        "do not",
        "do not ask",
        "i prefer",
        "i want",
        "i require",
        "you should",
        "prioritize",
        "prefer",
        "read back",
        "从现在",
        "以后",
        "默认",
        "每次",
        "总是",
        "必须",
        "不要",
        "我希望",
        "我认为",
        "我需要",
        "偏好",
        "优先",
        "记住",
    )
    confirmation_markers = ("should we", "whether", "do you need", "would you like", "okay?", "confirm", "?")
    change_markers = ("from now on", "change to", "default to")

    def extract_preferences(self, session: Session) -> list[PreferenceRecord]:
        candidates: list[PreferenceRecord] = []
        messages = session.messages
        for index, message in enumerate(messages):
            content = _clean(message.content)
            if not content:
                continue
            if message.role == "assistant" and _has_any(content, self.confirmation_markers):
                next_user = _next_user_message(messages, index + 1)
                if next_user:
                    generalized = _generalize_preference(_clean(next_user.content), context=content)
                    if not generalized:
                        continue
                    candidates.append(
                        _record(
                            title=f"User preference: {_short(generalized, 32)}",
                            applies_to=_infer_applies_to(generalized),
                            preference=generalized,
                            triggers=[content],
                            exceptions=["Escalate to the user when context changes materially, risk increases, or recent answers disagree."],
                            source=session.source,
                            session_id=session.session_id,
                            quote=next_user.content,
                            confidence="medium",
                        )
                    )
            if message.role == "user":
                for sentence in _sentences(content):
                    diagnostic = evaluate_capture_candidate(sentence)
                    if (
                        not _has_any(sentence, self.preference_markers)
                        and not should_recall_user_text(sentence)
                        and not diagnostic.should_extract
                    ):
                        continue
                    generalized = _generalize_preference(sentence)
                    if generalized and len(generalized) >= 8:
                        candidates.append(
                            _record(
                                title=f"User preference: {_short(generalized, 32)}",
                                applies_to=_infer_applies_to(generalized),
                                preference=generalized,
                                triggers=_infer_triggers(generalized),
                                exceptions=_infer_exceptions(generalized),
                                source=session.source,
                                session_id=session.session_id,
                                quote=sentence,
                                confidence="medium",
                            )
                        )
        return _dedupe_records(candidates)

    def classify_preference_candidate(self, text: str, context_hint: str = "") -> dict[str, Any]:
        return evaluate_capture_candidate(text, context_hint=context_hint).to_dict()

    def merge_decision(
        self, candidate: PreferenceRecord, existing: list[PreferenceRecord]
    ) -> dict[str, Any]:
        best: tuple[float, PreferenceRecord | None] = (0.0, None)
        candidate_scope = _scope_text(candidate)
        candidate_category = _category(candidate.preference or candidate_scope)
        for record in existing:
            record_category = _category(record.preference or _scope_text(record))
            if candidate_category and record_category and candidate_category != record_category:
                continue
            score = semantic_similarity(candidate_scope, _scope_text(record))
            if candidate_category and record_category == candidate_category:
                if candidate.preference in record.preference or record.preference in candidate.preference:
                    score = max(score, 0.6)
            if score > best[0]:
                best = (score, record)
        score, record = best
        if not record or score < 0.27:
            return {"action": "new", "reason": "No sufficiently similar existing preference.", "score": round(score, 3)}
        preference_score = semantic_similarity(candidate.preference, record.preference)
        if candidate.preference in record.preference or record.preference in candidate.preference:
            preference_score = max(preference_score, 0.6)
        if _has_any(candidate.preference, self.change_markers):
            return {
                "action": "replace",
                "target_id": record.id,
                "reason": "Candidate contains an explicit change signal; treat it as an update.",
                "score": round(score, 3),
            }
        if preference_score >= 0.55:
            return {
                "action": "merge",
                "target_id": record.id,
                "reason": "New evidence reinforces an existing preference with the same intent.",
                "score": round(score, 3),
            }
        return {
            "action": "conflict",
            "target_id": record.id,
            "reason": "Context is similar but behavior differs; keep conflict evidence.",
            "score": round(score, 3),
        }

    def decide(
        self,
        task: str,
        context: dict[str, Any],
        records: list[PreferenceRecord],
        agent: str = "agent",
    ) -> dict[str, Any]:
        if not records:
            return _no_preference(task)
        scored = _live_scored_records(task=task, context=context, records=records)
        matches = [
            {
                "id": record.id,
                "title": record.title,
                "confidence": live.label,
                "stored_confidence": record.confidence,
                "live_confidence": live.score,
                "confidence_factors": {
                    "base": live.base,
                    "evidence_boost": live.evidence_boost,
                    "recency_decay": live.recency_decay,
                    "consistency_penalty": live.consistency_penalty,
                    "relevance_bonus": live.relevance_bonus,
                },
                "confidence_reasons": live.reasons,
                "score": round(relevance, 3),
                "instruction": record.preference,
                "applies_to": record.applies_to,
                "category": gate["family"],
                "gate_reason": gate["reason"],
                "gate": gate,
            }
            for _live_score, relevance, live, record, gate in scored[:5]
        ]
        if not matches:
            return _no_preference(task, gate_summary=_gate_summary(records, scored))
        conflict_group = _find_conflicts_among([record for _score, _relevance, _live, record, _gate in scored[:3]])
        if conflict_group:
            return _backend_conflict_response(agent=agent, matches=matches, conflicts=conflict_group)
        combined = "; ".join(match["instruction"] for match in matches[:3])
        return {
            "decision": "apply",
            "agent": agent,
            "matched_preferences": matches,
            "agent_instruction": f"Apply these user preferences before replying or acting: {combined}",
            "escalate": False,
            "reason": "Found semantically relevant user preferences.",
            "gate_summary": _gate_summary(records, scored),
        }


class OpenAICompatibleBackend(PreferenceModelBackend):
    def __init__(self) -> None:
        self.base_url = (
            os.getenv("PREFERENCE_MODEL_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "http://localhost:11434/v1"
        ).rstrip("/")
        self.api_key = os.getenv("PREFERENCE_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY") or "local"
        self.model = os.getenv("PREFERENCE_MODEL_NAME") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        self.timeout = float(os.getenv("PREFERENCE_MODEL_TIMEOUT", "90"))
        self.temperature = _env_optional_float("PREFERENCE_MODEL_TEMPERATURE", 0.1)
        self.send_temperature = _env_bool("PREFERENCE_MODEL_SEND_TEMPERATURE", True) and self.temperature is not None
        self.response_format = _env_response_format("PREFERENCE_MODEL_RESPONSE_FORMAT")
        self._omit_temperature_after_rejection = False
        self._omit_response_format_after_rejection = False

    def extract_preferences(self, session: Session) -> list[PreferenceRecord]:
        payload = {
            "source": session.source,
            "messages": [message.__dict__ for message in session.messages],
        }
        result = self._chat_json(
            system=EXTRACT_SYSTEM_PROMPT,
            user=json.dumps(payload, ensure_ascii=False),
        )
        candidates = result.get("preferences", []) if isinstance(result, dict) else []
        records: list[PreferenceRecord] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            item.setdefault("evidence", [])
            if not item["evidence"]:
                item["evidence"] = [
                    {
                        "source": session.source,
                        "session_id": session.session_id,
                        "quote": item.get("evidence_quote") or item.get("preference") or "",
                        "role": "user",
                        "source_type": "user_explicit",
                    }
                ]
            for evidence in item["evidence"]:
                if isinstance(evidence, dict):
                    evidence.setdefault("source", session.source)
                    evidence.setdefault("session_id", session.session_id)
            records.append(PreferenceRecord.from_dict(item))
        return records

    def classify_preference_candidate(self, text: str, context_hint: str = "") -> dict[str, Any]:
        result = self._chat_json(
            system=CLASSIFY_SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "text": text,
                    "context_hint": context_hint,
                    "taxonomy": sorted(TAXONOMY),
                },
                ensure_ascii=False,
            ),
        )
        return result if isinstance(result, dict) else {"has_preference_signal": False, "reason_codes": ["fast_gate_no_match"]}

    def merge_decision(
        self, candidate: PreferenceRecord, existing: list[PreferenceRecord]
    ) -> dict[str, Any]:
        result = self._chat_json(
            system=MERGE_SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "candidate": candidate.to_dict(),
                    "existing": [record.to_dict() for record in existing],
                },
                ensure_ascii=False,
            ),
        )
        return result if isinstance(result, dict) else {"action": "new", "reason": "model returned invalid JSON"}

    def decide(
        self,
        task: str,
        context: dict[str, Any],
        records: list[PreferenceRecord],
        agent: str = "agent",
    ) -> dict[str, Any]:
        if not records:
            return _no_preference(task)
        candidates = _live_scored_preference_payloads(
            task=task,
            context=context,
            records=records,
            require_injection_threshold=True,
            limit=LLM_DECISION_CANDIDATE_LIMIT,
        )
        if not candidates:
            return _no_preference(task, gate_summary=_gate_summary(records, []))
        result = self._chat_json(
            system=DECIDE_SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "agent": agent,
                    "task": task,
                    "context": context,
                    "preferences": candidates,
                },
                ensure_ascii=False,
            ),
        )
        if not isinstance(result, dict):
            return _no_preference(task)
        result.setdefault("agent", agent)
        result.setdefault("escalate", result.get("decision") != "apply")
        result.setdefault(
            "gate_summary",
            {
                "active_candidates": sum(1 for record in records if str(record.status or "").casefold() == "active"),
                "passed": len(candidates),
                "reason": "prefiltered_candidates_for_model",
            },
        )
        return result

    def _chat_json(self, system: str, user: str) -> dict[str, Any]:
        include_temperature = self.send_temperature and not self._omit_temperature_after_rejection
        include_response_format = self.response_format is not None and not self._omit_response_format_after_rejection
        while True:
            try:
                return self._chat_json_once(
                    system=system,
                    user=user,
                    include_temperature=include_temperature,
                    include_response_format=include_response_format,
                )
            except ModelRequestError as exc:
                changed = False
                if include_temperature and _looks_like_temperature_incompatibility(exc):
                    self._omit_temperature_after_rejection = True
                    include_temperature = False
                    changed = True
                if include_response_format and _looks_like_response_format_incompatibility(exc):
                    self._omit_response_format_after_rejection = True
                    include_response_format = False
                    changed = True
                if changed:
                    continue
                raise

    def _chat_json_once(
        self,
        system: str,
        user: str,
        include_temperature: bool,
        include_response_format: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if include_response_format and self.response_format is not None:
            body["response_format"] = self.response_format
        if include_temperature and self.temperature is not None:
            body["temperature"] = self.temperature
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise ModelRequestError(
                f"model request failed: HTTP {exc.code} {response_body}",
                status_code=exc.code,
                response_body=response_body,
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelRequestError(f"model request failed: {exc}") from exc
        content = data["choices"][0]["message"]["content"]
        return _json_from_text(content)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() not in {"0", "false", "no", "off", "disabled"}


def _env_optional_float(name: str, default: float) -> float | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if value.casefold() in {"", "omit", "none", "null", "off", "false", "disabled"}:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number or one of omit/none/off") from exc


def _env_response_format(name: str) -> dict[str, Any] | None:
    raw = os.getenv(name)
    if raw is None:
        return {"type": "json_object"}
    value = raw.strip()
    if value.casefold() in {"", "omit", "none", "null", "off", "false", "disabled"}:
        return None
    if value.casefold() in {"json", "json_object"}:
        return {"type": "json_object"}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be omit, json_object, or a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be omit, json_object, or a JSON object")
    return parsed


def _looks_like_temperature_incompatibility(error: ModelRequestError) -> bool:
    text = " ".join([str(error), error.response_body]).casefold()
    if "temperature" not in text:
        return False
    if error.status_code in {400, 422}:
        return True
    return _has_any(
        text,
        (
            "invalid",
            "unsupported",
            "not support",
            "not supported",
            "not allowed",
            "unknown parameter",
            "unrecognized",
            "extra",
            "only",
            "must be",
        ),
    )


def _looks_like_response_format_incompatibility(error: ModelRequestError) -> bool:
    text = " ".join([str(error), error.response_body]).casefold()
    if not _has_any(text, ("response_format", "response format", "json_object", "json mode")):
        return False
    if error.status_code in {400, 422}:
        return True
    return _has_any(
        text,
        (
            "invalid",
            "unsupported",
            "not support",
            "not supported",
            "not allowed",
            "unknown parameter",
            "unrecognized",
            "extra",
            "only",
            "must be",
        ),
    )


CLASSIFY_SYSTEM_PROMPT = """You are a semantic preference-signal classifier for a local-first personal preference agent.
Classify whether the input text contains a durable user preference that should be inspected by extraction.
Prefer recall over precision at this stage, but do not classify one-off tasks, paths, URLs, issue IDs, secrets, current-day instructions, or project facts as durable global preferences.
Multilingual Chinese and English signals are first-class.
Use this taxonomy only: communication_style, execution_policy, verification_standard, tool_preference, risk_boundary, documentation_habit, review_standard, negative_constraint, scope_exception.
Output JSON:
{
  "has_preference_signal": true|false,
  "signal_type": "taxonomy value",
  "scope": "global|project|session|none",
  "durability": "durable|likely_durable|temporary|one_off",
  "confidence": 0.0,
  "reason_codes": ["semantic_candidate|one_off_filtered|fast_gate_no_match|needs_review"],
  "should_extract": true|false,
  "summary": "short reason"
}
"""

EXTRACT_SYSTEM_PROMPT = """You are a personal preference extraction engine. Extract stable, reusable user preferences that can guide future agent behavior from recalled candidates.
Only extract preferences related to agent behavior, response style, execution habits, verification standards, escalation conditions, tool choice, language style, or information organization.
Never extract one-off business tasks, project facts, paths, URLs, issue IDs, temporary instructions, debugging requests, or tasks the agent should do right now as preferences.
If a candidate only asks the agent to edit, check, create, push, test, or inspect a specific project or issue, return an empty array.
If a candidate reflects a preference, rewrite it as a reusable rule instead of copying the user verbatim.
`applies_to` must be a specific scenario. Do not use generic fake scopes such as "when the agent is about to reply, execute a task, or make a decision".
`preference` must be a short executable instruction. It must not contain local paths, URLs, AIC/issue IDs, branch names, specific file names, or one-off object names.
Output a JSON object: {"preferences": [...]}.
Each preference field:
- title: short title.
- summary: one-sentence summary.
- applies_to: specific scenario, preferably starting with "When ...".
- preference: executable preference statement for the agent; concise imperative or third-person wording is fine.
- triggers[]: intents or scenarios that trigger this preference.
- exceptions[]: cases where it does not apply or needs escalation.
- confidence: high/medium/low.
- evidence[]: each item must include source, quote, role.
Optional fields such as category, is_negation, or quality are allowed, but do not invent evidence.
Preserve negative meaning. For example, "do not write long answers" can become "Prefer concise replies", while evidence.quote keeps the original text.
Use high confidence only for explicit stable signals such as "from now on", "default", "every time", "always", "I want", "I prefer", "I said before", "remember", or repeated habits across multiple candidates.
"""

MERGE_SYSTEM_PROMPT = """You are a preference-store merger. Decide whether candidate and existing preferences express the same user-preference intent.
Output JSON: {"action":"new|merge|replace|conflict","target_id":null or id,"reason":"..."}.
Rules:
- new: no similar preference exists.
- merge: same intent and consistent; append evidence.
- replace: clear preference change; update the primary preference.
- conflict: same intent but uncertain change; keep conflict evidence and do not overwrite automatically.
"""

DECIDE_SYSTEM_PROMPT = """You are a preference-decision engine. Given the agent's current task and preference store, decide which preferences should apply before replying or acting.
Prioritize semantic intent over exact wording.
Input preferences include live_confidence, a dynamic score based on base confidence, evidence, recency, consistency, and task relevance.
Prefer preferences with high live_confidence.score that genuinely fit the current task. Do not force low-relevance preferences just because stored confidence is high.
Preferences whose status is not active must not be injected directly; if strongly relevant, use them only to explain uncertainty or trigger clarification.
Output JSON:
{
  "decision": "apply|no_preference|escalate",
  "matched_preferences": [{"id":"...","title":"...","confidence":"high|medium|low","instruction":"...","reason":"..."}],
  "agent_instruction": "executable instruction for the agent",
  "escalate": false,
  "reason": "..."
}
Return apply only when preferences truly fit; otherwise return no_preference or escalate.
"""


def _record(
    title: str,
    applies_to: str,
    preference: str,
    triggers: list[str],
    exceptions: list[str],
    source: str,
    session_id: str,
    quote: str,
    confidence: str,
) -> PreferenceRecord:
    return PreferenceRecord(
        title=title,
        summary=preference[:120],
        applies_to=applies_to,
        preference=preference,
        triggers=unique_strings(triggers, limit=8),
        exceptions=unique_strings(exceptions, limit=8),
        confidence=confidence,
        evidence=[Evidence(source=source, session_id=session_id, quote=quote, role="user", source_type="user_explicit")],
    )


def semantic_similarity(left: str, right: str) -> float:
    left_norm = _normalize_for_similarity(left)
    right_norm = _normalize_for_similarity(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    seq = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_grams = _char_ngrams(left_norm)
    right_grams = _char_ngrams(right_norm)
    jaccard = len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
    keyword = _keyword_overlap(left_norm, right_norm)
    return max(seq * 0.45 + jaccard * 0.35 + keyword * 0.2, keyword * 0.9)


def _normalize_for_similarity(text: str) -> str:
    text = text.casefold()
    replacements = {
        "tests": "test",
        "verification": "test",
        "verify": "test",
        "code review": "review",
        "check": "review",
        "readback": "read back",
        "agent": "assistant",
        "ai": "assistant",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"\s+", "", text)


def _char_ngrams(text: str, size: int = 2) -> set[str]:
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _keyword_overlap(left: str, right: str) -> float:
    keywords = [
        "test",
        "code",
        "implementation",
        "change",
        "review",
        "verify",
        "coverage",
        "edge",
        "safety",
        "language",
        "reply",
        "answer",
        "user question",
        "concise",
        "detailed",
        "read back",
        "linear",
        "document",
        "do not ask",
        "auto",
        "local",
        "preference",
        "codex",
        "openclaw",
    ]
    left_hits = {word for word in keywords if word in left}
    right_hits = {word for word in keywords if word in right}
    if not left_hits or not right_hits:
        return 0.0
    overlap = left_hits & right_hits
    generic = {"code", "implementation", "change", "reply", "answer", "document", "local", "preference", "auto"}
    if len(overlap) == 1 and next(iter(overlap)) in generic:
        return 0.0
    return len(overlap) / len(left_hits | right_hits)


def _scope_text(record: PreferenceRecord) -> str:
    return " ".join([record.title, record.summary, record.applies_to, record.preference, *record.triggers])


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _short(text: str, limit: int) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s*|\n+", text)
    return [_clean(part) for part in parts if _clean(part)]


def _generalize_preference(sentence: str, context: str = "") -> str:
    text = _clean(sentence)
    joined = _clean(" ".join([context, text]))
    lowered = joined.casefold()
    if not text:
        return ""
    if any(word in lowered for word in ("not what i meant", "review first", "patches only after", "before i approve", "after i approve")):
        return "When the user asks for review or analysis before code changes, review first and only patch after user approval."
    if any(word in joined for word in ("又直接改", "先分析", "不要动代码", "先别改", "不要直接改")):
        return "Before modifying code, analyze the request and risks first; only edit after the user approves or the request clearly asks for code changes."
    if any(word in lowered for word in ("read back", "external system", "encoding", "mojibake")) and any(
        word in lowered for word in ("must", "always", "check", "confirm", "verify")
    ):
        return "After writing content to an external system, read back the content and verify text and Markdown structure."
    if any(word in joined for word in ("回读", "外部系统", "乱码", "中文", "编码")) and any(
        word in joined for word in ("必须", "每次", "确认", "检查", "验证")
    ):
        return "After writing content to an external system, read back the content and verify Chinese text, encoding, and Markdown structure."
    if any(word in lowered for word in ("test", "pytest", "verify", "verification")) and any(
        word in lowered for word in ("from now on", "default", "every time", "do not ask")
    ):
        return "After code or script changes, run relevant tests or verification by default; if they cannot run, explain why and provide alternative verification."
    if any(word in joined for word in ("测试", "验证", "验收")) and any(
        word in joined for word in ("从现在", "以后", "默认", "每次", "不要问", "必须")
    ):
        return "After code or script changes, run relevant tests or verification by default; if they cannot run, explain why and provide alternative verification."
    if "review" in lowered and (
        any(word in lowered for word in ("code", "implementation", "change", "edge", "exception", "regression"))
    ):
        return "During code review, prioritize bugs, regression risk, and missing tests before adding a summary."
    if any(word in lowered for word in ("document", "write down", "retrospective", "save the conclusion", "save the conclusions")) and any(
        word in lowered for word in ("plan", "design", "deep", "discussion", "reusable", "complex", "decision", "organize")
    ):
        return "When deep discussion, solution design, or retrospective work produces reusable conclusions, ask whether to save them as Markdown documentation."
    if any(word in joined for word in ("文档", "沉淀", "复盘", "保存结论")) and any(
        word in joined for word in ("方案", "设计", "深度", "复杂", "决策", "复用", "整理")
    ):
        return "When deep discussion, solution design, or retrospective work produces reusable conclusions, ask whether to save them as Markdown documentation."
    if any(word in joined for word in ("偏好", "上限", "50")) and any(word in joined for word in ("查重", "归纳", "概括", "含金量", "治理")):
        return "Keep the canonical preference store capped and improve each preference through deduplication, merge, and concise generalization instead of accumulating low-value duplicates."
    if "Linear" in joined or "linear" in lowered or "issue" in lowered:
        if any(word in lowered for word in ("done", "completed", "phase", "record", "update", "read back")) and not looks_like_one_off_task(text):
            return "After a phase is completed, ask whether the Linear issue should be updated; read back external writes when applicable."
        return ""
    if any(word in lowered for word in ("git", "commit", "push", "branch")) and any(
        word in lowered for word in ("do not", "no ", "local", "keep local")
    ):
        return "Keep code changes local by default and do not commit or push unless the user explicitly asks."
    if "edit" in lowered and any(word in lowered for word in ("do not overwrite", "avoid overwrite", "overwrite")):
        return "When modifying existing files, prefer incremental edits and avoid overwriting unsynced local changes."
    if any(word in lowered for word in ("statement headline", "question headline")) and "title" in lowered:
        return "When writing titles, use statement-style titles and avoid question-style titles."
    if "english" in lowered and any(word in lowered for word in ("reply", "answer", "output")):
        return "Use English when replying to the user by default."
    if "animation" in lowered and any(word in lowered for word in ("slow", "slower", "too fast", "speed")):
        return "When designing interaction animations or transitions, use slower motion and avoid overly fast jumps or ripple effects."
    if any(word in lowered for word in ("do not ask", "no confirmation", "stop asking", "just do it")):
        return "When the user has clearly asked for execution, avoid repeated confirmation; proceed when risk is controlled and report results."
    if any(word in joined for word in ("不要询问", "不要反问", "直接执行", "由你负责")):
        return "When the user has clearly asked for execution, avoid repeated confirmation; proceed when risk is controlled and report results."
    if any(word in lowered for word in ("concise", "brief", "two sentences", "less verbose", "no long answer", "bullets", "scannable")):
        return "Prefer concise, bulleted, scannable replies and documents; avoid long unstructured paragraphs."
    if any(word in joined for word in ("简洁", "分点", "要点", "易扫读", "不要冗长")):
        return "Prefer concise, bulleted, scannable replies and documents; avoid long unstructured paragraphs."
    if any(word in lowered for word in ("conclusion", "outline")) and any(word in lowered for word in ("from now on", "default", "every time", "first")):
        return "For complex questions, give the conclusion or outline first, then expand only as needed."
    if looks_like_one_off_task(text) and not should_recall_user_text(text):
        return ""
    if looks_like_raw_user_fragment(text):
        return ""
    if not should_recall_user_text(text):
        return ""
    return ""


def _category(text: str) -> str:
    lowered = text.casefold()
    if any(word in lowered for word in ("review", "edge case", "exception path", "regression risk", "bug")):
        return "review"
    if any(word in lowered for word in ("test", "verify", "coverage", "dependency", "cannot run", "code complete", "code change", "code", "implementation")):
        return "test"
    if any(word in lowered for word in ("document", "documentation", "solution design", "retrospective", "markdown")):
        return "docs"
    if any(word in lowered for word in ("reply", "answer", "user question", "concise", "brief", "detailed", "long-form")):
        return "reply"
    if any(word in lowered for word in ("linear", "issue")):
        return "linear"
    if any(word in lowered for word in ("read back", "encoding", "external write")):
        return "external-write"
    return ""


def _next_user_message(messages: list[Any], start: int) -> Any | None:
    for message in messages[start : start + 4]:
        if message.role == "user" and _clean(message.content):
            return message
    return None


def _infer_applies_to(sentence: str) -> str:
    lowered = sentence.casefold()
    if any(word in lowered for word in ("git", "commit", "push", "branch", "code", "test", "review", "verify", "coverage")):
        return "When the agent changes code, completes implementation, or discusses test/review standards"
    if any(word in sentence for word in ("代码", "测试", "验证", "验收", "评审")):
        return "When the agent changes code, completes implementation, or discusses test/review standards"
    if "Linear" in sentence or "issue" in sentence.casefold():
        return "When the agent completes a phase and may need to update a Linear issue"
    if "english" in lowered and any(word in lowered for word in ("reply", "answer", "output")):
        return "When the agent replies to the user"
    if "read back" in lowered:
        return "After the agent writes content to an external system"
    if "回读" in sentence or "外部系统" in sentence:
        return "After the agent writes content to an external system"
    if "document" in lowered or "documentation" in lowered:
        return "When a discussion produces reusable decisions or plans"
    if "文档" in sentence or "沉淀" in sentence:
        return "When a discussion produces reusable decisions or plans"
    if any(word in lowered for word in ("concise", "bullets", "conclusion", "outline", "reply", "long-form")):
        return "When the agent answers questions, reports results, or organizes long content"
    if any(word in sentence for word in ("简洁", "分点", "结论", "大纲", "回复", "输出")):
        return "When the agent answers questions, reports results, or organizes long content"
    if any(word in lowered for word in ("repeated confirmation", "directly proceed")):
        return "When the user has clearly asked the agent to execute a task"
    return "When the agent chooses response style or execution cadence"


def _infer_triggers(sentence: str) -> list[str]:
    triggers = []
    lowered = sentence.casefold()
    if any(word in lowered for word in ("code", "test", "review", "verify")):
        triggers.append("code completion, tests, review, verification")
    if any(word in sentence for word in ("代码", "测试", "验证", "验收", "评审")):
        triggers.append("code completion, tests, review, verification")
    if "Linear" in sentence or "issue" in sentence.casefold():
        triggers.append("task or phase completion")
    if "read back" in lowered:
        triggers.append("external content write")
    if "回读" in sentence or "外部系统" in sentence:
        triggers.append("external content write")
    if "document" in lowered or "documentation" in lowered:
        triggers.append("deep discussion, solution design, retrospective")
    if "文档" in sentence or "沉淀" in sentence:
        triggers.append("deep discussion, solution design, retrospective")
    return triggers or [sentence[:60]]


def _infer_exceptions(sentence: str) -> list[str]:
    lowered = sentence.casefold()
    exceptions = ["When the user explicitly gives a conflicting instruction, follow the latest instruction."]
    if "do not" in lowered or "no " in lowered:
        exceptions.append("Escalate to the user when risk is high or context is insufficient.")
    return exceptions


def _dedupe_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    deduped: list[PreferenceRecord] = []
    for record in records:
        match = next(
            (
                item
                for item in deduped
                if semantic_similarity(_scope_text(record), _scope_text(item)) > 0.82
                and semantic_similarity(record.preference, item.preference) > 0.72
            ),
            None,
        )
        if match:
            match.add_evidence_from(record)
            continue
        deduped.append(record)
    return deduped


def _live_scored_records(
    task: str,
    context: dict[str, Any],
    records: list[PreferenceRecord],
    require_injection_threshold: bool = True,
) -> list[tuple[float, float, LiveConfidence, PreferenceRecord, dict[str, Any]]]:
    query = " ".join([task, json.dumps(context, ensure_ascii=False)])
    scored: list[tuple[float, float, LiveConfidence, PreferenceRecord, dict[str, Any]]] = []
    for record in records:
        if str(record.status or "").casefold() in {"rejected", "archived", "deleted"}:
            continue
        relevance = _relevance_score(query, record)
        live = live_confidence(record, relevance_score=relevance)
        gate = _injection_gate(query=query, record=record, relevance=relevance, live=live)
        if not require_injection_threshold or gate["passed"]:
            scored.append((live.score, relevance, live, record, gate))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored


def _live_scored_preference_payloads(
    task: str,
    context: dict[str, Any],
    records: list[PreferenceRecord],
    require_injection_threshold: bool = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for _score, relevance, live, record, gate in _live_scored_records(
        task=task,
        context=context,
        records=records,
        require_injection_threshold=require_injection_threshold,
    )[: limit or None]:
        item = record.to_dict()
        item["relevance_score"] = round(relevance, 4)
        item["live_confidence"] = live.to_dict()
        item["gate"] = gate
        item["gate_reason"] = gate["reason"]
        item["category"] = gate["family"]
        payloads.append(item)
    return payloads


def _relevance_score(query: str, record: PreferenceRecord) -> float:
    record_scope = _scope_text(record)
    score = semantic_similarity(query, record_scope)
    query_families = _query_families(query)
    record_family = _preference_family(record)
    if record_family in query_families and _query_has_family_signal(query, record_family):
        score = max(score, _family_signal_bump(record_family))
    elif query_families and record_family and record_family not in query_families:
        score = min(score, 0.12)
    return score


def _injection_gate(
    query: str,
    record: PreferenceRecord,
    relevance: float,
    live: LiveConfidence,
) -> dict[str, Any]:
    family = _preference_family(record)
    active = str(record.status or "").casefold() == "active"
    trigger_matched = _query_has_family_signal(query, family) or _trigger_overlap(query, record)
    threshold = _family_relevance_threshold(family, trigger_matched)
    live_passed = should_inject_live_confidence(live)
    semantic_override = relevance >= STRONG_SEMANTIC_RELEVANCE_MIN and live.score >= 0.60
    passed = active and live_passed and (relevance >= threshold or semantic_override)
    if not active:
        reason = f"rejected_status_{record.status}"
    elif not live_passed:
        reason = "rejected_live_or_relevance_threshold"
    elif relevance < threshold and not semantic_override:
        reason = f"rejected_{family}_context_gate"
    elif trigger_matched:
        reason = f"passed_{family}_explicit_trigger"
    else:
        reason = f"passed_{family}_semantic_relevance"
    return {
        "passed": passed,
        "reason": reason,
        "family": family,
        "relevance_score": round(relevance, 4),
        "live_confidence": live.score,
        "threshold": threshold,
        "trigger_matched": trigger_matched,
    }


def _gate_summary(
    records: list[PreferenceRecord],
    scored: list[tuple[float, float, LiveConfidence, PreferenceRecord, dict[str, Any]]],
) -> dict[str, Any]:
    active_count = sum(1 for record in records if str(record.status or "").casefold() == "active")
    return {
        "active_candidates": active_count,
        "passed": len(scored),
        "reason": "passed_injection_gate" if scored else "no_active_preference_passed_injection_gate",
    }


def _preference_family(record: PreferenceRecord) -> str:
    text = _scope_text(record).casefold()
    if _has_any(text, ("localhost", "dev server", "local server", "port", "process", "kill", "terminate", "external system", "read back", "encoding", "commit", "push", "delete", "destructive")):
        return "guardrail"
    if _has_any(text, ("test", "verify", "verification", "pytest", "regression", "code review", "format", "formatting", "linear", "issue", "complete implementation", "code changes")):
        return "workflow"
    if _has_any(text, ("deep discussion", "solution design", "reusable conclusions", "retrospective", "save markdown", "沉淀", "复盘", "方案", "架构")):
        return "documentation"
    if _has_any(text, ("wechat", "wecom", "obsidian", "markdown bold", "platform", "微信", "企业微信")):
        return "platform"
    if _has_any(text, ("reply", "answer", "question", "concise", "brief", "detailed", "outline", "conclusion", "language", "english", "中文")):
        return "communication"
    return "general"


def _query_families(query: str) -> set[str]:
    lowered = query.casefold()
    families: set[str] = set()
    if _has_any(lowered, ("localhost", "dev server", "local server", "port", "process", "kill", "terminate", "commit", "push", "delete", "destructive")) or _has_external_write_signal(lowered):
        families.add("guardrail")
    if _has_any(lowered, ("test", "tests", "verify", "verification", "pytest", "regression", "implement", "implementation", "implemented", "code change", "code changes", "completing code", "complete code", "code review", "format", "formatting", "linear", "issue", "测试", "验证", "验收", "评审", "实现")):
        families.add("workflow")
    if _has_deep_documentation_signal(lowered):
        families.add("documentation")
    if _has_any(lowered, ("wechat", "wecom", "obsidian", "markdown", "platform", "微信", "企业微信")):
        families.add("platform")
    if _has_any(lowered, ("reply", "answer", "question", "concise", "brief", "detailed", "outline", "conclusion", "explain", "font", "style", "回复", "回答", "简洁", "字体", "标题")):
        families.add("communication")
    return families


def _has_external_write_signal(query: str) -> bool:
    if _has_read_only_signal(query):
        return False
    has_external_target = _has_any(
        query,
        (
            "external system",
            "linear",
            "slack",
            "notion",
            "github issue",
            "issue comment",
            "email",
            "外部系统",
            "linear",
            "github",
        ),
    )
    has_write_action = _has_any(
        query,
        (
            "write",
            "writing",
            "post",
            "comment",
            "send",
            "publish",
            "update",
            "create",
            "save",
            "read back",
            "回读",
            "写入",
            "评论",
            "发送",
            "更新",
            "创建",
        ),
    )
    return (has_external_target and has_write_action) or _has_any(query, ("read back", "回读", "写入外部"))


def _has_read_only_signal(query: str) -> bool:
    return _has_any(
        query,
        (
            "without writing",
            "do not write",
            "don't write",
            "no writing",
            "without posting",
            "do not post",
            "don't post",
            "read only",
            "read-only",
            "locally without",
            "summarize locally",
            "only summarize",
            "只读",
            "不要写",
            "不要发",
        ),
    )


def _has_deep_documentation_signal(query: str) -> bool:
    return _has_any(
        query,
        (
            "deep discussion",
            "solution design",
            "reusable conclusion",
            "retrospective",
            "tradeoff",
            "decision",
            "architecture decision",
            "discuss",
            "analyze pros",
            "沉淀",
            "复盘",
            "讨论",
            "方案",
            "利弊",
            "架构怎么改",
        ),
    )


def _query_has_family_signal(query: str, family: str) -> bool:
    return family in _query_families(query)


def _trigger_overlap(query: str, record: PreferenceRecord) -> bool:
    query_norm = query.casefold()
    for trigger in [*record.triggers, record.applies_to]:
        trigger_norm = str(trigger or "").casefold()
        if trigger_norm and semantic_similarity(query_norm, trigger_norm) >= 0.34:
            return True
    return False


def _family_relevance_threshold(family: str, trigger_matched: bool) -> float:
    if family == "guardrail":
        return GUARDRAIL_RELEVANCE_MIN if trigger_matched else STRONG_SEMANTIC_RELEVANCE_MIN
    if family == "workflow":
        return WORKFLOW_RELEVANCE_MIN if trigger_matched else STRONG_SEMANTIC_RELEVANCE_MIN
    if family in {"communication", "documentation", "platform"}:
        return CONTEXTUAL_RELEVANCE_MIN
    return CONTEXTUAL_RELEVANCE_MIN if trigger_matched else STRONG_SEMANTIC_RELEVANCE_MIN


def _family_signal_bump(family: str) -> float:
    if family in {"guardrail", "workflow"}:
        return 0.30
    if family in {"communication", "documentation", "platform"}:
        return 0.32
    return 0.30


DETAIL_CONFLICT_WORDS = ("exhaustive", "detailed", "full explanation", "long-form", "fully explain")
CONCISE_CONFLICT_WORDS = ("concise", "brief", "short", "shorter", "no long-form", "less verbose")
TEST_REQUIRED_WORDS = ("test", "verify", "verification", "pytest", "regression")
TEST_SKIP_WORDS = ("no test", "skip test", "without verification", "do not test", "skip verification")
ASK_WORDS = ("ask", "confirm", "manual", "clarify")
NO_ASK_WORDS = ("do not ask", "no confirmation", "directly", "automatic", "no clarification")


def _find_conflicts_among(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            if _records_conflict_for_injection(left, right):
                return [left, right]
    return []


def _records_conflict_for_injection(left: PreferenceRecord, right: PreferenceRecord) -> bool:
    left_text = _scope_text(left)
    right_text = _scope_text(right)
    return (
        _opposes(left_text, right_text, DETAIL_CONFLICT_WORDS, CONCISE_CONFLICT_WORDS)
        or _opposes(left_text, right_text, TEST_REQUIRED_WORDS, TEST_SKIP_WORDS)
        or _opposes(left_text, right_text, ASK_WORDS, NO_ASK_WORDS)
    )


def _opposes(left: str, right: str, first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    return (_has_any(left, first) and _has_any(right, second)) or (_has_any(left, second) and _has_any(right, first))


def _backend_conflict_response(
    agent: str,
    matches: list[dict[str, Any]],
    conflicts: list[PreferenceRecord],
) -> dict[str, Any]:
    options = "; ".join(f"{index + 1}. {record.preference}" for index, record in enumerate(conflicts))
    conflict_ids = [record.id for record in conflicts]
    return {
        "decision": "escalate",
        "agent": agent,
        "matched_preferences": matches,
        "conflict": {
            "preference_ids": conflict_ids,
            "options": [
                {
                    "id": record.id,
                    "title": record.title,
                    "instruction": record.preference,
                    "applies_to": record.applies_to,
                }
                for record in conflicts
            ],
        },
        "conflict_preference_ids": sorted(conflict_ids),
        "agent_instruction": f"Matched user preferences conflict and cannot be applied together. Briefly ask which option to use this time, or whether neither applies: {options}.",
        "escalate": True,
        "clarification_required": True,
        "reason": "The top matched preferences conflict and cannot be concatenated for injection.",
    }


def _no_preference(task: str, gate_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "decision": "no_preference",
        "matched_preferences": [],
        "agent_instruction": "",
        "escalate": True,
        "reason": "Preference store is empty or no sufficiently relevant preference was found.",
        "cold_start_hint": "Capture the current session or import conversation history first, then incrementally update the Markdown preference store.",
        "task": task,
    }
    if gate_summary:
        result["gate_summary"] = gate_summary
        if gate_summary.get("active_candidates"):
            result["reason"] = "No active preference passed the contextual injection gate."
    return result


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))
