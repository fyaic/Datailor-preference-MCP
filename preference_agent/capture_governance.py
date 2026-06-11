from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .models import now_iso
from .quality import has_guidance_terms, looks_like_one_off_task, should_recall_user_text


TAXONOMY = {
    "communication_style",
    "execution_policy",
    "verification_standard",
    "tool_preference",
    "risk_boundary",
    "documentation_habit",
    "review_standard",
    "negative_constraint",
    "scope_exception",
}

SCOPES = {"global", "project", "session", "none"}
DURABILITY = {"durable", "likely_durable", "temporary", "one_off"}


@dataclass
class CaptureDiagnostic:
    candidate_id: str
    is_candidate: bool
    should_extract: bool
    reason_codes: list[str]
    signal_type: str = "scope_exception"
    scope: str = "none"
    durability: str = "one_off"
    confidence: float = 0.0
    backend: str = "heuristic"
    summary: str = ""
    evidence_quote: str = ""
    context_hint: str = ""
    created_at: str = field(default_factory=now_iso)

    @property
    def primary_reason(self) -> str:
        return self.reason_codes[0] if self.reason_codes else "fast_gate_no_match"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["primary_reason"] = self.primary_reason
        return data


def evaluate_capture_candidate(text: str, context_hint: str = "", assistant_response: str = "") -> CaptureDiagnostic:
    cleaned = _clean(text)
    candidate_id = _candidate_id(cleaned, context_hint)
    if len(cleaned) < 6:
        return CaptureDiagnostic(
            candidate_id=candidate_id,
            is_candidate=False,
            should_extract=False,
            reason_codes=["fast_gate_no_match"],
            evidence_quote=cleaned,
            context_hint=context_hint,
            summary="Text is too short to carry a durable preference signal.",
        )
    one_off = looks_like_one_off_task(cleaned)
    stable = should_recall_user_text(cleaned)
    implicit = _implicit_preference_signal(cleaned, context_hint=context_hint, assistant_response=assistant_response)
    if one_off and not stable and not implicit:
        return CaptureDiagnostic(
            candidate_id=candidate_id,
            is_candidate=False,
            should_extract=False,
            reason_codes=["one_off_filtered"],
            signal_type="scope_exception",
            scope=_scope_for_text(cleaned),
            durability="one_off",
            confidence=0.75,
            evidence_quote=cleaned,
            context_hint=context_hint,
            summary="Looks like a one-off task, path, URL, issue, or temporary instruction.",
        )
    if stable or implicit:
        reason_codes: list[str] = []
        if stable:
            reason_codes.append("fast_gate_candidate")
        if implicit:
            reason_codes.extend(["semantic_candidate", "heuristic_candidate"])
        reason_codes = _unique(reason_codes)
        signal_type = _signal_type(cleaned)
        durability = _durability(cleaned, stable=stable, implicit=implicit)
        return CaptureDiagnostic(
            candidate_id=candidate_id,
            is_candidate=True,
            should_extract=durability not in {"temporary", "one_off"},
            reason_codes=reason_codes,
            signal_type=signal_type,
            scope=_scope_for_text(cleaned),
            durability=durability,
            confidence=_confidence(cleaned, stable=stable, implicit=implicit),
            evidence_quote=cleaned,
            context_hint=context_hint,
            summary=_summary(cleaned, signal_type),
        )
    if _assistant_prompt_user_confirmed(assistant_response, cleaned):
        return CaptureDiagnostic(
            candidate_id=candidate_id,
            is_candidate=True,
            should_extract=True,
            reason_codes=["heuristic_candidate"],
            signal_type=_signal_type(cleaned),
            scope=_scope_for_text(cleaned),
            durability="likely_durable",
            confidence=0.62,
            evidence_quote=cleaned,
            context_hint=context_hint,
            summary="User answered a preference-oriented assistant prompt.",
        )
    return CaptureDiagnostic(
        candidate_id=candidate_id,
        is_candidate=False,
        should_extract=False,
        reason_codes=["fast_gate_no_match"],
        evidence_quote=cleaned,
        context_hint=context_hint,
        summary="No durable user preference candidate was detected.",
    )


