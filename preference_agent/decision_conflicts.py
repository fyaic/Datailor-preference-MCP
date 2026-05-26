from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import PreferenceRecord, now_iso
from .refinement import conflict_likely
from .store import MarkdownPreferenceStore


ASKED_CONFLICTS_FILENAME = ".asked_conflicts.jsonl"
_ASKED_CONFLICTS: set[str] = set()

DETAIL_WORDS = ("详细", "展开", "完整", "充分解释", "长文", "exhaustive", "detailed")
CONCISE_WORDS = ("简洁", "简短", "短一点", "不要长文", "别啰嗦", "少废话", "concise", "brief", "short")
TEST_REQUIRED_WORDS = ("测试", "验证", "pytest", "test", "verify", "verification", "回归")
TEST_SKIP_WORDS = ("不用测试", "不要测试", "跳过测试", "不测", "无需验证", "no test", "skip test", "without verification")
ASK_WORDS = ("确认", "询问", "反问", "ask", "confirm", "manual")
NO_ASK_WORDS = ("不用问", "不要问", "无需确认", "直接", "自动", "不反问", "do not ask", "no confirmation")


@dataclass(frozen=True)
class AskDecision:
    should_ask: bool
    key: str
    reason: str
    last_asked_at: str = ""
    resolved_at: str = ""


