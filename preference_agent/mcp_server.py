from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .backends import HeuristicBackend, build_backend
from .engine import PreferenceEngine
from .feedback import record_feedback
from .injection import prewarm_session, sync_injection_artifacts
from .store import MarkdownPreferenceStore
from .ui.server import open_preference_panel


def _engine() -> PreferenceEngine:
    store = Path(os.getenv("PREFERENCE_STORE_PATH") or Path(__file__).resolve().parents[1] / "data" / "个人偏好.md")
    backend = build_backend(os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
    fallback = HeuristicBackend() if backend.__class__.__name__ != "HeuristicBackend" else None
    return PreferenceEngine(MarkdownPreferenceStore(store), backend=backend, fallback_backend=fallback)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    engine = _engine()
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
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "personal-preference-agent-poc", "version": "0.1.0"},
            },
        )
    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "get_preference_decision":
            result = engine.decide(
                task=arguments.get("task") or arguments.get("question") or "",
                context=arguments.get("context") or {},
                agent=arguments.get("agent") or "agent",
            )
            return _tool_response(request_id, result)
        if name == "capture_preferences_from_session":
            result = engine.capture_path(
                arguments["source_path"],
                dry_run=bool(arguments.get("dry_run", False)),
            )
            return _tool_response(request_id, result.to_dict())
        if name == "prewarm_preferences":
            result = prewarm_session(
                store_path=engine.store.path,
                task=arguments.get("task") or "新 Session 初始化",
                agent=arguments.get("agent") or "agent",
                context=arguments.get("context") or {},
                output_dir=arguments.get("output_dir") or None,
            )
            return _tool_response(request_id, result.to_dict())
        if name == "sync_preference_injection":
            result = sync_injection_artifacts(
                store_path=engine.store.path,
                output_dir=arguments.get("output_dir") or None,
                target_files=arguments.get("target_files") or [],
            )
            return _tool_response(request_id, result.to_dict())
        if name == "report_preference_feedback":
            result = record_feedback(
                feedback_type=arguments.get("feedback_type") or arguments.get("type") or "correction",
                user_feedback=arguments.get("user_feedback") or "",
                preference_id=arguments.get("preference_id") or "",
                preference_text=arguments.get("preference_text") or arguments.get("preference") or "",
                agent=arguments.get("agent") or "agent",
                task=arguments.get("task") or "",
                source="mcp",
                context=arguments.get("context") or {},
            )
            return _tool_response(request_id, result)
        if name == "open_preference_panel":
            requested_port = arguments.get("port")
            result = open_preference_panel(
                store_path=engine.store.path,
                host=arguments.get("host") or None,
                port=int(requested_port) if requested_port is not None else int(os.getenv("PREFERENCE_UI_PORT", "8080")),
                open_browser=bool(arguments.get("open_browser", False)),
            )
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


TOOLS = [
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
        "description": "从 session 文件或目录中捕获用户偏好，并增量更新 Markdown 偏好库。",
        "inputSchema": {
            "type": "object",
            "required": ["source_path"],
            "properties": {
                "source_path": {"type": "string", "description": "session 文件或目录路径"},
                "dry_run": {"type": "boolean", "description": "只分析不写入"},
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
        "name": "open_preference_panel",
        "description": "启动本地 Personal Preference Manifesto 面板，并返回 localhost 链接，供用户查看偏好、反馈和待处理项。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "默认 127.0.0.1，仅本机访问"},
                "port": {"type": "integer", "description": "默认 8080；如端口被占用会自动后移"},
                "open_browser": {"type": "boolean", "description": "是否尝试自动打开浏览器"},
            },
        },
    },
]


if __name__ == "__main__":
    raise SystemExit(main())
