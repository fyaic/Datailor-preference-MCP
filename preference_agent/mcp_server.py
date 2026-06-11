from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .agent_discovery import cold_start_scan, discovery_report
from .capture_runner import CaptureConfig
from .backends import HeuristicBackend, build_backend
from .decision_conflicts import resolve_preference_conflict
from .engine import PreferenceEngine
from .feedback import record_feedback
from .hooks import PreferenceHookManager
from .injection import prewarm_session, sync_injection_artifacts
from .onboarding import get_onboarding_status, run_onboarding
from .paths import default_store_path, package_version
from .preference_actions import apply_preference_feedback
from .store import MarkdownPreferenceStore
from .ui.server import open_preference_panel
from .fitting import apply_fitting_plan, get_fitting_job, latest_fitting, run_fitting
from .fitting_background import mark_fitting_plan_reviewed, run_fitting_for_mode
from .fitting_trigger import normalize_mode


_AUTO_COLD_START_DONE: set[str] = set()


def _engine(store_path: str | Path | None = None, backend_name: str | None = None) -> PreferenceEngine:
    store = Path(store_path) if store_path else default_store_path()
    backend = build_backend(backend_name or os.getenv("PREFERENCE_MODEL_BACKEND", "auto"))
    fallback = HeuristicBackend() if backend.__class__.__name__ != "HeuristicBackend" else None
    return PreferenceEngine(MarkdownPreferenceStore(store), backend=backend, fallback_backend=fallback)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    from .config_env import load_local_env

    load_local_env()
    parser = argparse.ArgumentParser(prog="datailor-mcp")
    parser.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", ""))
    parser.add_argument("--store", default=os.getenv("PREFERENCE_STORE_PATH", ""))
    parser.add_argument("--backend", default=os.getenv("PREFERENCE_MODEL_BACKEND") or "auto")
    args = parser.parse_args(argv)
    if args.agent:
        os.environ["PREFERENCE_CALLER_AGENT"] = args.agent
    engine = _engine(args.store or None, args.backend)
    for line in sys.stdin:
        line = line.lstrip("\ufeff").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request, engine)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except Exception as exc:
            request_id = None
            try:
                request_id = json.loads(line).get("id")
            except Exception:
                pass
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": str(exc)},
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 0


