from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from typing import Any

from .models import DEFAULT_MCP_COMMAND, DEFAULT_SERVER_NAME, ClientProfile


def export_plugin_template(
    profile: ClientProfile,
    output_dir: str | Path,
    *,
    server_name: str = DEFAULT_SERVER_NAME,
    mcp_command: str = DEFAULT_MCP_COMMAND,
    backend: str = "heuristic",
    store: str | Path | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    destination = output / f"{profile.name}-datailor"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    server = _server_config(profile.agent_arg, mcp_command, backend, store)

    if profile.name == "codex":
        _write_codex_plugin(destination, server_name, server)
    elif profile.name == "claude":
        _write_claude_plugin(destination, server_name, server)
    elif profile.name == "kimi":
        _write_kimi_plugin(destination)
    else:
        raise ValueError(f"unsupported plugin client: {profile.name}")
    files = [str(path.relative_to(destination)) for path in sorted(destination.rglob("*")) if path.is_file()]
    return {"destination": str(destination), "files": files}


def _server_config(agent: str, command: str, backend: str, store: str | Path | None) -> dict[str, Any]:
    env = {"PREFERENCE_MODEL_BACKEND": backend}
    if store:
        env["PREFERENCE_STORE_PATH"] = str(store)
    return {"command": command, "args": ["--agent", agent], "env": env}


def _write_codex_plugin(destination: Path, server_name: str, server: dict[str, Any]) -> None:
    _write_json(
        destination / ".codex-plugin" / "plugin.json",
        {
            "name": "datailor-preferences",
            "version": "0.1.0",
            "description": "Use the local Datailor preference MCP from Codex.",
            "skills": "./skills/",
            "mcpServers": "./.mcp.json",
            "interface": {
                "displayName": "Datailor Preferences",
                "shortDescription": "Local preference MCP and workflow reminders.",
                "developerName": "Datailor",
                "category": "Productivity",
                "capabilities": ["Read", "Write"],
            },
        },
    )
    _write_json(destination / ".mcp.json", {server_name: server})
    _write_skill(destination / "skills" / "datailor-preferences" / "SKILL.md", "codex")
    _write_readme(destination / "README.md", "Codex")


def _write_claude_plugin(destination: Path, server_name: str, server: dict[str, Any]) -> None:
    _write_json(
        destination / ".claude-plugin" / "plugin.json",
        {
            "name": "datailor-preferences",
            "version": "0.1.0",
            "description": "Use the local Datailor preference MCP from Claude Code.",
            "skills": "./skills/",
            "mcpServers": "./.mcp.json",
        },
    )
    _write_json(destination / ".mcp.json", {"mcpServers": {server_name: server}})
    _write_skill(destination / "skills" / "datailor-preferences" / "SKILL.md", "claude")
    _write_readme(destination / "README.md", "Claude Code")


def _write_kimi_plugin(destination: Path) -> None:
    script_path = destination / "scripts" / "datailor_tool.py"
    _write_json(
        destination / "plugin.json",
        {
            "name": "datailor-preferences",
            "version": "0.1.0",
            "description": "Datailor local preference utility commands.",
            "tools": [
                {
                    "name": "datailor",
                    "description": "Run short Datailor utility commands.",
                    "command": [sys.executable, str(script_path)],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "enum": ["doctor", "onboard", "ui", "mcp-config"],
                            }
                        },
                        "required": ["command"],
                    },
                }
            ],
        },
    )
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_kimi_tool_script(), encoding="utf-8")
    _write_skill(destination / "SKILL.md", "kimi")
    _write_readme(destination / "README.md", "Kimi Code")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_skill(path: Path, agent: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
name: datailor-preferences
description: Use Datailor before replying or taking preference-sensitive actions.
---

Before acting in {agent}, call the Datailor MCP hooks when they are available. Use `datailor doctor --agent {agent}` to check setup, `datailor onboard --agent {agent}` to initialize, and `datailor ui` to review preferences.
""",
        encoding="utf-8",
    )


def _write_readme(path: Path, client: str) -> None:
    path.write_text(
        f"""# Datailor Preferences for {client}

This local plugin exposes the Datailor preference workflow to {client}. Datailor itself runs through the `datailor-mcp` stdio server and stores preferences on the local machine.

Useful commands:

- `datailor doctor`
- `datailor onboard`
- `datailor ui`
- `datailor mcp-config --no-agent-rules`
""",
        encoding="utf-8",
    )


def _kimi_tool_script() -> str:
    return """#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys


COMMANDS = {
    "doctor": ["-m", "preference_agent.cli", "doctor", "--agent", "kimi"],
    "onboard": ["-m", "preference_agent.cli", "onboard", "--agent", "kimi", "--no-capture"],
    "ui": ["-m", "preference_agent.cli", "ui", "--no-open"],
    "mcp-config": ["-m", "preference_agent.cli", "mcp-config", "--agent", "kimi", "--no-agent-rules", "--no-kimi-hooks"],
}


def main() -> int:
    params = json.load(sys.stdin)
    command = params.get("command")
    argv = COMMANDS.get(command)
    if not argv:
        print(json.dumps({"error": f"unsupported command: {command}"}, ensure_ascii=False))
        return 2
    completed = subprocess.run([sys.executable, *argv], text=True, encoding="utf-8", errors="replace", capture_output=True)
    print(json.dumps({"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
