from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid5, NAMESPACE_URL

from .models import Session, SessionMessage


SUPPORTED_EXTENSIONS = {".json", ".jsonl", ".md", ".txt"}
ROLE_ALIASES = {
    "user": "user",
    "human": "user",
    "me": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "kimi": "assistant",
    "codex": "assistant",
    "openclaw": "assistant",
    "system": "system",
    "tool": "system",
    "model": "assistant",
}


def load_sessions(path: str | Path) -> list[Session]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(str(source))
    files = [source] if source.is_file() else sorted(
        item for item in source.rglob("*") if item.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    sessions: list[Session] = []
    for file_path in files:
        sessions.extend(_load_file(file_path))
    return [session for session in sessions if session.messages]


def _load_file(path: Path) -> list[Session]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        sessions = _sessions_from_json(data, str(path))
        return sessions or [_plain_session(path)]
    if suffix == ".jsonl":
        sessions: list[Session] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            sessions.extend(_sessions_from_json(data, f"{path}#{line_no}"))
        return sessions
    return [_markdown_or_text_session(path)]


def _sessions_from_json(data: Any, source: str) -> list[Session]:
    if isinstance(data, list):
        if _looks_like_message_list(data):
            return [_session_from_messages(source, data)]
        sessions: list[Session] = []
        for index, item in enumerate(data):
            child_source = f"{source}[{index}]"
            sessions.extend(_sessions_from_json(item, child_source))
        return sessions
    if isinstance(data, dict):
        if data.get("type") == "message" and isinstance(data.get("message"), dict):
            nested = dict(data["message"])
            for key in ("session_id", "sessionId", "conversation_id", "chat_id", "id", "created_at", "time", "timestamp"):
                if key in data and key not in nested:
                    nested[key] = data[key]
            return [_session_from_messages(source, [nested], data)]
        # 优先探测内部消息列表，避免 content + messages 的 wrapper 只返回顶层 content
        for key in ("messages", "conversation", "conversations", "chat", "items"):
            value = data.get(key)
            if isinstance(value, list) and _looks_like_message_list(value):
                return [_session_from_messages(source, value, data)]
        if _message_content(data):
            return [_session_from_messages(source, [data], data)]
        found = _find_message_lists(data)
        return [_session_from_messages(f"{source}:{index}", messages) for index, messages in enumerate(found)]
    return []


def _looks_like_message_list(value: list[Any]) -> bool:
    if not value:
        return False
    hits = 0
    for item in value[:8]:
        if isinstance(item, dict) and _message_content(item):
            hits += 1
    return hits >= max(1, min(2, len(value)))


def _find_message_lists(data: Any) -> list[list[dict[str, Any]]]:
    results: list[list[dict[str, Any]]] = []
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and _looks_like_message_list(value):
                results.append(value)
            else:
                results.extend(_find_message_lists(value))
    elif isinstance(data, list):
        for item in data:
            results.extend(_find_message_lists(item))
    return results


def _session_from_messages(
    source: str, messages: list[dict[str, Any]], metadata: dict[str, Any] | None = None
) -> Session:
    normalized: list[SessionMessage] = []
    for item in messages:
        content = _message_content(item)
        if not content:
            continue
        role = _normalize_role(str(item.get("role") or item.get("sender") or item.get("author") or item.get("type") or item.get("kind") or item.get("source") or "user"))
        created_at = str(item.get("created_at") or item.get("time") or item.get("timestamp") or "")
        normalized.append(SessionMessage(role=role, content=content, created_at=created_at))
    session_id = str(
        (metadata or {}).get("session_id")
        or (metadata or {}).get("sessionId")
        or (metadata or {}).get("conversation_id")
        or (metadata or {}).get("chat_id")
        or (metadata or {}).get("id")
        or uuid5(NAMESPACE_URL, source)
    )
    created_at = str((metadata or {}).get("created_at") or (normalized[0].created_at if normalized else ""))
    return Session(source=source, session_id=session_id, messages=normalized, created_at=created_at)


def _message_content(item: dict[str, Any]) -> str:
    for key in ("content", "text", "message", "display", "value", "markdown"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            text = _content_parts_text(value)
            if text.strip():
                return text.strip()
    return ""


def _content_parts_text(parts: list[Any]) -> str:
    texts: list[str] = []
    for part in parts:
        if isinstance(part, str) and part.strip():
            texts.append(part.strip())
            continue
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").casefold()
        if part_type and part_type != "text":
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n".join(texts)


def _markdown_or_text_session(path: Path) -> Session:
    text = path.read_text(encoding="utf-8-sig")
    marker_re = re.compile(
        r"^(User|Human|Me|Assistant|AI|Kimi|Codex|OpenClaw|System)\s*[:]\s*(.*)$",
        re.I,
    )
    messages: list[SessionMessage] = []
    current_role: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        match = marker_re.match(line.strip())
        if match:
            if current_role and current_lines:
                messages.append(SessionMessage(role=current_role, content="\n".join(current_lines).strip()))
            current_role = _normalize_role(match.group(1))
            current_lines = [match.group(2).strip()] if match.group(2).strip() else []
        elif current_role:
            current_lines.append(line)
    if current_role and current_lines:
        messages.append(SessionMessage(role=current_role, content="\n".join(current_lines).strip()))
    if not messages:
        messages = [SessionMessage(role="user", content=text.strip())]
    return Session(source=str(path), session_id=str(uuid5(NAMESPACE_URL, str(path))), messages=messages)


def _plain_session(path: Path) -> Session:
    text = path.read_text(encoding="utf-8-sig")
    return Session(
        source=str(path),
        session_id=str(uuid5(NAMESPACE_URL, str(path))),
        messages=[SessionMessage(role="user", content=text.strip())],
    )


def _normalize_role(role: str) -> str:
    return ROLE_ALIASES.get(role.strip().casefold(), ROLE_ALIASES.get(role.strip(), "user"))
