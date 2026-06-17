from __future__ import annotations

import os
from pathlib import Path
from shutil import which

from .models import ClientProfile, ClientTarget, ScopeName


def integration_home() -> Path:
    configured = os.getenv("DATAILOR_INTEGRATION_HOME") or os.getenv("PREFERENCE_DISCOVERY_HOME")
    return Path(configured) if configured else Path.home()


def project_root_path(project_root: str | Path | None = None) -> Path:
    return Path(project_root).resolve() if project_root else Path.cwd().resolve()


def target_for_profile(profile: ClientProfile, scope: ScopeName, project_root: str | Path | None = None) -> ClientTarget:
    home = integration_home()
    root = project_root_path(project_root)
    warnings: list[str] = []
    config_path: Path | None = None
    plugin_path: Path | None = None

    if profile.name == "codex":
        if scope == "project":
            config_path = root / ".codex" / "config.toml"
            warnings.append("Codex only loads project .codex/config.toml after the project is trusted.")
        else:
            config_path = home / ".codex" / "config.toml"
        plugin_path = home / ".codex" / "plugins" / "datailor-preferences"
        installed = (home / ".codex").exists() or bool(which("codex")) or config_path.exists()
        return ClientTarget(profile.name, scope, "toml", config_path, plugin_path, installed, True, tuple(warnings))

    if profile.name == "claude":
        if scope == "project":
            config_path = root / ".mcp.json"
            warnings.append("Claude Code prompts for approval before using project-scoped .mcp.json servers.")
        else:
            config_path = home / ".claude.json"
            if scope == "local":
                warnings.append("Claude local scope is stored in ~/.claude.json under the current project path.")
            else:
                warnings.append("Claude user scope is stored in ~/.claude.json and is private to this machine.")
        plugin_path = home / ".claude" / "plugins" / "datailor-preferences"
        installed = (home / ".claude").exists() or (home / ".claude.json").exists() or bool(which("claude")) or config_path.exists()
        return ClientTarget(profile.name, scope, "claude-json", config_path, plugin_path, installed, True, tuple(warnings))

    if profile.name == "kimi":
        kimi_home = Path(os.getenv("KIMI_SHARE_DIR")) if os.getenv("KIMI_SHARE_DIR") else home / ".kimi"
        if scope != "user":
            warnings.append("Kimi Code CLI stores MCP servers in the user share directory; project scope maps to user config.")
        config_path = kimi_home / "mcp.json"
        plugin_path = kimi_home / "plugins" / "datailor-preferences"
        installed = kimi_home.exists() or bool(which("kimi")) or config_path.exists()
        return ClientTarget(profile.name, scope, "json", config_path, plugin_path, installed, True, tuple(warnings))

    if profile.name == "openclaw":
        openclaw_home = Path(os.getenv("OPENCLAW_HOME")) if os.getenv("OPENCLAW_HOME") else home / ".openclaw"
        if scope != "user":
            warnings.append("OpenClaw MCP servers are managed by the OpenClaw CLI; project scope maps to user-level CLI config.")
        plugin_path = openclaw_home / "plugins" / "datailor-preferences"
        installed = openclaw_home.exists() or bool(which("openclaw"))
        return ClientTarget(profile.name, scope, "cli", None, plugin_path, installed, True, tuple(warnings))

    raise ValueError(f"unsupported client: {profile.name}")
