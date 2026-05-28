from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def dump_json_object(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def mcp_server_state(path: Path, server_name: str) -> tuple[str, dict[str, Any]]:
    data = load_json_object(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return "not_configured", data
    return ("configured" if server_name in servers else "not_configured"), data


def install_mcp_server(path: Path, server_name: str, server: dict[str, Any]) -> tuple[str, bool]:
    data = load_json_object(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"mcpServers must be an object in {path}")
    before = dump_json_object(data)
    servers[server_name] = server
    after = dump_json_object(data)
    return after, before != after


def remove_mcp_server(path: Path, server_name: str) -> tuple[str, bool]:
    data = load_json_object(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or server_name not in servers:
        return dump_json_object(data), False
    before = dump_json_object(data)
    del servers[server_name]
    after = dump_json_object(data)
    return after, before != after