def handle_request(request: dict[str, Any], engine: PreferenceEngine) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        return _response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "prompts": {"listChanged": False}},
                "serverInfo": {"name": "datailor-preference-mcp", "version": package_version()},
            },
        )
    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS})
    if method == "prompts/list":
        return _response(request_id, {"prompts": PROMPTS})
    if method == "prompts/get":
        params = request.get("params", {})
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if name in {"preferences", "preference_manifesto"}:
            return _response(request_id, _preferences_prompt(arguments))
        return _error(request_id, -32601, f"Unknown prompt: {name}")
    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "get_preference_decision":
            agent = arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"
            auto_discovery = _maybe_auto_cold_start(engine, agent=str(agent))
            result = engine.decide(
                task=arguments.get("task") or arguments.get("question") or "",
                context=arguments.get("context") or {},
                agent=str(agent),
            )
            if auto_discovery:
                result["auto_discovery"] = auto_discovery
            return _runtime_tool_response(request_id, name, arguments, result)
        if name == "get_onboarding_status":
            result = get_onboarding_status(
                store_path=engine.store.path,
                agent_hint=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                backend=os.getenv("PREFERENCE_MODEL_BACKEND") or "auto",
            )
            return _tool_response(request_id, result.to_dict())
        if name == "start_cold_start_capture":
            install_rules_arg = arguments.get("install_agent_rules")
            install_kimi_hooks_arg = arguments.get("install_kimi_hooks")
            result = run_onboarding(
                store_path=engine.store.path,
                agent_hint=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                backend=os.getenv("PREFERENCE_MODEL_BACKEND") or "auto",
                mode=str(arguments.get("mode") or os.getenv("PREFERENCE_AUTO_DISCOVERY_MODE", "recall-extract")),
                dry_run=bool(arguments.get("dry_run", False)),
                capture=True,
                max_files=int(arguments.get("max_files") or 0),
                max_minutes=float(arguments.get("max_minutes") or 0.0),
                state_file=arguments.get("state_file") or None,
                open_ui=bool(arguments.get("open_ui", False)),
                host=str(arguments.get("host") or "127.0.0.1"),
                port=int(arguments.get("port") or 8080),
                install_agent_rules_enabled=True if install_rules_arg is None else bool(install_rules_arg),
                agent_rules_target=arguments.get("agent_rules_target") or None,
                install_kimi_hooks_enabled=None if install_kimi_hooks_arg is None else bool(install_kimi_hooks_arg),
                kimi_hooks_target=arguments.get("kimi_hooks_target") or None,
            )
            return _tool_response(request_id, result)
        if name == "capture_preferences_from_session":
            source_path = str(arguments.get("source_path") or "").strip()
            if source_path:
                result = engine.capture_path(
                    source_path,
                    dry_run=bool(arguments.get("dry_run", False)),
                )
                return _tool_response(request_id, result.to_dict())
            config = CaptureConfig.from_env()
            mode = str(arguments.get("mode") or "").strip()
            if mode:
                config.mode = mode
            max_files = int(arguments.get("max_files") or 0)
            max_minutes = float(arguments.get("max_minutes") or 0.0)
            if max_minutes:
                config.max_minutes = max_minutes
            result = cold_start_scan(
                store_path=engine.store.path,
                agent_hint=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT", "")),
                config=config,
                dry_run=bool(arguments.get("dry_run", False)),
                max_files=max_files,
            )
            return _tool_response(request_id, result.to_dict())
        if name == "discover_agents":
            result = discovery_report(agent_hint=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT", "")))
            return _tool_response(request_id, result)
        if name == "prewarm_preferences":
            agent = str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent")
            auto_discovery = _maybe_auto_cold_start(engine, agent=agent)
            result = prewarm_session(
                store_path=engine.store.path,
                task=arguments.get("task") or "new session initialization",
                agent=agent,
                context=arguments.get("context") or {},
                output_dir=arguments.get("output_dir") or None,
            )
            payload = result.to_dict()
            if auto_discovery:
                payload["auto_discovery"] = auto_discovery
            return _runtime_tool_response(request_id, name, arguments, payload)
        if name == "hook_session_start":
            manager = PreferenceHookManager(engine)
            auto_discovery = _maybe_auto_cold_start(
                engine,
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
            )
            result = manager.on_session_start(
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                session_id=str(arguments.get("session_id") or "default"),
                task=str(arguments.get("task") or ""),
                context=arguments.get("context") if isinstance(arguments.get("context"), dict) else {},
                output_dir=arguments.get("output_dir") or None,
            )
            if auto_discovery:
                result["auto_discovery"] = auto_discovery
            return _runtime_tool_response(request_id, name, arguments, result)
        if name == "hook_user_message":
            manager = PreferenceHookManager(engine)
            auto_discovery = _maybe_auto_cold_start(
                engine,
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
            )
            result = manager.on_user_message(
                message=str(arguments.get("message") or ""),
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                session_id=str(arguments.get("session_id") or "default"),
                context=arguments.get("context") if isinstance(arguments.get("context"), dict) else {},
            )
            if auto_discovery:
                result["auto_discovery"] = auto_discovery
            return _runtime_tool_response(request_id, name, arguments, result)
        if name == "hook_turn_complete":
            manager = PreferenceHookManager(engine)
            result = manager.on_turn_complete(
                user_message=str(arguments.get("user_message") or arguments.get("user") or ""),
                assistant_response=str(arguments.get("assistant_response") or arguments.get("assistant") or ""),
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                session_id=str(arguments.get("session_id") or "default"),
                context=arguments.get("context") if isinstance(arguments.get("context"), dict) else {},
                dry_run=bool(arguments.get("dry_run", False)),
            )
            return _runtime_tool_response(request_id, name, arguments, result)
        if name == "hook_action_executed":
            manager = PreferenceHookManager(engine)
            result = manager.on_action_executed(
                action=str(arguments.get("action") or ""),
                result=str(arguments.get("result") or ""),
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                session_id=str(arguments.get("session_id") or "default"),
                metadata=arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {},
                dry_run=bool(arguments.get("dry_run", False)),
            )
            return _runtime_tool_response(request_id, name, arguments, result)
        if name == "hook_session_end":
            manager = PreferenceHookManager(engine)
            messages = arguments.get("messages") if isinstance(arguments.get("messages"), list) else []
            result = manager.on_session_end(
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                session_id=str(arguments.get("session_id") or "default"),
                messages=messages,
                dry_run=bool(arguments.get("dry_run", False)),
            )
            return _runtime_tool_response(request_id, name, arguments, result)
        if name == "sync_preference_injection":
            result = sync_injection_artifacts(
                store_path=engine.store.path,
                output_dir=arguments.get("output_dir") or None,
                target_files=arguments.get("target_files") or [],
            )
            return _tool_response(request_id, result.to_dict())
        if name == "report_preference_feedback":
            feedback_type = arguments.get("feedback_type") or arguments.get("type") or "correction"
            user_feedback = arguments.get("user_feedback") or ""
            preference_id = arguments.get("preference_id") or ""
            preference_text = arguments.get("preference_text") or arguments.get("preference") or ""
            result = record_feedback(
                feedback_type=feedback_type,
                user_feedback=user_feedback,
                preference_id=preference_id,
                preference_text=preference_text,
                agent=arguments.get("agent") or "agent",
                task=arguments.get("task") or "",
                source="mcp",
                context=arguments.get("context") or {},
            )
            result["preference_update"] = apply_preference_feedback(
                store_path=engine.store.path,
                feedback_type=str(feedback_type),
                user_feedback=str(user_feedback),
                preference_id=str(preference_id),
                preference_text=str(preference_text),
            ).to_dict()
            return _tool_response(request_id, result)
        if name == "resolve_preference_conflict":
            result = resolve_preference_conflict(
                store_path=engine.store.path,
                conflict_key_value=str(arguments.get("conflict_key") or ""),
                resolution=str(arguments.get("resolution") or ""),
                selected_preference_id=str(arguments.get("selected_preference_id") or ""),
                conflict_ids=[str(item) for item in arguments.get("conflict_ids", [])] if isinstance(arguments.get("conflict_ids"), list) else None,
                user_feedback=str(arguments.get("user_feedback") or ""),
                context=arguments.get("context") if isinstance(arguments.get("context"), dict) else {},
            )
            return _tool_response(request_id, result)
        if name == "open_preference_panel":
            auto_discovery = _maybe_auto_cold_start(
                engine,
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
            )
            requested_port = arguments.get("port")
            result = open_preference_panel(
                store_path=engine.store.path,
                host=arguments.get("host") or None,
                port=int(requested_port) if requested_port is not None else int(os.getenv("PREFERENCE_UI_PORT", "8080")),
                open_browser=bool(arguments.get("open_browser", False)),
            )
            if auto_discovery:
                result["auto_discovery"] = auto_discovery
            return _tool_response(request_id, result)
        if name == "start_fitting":
            mode_argument = arguments.get("mode")
            auto_apply_requested = bool(arguments.get("auto_apply", False))
            mode = normalize_mode(mode_argument or ("auto" if auto_apply_requested else "curate"))
            # Explicit mode wins: mode="auto" means auto-apply even if auto_apply is false.
            if not bool(arguments.get("dry_run", False)):
                result = run_fitting_for_mode(
                    store_path=engine.store.path,
                    mode=mode,
                    agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                    source=arguments.get("source") or None,
                    instructions=str(arguments.get("instructions") or ""),
                    instructions_file=arguments.get("instructions_file") or None,
                    fitting_dir=arguments.get("fitting_dir") or None,
                    max_files=int(arguments.get("max_files") or 0),
                )
                return _tool_response(request_id, result)
            result = run_fitting(
                store_path=engine.store.path,
                instructions=str(arguments.get("instructions") or ""),
                instructions_file=arguments.get("instructions_file") or None,
                source=arguments.get("source") or None,
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                fitting_dir=arguments.get("fitting_dir") or None,
                dry_run=bool(arguments.get("dry_run", False)),
                review=True,
                max_files=int(arguments.get("max_files") or 0),
            )
            return _tool_response(request_id, {"ok": True, **result.to_dict()})
        if name == "get_fitting_status":
            job_id = str(arguments.get("job_id") or "").strip()
            if job_id:
                result = get_fitting_job(job_id, fitting_dir=arguments.get("fitting_dir") or None)
            else:
                result = latest_fitting(fitting_dir=arguments.get("fitting_dir") or None)
            return _tool_response(request_id, result if isinstance(result, dict) else {"ok": True, **result})
        if name == "apply_fitting_plan":
            accepted = arguments.get("accepted_change_ids") if isinstance(arguments.get("accepted_change_ids"), list) else []
            result = apply_fitting_plan(
                job_id=str(arguments.get("job_id") or ""),
                accepted_change_ids=[str(item) for item in accepted],
                store_path=engine.store.path,
                fitting_dir=arguments.get("fitting_dir") or None,
            )
            if result.get("ok") and not result.get("remaining_pending"):
                mark_fitting_plan_reviewed(
                    str(arguments.get("job_id") or ""),
                    fitting_dir=arguments.get("fitting_dir") or None,
                    accepted=len(result.get("applied") or []),
                )
            return _tool_response(request_id, result)
        if name == "toggle_preferences":
            from .config_env import update_env_file, user_env_path

            enabled = bool(arguments.get("enabled", True))
            update_env_file({"DATAILOR_ENABLED": "true" if enabled else "false"})
            return _tool_response(
                request_id,
                {
                    "ok": True,
                    "enabled": enabled,
                    "env_file": str(user_env_path()),
                    "message": f"Datailor preference injection {'enabled' if enabled else 'disabled'}.",
                },
            )
        return _error(request_id, -32601, f"Unknown tool: {name}")
    return _error(request_id, -32601, f"Unknown method: {method}")


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_response(request_id: Any, data: dict[str, Any]) -> dict[str, Any]:
    return _response(
        request_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(data, ensure_ascii=False, indent=2),
                }
            ],
            "isError": False,
        },
    )