def apply_decision_conflict_policy(
    decision: dict[str, Any],
    records: list[PreferenceRecord],
    store_path: str | Path,
    task: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matches = decision.get("matched_preferences")
    decision_type = str(decision.get("decision") or "")
    if decision_type not in {"apply", "escalate", "needs_clarification"} or not isinstance(matches, list) or len(matches) < 2:
        return decision

    by_id = {record.id: record for record in records}
    matched_records = [by_id[str(item.get("id"))] for item in matches if isinstance(item, dict) and str(item.get("id")) in by_id]
    groups = _conflict_groups(matched_records)
    if not groups:
        return decision

    group = groups[0]
    conflict_ids = [record.id for record in group]
    ask = should_ask_conflict(
        store_path=store_path,
        conflict_ids=conflict_ids,
        task=task,
        context=context or {},
    )
    conflict_payload = _conflict_payload(group, ask)
    if ask.should_ask:
        return {
            **decision,
            "decision": "escalate",
            "escalate": True,
            "matched_preferences": [item for item in matches if isinstance(item, dict) and item.get("id") in conflict_ids],
            "conflict": conflict_payload,
            "agent_instruction": _ask_instruction(group, ask.key),
            "reason": "命中的 active 偏好之间存在冲突，不能同时注入，需让用户选择。",
        }

    safe_matches = [item for item in matches if not isinstance(item, dict) or item.get("id") not in conflict_ids]
    if safe_matches:
        combined = "；".join(str(item.get("instruction") or "") for item in safe_matches[:3] if isinstance(item, dict))
        return {
            **decision,
            "matched_preferences": safe_matches,
            "conflict_suppressed": conflict_payload,
            "agent_instruction": f"在回复或执行前应用这些用户偏好：{combined}",
            "reason": "已排除一组未解决或已询问过的冲突偏好，仅注入非冲突偏好。",
        }
    return {
        **decision,
        "decision": "no_preference",
        "escalate": False,
        "matched_preferences": [],
        "conflict_suppressed": conflict_payload,
        "agent_instruction": "",
        "reason": "命中的偏好互相冲突，且该冲突组合已询问过或已解决；为避免重复询问，本轮不注入冲突偏好。",
    }


def should_ask_conflict(
    store_path: str | Path,
    conflict_ids: list[str],
    task: str = "",
    context: dict[str, Any] | None = None,
    cooldown_hours: int | None = None,
) -> AskDecision:
    key = conflict_key(conflict_ids)
    path = asked_conflicts_path(store_path)
    memory_key = f"{path.resolve()}::{key}"
    events = _read_events(path)
    resolved = _latest_event(events, key, "resolved")
    if resolved:
        return AskDecision(
            should_ask=False,
            key=key,
            reason="resolved",
            resolved_at=str(resolved.get("created_at") or ""),
        )
    asked = _latest_event(events, key, "asked")
    if memory_key in _ASKED_CONFLICTS:
        return AskDecision(
            should_ask=False,
            key=key,
            reason="asked_in_process",
            last_asked_at=str((asked or {}).get("created_at") or ""),
        )
    if asked:
        created_at = str(asked.get("created_at") or "")
        if _within_cooldown(created_at, cooldown_hours if cooldown_hours is not None else _cooldown_hours()):
            reason = "asked_recently"
        else:
            reason = "asked_before"
        _ASKED_CONFLICTS.add(memory_key)
        return AskDecision(should_ask=False, key=key, reason=reason, last_asked_at=created_at)

    _ASKED_CONFLICTS.add(memory_key)
    _append_event(
        path,
        {
            "event": "asked",
            "key": key,
            "conflict_ids": sorted(conflict_ids),
            "task": task,
            "context": context or {},
        },
    )
    return AskDecision(should_ask=True, key=key, reason="first_ask")


def resolve_preference_conflict(
    store_path: str | Path,
    conflict_key_value: str,
    resolution: str,
    selected_preference_id: str = "",
    conflict_ids: list[str] | None = None,
    user_feedback: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    records = store.load()
    path = asked_conflicts_path(store_path)
    ids = sorted(conflict_ids or _ids_for_key(_read_events(path), conflict_key_value))
    if not ids:
        ids = conflict_key_value.split("-")
    affected = [record for record in records if record.id in ids]
    if not affected:
        return {
            "ok": False,
            "conflict_key": conflict_key_value,
            "error": "conflict_preferences_not_found",
        }

    normalized = resolution.strip().lower()
    selected_preference_id = selected_preference_id.strip()
    if normalized in {"prefer", "select", "selected", "choose", "keep_one"} and selected_preference_id:
        for record in affected:
            record.status = "active" if record.id == selected_preference_id else "needs_review"
            if record.id != selected_preference_id:
                record.conflict_notes.append(f"{now_iso()} | resolved_conflict={conflict_key_value} | deactivated_by_user_choice")
            record.touch()
    elif normalized in {"neither", "none", "both_not_apply", "exception"}:
        for record in affected:
            record.status = "needs_review"
            record.exceptions.append(user_feedback or "用户选择本上下文两个冲突偏好都不适用。")
            record.conflict_notes.append(f"{now_iso()} | resolved_conflict={conflict_key_value} | exception={user_feedback}")
            record.touch()
    elif normalized in {"custom", "correct"} and user_feedback.strip():
        target = next((record for record in affected if record.id == selected_preference_id), affected[0])
        target.preference = user_feedback.strip()
        target.summary = user_feedback.strip()
        target.title = user_feedback.strip()[:48]
        target.status = "active"
        target.confidence = "high"
        target.touch()
        for record in affected:
            if record.id != target.id:
                record.status = "needs_review"
                record.touch()
    else:
        return {
            "ok": False,
            "conflict_key": conflict_key_value,
            "error": "unsupported_resolution",
        }

    store.save(records)
    _append_event(
        path,
        {
            "event": "resolved",
            "key": conflict_key_value,
            "conflict_ids": ids,
            "resolution": normalized,
            "selected_preference_id": selected_preference_id,
            "user_feedback": user_feedback,
            "context": context or {},
        },
    )
    return {
        "ok": True,
        "conflict_key": conflict_key_value,
        "resolution": normalized,
        "selected_preference_id": selected_preference_id,
        "updated_preferences": [
            {"id": record.id, "status": record.status, "preference": record.preference}
            for record in affected
        ],
        "asked_conflicts_file": str(path),
    }


def conflict_key(conflict_ids: list[str]) -> str:
    return "-".join(sorted(str(item) for item in conflict_ids if str(item).strip()))


def asked_conflicts_path(store_path: str | Path) -> Path:
    configured = os.getenv("PREFERENCE_ASKED_CONFLICTS_FILE")
    if configured:
        return Path(configured)
    return Path(store_path).parent / ASKED_CONFLICTS_FILENAME


def _conflict_groups(records: list[PreferenceRecord]) -> list[list[PreferenceRecord]]:
    edges: dict[str, set[str]] = {record.id: set() for record in records}
    by_id = {record.id: record for record in records}
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            if _records_conflict(left, right):
                edges[left.id].add(right.id)
                edges[right.id].add(left.id)
    groups: list[list[PreferenceRecord]] = []
    seen: set[str] = set()
    for record in records:
        if record.id in seen or not edges[record.id]:
            continue
        stack = [record.id]
        component: list[PreferenceRecord] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(by_id[current])
            stack.extend(edges[current] - seen)
        if len(component) >= 2:
            groups.append(component)
    return groups


def _records_conflict(left: PreferenceRecord, right: PreferenceRecord) -> bool:
    if conflict_likely(left, right):
        return True
    left_text = _record_text(left)
    right_text = _record_text(right)
    return (
        _opposes(left_text, right_text, DETAIL_WORDS, CONCISE_WORDS)
        or _opposes(left_text, right_text, TEST_REQUIRED_WORDS, TEST_SKIP_WORDS)
        or _opposes(left_text, right_text, ASK_WORDS, NO_ASK_WORDS)
    )


def _opposes(left: str, right: str, first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    return (_has_any(left, first) and _has_any(right, second)) or (_has_any(left, second) and _has_any(right, first))


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(word.casefold() in lowered for word in words)


def _record_text(record: PreferenceRecord) -> str:
    return " ".join([record.title, record.summary, record.applies_to, record.preference, *record.triggers])


def _conflict_payload(records: list[PreferenceRecord], ask: AskDecision) -> dict[str, Any]:
    return {
        "key": ask.key,
        "reason": ask.reason,
        "should_ask": ask.should_ask,
        "last_asked_at": ask.last_asked_at,
        "resolved_at": ask.resolved_at,
        "preference_ids": [record.id for record in records],
        "options": [
            {
                "id": record.id,
                "title": record.title,
                "instruction": record.preference,
                "applies_to": record.applies_to,
            }
            for record in records
        ],
    }


def _ask_instruction(records: list[PreferenceRecord], key: str) -> str:
    options = "；".join(f"{index + 1}. {record.preference}" for index, record in enumerate(records))
    return (
        "检测到本轮命中的用户偏好互相冲突，不能同时应用。"
        f"请简短反问用户本次采用哪一种，或是否两个都不适用：{options}。"
        f"用户回答后调用 `resolve_preference_conflict`，conflict_key={key}。"
    )


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def _append_event(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"created_at": now_iso(), **item}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _latest_event(events: list[dict[str, Any]], key: str, event: str) -> dict[str, Any] | None:
    for item in reversed(events):
        if item.get("key") == key and item.get("event") == event:
            return item
    return None


def _ids_for_key(events: list[dict[str, Any]], key: str) -> list[str]:
    for item in reversed(events):
        if item.get("key") == key and isinstance(item.get("conflict_ids"), list):
            return [str(value) for value in item["conflict_ids"]]
    return []


def _within_cooldown(created_at: str, hours: int) -> bool:
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError:
        return True
    return datetime.now(timestamp.tzinfo) - timestamp < timedelta(hours=hours)


def _cooldown_hours() -> int:
    try:
        return int(os.getenv("PREFERENCE_CONFLICT_ASK_COOLDOWN_HOURS", "24"))
    except ValueError:
        return 24
