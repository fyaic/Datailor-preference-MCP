from __future__ import annotations

from datetime import datetime
from pathlib import Path
from shutil import which
from typing import Any

from . import json_config, toml_config
from .models import DEFAULT_MCP_COMMAND, DEFAULT_SERVER_NAME, ClientProfile, IntegrationResult, ScopeName
from .paths import project_root_path, target_for_profile
from .plugins import export_plugin_template
from .registry import expand_clients, get_profile


def run_integrations(
    *,
    action: str,
    client: str = "all",
    scope: str = "",
    project_root: str | Path | None = None,
    dry_run: bool = False,
    output_dir: str | Path | None = None,
    server_name: str = DEFAULT_SERVER_NAME,
    mcp_command: str = DEFAULT_MCP_COMMAND,
    backend: str = "heuristic",
    store: str | Path | None = None,
) -> dict[str, Any]:
    selected = expand_clients(client)
    results: list[dict[str, Any]] = []
    for name in selected:
        profile = get_profile(name)
        effective_scope = _effective_scope(profile, scope)
        result = run_integration(
            profile,
            action=action,
            scope=effective_scope,
            project_root=project_root,
            dry_run=dry_run,
            output_dir=output_dir,
            server_name=server_name,
            mcp_command=mcp_command,
            backend=backend,
            store=store,
        )
        results.append(result.to_dict())
    return {
        "ok": all(item["ok"] for item in results),
        "action": action,
        "client": client,
        "scope": scope or "default",
        "dry_run": dry_run,
        "results": results,
    }


def run_integration(
    profile: ClientProfile,
    *,
    action: str,
    scope: ScopeName,
    project_root: str | Path | None,
    dry_run: bool,
    output_dir: str | Path | None,
    server_name: str,
    mcp_command: str,
    backend: str,
    store: str | Path | None,
) -> IntegrationResult:
    if action == "export-plugin":
        return _export_plugin(profile, scope, output_dir, server_name, mcp_command, backend, store, dry_run=dry_run)
    target = target_for_profile(profile, scope, project_root)
    warnings = list(target.warnings)
    server = _server_config(profile.agent_arg, mcp_command, backend, store)
    try:
        if action == "status":
            state = _state(profile, target.config_path, server_name, scope, project_root)
            return _result(True, profile, action, scope, False, dry_run, target, "", state, warnings)
        if action == "doctor":
            state = _state(profile, target.config_path, server_name, scope, project_root)
            if not which(mcp_command):
                warnings.append(f"{mcp_command} is not available on PATH.")
            ok = state == "configured" and not any("not available" in item for item in warnings)
            return _result(ok, profile, action, scope, False, dry_run, target, "", state, warnings)
        if action == "install":
            content, changed, extra_warnings = _install_content(profile, target.config_path, server_name, server, scope, project_root)
            warnings.extend(extra_warnings)
            backup = "" if dry_run or not changed else _write_content(target.config_path, content)
            return _result(True, profile, action, scope, changed, dry_run, target, backup, "configured", warnings)
        if action == "remove":
            content, changed, extra_warnings = _remove_content(profile, target.config_path, server_name, scope, project_root)
            warnings.extend(extra_warnings)
            backup = "" if dry_run or not changed else _write_content(target.config_path, content)
            state = "not_configured" if changed else _state(profile, target.config_path, server_name, scope, project_root)
            return _result(True, profile, action, scope, changed, dry_run, target, backup, state, warnings)
        raise ValueError(f"unsupported integration action: {action}")
    except ValueError as exc:
        warnings.append(str(exc))
        return _result(False, profile, action, scope, False, dry_run, target, "", "invalid", warnings)


def _effective_scope(profile: ClientProfile, requested: str) -> ScopeName:
    if not requested or requested == "default":
        return profile.default_scope
    if requested in {"user", "project", "local"}:
        return requested  # type: ignore[return-value]
    return profile.default_scope


def _server_config(agent: str, command: str, backend: str, store: str | Path | None) -> dict[str, Any]:
    env = {"PREFERENCE_MODEL_BACKEND": backend}
    if store:
        env["PREFERENCE_STORE_PATH"] = str(store)
    return {"command": command, "args": ["--agent", agent], "env": env}


def _state(profile: ClientProfile, path: Path | None, server_name: str, scope: ScopeName, project_root: str | Path | None) -> str:
    if path is None or not path.exists():
        return "not_configured"
    if profile.name == "codex":
        state, _data = toml_config.mcp_server_state(path, server_name)
        return state
    if profile.name == "claude" and scope in {"user", "local"}:
        data = json_config.load_json_object(path)
        servers = _claude_servers(data, scope, project_root)
        return "configured" if server_name in servers else "not_configured"
    state, _data = json_config.mcp_server_state(path, server_name)
    return state