def _runtime_tool_response(request_id: Any, tool_name: str, arguments: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    if bool(arguments.get("include_debug", False)):
        return _tool_response(request_id, data)
    return _tool_response(request_id, _compact_runtime_payload(tool_name, data))


def _compact_runtime_payload(tool_name: str, data: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "get_preference_decision":
        return _compact_decision_payload(data, tool=tool_name)
    if tool_name == "prewarm_preferences":
        return _compact_prewarm_payload(data, tool=tool_name)
    if tool_name in {"hook_session_start", "hook_user_message"}:
        return _compact_preference_hook_payload(data, tool=tool_name)
    if tool_name == "hook_turn_complete":
        return _compact_turn_payload(data, tool=tool_name)
    if tool_name == "hook_action_executed":
        return _compact_action_payload(data, tool=tool_name)
    if tool_name == "hook_session_end":
        return _compact_session_end_payload(data, tool=tool_name)
    return data


def _compact_decision_payload(data: dict[str, Any], tool: str) -> dict[str, Any]:
    from .config_env import is_datailor_enabled

    payload = {
        "ok": True,
        "tool": tool,
        "enabled": is_datailor_enabled(),
        "decision": str(data.get("decision") or ""),
        "agent_instruction": str(data.get("agent_instruction") or ""),
        "matched_count": _matched_count(data.get("matched_preferences")),
        "matched_preferences": _compact_matches(data.get("matched_preferences")),
        "escalate": bool(data.get("escalate", False)),
        "reason": str(data.get("reason") or ""),
        "gate_summary": _compact_gate_summary(data.get("gate_summary")),
        "checked_at": str(data.get("checked_at") or ""),
        "debug_ref": "Re-run with include_debug=true for full Datailor metadata.",
    }
    _add_conflict(payload, data)
    _add_auto_discovery(payload, data)
    return _drop_empty(payload)


def _compact_prewarm_payload(data: dict[str, Any], tool: str) -> dict[str, Any]:
    from .config_env import is_datailor_enabled

    payload = {
        "ok": True,
        "tool": tool,
        "enabled": is_datailor_enabled(),
        "decision": str(data.get("decision") or ""),
        "agent_instruction": str(data.get("agent_instruction") or ""),
        "matched_count": _matched_count(data.get("matched_preferences")),
        "matched_preferences": _compact_matches(data.get("matched_preferences")),
        "fallback_instruction": str(data.get("fallback_instruction") or ""),
        "fallback_applied": data.get("fallback_applied"),
        "session_cache_file": str(data.get("session_cache_file") or ""),
        "fallback_file": str(data.get("fallback_file") or ""),
        "gate_summary": _compact_gate_summary(data.get("gate_summary")),
        "checked_at": str(data.get("checked_at") or ""),
        "debug_ref": "Re-run with include_debug=true for full Datailor metadata.",
    }
    _add_auto_discovery(payload, data)
    return _drop_empty(payload)


def _compact_preference_hook_payload(data: dict[str, Any], tool: str) -> dict[str, Any]:
    from .config_env import is_datailor_enabled

    decision = data.get("decision") if isinstance(data.get("decision"), dict) else {}
    prewarm = data.get("prewarm") if isinstance(data.get("prewarm"), dict) else {}
    instruction = str(decision.get("agent_instruction") or "")
    payload = {
        "ok": bool(data.get("ok", True)),
        "tool": tool,
        "enabled": is_datailor_enabled(),
        "hook": str(data.get("hook") or ""),
        "agent": str(data.get("agent") or ""),
        "session_id": str(data.get("session_id") or ""),
        "preference_signal": data.get("preference_signal"),
        "decision": str(decision.get("decision") or ""),
        "agent_instruction": instruction,
        "matched_count": _matched_count(decision.get("matched_preferences")),
        "matched_preferences": _compact_matches(decision.get("matched_preferences")),
        "escalate": bool(decision.get("escalate", False)),
        "reason": str(decision.get("reason") or ""),
        "gate_summary": _compact_gate_summary(decision.get("gate_summary")),
        "fallback_instruction": str(prewarm.get("fallback_instruction") or ""),
        "fallback_applied": prewarm.get("fallback_applied"),
        "session_cache_file": str(prewarm.get("session_cache_file") or ""),
        "fallback_file": str(prewarm.get("fallback_file") or ""),
        "debug_ref": "Re-run with include_debug=true for full Datailor metadata.",
    }
    _add_conflict(payload, decision)
    _add_auto_discovery(payload, data)
    return _drop_empty(payload)


def _compact_turn_payload(data: dict[str, Any], tool: str) -> dict[str, Any]:
    diagnostic = data.get("capture_diagnostic") if isinstance(data.get("capture_diagnostic"), dict) else {}
    payload = {
        "ok": bool(data.get("ok", True)),
        "tool": tool,
        "hook": str(data.get("hook") or ""),
        "agent": str(data.get("agent") or ""),
        "session_id": str(data.get("session_id") or ""),
        "buffered": data.get("buffered"),
        "preference_signal": data.get("preference_signal"),
        "buffer_size": data.get("buffer_size"),
        "max_turns": data.get("max_turns"),
        "reason": str(data.get("reason") or ""),
        "capture_reason": str(diagnostic.get("primary_reason") or ""),
        "capture_reason_codes": diagnostic.get("reason_codes") if isinstance(diagnostic.get("reason_codes"), list) else None,
        "flush": _compact_flush(data.get("flush")),
        "fitting": _compact_fitting(data.get("fitting")),
        "debug_ref": "Re-run with include_debug=true for full Datailor metadata.",
    }
    return _drop_empty(payload)


def _compact_action_payload(data: dict[str, Any], tool: str) -> dict[str, Any]:
    payload = {
        "ok": bool(data.get("ok", True)),
        "tool": tool,
        "hook": str(data.get("hook") or ""),
        "agent": str(data.get("agent") or ""),
        "session_id": str(data.get("session_id") or ""),
        "captured": data.get("captured"),
        "degraded": data.get("degraded"),
        "reason": str(data.get("reason") or ""),
        "error": _compact_error(data.get("error")),
        "sync_error": _compact_error(data.get("sync_error")),
        "log_error": _compact_error(data.get("log_error")),
        "capture": _compact_capture(data.get("capture")),
        "debug_ref": "Re-run with include_debug=true for full Datailor metadata.",
    }
    return _drop_empty(payload)


def _compact_session_end_payload(data: dict[str, Any], tool: str) -> dict[str, Any]:
    payload = {
        "ok": bool(data.get("ok", True)),
        "tool": tool,
        "hook": str(data.get("hook") or ""),
        "agent": str(data.get("agent") or ""),
        "session_id": str(data.get("session_id") or ""),
        "buffer_flush": _compact_flush(data.get("buffer_flush")),
        "full_capture": _compact_capture(data.get("full_capture")),
        "sync": _compact_sync(data.get("sync")),
        "fitting": _compact_fitting(data.get("fitting")),
        "debug_ref": "Re-run with include_debug=true for full Datailor metadata.",
    }
    return _drop_empty(payload)


def _compact_matches(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    matches: list[dict[str, str]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        matches.append(
            _drop_empty(
                {
                    "id": str(item.get("id") or ""),
                    "title": str(item.get("title") or ""),
                    "instruction": str(item.get("instruction") or ""),
                    "score": item.get("score"),
                    "live_confidence": item.get("live_confidence"),
                    "category": str(item.get("category") or ""),
                    "gate_reason": str(item.get("gate_reason") or ""),
                }
            )
        )
    return matches


def _matched_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _add_conflict(payload: dict[str, Any], decision: dict[str, Any]) -> None:
    conflict = decision.get("conflict")
    if not isinstance(conflict, dict):
        return
    options = conflict.get("options")
    if not isinstance(options, list):
        return
    payload["conflict_options"] = [
        _drop_empty(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "instruction": str(item.get("instruction") or ""),
            }
        )
        for item in options
        if isinstance(item, dict)
    ]


def _add_auto_discovery(payload: dict[str, Any], data: dict[str, Any]) -> None:
    auto = data.get("auto_discovery")
    if not isinstance(auto, dict):
        return
    payload["auto_discovery"] = _drop_empty(
        {
            "attempted": auto.get("attempted"),
            "dry_run": auto.get("dry_run"),
            "scanned_sources_count": _collection_count(auto.get("scanned_sources")),
            "changed_files_count": _collection_count(auto.get("changed_files")),
            "total_files_seen": auto.get("total_files_seen"),
        }
    )


def _compact_capture(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "source": str(value.get("source") or ""),
            "sessions_seen": value.get("sessions_seen"),
            "candidates_seen": value.get("candidates_seen"),
            "added_count": _list_count(value.get("added")),
            "merged_count": _list_count(value.get("merged")),
            "replaced_count": _list_count(value.get("replaced")),
            "conflicts_count": _list_count(value.get("conflicts")),
            "dry_run": value.get("dry_run"),
            "filtered_candidates": value.get("filtered_candidates"),
            "backend_errors_count": _collection_count(value.get("backend_errors")),
            "cap": _compact_cap(value.get("cap")),
        }
    )


def _compact_flush(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "flushed": value.get("flushed"),
            "turns": value.get("turns"),
            "reason": str(value.get("reason") or ""),
            "capture": _compact_capture(value.get("capture")),
        }
    )


def _compact_sync(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "rules_count": value.get("rules_count"),
            "fallback_count": value.get("fallback_count"),
            "target_files_count": _list_count(value.get("target_files")),
        }
    )


