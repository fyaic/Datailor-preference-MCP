from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .backends import HeuristicBackend, build_backend
from .engine import PreferenceEngine
from .hooks import PreferenceHookManager
from .paths import default_hooks_dir, default_store_path
from .store import MarkdownPreferenceStore


KIMI_HOOKS_START = "# datailor:kimi-hooks:start"
KIMI_HOOKS_END = "# datailor:kimi-hooks:end"
KIMI_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
)
EMPTY_HOOKS_RE = re.compile(r"^(\s*)hooks\s*=\s*\[\s*\](\s*(?:#.*)?)$")


@dataclass(frozen=True)
class KimiHooksInstallResult:
    ok: bool
    target: str
    changed: bool
    action: str
    command: str
    events: list[str]
    managed_start: str
    managed_end: str
    backup: str = ""
    empty_hooks_disabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_kimi_config_path() -> Path:
    return Path.home() / ".kimi" / "config.toml"


def install_kimi_hooks(
    target: str | Path | None = None,
    command: str = "datailor-kimi-hook",
    agent: str = "kimi",
    backend: str = "heuristic",
    store: str | Path | None = None,
    timeout: int = 20,
) -> KimiHooksInstallResult:
    target_path = Path(target) if target else default_kimi_config_path()
    existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    hook_command = _hook_command(command=command, agent=agent, backend=backend, store=store)
    block = _hook_block(command=hook_command, timeout=timeout)
    without_old, action = _replace_managed_block(existing, block)
    updated, empty_hooks_disabled = _disable_empty_hooks_array(without_old)
    changed = updated != existing
    backup = ""
    if changed:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            backup_path = _backup_path(target_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(existing, encoding="utf-8")
            backup = str(backup_path)
        target_path.write_text(updated, encoding="utf-8")
    return KimiHooksInstallResult(
        ok=True,
        target=str(target_path),
        changed=changed,
        action=action,
        command=hook_command,
        events=list(KIMI_HOOK_EVENTS),
        managed_start=KIMI_HOOKS_START,
        managed_end=KIMI_HOOKS_END,
        backup=backup,
        empty_hooks_disabled=empty_hooks_disabled,
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="datailor-kimi-hook")
    parser.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", "kimi"))
    parser.add_argument("--store", default=os.getenv("PREFERENCE_STORE_PATH", ""))
    parser.add_argument("--backend", default=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = _read_stdin_json()
        result = handle_kimi_hook(
            payload=payload,
            agent=args.agent,
            store_path=args.store or None,
            backend=args.backend,
            dry_run=args.dry_run,
        )
        print(json.dumps({"ok": True, "datailor": result, "hookSpecificOutput": {"permissionDecision": "allow"}}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"Datailor Kimi hook failed open: {exc}", file=sys.stderr)
        print(json.dumps({"hookSpecificOutput": {"permissionDecision": "allow"}}, ensure_ascii=False))
        return 0


def handle_kimi_hook(
    payload: dict[str, Any],
    agent: str = "kimi",
    store_path: str | Path | None = None,
    backend: str = "heuristic",
    dry_run: bool = False,
) -> dict[str, Any]:
    store = Path(store_path) if store_path else default_store_path()
    engine = _engine(store, backend)
    manager = PreferenceHookManager(engine)
    event = str(payload.get("hook_event_name") or "")
    session_id = str(payload.get("session_id") or "kimi")
    context = _context_from_payload(payload)
    if event == "SessionStart":
        return manager.on_session_start(
            agent=agent,
            session_id=session_id,
            task=f"Kimi session start: {payload.get('source') or 'startup'}",
            context=context,
        )
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        _save_last_prompt(agent=agent, session_id=session_id, prompt=prompt)
        return manager.on_user_message(
            message=prompt,
            agent=agent,
            session_id=session_id,
            context=context,
        )
    if event in {"PostToolUse", "PostToolUseFailure"}:
        tool_name = str(payload.get("tool_name") or "tool")
        result = str(payload.get("tool_output") or payload.get("error") or "")
        return manager.on_action_executed(
            action=f"kimi.{event}.{tool_name}",
            result=result,
            agent=agent,
            session_id=session_id,
            metadata=context,
            dry_run=dry_run,
        )
    if event == "Stop":
        prompt = _load_last_prompt(agent=agent, session_id=session_id)
        return manager.on_turn_complete(
            user_message=prompt,
            assistant_response="",
            agent=agent,
            session_id=session_id,
            context={**context, "assistant_response_unavailable": True},
            dry_run=dry_run,
        )
    if event == "SessionEnd":
        return manager.on_session_end(
            agent=agent,
            session_id=session_id,
            messages=[],
            dry_run=dry_run,
        )
    return {
        "ok": True,
        "hook": "kimi_unhandled",
        "agent": agent,
        "session_id": session_id,
        "event": event,
        "reason": "unsupported_kimi_event",
    }


def _engine(store_path: Path, backend_name: str) -> PreferenceEngine:
    backend = build_backend(backend_name)
    fallback = HeuristicBackend() if backend.__class__.__name__ != "HeuristicBackend" else None
    return PreferenceEngine(MarkdownPreferenceStore(store_path), backend=backend, fallback_backend=fallback)


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read().lstrip("\ufeff").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _context_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    context = {
        "cwd": str(payload.get("cwd") or ""),
        "kimi_event": str(payload.get("hook_event_name") or ""),
    }
    for key in ("source", "reason", "tool_name", "tool_call_id", "stop_hook_active"):
        if key in payload:
            context[key] = payload.get(key)
    if isinstance(payload.get("tool_input"), dict):
        context["tool_input"] = payload["tool_input"]
    return context


def _hook_command(command: str, agent: str, backend: str, store: str | Path | None) -> str:
    parts = [command, "--agent", agent]
    if backend:
        parts.extend(["--backend", backend])
    if store:
        parts.extend(["--store", str(store)])
    return " ".join(_quote_shell_part(part) for part in parts)


def _hook_block(command: str, timeout: int) -> str:
    lines = ["", KIMI_HOOKS_START, ""]
    for event in KIMI_HOOK_EVENTS:
        lines.extend(
            [
                "[[hooks]]",
                f'event = "{event}"',
                f'command = "{_escape_toml_string(command)}"',
                f"timeout = {timeout}",
                "",
            ]
        )
    lines.extend([KIMI_HOOKS_END, ""])
    return "\n".join(lines)


def _replace_managed_block(existing: str, block: str) -> tuple[str, str]:
    if KIMI_HOOKS_START in existing and KIMI_HOOKS_END in existing:
        before, rest = existing.split(KIMI_HOOKS_START, 1)
        _old, after = rest.split(KIMI_HOOKS_END, 1)
        return before.rstrip() + block + after.lstrip(), "replace"
    prefix = existing.rstrip()
    return (prefix + block if prefix else block.lstrip()), "append"


def _disable_empty_hooks_array(text: str) -> tuple[str, bool]:
    changed = False
    lines: list[str] = []
    in_root = True
    for line in text.splitlines():
        stripped = line.strip()
        if in_root and stripped.startswith("["):
            in_root = False
        match = EMPTY_HOOKS_RE.match(line)
        if in_root and not stripped.startswith("#") and match:
            indent = match.group(1)
            lines.append(f"{indent}# hooks = []  # disabled by Datailor; managed hooks are defined below")
            changed = True
            continue
        lines.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix, changed


def _backup_path(target: Path) -> Path:
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return target.parent / ".datailor-backups" / f"{target.name}.{stamp}.bak"


def _last_prompt_path(agent: str, session_id: str) -> Path:
    import hashlib

    key = hashlib.sha256(f"kimi:{agent}:{session_id}".encode("utf-8")).hexdigest()[:16]
    return default_hooks_dir() / "kimi" / f"{key}.json"


def _save_last_prompt(agent: str, session_id: str, prompt: str) -> None:
    path = _last_prompt_path(agent, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"prompt": prompt}, ensure_ascii=False), encoding="utf-8")


def _load_last_prompt(agent: str, session_id: str) -> str:
    path = _last_prompt_path(agent, session_id)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(data.get("prompt") or "") if isinstance(data, dict) else ""


def _quote_shell_part(value: str) -> str:
    if not value:
        return '""'
    if re.search(r"\s|[\"&|<>^]", value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


if __name__ == "__main__":
    raise SystemExit(main())
