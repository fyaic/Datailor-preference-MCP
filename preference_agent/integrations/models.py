from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


DEFAULT_SERVER_NAME = "datailor-preferences"
DEFAULT_MCP_COMMAND = "datailor-mcp"

ClientName = Literal["codex", "claude", "kimi"]
ScopeName = Literal["user", "project", "local"]
ActionName = Literal["status", "install", "remove", "doctor", "export-plugin"]
ConfigKind = Literal["json", "toml", "claude-json", "plugin"]


@dataclass(frozen=True)
class ClientProfile:
    name: ClientName
    display_name: str
    agent_arg: str
    config_kind: ConfigKind
    default_scope: ScopeName
    supports_plugin: bool
    docs_url: str


@dataclass(frozen=True)
class ClientTarget:
    client: ClientName
    scope: ScopeName
    config_kind: ConfigKind
    config_path: Path | None
    plugin_path: Path | None
    installed: bool
    supported: bool
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["config_path"] = str(self.config_path) if self.config_path else ""
        data["plugin_path"] = str(self.plugin_path) if self.plugin_path else ""
        data["warnings"] = list(self.warnings)
        return data


@dataclass(frozen=True)
class IntegrationResult:
    ok: bool
    client: str
    action: str
    scope: str
    changed: bool
    dry_run: bool
    target_path: str
    backup_path: str
    state: str
    installed: bool
    supported: bool
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        if self.details is None:
            data["details"] = {}
        return data