def _compact_error(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "stage": str(value.get("stage") or ""),
            "error_type": str(value.get("type") or ""),
            "status_code": value.get("status_code"),
            "summary": str(value.get("summary") or _compact_error_summary(str(value.get("message") or ""))),
        }
    )


def _compact_gate_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "active_candidates": value.get("active_candidates"),
            "passed": value.get("passed"),
            "reason": str(value.get("reason") or ""),
        }
    )


def _compact_error_summary(message: str) -> str:
    text = str(message or "").strip().splitlines()[0]
    for marker in (" {", "\t{"):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
    return text[:160]


def _compact_cap(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _drop_empty(
        {
            "limit": value.get("limit"),
            "before_count": value.get("before_count"),
            "after_count": value.get("after_count"),
            "merged_count": _list_count(value.get("merged")),
            "review_required": value.get("review_required"),
            "review_candidates_count": _list_count(value.get("review_candidates")),
            "overflow_record_count": value.get("overflow_record_count"),
            "added_removed_by_cap_count": _list_count(value.get("added_removed_by_cap")),
            "artifact_file": str(value.get("artifact_file") or ""),
        }
    )


def _compact_fitting(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    decision = value.get("decision")
    reason = decision.get("reason") if isinstance(decision, dict) else value.get("reason")
    return _drop_empty(
        {
            "ok": value.get("ok"),
            "triggered": value.get("triggered"),
            "job_id": str(value.get("job_id") or ""),
            "reason": str(reason or ""),
        }
    )


def _list_count(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def _collection_count(value: Any) -> int:
    return len(value) if isinstance(value, (dict, list, tuple, set)) else 0


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key == "matched_preferences" or item not in ("", None, [], {})
    }


def _maybe_auto_cold_start(engine: PreferenceEngine, agent: str) -> dict[str, Any] | None:
    if not _env_flag("PREFERENCE_AUTO_DISCOVERY", True):
        return None
    engine.store.ensure()
    if engine.store.load():
        return None
    key = f"{engine.store.path.resolve()}::{agent.strip().casefold() or 'agent'}"
    if key in _AUTO_COLD_START_DONE:
        return None
    _AUTO_COLD_START_DONE.add(key)
    config = CaptureConfig.from_env()
    mode = os.getenv("PREFERENCE_AUTO_DISCOVERY_MODE", "").strip()
    if mode:
        config.mode = mode
    max_minutes = _env_float("PREFERENCE_AUTO_DISCOVERY_MAX_MINUTES", 0.0)
    if max_minutes:
        config.max_minutes = max_minutes
    result = cold_start_scan(
        store_path=engine.store.path,
        agent_hint=agent,
        config=config,
        dry_run=_env_flag("PREFERENCE_AUTO_DISCOVERY_DRY_RUN", False),
        max_files=_env_int("PREFERENCE_AUTO_DISCOVERY_MAX_FILES", 0),
    )
    return {
        "attempted": True,
        "agent_hint": agent,
        "dry_run": result.dry_run,
        "scanned_sources": result.scanned_sources,
        "changed_files": result.changed_files,
        "total_files_seen": result.total_files_seen,
        "state_file": result.state_file,
        "skipped_sources": result.skipped_sources,
        "sources": [
            {
                "agent": item.get("agent", ""),
                "path": item.get("path", ""),
                "latest_mtime_iso": item.get("latest_mtime_iso", ""),
            }
            for item in result.sources[:10]
        ],
    }


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _preferences_prompt(arguments: dict[str, Any]) -> dict[str, Any]:
    open_browser = bool(arguments.get("open_browser", True))
    port = arguments.get("port")
    host = arguments.get("host") or "127.0.0.1"
    details = [f"host={host}", f"open_browser={str(open_browser).lower()}"]
    if port is not None:
        details.append(f"port={port}")
    return {
        "description": "Open the local Personal Preference Manifesto panel.",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        "Open my Personal Preference Manifesto now. "
                        "Call the MCP tool `open_preference_panel` with "
                        f"{', '.join(details)}. "
                        "Then reply with the localhost URL only, plus a short note that it is local-only. "
                        "Do not summarize my preferences in chat."
                    ),
                },
            }
        ],
    }