def _install_content(
    profile: ClientProfile,
    path: Path | None,
    server_name: str,
    server: dict[str, Any],
    scope: ScopeName,
    project_root: str | Path | None,
) -> tuple[str, bool, tuple[str, ...]]:
    if path is None:
        raise ValueError(f"{profile.name} has no config target")
    if profile.name == "codex":
        return toml_config.install_mcp_server(path, server_name, server)
    if profile.name == "claude" and scope in {"user", "local"}:
        content, changed = _install_claude_user_or_local(path, server_name, server, scope, project_root)
        return content, changed, ()
    content, changed = json_config.install_mcp_server(path, server_name, server)
    return content, changed, ()


def _remove_content(
    profile: ClientProfile,
    path: Path | None,
    server_name: str,
    scope: ScopeName,
    project_root: str | Path | None,
) -> tuple[str, bool, tuple[str, ...]]:
    if path is None:
        raise ValueError(f"{profile.name} has no config target")
    if profile.name == "codex":
        return toml_config.remove_mcp_server(path, server_name)
    if profile.name == "claude" and scope in {"user", "local"}:
        content, changed = _remove_claude_user_or_local(path, server_name, scope, project_root)
        return content, changed, ()
    content, changed = json_config.remove_mcp_server(path, server_name)
    return content, changed, ()


def _install_claude_user_or_local(
    path: Path,
    server_name: str,
    server: dict[str, Any],
    scope: ScopeName,
    project_root: str | Path | None,
) -> tuple[str, bool]:
    data = json_config.load_json_object(path)
    before = json_config.dump_json_object(data)
    servers = _claude_servers(data, scope, project_root)
    servers[server_name] = server
    after = json_config.dump_json_object(data)
    return after, before != after


def _remove_claude_user_or_local(path: Path, server_name: str, scope: ScopeName, project_root: str | Path | None) -> tuple[str, bool]:
    data = json_config.load_json_object(path)
    before = json_config.dump_json_object(data)
    servers = _claude_servers(data, scope, project_root)
    servers.pop(server_name, None)
    after = json_config.dump_json_object(data)
    return after, before != after


def _claude_servers(data: dict[str, Any], scope: ScopeName, project_root: str | Path | None) -> dict[str, Any]:
    if scope == "local":
        root = str(project_root_path(project_root))
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise ValueError("Claude ~/.claude.json projects must be an object")
        project = projects.setdefault(root, {})
        if not isinstance(project, dict):
            raise ValueError(f"Claude project entry must be an object: {root}")
        servers = project.setdefault("mcpServers", {})
    else:
        servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("Claude mcpServers must be an object")
    return servers


def _write_content(path: Path | None, content: str) -> str:
    if path is None:
        return ""
    backup = ""
    if path.exists():
        backup_path = _backup_path(path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        backup = str(backup_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return backup


def _backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    candidate = path.with_name(f"{path.name}.datailor-backup-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.datailor-backup-{stamp}-{counter}")
        counter += 1
    return candidate


def _export_plugin(
    profile: ClientProfile,
    scope: ScopeName,
    output_dir: str | Path | None,
    server_name: str,
    mcp_command: str,
    backend: str,
    store: str | Path | None,
    dry_run: bool,
) -> IntegrationResult:
    target = target_for_profile(profile, scope)
    if not profile.supports_plugin:
        return _result(False, profile, "export-plugin", scope, False, dry_run, target, "", "unsupported", ["client has no plugin template"])
    output = Path(output_dir) if output_dir else Path.cwd() / "datailor-plugins"
    destination = output / f"{profile.name}-datailor"
    if dry_run:
        details = {
            "destination": str(destination),
            "files": [],
            "would_replace": destination.exists(),
            "server_name": server_name,
            "mcp_command": mcp_command,
        }
        return _result(
            True,
            profile,
            "export-plugin",
            scope,
            True,
            True,
            target,
            "",
            "planned",
            [],
            details=details,
            target_path=str(destination),
        )
    details = export_plugin_template(profile, output, server_name=server_name, mcp_command=mcp_command, backend=backend, store=store)
    return _result(
        True,
        profile,
        "export-plugin",
        scope,
        True,
        False,
        target,
        "",
        "exported",
        [],
        details=details,
        target_path=str(details["destination"]),
    )


def _result(
    ok: bool,
    profile: ClientProfile,
    action: str,
    scope: ScopeName,
    changed: bool,
    dry_run: bool,
    target: Any,
    backup: str,
    state: str,
    warnings: list[str],
    *,
    details: dict[str, Any] | None = None,
    target_path: str | None = None,
) -> IntegrationResult:
    return IntegrationResult(
        ok=ok,
        client=profile.name,
        action=action,
        scope=scope,
        changed=changed,
        dry_run=dry_run,
        target_path=target_path if target_path is not None else (str(target.config_path) if target.config_path else ""),
        backup_path=backup,
        state=state,
        installed=bool(getattr(target, "installed", False)),
        supported=bool(getattr(target, "supported", True)),
        warnings=tuple(warnings),
        details=details
        or {
            "display_name": profile.display_name,
            "plugin_path": str(target.plugin_path) if getattr(target, "plugin_path", None) else "",
            "docs_url": profile.docs_url,
        },
    )
