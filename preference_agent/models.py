from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


@dataclass
class Evidence:
    source: str
    quote: str
    role: str = "user"
    source_type: str = "user_explicit"
    observed_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        role = str(data.get("role", "user"))
        source = str(data.get("source", ""))
        return cls(
            source=source,
            quote=str(data.get("quote", "")),
            role=role,
            source_type=str(data.get("source_type") or infer_evidence_source_type(source, role)),
            observed_at=str(data.get("observed_at") or now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreferenceRecord:
    title: str
    applies_to: str
    preference: str
    id: str = field(default_factory=lambda: f"pref-{uuid4().hex[:8]}")
    summary: str = ""
    triggers: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    confidence: str = "medium"
    status: str = "active"
    evidence: list[Evidence] = field(default_factory=list)
    conflict_notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    version: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PreferenceRecord":
        record = cls(
            id=str(data.get("id") or f"pref-{uuid4().hex[:8]}"),
            title=str(data.get("title", "")).strip(),
            summary=str(data.get("summary", "")).strip(),
            applies_to=str(data.get("applies_to", "")).strip(),
            preference=str(data.get("preference", "")).strip(),
            triggers=_list(data.get("triggers")),
            exceptions=_list(data.get("exceptions")),
            confidence=str(data.get("confidence", "medium")),
            status=str(data.get("status", "active")),
            evidence=[
                Evidence.from_dict(item)
                for item in data.get("evidence", [])
                if isinstance(item, dict)
            ],
            conflict_notes=_list(data.get("conflict_notes")),
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
            version=int(data.get("version") or 1),
        )
        if not record.summary:
            record.summary = record.preference[:120]
        return record

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data

    def touch(self) -> None:
        self.updated_at = now_iso()

    def merge_from(self, candidate: "PreferenceRecord") -> None:
        self.summary = candidate.summary or self.summary
        self.applies_to = candidate.applies_to or self.applies_to
        self.preference = candidate.preference or self.preference
        self.triggers = unique_strings([*self.triggers, *candidate.triggers])
        self.exceptions = unique_strings([*self.exceptions, *candidate.exceptions])
        self.evidence.extend(candidate.evidence)
        self.version += 1
        self.touch()

    def add_evidence_from(self, candidate: "PreferenceRecord") -> None:
        self.triggers = unique_strings([*self.triggers, *candidate.triggers])
        self.exceptions = unique_strings([*self.exceptions, *candidate.exceptions])
        self.evidence.extend(candidate.evidence)
        self.touch()


@dataclass
class SessionMessage:
    role: str
    content: str
    created_at: str = ""


@dataclass
class Session:
    source: str
    session_id: str
    messages: list[SessionMessage]
    created_at: str = ""


def unique_strings(items: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
            if limit is not None and len(result) >= limit:
                break
    return result


def infer_evidence_source_type(source: str, role: str) -> str:
    lowered = f"{source} {role}".casefold()
    if any(marker in lowered for marker in ("confirm", "confirmation", "ui_feedback")):
        return "user_confirm"
    if any(marker in lowered for marker in ("behavior", "action", "hook")) or role == "system":
        return "action_signal"
    if any(marker in lowered for marker in ("context", "assistant")):
        return "context_inferred"
    return "user_explicit"


def _infer_evidence_source_type(source: str, role: str) -> str:
    return infer_evidence_source_type(source, role)