def classify_capture_candidate(
    text: str,
    context_hint: str = "",
    backend: Any | None = None,
) -> CaptureDiagnostic:
    if backend is None:
        return evaluate_capture_candidate(text, context_hint=context_hint)
    classifier = getattr(backend, "classify_preference_candidate", None)
    if not callable(classifier):
        return evaluate_capture_candidate(text, context_hint=context_hint)
    try:
        result = classifier(text, context_hint=context_hint)
    except Exception:
        diagnostic = evaluate_capture_candidate(text, context_hint=context_hint)
        diagnostic.backend = "heuristic"
        diagnostic.reason_codes = _unique(["model_failed_fallback", *diagnostic.reason_codes])
        return diagnostic
    diagnostic = _diagnostic_from_model_result(text, context_hint, result)
    diagnostic.backend = _backend_label(backend)
    return diagnostic


def _diagnostic_from_model_result(text: str, context_hint: str, result: Any) -> CaptureDiagnostic:
    cleaned = _clean(text)
    data = result if isinstance(result, dict) else {}
    has_signal = bool(data.get("has_preference_signal", data.get("is_candidate", False)))
    should_extract = bool(data.get("should_extract", has_signal))
    reason_codes = [str(item) for item in data.get("reason_codes", []) if str(item).strip()] if isinstance(data.get("reason_codes"), list) else []
    if has_signal and "semantic_candidate" not in reason_codes:
        reason_codes.append("semantic_candidate")
    if not has_signal and not reason_codes:
        reason_codes.append("fast_gate_no_match")
    durability = _normalize_choice(str(data.get("durability") or ""), DURABILITY, "likely_durable" if has_signal else "one_off")
    scope = _normalize_choice(str(data.get("scope") or ""), SCOPES, "global" if has_signal else "none")
    signal_type = _normalize_choice(str(data.get("signal_type") or ""), TAXONOMY, _signal_type(cleaned))
    confidence = _coerce_confidence(data.get("confidence"), default=0.72 if has_signal else 0.2)
    return CaptureDiagnostic(
        candidate_id=_candidate_id(cleaned, context_hint),
        is_candidate=has_signal,
        should_extract=should_extract and has_signal and durability not in {"temporary", "one_off"},
        reason_codes=_unique(reason_codes),
        signal_type=signal_type,
        scope=scope,
        durability=durability,
        confidence=confidence,
        summary=str(data.get("summary") or _summary(cleaned, signal_type)),
        evidence_quote=cleaned,
        context_hint=context_hint,
    )


def _implicit_preference_signal(text: str, context_hint: str = "", assistant_response: str = "") -> bool:
    lowered = text.casefold()
    joined = f"{context_hint} {assistant_response} {text}".casefold()
    if _temporary_only(lowered):
        return False
    correction = _has_any(
        lowered,
        (
            "not what i meant",
            "you misunderstood",
            "wrong",
            "incorrect",
            "i meant",
            "that's not",
            "again",
            "before i approve",
            "after i approve",
        ),
    ) or _has_any(
        text,
        (
            "不是这个意思",
            "不是我要的",
            "误解",
            "又直接",
            "刚才是要",
            "我刚才要",
            "我说的是",
            "先分析",
            "不要动代码",
            "先别改",
        ),
    )
    if correction and (
        has_guidance_terms(text)
        or _has_any(lowered, ("review first", "patch", "approve", "approval", "analyze first", "do not patch", "don't patch"))
        or _has_any(text, ("先分析", "不要动代码", "先别改", "回读", "测试", "评审", "确认"))
    ):
        return True
    if _has_any(lowered, ("i always", "i usually", "i keep asking", "i keep wanting", "not a one-time request")):
        return True
    if _has_any(text, ("一直希望", "不是一次性要求", "总是希望", "反复说过")):
        return True
    if re.search(r"^(after|when)\b", lowered) and has_guidance_terms(text):
        return True
    if re.search(r"^(当|在|每当).{0,24}(时|后)", text) and has_guidance_terms(text):
        return True
    if _has_any(joined, ("should i", "do you want", "do we need", "confirm")) and _has_any(lowered, ("yes", "always", "default", "from now on")):
        return True
    return False


