from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .backends import HeuristicBackend
from .injection_log import log_injection_event
from .models import PreferenceRecord, now_iso
from .paths import default_injection_dir as default_injection_dir_path
from .store import MarkdownPreferenceStore


MANAGED_START = "<!-- personal-preference-agent:start -->"
MANAGED_END = "<!-- personal-preference-agent:end -->"


@dataclass
class InjectionResult:
    static_rules_file: str
    fallback_json_file: str
    fallback_md_file: str
    snapshot_file: str
    target_files: list[str] = field(default_factory=list)
    rules_count: int = 0
    fallback_count: int = 0
    generated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrewarmResult:
    decision: str
    agent_instruction: str
    session_cache_file: str
    fallback_file: str
    fallback_instruction: str = ""
    fallback_applied: bool = False
    gate_summary: dict[str, Any] = field(default_factory=dict)
    matched_preferences: list[dict[str, Any]] = field(default_factory=list)
    checked_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_injection_dir() -> Path:
    return default_injection_dir_path()


def sync_injection_artifacts(
    store_path: str | Path,
    output_dir: str | Path | None = None,
    target_files: list[str | Path] | None = None,
    max_rules: int | None = None,
    max_fallback: int | None = None,
) -> InjectionResult:
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    records = _active_records(store.load())
    out_dir = Path(output_dir) if output_dir else default_injection_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    max_rules = max_rules or _env_int("PREFERENCE_INJECTION_MAX_RULES", 30)
    max_fallback = max_fallback or _env_int("PREFERENCE_FALLBACK_MAX_RULES", 10)

    selected = records[:max_rules]
    fallback = _select_fallback_records(records, max_fallback)
    static_text = render_static_rules(selected)
    fallback_rules = [_fallback_item(record) for record in fallback]
    fallback_md = render_fallback_md(fallback)
    snapshot_text = render_snapshot(selected)

    static_file = out_dir / "preference_rules.md"
    fallback_json_file = out_dir / "fallback_rules.json"
    fallback_md_file = out_dir / "fallback_rules.md"
    snapshot_dir = out_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_file = snapshot_dir / "global-default.md"

    static_file.write_text(static_text, encoding="utf-8")
    fallback_json_file.write_text(
        json.dumps(
            {
                "version": now_iso(),
                "fallback_rules": fallback_rules,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    fallback_md_file.write_text(fallback_md, encoding="utf-8")
    snapshot_file.write_text(snapshot_text, encoding="utf-8")

    updated_targets: list[str] = []
    for target in target_files or []:
        target_path = Path(target)
        _write_managed_block(target_path, static_text)
        updated_targets.append(str(target_path))

    return InjectionResult(
        static_rules_file=str(static_file),
        fallback_json_file=str(fallback_json_file),
        fallback_md_file=str(fallback_md_file),
        snapshot_file=str(snapshot_file),
        target_files=updated_targets,
        rules_count=len(selected),
        fallback_count=len(fallback),
    )


def prewarm_session(
    store_path: str | Path,
    task: str,
    agent: str = "agent",
    context: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> PrewarmResult:
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    records = _active_records(store.load())
    out_dir = Path(output_dir) if output_dir else default_injection_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    sync_result = sync_injection_artifacts(store.path, out_dir)
    decision = HeuristicBackend().decide(task=task, context=context or {}, records=records, agent=agent)
    from .decision_conflicts import apply_decision_conflict_policy

    decision = apply_decision_conflict_policy(
        decision,
        records=records,
        store_path=store.path,
        task=task,
        context=context or {},
    )
    fallback_text = Path(sync_result.fallback_md_file).read_text(encoding="utf-8")
    instruction = decision.get("agent_instruction") or ""
    fallback_instruction = ""
    if not instruction:
        fallback_instruction = "No dynamic preference matched this session; fallback rules are available in fallback_rules.md."
    decision = {
        **decision,
        "fallback_instruction": fallback_instruction,
        "fallback_applied": bool(fallback_instruction),
    }
    log_injection_event(
        store_path=store.path,
        hook="prewarm",
        agent=agent,
        task=task,
        context=context or {},
        decision=decision,
        source="prewarm_session",
    )
    session_text = render_session_cache(
        agent=agent,
        task=task,
        context=context or {},
        instruction=instruction,
        decision=decision,
        fallback_instruction=fallback_instruction,
        fallback_text=fallback_text,
    )
    cache_dir = out_dir / "session-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(json.dumps({"agent": agent, "task": task, "context": context or {}}, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    cache_file = cache_dir / f"{cache_key}.md"
    cache_file.write_text(session_text, encoding="utf-8")
    return PrewarmResult(
        decision=str(decision.get("decision", "no_preference")),
        agent_instruction=instruction,
        session_cache_file=str(cache_file),
        fallback_file=sync_result.fallback_md_file,
        fallback_instruction=fallback_instruction,
        fallback_applied=bool(fallback_instruction),
        gate_summary=decision.get("gate_summary") if isinstance(decision.get("gate_summary"), dict) else {},
        matched_preferences=decision.get("matched_preferences", []),
    )


def render_static_rules(records: list[PreferenceRecord]) -> str:
    grouped: dict[str, list[str]] = {}
    for record in records:
        grouped.setdefault(_category(record), []).append(_statement(record))
    lines = [
        "# User Preference Rules",
        "",
        "Generated by Datailor. Follow these rules first; if they conflict with the user's current explicit instruction, follow the current instruction and explain the tradeoff.",
        "",
        f"Generated at: {now_iso()}",
        f"Rule count: {sum(len(items) for items in grouped.values())}",
        "",
    ]
    if not grouped:
        lines.extend(["## Current State", "", "- No injectable preferences yet."])
    for category, items in grouped.items():
        lines.extend([f"## {category}", ""])
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    lines.extend(
        [
            "## Runtime Rules",
            "",
            "- At the start of each session, read this file and fallback_rules.md first.",
            "- Before replying or executing, call `prewarm_preferences` or `get_preference_decision` when available.",
            "- If MCP is unavailable, at least follow the minimal fallback preferences in fallback_rules.md.",
            "- If the user corrects or confirms a preference, call `report_preference_feedback` to record the feedback.",
            "",
        ]
    )
    return "\n".join(lines)


def render_fallback_md(records: list[PreferenceRecord]) -> str:
    lines = [
        "# Minimal Fallback Preferences",
        "",
        "When MCP, dynamic retrieval, or model calls are unavailable, still follow these fallback rules.",
        "",
    ]
    if records:
        lines.extend(f"- {_statement(record)}" for record in records)
    else:
        lines.append("- No fallback preferences yet; use normal engineering judgment and do not invent user preferences.")
    lines.append("")
    return "\n".join(lines)


def render_snapshot(records: list[PreferenceRecord]) -> str:
    lines = [
        "# Global Preference Snapshot",
        "",
        f"Generated at: {now_iso()}",
        "",
        "## Active Rules",
        "",
    ]
    if records:
        lines.extend(f"- {_statement(record)}" for record in records)
    else:
        lines.append("- No active preferences.")
    lines.append("")
    return "\n".join(lines)


def render_session_cache(
    agent: str,
    task: str,
    context: dict[str, Any],
    instruction: str,
    decision: dict[str, Any],
    fallback_instruction: str,
    fallback_text: str,
) -> str:
    return "\n".join(
        [
            "# Session Preference Cache",
            "",
            f"- Agent: `{agent}`",
            f"- Task: {task}",
            f"- Checked at: {now_iso()}",
            "",
            "## Agent Instruction",
            "",
            instruction or "No dynamic preference matched.",
            "",
            "## Fallback Instruction",
            "",
            fallback_instruction or "No fallback instruction needed because a dynamic preference matched.",
            "",
            "## Matched Preferences",
            "",
            "```json",
            json.dumps(decision.get("matched_preferences", []), ensure_ascii=False, indent=2),
            "```",
            "",
            "## Context",
            "",
            "```json",
            json.dumps(context, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Fallback",
            "",
            fallback_text.strip(),
            "",
        ]
    )


def _write_managed_block(path: Path, block_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    managed = "\n".join([MANAGED_START, block_text.strip(), MANAGED_END, ""])
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""
    if MANAGED_START in text and MANAGED_END in text:
        before, rest = text.split(MANAGED_START, 1)
        _old, after = rest.split(MANAGED_END, 1)
        text = before.rstrip() + "\n\n" + managed + after.lstrip()
    else:
        text = text.rstrip() + ("\n\n" if text.strip() else "") + managed
    path.write_text(text, encoding="utf-8")


def _active_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    return [record for record in records if record.status == "active"]


def _select_fallback_records(records: list[PreferenceRecord], limit: int) -> list[PreferenceRecord]:
    scored = sorted(records, key=_fallback_score, reverse=True)
    return scored[:limit]


def _fallback_score(record: PreferenceRecord) -> int:
    text = _statement(record).casefold()
    score = 0
    for word in ("must", "do not", "forbid", "cannot", "default", "read back", "English", "test", "verify", "linear", "safe"):
        if word in text:
            score += 2
    if record.confidence == "high":
        score += 2
    if record.confidence == "medium":
        score += 1
    return score


def _fallback_item(record: PreferenceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "statement": _statement(record),
        "priority": min(5, max(1, 3 + _fallback_score(record) // 3)),
        "always_apply": _fallback_score(record) >= 3,
    }


def _statement(record: PreferenceRecord) -> str:
    preference = _clean(record.preference)
    applies_to = _clean(record.applies_to)
    if preference.casefold().startswith(("when ", "after ", "before ", "during ")):
        return _ensure_period(preference)
    if applies_to.casefold().startswith(("when ", "after ", "before ", "during ")):
        return _ensure_period(f"{applies_to}, {preference}")
    return _ensure_period(preference or record.summary or record.title)


def _category(record: PreferenceRecord) -> str:
    text = _statement(record).casefold()
    if any(word in text for word in ("english", "concise", "reply", "conclusion", "explain")):
        return "Communication Style"
    if any(word in text for word in ("test", "verify", "code", "review", "document", "linear", "issue")):
        return "Workflow"
    if any(word in text for word in ("do not", "forbid", "cannot", "safe", "high risk", "confirm")):
        return "Safety Constraints"
    return "General Preferences"


def _clean(text: str) -> str:
    return " ".join(str(text).split()).strip().rstrip(".")


def _ensure_period(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
