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
from .paths import default_store_path
from .preference_actions import apply_preference_feedback
from .store import MarkdownPreferenceStore
from .ui.server import open_preference_panel


_AUTO_COLD_START_DONE: set[str] = set()


def _engine(store_path: str | Path | None = None, backend_name: str | None = None) -> PreferenceEngine:
    store = Path(store_path) if store_path else default_store_path()
    backend = build_backend(backend_name or os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
    fallback = HeuristicBackend() if backend.__class__.__name__ != "HeuristicBackend" else None
    return PreferenceEngine(MarkdownPreferenceStore(store), backend=backend, fallback_backend=fallback)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="datailor-mcp")
    parser.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", ""))
    parser.add_argument("--store", default=os.getenv("PREFERENCE_STORE_PATH", ""))
    parser.add_argument("--backend", default=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
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
                "serverInfo": {"name": "datailor-preference-mcp", "version": "0.1.0"},
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
            return _tool_response(request_id, result)
        if name == "get_onboarding_status":
            result = get_onboarding_status(
                store_path=engine.store.path,
                agent_hint=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                backend=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"),
            )
            return _tool_response(request_id, result.to_dict())
        if name == "start_cold_start_capture":
            result = run_onboarding(
                store_path=engine.store.path,
                agent_hint=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                backend=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"),
                mode=str(arguments.get("mode") or os.getenv("PREFERENCE_AUTO_DISCOVERY_MODE", "recall-extract")),
                dry_run=bool(arguments.get("dry_run", False)),
                capture=True,
                max_files=int(arguments.get("max_files") or 0),
                max_minutes=float(arguments.get("max_minutes") or 0.0),
                state_file=arguments.get("state_file") or None,
                open_ui=bool(arguments.get("open_ui", False)),
                host=str(arguments.get("host") or "127.0.0.1"),
                port=int(arguments.get("port") or 8080),
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
                task=arguments.get("task") or "新 Session 初始化",
                agent=agent,
                context=arguments.get("context") or {},
                output_dir=arguments.get("output_dir") or None,
            )
            payload = result.to_dict()
            if auto_discovery:
                payload["auto_discovery"] = auto_discovery
            return _tool_response(request_id, payload)
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
            return _tool_response(request_id, result)
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
            return _tool_response(request_id, result)
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
            return _tool_response(request_id, result)
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
            return _tool_response(request_id, result)
        if name == "hook_session_end":
            manager = PreferenceHookManager(engine)
            messages = arguments.get("messages") if isinstance(arguments.get("messages"), list) else []
            result = manager.on_session_end(
                agent=str(arguments.get("agent") or os.getenv("PREFERENCE_CALLER_AGENT") or "agent"),
                session_id=str(arguments.get("session_id") or "default"),
                messages=messages,
                dry_run=bool(arguments.get("dry_run", False)),
            )
            return _tool_response(request_id, result)
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
        "description": "检查 Datailor 是否已完成首次配置：MCP 是否可用、偏好库是否为空、是否发现本地 agent 历史，以及下一步命令。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "调用方 agent 名称，例如 codex/kimi/claude"},
            },
        },
    },
    {
        "name": "start_cold_start_capture",
        "description": "启动客户端无关的冷启动捕获：初始化偏好库、自动发现 Claude/Codex/Kimi 历史并扫描。等价于 CLI `datailor onboard` 的捕获部分。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "调用方 agent 名称，用于发现源排序"},
                "mode": {"type": "string", "description": "capture mode，例如 recall-extract 或 semantic-extract"},
                "max_files": {"type": "integer", "description": "可选扫描文件数上限；默认不限制"},
                "max_minutes": {"type": "number", "description": "单个源的可选运行时长上限；默认不限制"},
                "dry_run": {"type": "boolean", "description": "只演练，不写入偏好库"},
                "open_ui": {"type": "boolean", "description": "完成后尝试打开本地 Manifesto UI"},
                "host": {"type": "string", "description": "UI host，默认 127.0.0.1"},
                "port": {"type": "integer", "description": "UI port，默认 8080"},
                "state_file": {"type": "string", "description": "可选增量扫描状态文件"},
            },
        },
    },
    {
        "name": "get_preference_decision",
        "description": "在 agent 回复或做事前读取用户偏好，并返回应应用的偏好指令。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "当前任务、计划动作或用户问题"},
                "question": {"type": "string", "description": "兼容字段：agent 正准备询问用户的问题"},
                "agent": {"type": "string", "description": "调用方 agent 名称，例如 codex/openclaw"},
                "context": {"type": "object", "description": "项目、语言、风险、文件等上下文"},
            },
        },
    },
    {
        "name": "capture_preferences_from_session",
        "description": "从 session 文件或目录中捕获用户偏好，并增量更新 Markdown 偏好库。source_path 省略时会自动发现 Claude/Codex/Kimi 历史并冷启动扫描。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "session 文件或目录路径；省略则自动发现已安装 agent 的历史"},
                "agent": {"type": "string", "description": "调用方 agent 名称，用于冷启动扫描排序"},
                "mode": {"type": "string", "description": "自动发现扫描时的 capture mode，例如 recall-extract"},
                "max_files": {"type": "integer", "description": "自动发现扫描的可选上限；默认不限制"},
                "max_minutes": {"type": "number", "description": "单个源的可选运行时长上限；默认不限制"},
                "dry_run": {"type": "boolean", "description": "只分析不写入"},
            },
        },
    },
    {
        "name": "discover_agents",
        "description": "检测本机已安装的 Claude/Codex/Kimi/Cursor/Trae，并返回可自动采集的历史文件路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "调用方 agent 名称，用于排序提示"},
            },
        },
    },
    {
        "name": "prewarm_preferences",
        "description": "在 session 开始时预热偏好，生成 session 级偏好缓存和可执行指令。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "本 session 的任务或初始化说明"},
                "agent": {"type": "string", "description": "调用方 agent 名称"},
                "context": {"type": "object", "description": "项目、路径、风险、任务类型等上下文"},
                "output_dir": {"type": "string", "description": "可选：session cache 输出目录"},
            },
        },
    },
    {
        "name": "hook_session_start",
        "description": "H1：session 开始 hook。自动执行偏好决策和 session prewarm，返回本 session 应用的偏好指令与缓存文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "调用方 agent 名称"},
                "session_id": {"type": "string", "description": "当前 session 标识"},
                "task": {"type": "string", "description": "本 session 的任务或初始化说明"},
                "context": {"type": "object", "description": "项目、路径、风险、任务类型等上下文"},
                "output_dir": {"type": "string", "description": "可选：session cache 输出目录"},
            },
        },
    },
    {
        "name": "hook_user_message",
        "description": "H2：用户消息 hook。每条用户消息到达时检索相关偏好，返回本轮回复前应遵守的偏好指令。",
        "inputSchema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string", "description": "用户当前消息"},
                "agent": {"type": "string", "description": "调用方 agent 名称"},
                "session_id": {"type": "string", "description": "当前 session 标识"},
                "context": {"type": "object", "description": "当前上下文"},
            },
        },
    },
    {
        "name": "hook_turn_complete",
        "description": "H3：turn 完成 hook。条件过滤后写入 turn buffer，满 N 个 turns 后批量提取并走 merge/conflict 入库。",
        "inputSchema": {
            "type": "object",
            "required": ["user_message", "assistant_response"],
            "properties": {
                "user_message": {"type": "string", "description": "本 turn 的用户消息"},
                "assistant_response": {"type": "string", "description": "本 turn 的 agent 回复"},
                "agent": {"type": "string", "description": "调用方 agent 名称"},
                "session_id": {"type": "string", "description": "当前 session 标识"},
                "context": {"type": "object", "description": "当前上下文"},
                "dry_run": {"type": "boolean", "description": "只演练，不写入偏好库"},
            },
        },
    },
    {
        "name": "hook_action_executed",
        "description": "H4：action hook。从代码验证、格式化等行为信号生成低置信度待审偏好。",
        "inputSchema": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "description": "已执行动作，例如 run_tests_before_done"},
                "result": {"type": "string", "description": "动作结果或摘要"},
                "agent": {"type": "string", "description": "调用方 agent 名称"},
                "session_id": {"type": "string", "description": "当前 session 标识"},
                "metadata": {"type": "object", "description": "动作元数据"},
                "dry_run": {"type": "boolean", "description": "只演练，不写入偏好库"},
            },
        },
    },
    {
        "name": "hook_session_end",
        "description": "H5：session 结束 hook。强制 flush turn buffer，可选捕获完整 message history，并同步注入产物。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "调用方 agent 名称"},
                "session_id": {"type": "string", "description": "当前 session 标识"},
                "messages": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "可选完整 session 消息列表，每项含 role/content",
                },
                "dry_run": {"type": "boolean", "description": "只演练，不写入偏好库"},
            },
        },
    },
    {
        "name": "sync_preference_injection",
        "description": "生成静态规则、快照和本地兜底文件；可选同步到 AGENTS.md/.cursorrules 等目标文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "description": "注入产物输出目录"},
                "target_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要写入 managed block 的目标规则文件",
                },
            },
        },
    },
    {
        "name": "report_preference_feedback",
        "description": "当用户纠正、确认、拒绝或使用某个偏好时记录反馈，用于后续偏好进化。",
        "inputSchema": {
            "type": "object",
            "required": ["feedback_type", "user_feedback"],
            "properties": {
                "feedback_type": {
                    "type": "string",
                    "enum": ["correction", "confirmation", "usage", "rejection"],
                    "description": "反馈类型",
                },
                "user_feedback": {"type": "string", "description": "用户原始反馈或纠正文本"},
                "preference_id": {"type": "string", "description": "相关偏好 ID，如果知道"},
                "preference_text": {"type": "string", "description": "相关偏好文本，如果不知道 ID"},
                "agent": {"type": "string", "description": "调用方 agent"},
                "task": {"type": "string", "description": "发生反馈时的任务"},
                "context": {"type": "object", "description": "反馈上下文"},
            },
        },
    },
    {
        "name": "resolve_preference_conflict",
        "description": "当 decide 检测到命中偏好互相冲突并询问用户后，用用户选择更新偏好状态并标记该冲突已解决。",
        "inputSchema": {
            "type": "object",
            "required": ["conflict_key", "resolution"],
            "properties": {
                "conflict_key": {"type": "string", "description": "decide 返回的 conflict.key"},
                "resolution": {
                    "type": "string",
                    "enum": ["prefer", "select", "neither", "exception", "custom", "correct"],
                    "description": "prefer/select=选择一条；neither/exception=两个都不适用；custom/correct=用户给出新规则",
                },
                "selected_preference_id": {"type": "string", "description": "用户选择保留的偏好 ID"},
                "conflict_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "冲突偏好 ID 列表；可省略，系统会从 conflict_key 查找",
                },
                "user_feedback": {"type": "string", "description": "用户回答或自定义规则"},
                "context": {"type": "object", "description": "本次冲突适用或例外的上下文"},
            },
        },
    },
    {
        "name": "open_preference_panel",
        "description": "启动本地 Personal Preference Manifesto 面板，并返回 localhost 链接，供用户查看偏好、反馈和待处理项。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "调用方 agent 名称，用于空偏好库自动冷启动排序"},
                "host": {"type": "string", "description": "默认 127.0.0.1，仅本机访问"},
                "port": {"type": "integer", "description": "默认 8080；如端口被占用会自动后移"},
                "open_browser": {"type": "boolean", "description": "是否尝试自动打开浏览器"},
            },
        },
    },
]


if __name__ == "__main__":
    raise SystemExit(main())
