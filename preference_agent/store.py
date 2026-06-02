from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

from .capacity import ARCHIVED_STATUSES, enforce_preference_cap, write_cap_review_artifact
from .models import PreferenceRecord
from .privacy import redact_sensitive
from .snapshots import create_store_snapshot


PREF_START = "<!-- preference-agent:records-start -->"
PREF_END = "<!-- preference-agent:records-end -->"
RECORD_RE = re.compile(r"```json preference-record\s+(.*?)\s+```", re.S)
BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$")


EMPTY_STORE = """# Personal Preferences

## Active Preferences

No active preferences yet.

## Observed Preferences

No observed preferences yet.
"""


class MarkdownPreferenceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def ensure(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(EMPTY_STORE, encoding="utf-8")

    def load(self) -> list[PreferenceRecord]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8")
        structured = self._load_structured_records(text)
        if structured:
            return structured
        return self._load_simple_bullets(text)

    def _load_structured_records(self, text: str) -> list[PreferenceRecord]:
        records: list[PreferenceRecord] = []
        for match in RECORD_RE.finditer(text):
            raw = match.group(1)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(PreferenceRecord.from_dict(data))
        return records

    def save(self, records: list[PreferenceRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        create_store_snapshot(self.path, reason="pre-save")
        cap = enforce_preference_cap(records, mode="safe")
        if cap.review_required or cap.merged:
            write_cap_review_artifact(self.path, cap)
        text = self._render(cap.records)
        self.path.write_text(text, encoding="utf-8")

    def _render(self, records: list[PreferenceRecord]) -> str:
        active = [_statement(record) for record in records if record.status == "active"]
        observed = [
            _statement(record)
            for record in records
            if record.status != "active" and str(record.status or "").casefold() not in ARCHIVED_STATUSES
        ]
        active = _unique(active)
        observed = _unique(observed)
        lines: list[str] = [
            "# Personal Preferences",
            "",
            "## Active Preferences",
            "",
        ]
        if active:
            lines.extend(f"- {item}" for item in active)
        else:
            lines.append("No active preferences yet.")
        lines.extend(["", "## Observed Preferences", ""])
        if observed:
            lines.extend(f"- {item}" for item in observed)
        else:
            lines.append("No observed preferences yet.")
        lines.extend(["", PREF_START])
        for record in records:
            lines.extend(
                [
                    "```json preference-record",
                    json.dumps(_redact_record_payload(record.to_dict()), ensure_ascii=False, indent=2, sort_keys=True),
                    "```",
                ]
            )
        lines.extend([PREF_END, ""])
        return "\n".join(lines)

    def _load_simple_bullets(self, text: str) -> list[PreferenceRecord]:
        records: list[PreferenceRecord] = []
        current_status = "active"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                lowered = stripped.casefold()
                current_status = "needs_review" if "observed" in lowered or "pending" in lowered else "active"
                continue
            match = BULLET_RE.match(line)
            if not match:
                continue
            statement = match.group(1).strip()
            if not statement or statement.casefold().startswith("no "):
                continue
            records.append(_record_from_statement(statement, current_status))
        return records


def _statement(record: PreferenceRecord) -> str:
    preference = _ensure_period(_clean_text(record.preference))
    applies_to = _clean_text(record.applies_to)
    if preference.casefold().startswith("when "):
        return preference
    if applies_to.casefold().startswith("when "):
        return f"{applies_to}, {preference}"
    return preference


def _record_from_statement(statement: str, status: str) -> PreferenceRecord:
    return PreferenceRecord(
        id=f"pref-{uuid5(NAMESPACE_URL, statement).hex[:8]}",
        title=statement[:48],
        summary=statement,
        applies_to=statement,
        preference=statement,
        status=status,
        confidence="medium",
    )


def _clean_statement(text: str) -> str:
    return _ensure_period(_clean_text(text))


def _clean_text(text: str) -> str:
    return redact_sensitive(" ".join(str(text).split()).strip().rstrip("."))


def _redact_record_payload(value: object) -> object:
    if isinstance(value, str):
        return redact_sensitive(value)
    if isinstance(value, list):
        return [_redact_record_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_record_payload(item) for key, item in value.items()}
    return value


def _ensure_period(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result
