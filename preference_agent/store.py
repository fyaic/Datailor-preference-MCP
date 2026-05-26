from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

from .models import PreferenceRecord
from .privacy import redact_sensitive
from .snapshots import create_store_snapshot


PREF_START = "<!-- preference-agent:records-start -->"
PREF_END = "<!-- preference-agent:records-end -->"
RECORD_RE = re.compile(r"```json preference-record\s+(.*?)\s+```", re.S)
BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$")


EMPTY_STORE = """# 个人偏好

## 已确认偏好

暂无已确认偏好。

## 待观察偏好

暂无待观察偏好。
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
        records: list[PreferenceRecord] = []
        records.extend(self._load_simple_bullets(text))
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
        text = self._render(records)
        self.path.write_text(text, encoding="utf-8")

    def _render(self, records: list[PreferenceRecord]) -> str:
        active = [_statement(record) for record in records if record.status == "active"]
        observed = [_statement(record) for record in records if record.status != "active"]
        active = _unique(active)
        observed = _unique(observed)
        lines: list[str] = [
            "# 个人偏好",
            "",
            "## 已确认偏好",
            "",
        ]
        if active:
            lines.extend(f"- {item}" for item in active)
        else:
            lines.append("暂无已确认偏好。")
        lines.extend(["", "## 待观察偏好", ""])
        if observed:
            lines.extend(f"- {item}" for item in observed)
        else:
            lines.append("暂无待观察偏好。")
        lines.append("")
        return "\n".join(lines)

    def _load_simple_bullets(self, text: str) -> list[PreferenceRecord]:
        records: list[PreferenceRecord] = []
        current_status = "active"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                current_status = "needs_review" if "待观察" in stripped else "active"
                continue
            match = BULLET_RE.match(line)
            if not match:
                continue
            statement = match.group(1).strip()
            if not statement or statement.startswith("暂无"):
                continue
            records.append(_record_from_statement(statement, current_status))
        return records


def _statement(record: PreferenceRecord) -> str:
    preference = _ensure_period(_clean_text(record.preference))
    applies_to = _clean_text(record.applies_to)
    if preference.startswith("当"):
        return preference
    if applies_to.startswith("当"):
        return f"{applies_to}，{preference}"
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
    return redact_sensitive(" ".join(str(text).split()).strip().rstrip("。"))


def _ensure_period(text: str) -> str:
    return text if text.endswith(("。", "！", "？", ".", "!", "?")) else text + "。"


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result
