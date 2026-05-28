from __future__ import annotations

import json
from pathlib import Path
import tomllib
from typing import Any


MARKER_START = "# datailor:integrations:start"
MARKER_END = "# datailor:integrations:end"


def mcp_server_state(path: Path, server_name: str) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    data = _parse_toml(text, path)
    servers = data.get("mcp_servers")
    if isinstance(servers, dict) and server_name in servers:
        return "configured", data
    return "not_configured", data


def install_mcp_server(path: Path, server_name: str, server: dict[str, Any]) -> tuple[str, bool, tuple[str, ...]]:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    has_marker = MARKER_START in existing and MARKER_END in existing
    if not has_marker:
        state, _data = mcp_server_state(path, server_name)
        if state == "configured":
            warning = f"{server_name} already exists in {path} outside the Datailor managed block; leaving it unchanged."
            return existing, False, (warning,)
    block = _managed_block(server_name, server)
    updated = _replace_or_append_block(existing, block)
    _parse_toml(updated, path)
    return updated, updated != existing, ()


def remove_mcp_server(path: Path, server_name: str) -> tuple[str, bool, tuple[str, ...]]:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if MARKER_START in existing and MARKER_END in existing:
        updated = _remove_managed_block(existing)
        _parse_toml(updated, path)
        return updated, updated != existing, ()
    state, _data = mcp_server_state(path, server_name)
    if state == "configured":
        warning = f"{server_name} exists in {path} but is not Datailor-managed; remove it manually if desired."
        return existing, False, (warning,)
    return existing, False, ()


def _parse_toml(text: str, path: Path) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML in {path}: {exc}") from exc


def _managed_block(server_name: str, server: dict[str, Any]) -> str:
    env = server.get("env") or {}
    lines = [
        MARKER_START,
        f"[mcp_servers.{_toml_key(server_name)}]",
        f"command = {_toml_value(server['command'])}",
        f"args = {_toml_value(server.get('args') or [])}",
        "",
        f"[mcp_servers.{_toml_key(server_name)}.env]",
    ]
    for key in sorted(env):
        lines.append(f"{_toml_key(str(key))} = {_toml_value(str(env[key]))}")
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def _replace_or_append_block(existing: str, block: str) -> str:
    if MARKER_START in existing and MARKER_END in existing:
        before, rest = existing.split(MARKER_START, 1)
        _old, after = rest.split(MARKER_END, 1)
        return before.rstrip() + "\n\n" + block + after.lstrip()
    return existing.rstrip() + ("\n\n" if existing.strip() else "") + block


def _remove_managed_block(existing: str) -> str:
    before, rest = existing.split(MARKER_START, 1)
    _old, after = rest.split(MARKER_END, 1)
    return before.rstrip() + ("\n\n" + after.lstrip() if after.strip() else "\n")


def _toml_key(key: str) -> str:
    if key.replace("-", "_").replace("_", "").isalnum() and key:
        return key
    return json.dumps(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value))
