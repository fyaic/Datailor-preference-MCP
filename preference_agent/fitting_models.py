from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from .models import Evidence, PreferenceRecord, now_iso, unique_strings


INSIGHT_KINDS = {
    "preference",
    "workflow",
    "error_pattern",
    "tool_quirk",
    "recovery_strategy",
    "handoff_pattern",
    "app_usage",
    "personal_habit",
}
ROT_TYPES = {
    "duplicate",
    "overlap",
    "conflict",
    "stale",
    "negative_feedback",
    "archive_candidate",
}
ROT_ACTIONS = {
    "merge",
    "archive",
    "downgrade",
    "mark_needs_review",
    "keep",
    "manual_review",
}


@dataclass
class FittingInstruction:
    text: str = ""
    focus: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    source: str = "cli"
    max_chars: int = 4096
    truncated: bool = False
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_text(cls, text: str = "", source: str = "cli", max_chars: int = 4096) -> "FittingInstruction":
        normalized = " ".join(str(text or "").split())
        truncated = False
        if len(normalized) > max_chars:
            normalized = normalized[:max_chars]
            truncated = True
        focus, ignore = _parse_instruction_lists(normalized)
        return cls(
            text=normalized,
            focus=focus,
            ignore=ignore,
            source=source,
            max_chars=max_chars,
            truncated=truncated,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FittingInstruction":
        return cls(
            text=str(data.get("text") or ""),
            focus=_string_list(data.get("focus")),
            ignore=_string_list(data.get("ignore")),
            source=str(data.get("source") or "cli"),
            max_chars=int(data.get("max_chars") or 4096),
            truncated=bool(data.get("truncated", False)),
            created_at=str(data.get("created_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def should_ignore(self, text: str) -> bool:
        blob = text.casefold()
        return any(_instruction_phrase_matches(phrase, blob) for phrase in self.ignore)

    def matches_focus(self, text: str) -> bool:
        if not self.focus:
            return True
        blob = text.casefold()
        return any(_instruction_phrase_matches(phrase, blob) for phrase in self.focus)


@dataclass
class InsightRecord:
    kind: str
    title: str
    summary: str
    guidance: str
    applies_to: str = ""
    triggers: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    confidence: str = "medium"
    status: str = "draft"
    evidence: list[Evidence] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"insight-{uuid4().hex[:8]}")
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    version: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InsightRecord":
        kind = str(data.get("kind") or "preference")
        if kind not in INSIGHT_KINDS:
            kind = "preference"
        return cls(
            id=str(data.get("id") or f"insight-{uuid4().hex[:8]}"),
            kind=kind,
            title=str(data.get("title") or "").strip(),
            summary=str(data.get("summary") or "").strip(),
            guidance=str(data.get("guidance") or "").strip(),
            applies_to=str(data.get("applies_to") or "").strip(),
            triggers=_string_list(data.get("triggers")),
            exceptions=_string_list(data.get("exceptions")),
            confidence=str(data.get("confidence") or "medium"),
            status=str(data.get("status") or "draft"),
            evidence=[
                Evidence.from_dict(item)
                for item in data.get("evidence", [])
                if isinstance(item, dict)
            ],
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
            version=int(data.get("version") or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data

    def to_preference_record(self) -> PreferenceRecord:
        return PreferenceRecord(
            title=self.title[:48] or self.summary[:48],
            summary=self.summary or self.guidance,
            applies_to=self.applies_to or self.summary,
            preference=self.guidance or self.summary,
            triggers=self.triggers,
            exceptions=self.exceptions,
            confidence=self.confidence,
            status="needs_review",
            evidence=list(self.evidence),
        )


@dataclass
class RotSuggestion:
    type: str
    target_ids: list[str]
    reason: str
    proposed_action: str
    risk: str = "medium"
    proposed_record: dict[str, Any] | None = None
    evidence: list[str] = field(default_factory=list)
    status: str = "draft"
    id: str = field(default_factory=lambda: f"rot-{uuid4().hex[:8]}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RotSuggestion":
        suggestion_type = str(data.get("type") or "overlap")
        if suggestion_type not in ROT_TYPES:
            suggestion_type = "overlap"
        action = str(data.get("proposed_action") or "manual_review")
        if action not in ROT_ACTIONS:
            action = "manual_review"
        proposed = data.get("proposed_record") if isinstance(data.get("proposed_record"), dict) else None
        return cls(
            id=str(data.get("id") or f"rot-{uuid4().hex[:8]}"),
            type=suggestion_type,
            target_ids=_string_list(data.get("target_ids")),
            reason=str(data.get("reason") or ""),
            proposed_action=action,
            risk=str(data.get("risk") or "medium"),
            proposed_record=proposed,
            evidence=_string_list(data.get("evidence")),
            status=str(data.get("status") or "draft"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApplyChange:
    change_id: str
    type: str
    target_store: str
    record_id: str
    status: str = "pending"
    risk: str = "low"
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApplyChange":
        return cls(
            change_id=str(data.get("change_id") or f"chg-{uuid4().hex[:8]}"),
            type=str(data.get("type") or ""),
            target_store=str(data.get("target_store") or "preference"),
            record_id=str(data.get("record_id") or ""),
            status=str(data.get("status") or "pending"),
            risk=str(data.get("risk") or "low"),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApplyPlan:
    job_id: str
    changes: list[ApplyChange] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApplyPlan":
        return cls(
            job_id=str(data.get("job_id") or ""),
            changes=[
                ApplyChange.from_dict(item)
                for item in data.get("changes", [])
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "changes": [item.to_dict() for item in self.changes]}


@dataclass
class FittingJobResult:
    job_id: str
    status: str
    store_file: str
    job_dir: str
    report_file: str
    result_file: str
    instructions: FittingInstruction
    insights: list[InsightRecord] = field(default_factory=list)
    rot_suggestions: list[RotSuggestion] = field(default_factory=list)
    apply_plan: ApplyPlan | None = None
    stats: dict[str, int] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    ignored_inputs: list[dict[str, str]] = field(default_factory=list)
    next_commands: list[str] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FittingJobResult":
        return cls(
            job_id=str(data.get("job_id") or ""),
            status=str(data.get("status") or ""),
            store_file=str(data.get("store_file") or ""),
            job_dir=str(data.get("job_dir") or ""),
            report_file=str(data.get("report_file") or ""),
            result_file=str(data.get("result_file") or ""),
            instructions=FittingInstruction.from_dict(data.get("instructions") if isinstance(data.get("instructions"), dict) else {}),
            insights=[
                InsightRecord.from_dict(item)
                for item in data.get("insights", [])
                if isinstance(item, dict)
            ],
            rot_suggestions=[
                RotSuggestion.from_dict(item)
                for item in data.get("rot_suggestions", [])
                if isinstance(item, dict)
            ],
            apply_plan=ApplyPlan.from_dict(data["apply_plan"]) if isinstance(data.get("apply_plan"), dict) else None,
            stats={str(key): int(value) for key, value in (data.get("stats") or {}).items()},
            inputs=_string_list(data.get("inputs")),
            ignored_inputs=[
                {"source": str(item.get("source") or ""), "reason": str(item.get("reason") or "")}
                for item in data.get("ignored_inputs", [])
                if isinstance(item, dict)
            ],
            next_commands=_string_list(data.get("next_commands")),
            error=str(data.get("error") or ""),
            created_at=str(data.get("created_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "store_file": self.store_file,
            "job_dir": self.job_dir,
            "report_file": self.report_file,
            "result_file": self.result_file,
            "instructions": self.instructions.to_dict(),
            "insights": [item.to_dict() for item in self.insights],
            "rot_suggestions": [item.to_dict() for item in self.rot_suggestions],
            "apply_plan": self.apply_plan.to_dict() if self.apply_plan else None,
            "stats": self.stats,
            "inputs": self.inputs,
            "ignored_inputs": self.ignored_inputs,
            "next_commands": self.next_commands,
            "error": self.error,
            "created_at": self.created_at,
        }


def _parse_instruction_lists(text: str) -> tuple[list[str], list[str]]:
    focus: list[str] = []
    ignore: list[str] = []
    parts = [part.strip(" .。；;") for part in re.split(r"[;；\n]+", text) if part.strip()]
    for part in parts:
        lowered = part.casefold()
        if lowered.startswith(("focus on ", "focus ", "关注", "重点关注")):
            focus.append(_strip_instruction_prefix(part, ("focus on", "focus", "关注", "重点关注")))
        elif lowered.startswith(("ignore ", "忽略", "不要关注", "不看")):
            ignore.append(_strip_instruction_prefix(part, ("ignore", "忽略", "不要关注", "不看")))
    return unique_strings(focus), unique_strings(ignore)


def _strip_instruction_prefix(text: str, prefixes: tuple[str, ...]) -> str:
    stripped = text.strip()
    lowered = stripped.casefold()
    for prefix in prefixes:
        if lowered.startswith(prefix.casefold()):
            return stripped[len(prefix) :].strip(" :：,，")
    return stripped


def _instruction_phrase_matches(phrase: str, blob: str) -> bool:
    normalized = phrase.casefold().strip()
    if not normalized:
        return False
    if normalized in blob:
        return True
    tokens = [token for token in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", normalized) if len(token) >= 2]
    if not tokens:
        return False
    return any(token in blob for token in tokens)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []
