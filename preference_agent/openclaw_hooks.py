from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .backends import HeuristicBackend, build_backend
from .engine import PreferenceEngine
from .hooks import PreferenceHookManager
from .paths import default_store_path
from .store import MarkdownPreferenceStore


def handle_openclaw_event(
    event: str,
    payload: dict[str, Any],
    *,
    agent: str = "openclaw",
    store_path: str | Path | None = None,
    backend: str = "heuristic",
    dry_run: bool = False,
    manager: PreferenceHookManager | None = None,
) -> dict[str, Any]:
    hook_manager = manager or PreferenceHookManager(_engine(Path(store_path) if store_path else default_store_path(), backend))
    event_payload, hook_context = _split_openclaw_payload(payload)
    session_id = _session_id(event_payload, hook_context)
    context = _context_from_payload(event_payload, hook_context)
    event_name = event.strip()

    if event_name == "session_start":
        datailor = hook_manager.on_session_start(
            agent=agent,
            session_id=session_id,
            task=_text(event_payload, "task", "prompt", "message") or "OpenClaw session start",
            context=context,
        )
        return _response(event_name, datailor)

    if event_name == "before_prompt_build":
        prompt = _user_message_text(event_payload)
        datailor = hook_manager.on_user_message(
            message=prompt,
            agent=agent,
            session_id=session_id,
            context=context,
        )
        response = _response(event_name, datailor)
        instruction = str(datailor.get("agent_instruction") or "").strip()
        if datailor.get("decision") == "apply" and instruction:
            response["prependSystemContext"] = instruction
        return response

    if event_name == "agent_turn_prepare":
        prompt = _user_message_text(event_payload)
        datailor = hook_manager.on_user_message(
            message=prompt,
            agent=agent,
            session_id=session_id,
            context={**context, "openclaw_event": event_name},
        )
        response = _response(event_name, datailor)
        instruction = str(datailor.get("agent_instruction") or "").strip()
        if datailor.get("decision") == "apply" and instruction:
            response["appendContext"] = instruction
        return response

    if event_name == "after_tool_call":
        tool_name = _tool_name(event_payload)
        result = _value_text(_first_value(event_payload, "result", "output", "tool_output", "toolResult", "error"))
        datailor = hook_manager.on_action_executed(
            action=f"openclaw.after_tool_call.{tool_name}",
            result=result,
            agent=agent,
            session_id=session_id,
            metadata=context,
            dry_run=dry_run,
        )
        return _response(event_name, datailor)

    if event_name in {"llm_output", "agent_end"}:
        user_message, assistant_response = _turn_texts(event_payload)
        datailor = hook_manager.on_turn_complete(
            user_message=user_message,
            assistant_response=assistant_response,
            agent=agent,
            session_id=session_id,
            context=context,
            dry_run=dry_run,
        )
        return _response(event_name, datailor)

    if event_name == "session_end":
        datailor = hook_manager.on_session_end(
            agent=agent,
            session_id=session_id,
            messages=[],
            dry_run=dry_run,
        )
        return _response(event_name, datailor)

    return {
        "ok": True,
        "event": event_name,
        "reason": "unsupported_openclaw_event",
        "datailor": {"ok": True, "hook": "openclaw_unhandled", "event": event_name},
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="datailor-openclaw-hook")
    parser.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", "openclaw"))
    parser.add_argument("--event", required=True)
    parser.add_argument("--store", default=os.getenv("PREFERENCE_STORE_PATH", ""))
    parser.add_argument("--backend", default=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = _read_stdin_json()
        result = handle_openclaw_event(
            args.event,
            payload,
            agent=args.agent,
            store_path=args.store or None,
            backend=args.backend,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"Datailor OpenClaw hook failed open: {exc}", file=sys.stderr)
        print(json.dumps({"ok": True, "failOpen": True, "event": args.event}, ensure_ascii=False))
        return 0


def _engine(store_path: Path, backend_name: str) -> PreferenceEngine:
    backend = build_backend(backend_name)
    fallback = HeuristicBackend() if backend.__class__.__name__ != "HeuristicBackend" else None
    return PreferenceEngine(MarkdownPreferenceStore(store_path), backend=backend, fallback_backend=fallback)


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {"payload": data}


def _split_openclaw_payload(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = raw.get("payload")
    context = raw.get("context") or raw.get("ctx")
    if not isinstance(context, dict) and isinstance(payload, dict):
        context = payload.get("context") or payload.get("ctx")
    event_payload = payload if isinstance(payload, dict) else raw
    hook_context = context if isinstance(context, dict) else {}
    return event_payload, hook_context


def _session_id(payload: dict[str, Any], context: dict[str, Any] | None = None) -> str:
    context = context or {}
    return str(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("session_key")
        or payload.get("sessionKey")
        or payload.get("conversation_id")
        or payload.get("conversationId")
        or context.get("session_id")
        or context.get("sessionId")
        or context.get("session_key")
        or context.get("sessionKey")
        or payload.get("id")
        or "openclaw"
    )


def _context_from_payload(payload: dict[str, Any], hook_context: dict[str, Any] | None = None) -> dict[str, Any]:
    hook_context = hook_context or {}
    context: dict[str, Any] = {"source": "openclaw"}
    key_map = {
        "workspace_id": "workspace_id",
        "workspaceId": "workspace_id",
        "channel_id": "channel_id",
        "channelId": "channel_id",
        "conversation_id": "conversation_id",
        "conversationId": "conversation_id",
        "message_id": "message_id",
        "messageId": "message_id",
        "user_id": "user_id",
        "userId": "user_id",
        "identity_id": "identity_id",
        "identityId": "identity_id",
        "sender_id": "sender_id",
        "senderId": "sender_id",
        "display_name": "display_name",
        "displayName": "display_name",
        "sessionKey": "session_key",
        "session_key": "session_key",
    }
    merged = {**payload, **hook_context}
    for key, normalized in key_map.items():
        value = merged.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value):
            context[normalized] = value
    return context


def _text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _value_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_value_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        part_type = str(value.get("type") or "").casefold()
        if part_type and part_type != "text" and "role" not in value:
            return ""
        text = _first_value(value, "text", "content", "message", "output", "result")
        if text is not None:
            return _value_text(text)
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value).strip()


def _user_message_text(payload: dict[str, Any]) -> str:
    prompt = _text(payload, "prompt", "userPrompt", "user_message", "message", "input")
    if prompt:
        return prompt
    user_message, _assistant_response = _turn_texts(payload)
    return user_message


def _tool_name(payload: dict[str, Any]) -> str:
    direct = _text(payload, "toolName", "tool_name", "name")
    if direct:
        return direct
    for key in ("toolCall", "tool_call", "tool"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested = _text(value, "name", "toolName", "tool_name")
            if nested:
                return nested
    return "tool"


def _turn_texts(payload: dict[str, Any]) -> tuple[str, str]:
    user_message = _text(payload, "prompt", "userPrompt", "user_message", "message", "input")
    assistant_response = _text(payload, "assistantResponse", "assistant_response", "output", "text", "content")
    assistant_texts = payload.get("assistantTexts")
    if not assistant_response and isinstance(assistant_texts, list):
        assistant_response = "\n".join(_value_text(item) for item in assistant_texts if _value_text(item)).strip()
    messages = payload.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("type") or item.get("kind") or "").casefold()
            content = _value_text(item.get("content") if "content" in item else item)
            if role == "user" and content:
                user_message = content
            elif role in {"assistant", "model", "ai"} and content:
                assistant_response = content
    return user_message, assistant_response


def _response(event: str, datailor: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "event": event, "datailor": datailor}


if __name__ == "__main__":
    raise SystemExit(main())
