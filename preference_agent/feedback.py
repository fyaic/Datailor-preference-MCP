from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import now_iso
from .paths import default_feedback_log as default_feedback_log_path


@dataclass
class PreferenceFeedback:
    feedback_type: str
    user_feedback: str
    preference_id: str = ""
    preference_text: str = ""
    agent: str = "agent"
    task: str = ""
    source: str = "manual"
    context: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"fb-{uuid4().hex[:8]}")
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_feedback_log() -> Path:
    return default_feedback_log_path()


def record_feedback(
    feedback_type: str,
    user_feedback: str,
    preference_id: str = "",
    preference_text: str = "",
    agent: str = "agent",
    task: str = "",
    source: str = "manual",
    context: dict[str, Any] | None = None,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    feedback_type = feedback_type.strip().lower()
    if feedback_type not in {"correction", "confirmation", "usage", "rejection"}:
        raise ValueError("feedback_type must be correction, confirmation, usage, or rejection")
    item = PreferenceFeedback(
        feedback_type=feedback_type,
        user_feedback=user_feedback.strip(),
        preference_id=preference_id.strip(),
        preference_text=preference_text.strip(),
        agent=agent.strip() or "agent",
        task=task.strip(),
        source=source.strip() or "manual",
        context=context or {},
    )
    path = Path(log_path) if log_path else default_feedback_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
    summary = feedback_report(path)
    return {
        "ok": True,
        "feedback": item.to_dict(),
        "feedback_log": str(path),
        "recommendation": _recommendation_for(item, summary),
    }


def feedback_report(log_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(log_path) if log_path else default_feedback_log()
    items = _read_feedback(path)
    by_preference: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item.get("preference_id") or item.get("preference_text") or "unknown"
        bucket = by_preference.setdefault(
            key,
            {
                "preference": key,
                "usage": 0,
                "correction": 0,
                "confirmation": 0,
                "rejection": 0,
                "recommendation": "observe",
            },
        )
        feedback_type = str(item.get("feedback_type", ""))
        if feedback_type in bucket:
            bucket[feedback_type] += 1
    for bucket in by_preference.values():
        bucket["recommendation"] = _bucket_recommendation(bucket)
    return {
        "feedback_log": str(path),
        "total": len(items),
        "by_preference": list(by_preference.values()),
    }


def _read_feedback(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def _recommendation_for(item: PreferenceFeedback, report: dict[str, Any]) -> str:
    key = item.preference_id or item.preference_text or "unknown"
    for bucket in report.get("by_preference", []):
        if bucket.get("preference") == key:
            return str(bucket.get("recommendation", "observe"))
    return "observe"


def _bucket_recommendation(bucket: dict[str, Any]) -> str:
    corrections = int(bucket.get("correction", 0))
    confirmations = int(bucket.get("confirmation", 0))
    rejections = int(bucket.get("rejection", 0))
    usage = int(bucket.get("usage", 0))
    if rejections >= 1 or corrections >= 3:
        return "review_needed"
    if confirmations >= 2 and corrections == 0:
        return "promote"
    if usage >= 5 and corrections == 0:
        return "stable"
    return "observe"
