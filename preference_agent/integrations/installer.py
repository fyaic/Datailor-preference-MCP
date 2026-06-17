from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from shutil import which
from typing import Any

from . import json_config, toml_config
from .models import DEFAULT_MCP_COMMAND, DEFAULT_SERVER_NAME, ClientProfile, IntegrationResult, ScopeName
from .paths import project_root_path, target_for_profile
from .plugins import export_plugin_template
from .registry import expand_clients, get_profile


OPENCLAW_PLUGIN_ID = "datailor-preferences"
OPENCLAW_EXPECTED_HOOKS = {
    "session_start",
    "before_prompt_build",
    "agent_turn_prepare",
    "after_tool_call",
    "agent_end",
    "session_end",
}


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
        if profile.name == "openclaw":
            return _run_openclaw_cli_integration(
                profile,
                action=action,
                scope=scope,
                dry_run=dry_run,
                target=target,
                server_name=server_name,
                server=server,
                warnings=warnings,
                mcp_command=mcp_command,
            )
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


def _run_openclaw_cli_integration(
    profile: ClientProfile,
    *,
    action: str,
    scope: ScopeName,
    dry_run: bool,
    target: Any,
    server_name: str,
    server: dict[str, Any],
    warnings: list[str],
    mcp_command: str,
) -> IntegrationResult:
    if action == "status":
        state = _openclaw_state(server_name)
        return _result(True, profile, action, scope, False, dry_run, target, "", state, warnings)
    if action == "doctor":
        state = _openclaw_state(server_name)
        command = _openclaw_command()
        plugin_registered = _openclaw_plugin_registered(OPENCLAW_PLUGIN_ID) if command else False
        plugin_enabled = _openclaw_plugin_enabled(OPENCLAW_PLUGIN_ID) if plugin_registered else False
        hooks_ready = _openclaw_runtime_hooks_ready(OPENCLAW_PLUGIN_ID) if plugin_enabled else False
        if not command:
            warnings.append("openclaw is not available on PATH.")
        if not which(mcp_command):
            warnings.append(f"{mcp_command} is not available on PATH.")
        if not _openclaw_plugin_installed(target):
            warnings.append("OpenClaw Datailor plugin files are not installed; run integrate install --client openclaw.")
        if not plugin_registered:
            warnings.append("OpenClaw Datailor plugin is not registered; run integrate install --client openclaw.")
        elif not plugin_enabled:
            warnings.append("OpenClaw Datailor plugin is disabled; run integrate install --client openclaw.")
        elif not hooks_ready:
            warnings.append("OpenClaw Datailor plugin hooks are not fully enabled; run integrate install --client openclaw to allow agent_end capture.")
        ok = state == "configured" and plugin_registered and plugin_enabled and hooks_ready and not warnings
        return _result(
            ok,
            profile,
            action,
            scope,
            False,
            dry_run,
            target,
            "",
            state,
            warnings,
            details=_openclaw_details(
                profile,
                target,
                "openclaw doctor",
                server_name,
                plugin_registered=plugin_registered,
                plugin_enabled=plugin_enabled,
                hooks_ready=hooks_ready,
            ),
        )
    if action == "install":
        command = _openclaw_command()
        if not command:
            warnings.append("openclaw is not available on PATH.")
            return _result(False, profile, action, scope, False, dry_run, target, "", "not_installed", warnings)
        state = _openclaw_state(server_name)
        plugin_registered = _openclaw_plugin_registered(OPENCLAW_PLUGIN_ID)
        plugin_enabled = _openclaw_plugin_enabled(OPENCLAW_PLUGIN_ID) if plugin_registered else False
        hooks_ready = _openclaw_runtime_hooks_ready(OPENCLAW_PLUGIN_ID) if plugin_enabled else False
        plugin_files_changed = _openclaw_plugin_files_changed(target)
        config_json = json.dumps(server, ensure_ascii=False)
        would_change = state != "configured" or not plugin_registered or not plugin_enabled or not hooks_ready or plugin_files_changed
        details = _openclaw_details(
            profile,
            target,
            "openclaw plugins install && openclaw mcp set",
            server_name,
            plugin_registered=plugin_registered,
            plugin_enabled=plugin_enabled,
            hooks_ready=hooks_ready,
            plugin_files_changed=plugin_files_changed,
        )
        if dry_run:
            planned_state = "would_configure" if would_change else "configured"
            return _result(True, profile, action, scope, would_change, dry_run, target, "", planned_state, warnings, details=details)
        plugin_changed = _install_openclaw_plugin_files(target)
        plugin_install_changed = False
        if not plugin_registered:
            completed = _run_openclaw(command, ["plugins", "install", str(target.plugin_path), "--link"], timeout=30)
            if isinstance(completed, Exception):
                warnings.append(f"openclaw plugins install failed to launch: {completed.__class__.__name__}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            if completed.returncode != 0:
                warnings.append(f"openclaw plugins install failed with exit code {completed.returncode}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            plugin_registered = True
            plugin_enabled = True
            plugin_install_changed = True
        plugin_enable_changed = False
        if plugin_registered and not plugin_enabled:
            completed = _run_openclaw(command, ["plugins", "enable", OPENCLAW_PLUGIN_ID], timeout=20)
            if isinstance(completed, Exception):
                warnings.append(f"openclaw plugins enable failed to launch: {completed.__class__.__name__}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            if completed.returncode != 0:
                warnings.append(f"openclaw plugins enable failed with exit code {completed.returncode}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            plugin_enabled = True
            plugin_enable_changed = True
        hooks_config_changed = False
        if not hooks_ready:
            completed = _run_openclaw(
                command,
                ["config", "patch", "--stdin"],
                input_text=_openclaw_hooks_config_patch(OPENCLAW_PLUGIN_ID),
                timeout=20,
            )
            if isinstance(completed, Exception):
                warnings.append(f"openclaw config patch failed to launch: {completed.__class__.__name__}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            if completed.returncode != 0:
                warnings.append(f"openclaw config patch failed with exit code {completed.returncode}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            hooks_ready = _openclaw_runtime_hooks_ready(OPENCLAW_PLUGIN_ID)
            if not hooks_ready:
                warnings.append("openclaw config patch completed, but Datailor plugin hooks are still not fully enabled.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            hooks_config_changed = True
        mcp_changed = state != "configured"
        if mcp_changed:
            completed = _run_openclaw(command, ["mcp", "set", server_name, config_json], timeout=20)
            if isinstance(completed, Exception):
                warnings.append(f"openclaw mcp set failed to launch: {completed.__class__.__name__}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            if completed.returncode != 0:
                warnings.append(f"openclaw mcp set failed with exit code {completed.returncode}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
        details = _openclaw_details(
            profile,
            target,
            "openclaw plugins install && openclaw mcp set",
            server_name,
            plugin_registered=plugin_registered,
            plugin_enabled=plugin_enabled,
            hooks_ready=hooks_ready,
            plugin_files_changed=False,
        )
        changed = plugin_changed or plugin_install_changed or plugin_enable_changed or hooks_config_changed or mcp_changed
        return _result(True, profile, action, scope, changed, dry_run, target, "", "configured", warnings, details=details)
    if action == "remove":
        command = _openclaw_command()
        if not command:
            warnings.append("openclaw is not available on PATH.")
            return _result(False, profile, action, scope, False, dry_run, target, "", "not_installed", warnings)
        state = _openclaw_state(server_name)
        plugin_registered = _openclaw_plugin_registered(OPENCLAW_PLUGIN_ID)
        plugin_enabled = _openclaw_plugin_enabled(OPENCLAW_PLUGIN_ID) if plugin_registered else False
        hooks_ready = _openclaw_runtime_hooks_ready(OPENCLAW_PLUGIN_ID) if plugin_enabled else False
        details = _openclaw_details(
            profile,
            target,
            "openclaw plugins disable && openclaw mcp unset",
            server_name,
            plugin_registered=plugin_registered,
            plugin_enabled=plugin_enabled,
            hooks_ready=hooks_ready,
        )
        would_change = state != "not_configured" or plugin_enabled or hooks_ready
        if dry_run:
            planned_state = "would_remove" if would_change else "not_configured"
            return _result(True, profile, action, scope, would_change, dry_run, target, "", planned_state, warnings, details=details)
        mcp_changed = state != "not_configured"
        if mcp_changed:
            completed = _run_openclaw(command, ["mcp", "unset", server_name], timeout=20)
            if isinstance(completed, Exception):
                warnings.append(f"openclaw mcp unset failed to launch: {completed.__class__.__name__}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            if completed.returncode != 0:
                warnings.append(f"openclaw mcp unset failed with exit code {completed.returncode}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
        plugin_disable_changed = False
        if plugin_registered and plugin_enabled:
            completed = _run_openclaw(command, ["plugins", "disable", OPENCLAW_PLUGIN_ID], timeout=20)
            if isinstance(completed, Exception):
                warnings.append(f"openclaw plugins disable failed to launch: {completed.__class__.__name__}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            if completed.returncode != 0:
                warnings.append(f"openclaw plugins disable failed with exit code {completed.returncode}.")
                return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
            plugin_disable_changed = True
        hooks_ready = _openclaw_runtime_hooks_ready(OPENCLAW_PLUGIN_ID)
        if hooks_ready:
            warnings.append("openclaw plugins disable completed, but Datailor plugin hooks are still enabled.")
            return _result(False, profile, action, scope, False, dry_run, target, "", "error", warnings, details=details)
        details = _openclaw_details(profile, target, "openclaw plugins disable && openclaw mcp unset", server_name)
        changed = mcp_changed or plugin_disable_changed
        return _result(True, profile, action, scope, changed, dry_run, target, "", "not_configured", warnings, details=details)
    raise ValueError(f"unsupported integration action: {action}")


def _run_openclaw(
    command: str,
    args: list[str],
    *,
    timeout: int,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str] | Exception:
    try:
        return subprocess.run(
            [command, *args],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return exc


def _openclaw_state(server_name: str) -> str:
    command = _openclaw_command()
    if not command:
        return "not_installed"
    completed = _run_openclaw(command, ["mcp", "list"], timeout=10)
    if isinstance(completed, Exception):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return "configured" if server_name in completed.stdout else "not_configured"


def _openclaw_details(
    profile: ClientProfile,
    target: Any,
    command: str,
    server_name: str,
    *,
    plugin_registered: bool | None = None,
    plugin_enabled: bool | None = None,
    hooks_ready: bool | None = None,
    plugin_files_changed: bool | None = None,
) -> dict[str, Any]:
    if plugin_registered is None:
        plugin_registered = _openclaw_plugin_registered(OPENCLAW_PLUGIN_ID)
    if plugin_enabled is None:
        plugin_enabled = _openclaw_plugin_enabled(OPENCLAW_PLUGIN_ID) if plugin_registered else False
    if hooks_ready is None:
        hooks_ready = _openclaw_runtime_hooks_ready(OPENCLAW_PLUGIN_ID) if plugin_enabled else False
    return {
        "display_name": profile.display_name,
        "plugin_path": str(target.plugin_path) if getattr(target, "plugin_path", None) else "",
        "docs_url": profile.docs_url,
        "command": command,
        "server_name": server_name,
        "plugin_id": OPENCLAW_PLUGIN_ID,
        "plugin_installed": _openclaw_plugin_installed(target),
        "plugin_registered": plugin_registered,
        "plugin_enabled": plugin_enabled,
        "hooks_ready": hooks_ready,
        "plugin_files_changed": plugin_files_changed,
    }


def _openclaw_command() -> str:
    return which("openclaw") or ""


def _openclaw_plugin_installed(target: Any) -> bool:
    plugin_path = getattr(target, "plugin_path", None)
    return bool(
        plugin_path
        and (Path(plugin_path) / "index.js").exists()
        and (Path(plugin_path) / "package.json").exists()
        and (Path(plugin_path) / "openclaw.plugin.json").exists()
    )


def _openclaw_plugin_files_changed(target: Any) -> bool:
    plugin_path = getattr(target, "plugin_path", None)
    if not plugin_path:
        return False
    destination = Path(plugin_path)
    template = Path(__file__).resolve().parents[1] / "resources" / "openclaw-plugin"
    for path in template.rglob("*"):
        if not path.is_file():
            continue
        target_file = destination / path.relative_to(template)
        content = path.read_bytes()
        if not target_file.exists() or target_file.read_bytes() != content:
            return True
    return False


def _install_openclaw_plugin_files(target: Any) -> bool:
    plugin_path = getattr(target, "plugin_path", None)
    if not plugin_path:
        return False
    destination = Path(plugin_path)
    template = Path(__file__).resolve().parents[1] / "resources" / "openclaw-plugin"
    changed = False
    for path in template.rglob("*"):
        if not path.is_file():
            continue
        target_file = destination / path.relative_to(template)
        content = path.read_bytes()
        if not target_file.exists() or target_file.read_bytes() != content:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(content)
            changed = True
    return changed


def _openclaw_plugin_registered(plugin_id: str) -> bool:
    return _openclaw_plugin_entry(plugin_id) is not None


def _openclaw_plugin_enabled(plugin_id: str) -> bool:
    entry = _openclaw_plugin_entry(plugin_id)
    if not entry:
        return False
    return entry.get("enabled") is True and entry.get("status") != "disabled"


def _openclaw_plugin_entry(plugin_id: str) -> dict[str, Any] | None:
    command = _openclaw_command()
    if not command:
        return None
    completed = _run_openclaw(command, ["plugins", "list", "--json"], timeout=20)
    if isinstance(completed, Exception) or completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return _json_plugin_entry(payload, plugin_id)


def _openclaw_runtime_hooks_ready(plugin_id: str) -> bool:
    command = _openclaw_command()
    if not command:
        return False
    completed = _run_openclaw(command, ["plugins", "inspect", plugin_id, "--runtime", "--json"], timeout=20)
    if isinstance(completed, Exception) or completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False
    hooks = payload.get("typedHooks") if isinstance(payload, dict) else None
    if not isinstance(hooks, list):
        return False
    hook_names = {item.get("name") for item in hooks if isinstance(item, dict)}
    return OPENCLAW_EXPECTED_HOOKS.issubset(hook_names)


def _openclaw_hooks_config_patch(plugin_id: str) -> str:
    return json.dumps({"plugins": {"entries": {plugin_id: {"hooks": {"allowConversationAccess": True}}}}})


def _json_has_plugin_id(payload: Any, plugin_id: str) -> bool:
    return _json_plugin_entry(payload, plugin_id) is not None


def _json_plugin_entry(payload: Any, plugin_id: str) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if payload.get("id") == plugin_id:
            return payload
        plugins = payload.get("plugins")
        if isinstance(plugins, list):
            for item in plugins:
                found = _json_plugin_entry(item, plugin_id)
                if found:
                    return found
        entries = payload.get("entries")
        if isinstance(entries, dict):
            if plugin_id in entries and isinstance(entries[plugin_id], dict):
                return entries[plugin_id]
            for item in entries.values():
                found = _json_plugin_entry(item, plugin_id)
                if found:
                    return found
        return None
    if isinstance(payload, list):
        for item in payload:
            found = _json_plugin_entry(item, plugin_id)
            if found:
                return found
    return None


def _state(profile: ClientProfile, path: Path | None, server_name: str, scope: ScopeName, project_root: str | Path | None) -> str:
    if profile.name == "openclaw":
        return _openclaw_state(server_name)
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