PROMPTS = [
    {
        "name": "preferences",
        "description": "Open the local Personal Preference Manifesto panel. In clients that expose MCP prompts as slash commands, this appears as /preferences.",
        "arguments": [
            {
                "name": "open_browser",
                "description": "Try to open the browser automatically. Defaults to true.",
                "required": False,
            },
            {
                "name": "host",
                "description": "Bind host. Defaults to 127.0.0.1.",
                "required": False,
            },
            {
                "name": "port",
                "description": "Preferred port. Defaults to 8080; the UI auto-increments if occupied.",
                "required": False,
            },
        ],
    }
]


TOOLS = [
    {
        "name": "get_onboarding_status",
        "description": "Check whether Datailor has completed first-run setup: MCP availability, empty store state, discovered local agent histories, and next commands.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Caller agent name, such as codex/kimi/claude"},
            },
        },
    },
    {
        "name": "start_cold_start_capture",
        "description": "Start client-independent cold-start capture: initialize the preference store, auto-discover Claude/Codex/Kimi histories, and scan them. Equivalent to the capture part of CLI `datailor onboard`.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Caller agent name, used to order discovered sources"},
                "mode": {"type": "string", "description": "Capture mode, such as recall-extract or semantic-extract"},
                "max_files": {"type": "integer", "description": "Optional scan file limit; unlimited by default"},
                "max_minutes": {"type": "number", "description": "Optional per-source runtime limit; unlimited by default"},
                "dry_run": {"type": "boolean", "description": "Preview only; do not write the preference store"},
                "open_ui": {"type": "boolean", "description": "Try opening the local Manifesto UI after completion"},
                "host": {"type": "string", "description": "UI host, defaults to 127.0.0.1"},
                "port": {"type": "integer", "description": "UI port, defaults to 8080"},
                "state_file": {"type": "string", "description": "Optional incremental scan state file"},
                "install_agent_rules": {"type": "boolean", "description": "Whether to install the AGENTS.md managed block automatically; defaults to true"},
                "agent_rules_target": {"type": "string", "description": "Optional AGENTS.md target path"},
                "install_kimi_hooks": {"type": "boolean", "description": "Whether to install Kimi CLI lifecycle hooks automatically when agent is kimi; defaults to true"},
                "kimi_hooks_target": {"type": "string", "description": "Optional Kimi config.toml target path"},
            },
        },
    },
    {
        "name": "get_preference_decision",
        "description": "Read user preferences before an agent replies or acts, and return the preference instruction to apply.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Current task, planned action, or user question"},
                "question": {"type": "string", "description": "Compatibility field: the question the agent is about to ask the user"},
                "agent": {"type": "string", "description": "Caller agent name, such as codex/openclaw"},
                "context": {"type": "object", "description": "Context such as project, language, risk, and files"},
                "include_debug": {"type": "boolean", "description": "Return full internal metadata instead of the compact visible payload"},
            },
        },
    },
    {
        "name": "capture_preferences_from_session",
        "description": "Capture user preferences from a session file or directory and incrementally update the Markdown preference store. If source_path is omitted, installed agent histories are auto-discovered for cold-start scan.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Session file or directory path; omitted means auto-discover installed agent histories"},
                "agent": {"type": "string", "description": "Caller agent name, used to order cold-start sources"},
                "mode": {"type": "string", "description": "Capture mode for auto-discovered scan, such as recall-extract"},
                "max_files": {"type": "integer", "description": "Optional auto-discovery scan limit; unlimited by default"},
                "max_minutes": {"type": "number", "description": "Optional per-source runtime limit; unlimited by default"},
                "dry_run": {"type": "boolean", "description": "Analyze only; do not write"},
            },
        },
    },
    {
        "name": "discover_agents",
        "description": "Detect installed Claude/Codex/Kimi/Cursor/Trae clients and return history paths that can be captured automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Caller agent name, used for ordering hints"},
            },
        },
    },
    {
        "name": "prewarm_preferences",
        "description": "Prewarm preferences at session start and generate session-level preference cache and executable instructions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task or initialization note for this session"},
                "agent": {"type": "string", "description": "Caller agent name"},
                "context": {"type": "object", "description": "Context such as project, path, risk, and task type"},
                "output_dir": {"type": "string", "description": "Optional session cache output directory"},
                "include_debug": {"type": "boolean", "description": "Return full internal metadata instead of the compact visible payload"},
            },
        },
    },
    {
        "name": "hook_session_start",
        "description": "H1 session-start hook. Runs preference decisioning and session prewarm, then returns preference instructions and cache files for this session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Caller agent name"},
                "session_id": {"type": "string", "description": "Current session id"},
                "task": {"type": "string", "description": "Task or initialization note for this session"},
                "context": {"type": "object", "description": "Context such as project, path, risk, and task type"},
                "output_dir": {"type": "string", "description": "Optional session cache output directory"},
                "include_debug": {"type": "boolean", "description": "Return full internal metadata instead of the compact visible payload"},
            },
        },
    },
    {
        "name": "hook_user_message",
        "description": "H2 user-message hook. Retrieves relevant preferences whenever a user message arrives and returns instructions for this turn.",
        "inputSchema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string", "description": "Current user message"},
                "agent": {"type": "string", "description": "Caller agent name"},
                "session_id": {"type": "string", "description": "Current session id"},
                "context": {"type": "object", "description": "Current context"},
                "include_debug": {"type": "boolean", "description": "Return full internal metadata instead of the compact visible payload"},
            },
        },
    },
    {
        "name": "hook_turn_complete",
        "description": "H3 turn-complete hook. Writes qualifying turns to a buffer, then batch-extracts and merges/conflicts after N turns.",
        "inputSchema": {
            "type": "object",
            "required": ["user_message", "assistant_response"],
            "properties": {
                "user_message": {"type": "string", "description": "User message for this turn"},
                "assistant_response": {"type": "string", "description": "Agent response for this turn"},
                "agent": {"type": "string", "description": "Caller agent name"},
                "session_id": {"type": "string", "description": "Current session id"},
                "context": {"type": "object", "description": "Current context"},
                "dry_run": {"type": "boolean", "description": "Preview only; do not write the preference store"},
                "include_debug": {"type": "boolean", "description": "Return full internal metadata instead of the compact visible payload"},
            },
        },
    },
    {
        "name": "hook_action_executed",
        "description": "H4 action hook. Creates low-confidence reviewable preferences from behavior signals such as code verification or formatting.",
        "inputSchema": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "description": "Executed action, such as run_tests_before_done"},
                "result": {"type": "string", "description": "Action result or summary"},
                "agent": {"type": "string", "description": "Caller agent name"},
                "session_id": {"type": "string", "description": "Current session id"},
                "metadata": {"type": "object", "description": "Action metadata"},
                "dry_run": {"type": "boolean", "description": "Preview only; do not write the preference store"},
                "include_debug": {"type": "boolean", "description": "Return full internal metadata instead of the compact visible payload"},
            },
        },
    },
    {
        "name": "hook_session_end",
        "description": "H5 session-end hook. Flushes the turn buffer, optionally captures full message history, and syncs injection artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Caller agent name"},
                "session_id": {"type": "string", "description": "Current session id"},
                "messages": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optional full session message list; each item includes role/content",
                },
                "dry_run": {"type": "boolean", "description": "Preview only; do not write the preference store"},
                "include_debug": {"type": "boolean", "description": "Return full internal metadata instead of the compact visible payload"},
            },
        },
    },
    {
        "name": "sync_preference_injection",
        "description": "Generate static rules, snapshots, and local fallback files; optionally sync them to targets such as AGENTS.md or .cursorrules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "description": "Injection artifact output directory"},
                "target_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Target rule files that should receive a managed block",
                },
            },
        },
    },
    {
        "name": "report_preference_feedback",
        "description": "Record feedback when the user corrects, confirms, rejects, or uses a preference; used for later preference evolution.",
        "inputSchema": {
            "type": "object",
            "required": ["feedback_type", "user_feedback"],
            "properties": {
                "feedback_type": {
                    "type": "string",
                    "enum": ["correction", "confirmation", "usage", "rejection"],
                    "description": "Feedback type",
                },
                "user_feedback": {"type": "string", "description": "Original user feedback or correction text"},
                "preference_id": {"type": "string", "description": "Related preference ID, if known"},
                "preference_text": {"type": "string", "description": "Related preference text when the ID is unknown"},
                "agent": {"type": "string", "description": "Caller agent"},
                "task": {"type": "string", "description": "Task during which feedback happened"},
                "context": {"type": "object", "description": "Feedback context"},
            },
        },
    },
    {
        "name": "resolve_preference_conflict",
        "description": "After decide detects conflicting matched preferences and the user answers, update preference state and mark the conflict resolved.",
        "inputSchema": {
            "type": "object",
            "required": ["conflict_key", "resolution"],
            "properties": {
                "conflict_key": {"type": "string", "description": "conflict.key returned by decide"},
                "resolution": {
                    "type": "string",
                    "enum": ["prefer", "select", "neither", "exception", "custom", "correct"],
                    "description": "prefer/select chooses one; neither/exception means neither applies; custom/correct records a new user rule",
                },
                "selected_preference_id": {"type": "string", "description": "Preference ID selected by the user"},
                "conflict_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Conflicting preference IDs; optional because they can be derived from conflict_key",
                },
                "user_feedback": {"type": "string", "description": "User answer or custom rule"},
                "context": {"type": "object", "description": "Context where this conflict applies or is excepted"},
            },
        },
    },
    {
        "name": "open_preference_panel",
        "description": "Start the local Personal Preference Manifesto panel and return a localhost link for reviewing preferences, feedback, and pending items.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Caller agent name, used to order cold-start discovery when the store is empty"},
                "host": {"type": "string", "description": "Defaults to 127.0.0.1 for local access only"},
                "port": {"type": "integer", "description": "Defaults to 8080; auto-increments when occupied"},
                "open_browser": {"type": "boolean", "description": "Whether to try opening the browser automatically"},
            },
        },
    },
    {
        "name": "start_fitting",
        "description": "Start Datailor Fitting offline consolidation: generate typed insights, memory-rot suggestions, and a Fitting Report from natural-language instructions. Review-first by default; does not directly overwrite the preference store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Caller agent name"},
                "instructions": {"type": "string", "description": "Focus and ignore scope, such as focus on UI writing preferences; ignore one-off install commands"},
                "instructions_file": {"type": "string", "description": "Optional UTF-8 instructions file"},
                "source": {"type": "string", "description": "Optional history source file or directory"},
                "max_files": {"type": "integer", "description": "Optional history file limit"},
                "dry_run": {"type": "boolean", "description": "Preview only; do not write job artifacts"},
                "mode": {"type": "string", "enum": ["auto", "curate"], "description": "Optional Fitting mode; auto applies high-confidence preferences automatically"},
                "auto_apply": {"type": "boolean", "description": "Whether to auto-apply high-confidence preferences; mode=auto takes precedence"},
                "fitting_dir": {"type": "string", "description": "Optional Fitting artifact directory"},
            },
        },
    },
    {
        "name": "get_fitting_status",
        "description": "Read Datailor Fitting job status; returns the latest job when job_id is omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Fitting job id"},
                "fitting_dir": {"type": "string", "description": "Optional Fitting artifact directory"},
            },
        },
    },
    {
        "name": "apply_fitting_plan",
        "description": "Apply explicitly accepted Fitting changes. Does not accept all by default; accepted_change_ids is required.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id", "accepted_change_ids"],
            "properties": {
                "job_id": {"type": "string", "description": "Fitting job id"},
                "accepted_change_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicitly accepted change id list",
                },
                "fitting_dir": {"type": "string", "description": "Optional Fitting artifact directory"},
            },
        },
    },
    {
        "name": "toggle_preferences",
        "description": "Enable or disable Datailor preference injection. When disabled, decide and hook tools return empty instructions, but capture continues.",
        "inputSchema": {
            "type": "object",
            "required": ["enabled"],
            "properties": {
                "enabled": {"type": "boolean", "description": "true to enable, false to disable"},
            },
        },
    },
]


if __name__ == "__main__":
    raise SystemExit(main())