def _assistant_prompt_user_confirmed(assistant_response: str, user_text: str) -> bool:
    assistant = assistant_response.casefold()
    user = user_text.casefold()
    return _has_any(assistant, ("should i", "do you want", "do we need", "confirm")) and _has_any(user, ("yes", "always", "default", "以后", "每次", "需要"))


def _signal_type(text: str) -> str:
    lowered = text.casefold()
    if _has_any(lowered, ("review", "bug", "line reference", "code review", "patch after approval")) or _has_any(text, ("评审", "审查")):
        return "review_standard"
    if _has_any(lowered, ("test", "verify", "read back", "encoding", "mojibake")) or _has_any(text, ("测试", "验证", "回读", "乱码", "编码")):
        return "verification_standard"
    if _has_any(lowered, ("ask", "approve", "approval", "do not patch", "don't patch", "analyze first")) or _has_any(text, ("先分析", "不要动代码", "先别改", "确认")):
        return "execution_policy"
    if _has_any(lowered, ("document", "documentation", "markdown", "save the conclusion")) or _has_any(text, ("文档", "沉淀", "复盘")):
        return "documentation_habit"
    if _has_any(lowered, ("tool", "api", "cli", "mcp", "linear", "obsidian")) or _has_any(text, ("工具", "接口")):
        return "tool_preference"
    if _has_any(lowered, ("do not", "avoid", "never", "forbid")) or _has_any(text, ("不要", "别", "禁止")):
        return "negative_constraint"
    if _has_any(lowered, ("concise", "brief", "reply", "answer", "language")) or _has_any(text, ("简洁", "回复", "回答", "中文")):
        return "communication_style"
    return "execution_policy"


def _scope_for_text(text: str) -> str:
    lowered = text.casefold()
    if _temporary_only(lowered):
        return "session"
    if _has_any(lowered, ("this project", "this repo", "this issue")) or _has_any(text, ("这个项目", "这个仓库", "这个 issue")):
        return "project"
    return "global"


def _durability(text: str, *, stable: bool, implicit: bool) -> str:
    lowered = text.casefold()
    if _temporary_only(lowered):
        return "temporary"
    if stable and _has_any(lowered, ("from now on", "default", "every time", "always", "must", "以后", "默认", "每次", "必须")):
        return "durable"
    if implicit:
        return "likely_durable"
    return "likely_durable"


def _confidence(text: str, *, stable: bool, implicit: bool) -> float:
    lowered = text.casefold()
    score = 0.45
    if stable:
        score += 0.25
    if implicit:
        score += 0.18
    if _has_any(lowered, ("always", "default", "from now on", "every time", "must", "以后", "默认", "每次", "必须")):
        score += 0.1
    if looks_like_one_off_task(text):
        score -= 0.2
    return round(max(0.0, min(1.0, score)), 3)


def _summary(text: str, signal_type: str) -> str:
    if signal_type == "execution_policy":
        return "Candidate execution policy preference."
    if signal_type == "verification_standard":
        return "Candidate verification standard preference."
    if signal_type == "review_standard":
        return "Candidate review standard preference."
    if signal_type == "documentation_habit":
        return "Candidate documentation habit preference."
    return f"Candidate {signal_type} preference."


def _temporary_only(lowered: str) -> bool:
    return _has_any(
        lowered,
        (
            "today",
            "this time",
            "for now",
            "temporary",
            "right now",
            "今天",
            "这次",
            "本次",
            "暂时",
            "临时",
        ),
    )


def _coerce_confidence(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return round(max(0.0, min(1.0, float(value))), 3)
    lowered = str(value or "").casefold()
    return {"high": 0.9, "medium": 0.65, "low": 0.38}.get(lowered, default)


def _normalize_choice(value: str, allowed: set[str], default: str) -> str:
    normalized = value.strip().casefold().replace("-", "_")
    return normalized if normalized in allowed else default


def _candidate_id(text: str, context_hint: str = "") -> str:
    return str(uuid5(NAMESPACE_URL, f"capture:{text}:{context_hint}"))


def _backend_label(backend: Any) -> str:
    name = backend.__class__.__name__
    if "OpenAI" in name:
        return "openai-compatible"
    if "Heuristic" in name:
        return "heuristic"
    return name


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item).strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result
