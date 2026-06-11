from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import now_iso
from .privacy import redact_sensitive


def default_injection_log(store_path: str | Path) -> Path:
    configured = os.getenv("PREFERENCE_INJECTION_LOG")
    if configured:
        return Path(configured)
    return Path(store_path).parent / ".injection-log.jsonl"


def log_injection_event(
    store_path: str | Path,
    hook: str,
    agent: str = "agent",
    session_id: str = "",
    task: str = "",
    context: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    source: str = "runtime",
    reason: str = "",
    extra: dict[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    decision = decision or {}
    matched = decision.get("matched_preferences", [])
    instruction = str(decision.get("agent_instruction") or "")
    injected = bool(instruction.strip() and decision.get("decision") == "apply")
    item = {
        "timestamp": now_iso(),
        "hook": hook,
        "source": source,
        "agent": agent,
        "session_id": session_id or _session_id(context or {}),
        "task": _clean(task or str(decision.get("task") or "")),
        "decision": str(decision.get("decision") or ""),
        "matched_count": len(matched) if isinstance(matched, list) else 0,
        "matched_preferences": _matched_view(matched),
        "agent_instruction": _clean(instruction, limit=0),
        "injected": injected,
        "reason": _clean(reason or str(decision.get("reason") or "")),
        "escalate": bool(decision.get("escalate", False)),
        "gate_summary": decision.get("gate_summary") if isinstance(decision.get("gate_summary"), dict) else {},
        "fallback_applied": bool(decision.get("fallback_applied", False)),
        "fallback_instruction": _clean(str(decision.get("fallback_instruction") or ""), limit=500),
    }
    if extra:
        item["extra"] = _safe_extra(extra)
    log_path = Path(path) if path else default_injection_log(store_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def read_injection_log(
    store_path: str | Path,
    path: str | Path | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    log_path = Path(path) if path else default_injection_log(store_path)
    if not log_path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            items.append(data)
    return list(reversed(items[-limit:]))


def injection_log_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(items),
        "injected": sum(1 for item in items if item.get("injected")),
        "escalated": sum(1 for item in items if item.get("escalate")),
        "no_preference": sum(1 for item in items if item.get("decision") == "no_preference"),
    }


def _matched_view(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": str(item.get("id") or ""),
                "title": _clean(str(item.get("title") or ""), limit=120),
                "instruction": _clean(str(item.get("instruction") or ""), limit=300),
                "confidence": str(item.get("confidence") or ""),
                "live_confidence": item.get("live_confidence"),
                "score": item.get("score"),
                "category": str(item.get("category") or ""),
                "gate_reason": str(item.get("gate_reason") or ""),
            }
        )
    return result


def _safe_extra(extra: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(extra, ensure_ascii=False, default=str))


def _session_id(context: dict[str, Any]) -> str:
    value = context.get("session_id") or context.get("session") or ""
    return str(value)


def _clean(text: str, limit: int = 500) -> str:
    cleaned = redact_sensitive(" ".join(str(text or "").split()).strip())
    if limit <= 0:
        return cleaned
    return cleaned if len(cleaned) <= limit else cleaned[: max(0, limit - 3)] + "..."
