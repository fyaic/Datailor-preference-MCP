from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .engine import PreferenceEngine
from .injection_log import log_injection_event
from .injection import prewarm_session, sync_injection_artifacts
from .models import Evidence, PreferenceRecord, Session, SessionMessage, now_iso
from .paths import default_hooks_dir as default_hooks_dir_path
from .quality import should_recall_user_text


TURN_SIGNAL_MARKERS = (
    "以后",
    "默认",
    "记住",
    "记下",
    "下次",
    "以后默认",
    "从现在开始",
    "不对",
    "错了",
    "不是这个意思",
    "不要",
    "别",
    "我希望",
    "我偏好",
    "你应该",
)
ASSISTANT_SIGNAL_MARKERS = ("确认", "要不要", "是否", "需不需要", "可以吗")
DEFAULT_MAX_TURNS = 5
DEFAULT_MIN_SESSION_MESSAGES = 3


@dataclass
class TurnEntry:
    user_message: str
    assistant_response: str
    agent: str
    session_id: str
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnEntry":
        return cls(
            user_message=str(data.get("user_message", "")),
            assistant_response=str(data.get("assistant_response", "")),
            agent=str(data.get("agent", "agent")),
            session_id=str(data.get("session_id", "")),
            context=data.get("context") if isinstance(data.get("context"), dict) else {},
            created_at=str(data.get("created_at") or now_iso()),
        )


class PreferenceHookManager:
    def __init__(
        self,
        engine: PreferenceEngine,
        hooks_dir: str | Path | None = None,
        max_turns: int | None = None,
    ) -> None:
        self.engine = engine
        self.hooks_dir = Path(hooks_dir) if hooks_dir else default_hooks_dir(engine.store.path)
        self.max_turns = max_turns or _env_int("PREFERENCE_TURN_BUFFER_MAX_TURNS", DEFAULT_MAX_TURNS)

    def on_session_start(
        self,
        agent: str,
        session_id: str,
        task: str = "",
        context: dict[str, Any] | None = None,
        output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        context = {**(context or {}), "session_id": session_id, "hook": "session_start"}
        task = task or "新 Session 初始化"
        decision = self.engine.decide(task=task, context=context, agent=agent, log_event=False)
        prewarm = prewarm_session(
            store_path=self.engine.store.path,
            task=task,
            agent=agent,
            context=context,
            output_dir=output_dir,
        )
        log_injection_event(
            store_path=self.engine.store.path,
            hook="session_start",
            agent=agent,
            session_id=session_id,
            task=task,
            context=context,
            decision=decision,
            source="hook",
            reason="hook_session_start_decision",
            extra={"prewarm": prewarm.to_dict()},
        )
        return {
            "ok": True,
            "hook": "session_start",
            "agent": agent,
            "session_id": session_id,
            "decision": decision,
            "prewarm": prewarm.to_dict(),
        }

    def on_user_message(
        self,
        message: str,
        agent: str,
        session_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = {**(context or {}), "session_id": session_id, "hook": "user_message"}
        decision = self.engine.decide(task=message, context=context, agent=agent, log_event=False)
        log_injection_event(
            store_path=self.engine.store.path,
            hook="user_message",
            agent=agent,
            session_id=session_id,
            task=message,
            context=context,
            decision=decision,
            source="hook",
            reason="hook_user_message_decision",
            extra={"preference_signal": _should_buffer_turn(message, "")},
        )
        return {
            "ok": True,
            "hook": "user_message",
            "agent": agent,
            "session_id": session_id,
            "preference_signal": _should_buffer_turn(message, ""),
            "decision": decision,
        }

    def on_turn_complete(
        self,
        user_message: str,
        assistant_response: str,
        agent: str,
        session_id: str,
        context: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not _should_buffer_turn(user_message, assistant_response):
            log_injection_event(
                store_path=self.engine.store.path,
                hook="turn_complete",
                agent=agent,
                session_id=session_id,
                task=user_message,
                context=context or {},
                source="hook",
                reason="no_preference_signal",
            )
            return {
                "ok": True,
                "hook": "turn_complete",
                "agent": agent,
                "session_id": session_id,
                "buffered": False,
                "preference_signal": False,
                "reason": "no_preference_signal",
            }
        entry = TurnEntry(
            user_message=user_message,
            assistant_response=assistant_response,
            agent=agent,
            session_id=session_id,
            context=context or {},
        )
        turns = self._load_turns(agent, session_id)
        turns.append(entry)
        self._save_turns(agent, session_id, turns)
        response: dict[str, Any] = {
            "ok": True,
            "hook": "turn_complete",
            "agent": agent,
            "session_id": session_id,
            "buffered": True,
            "preference_signal": True,
            "buffer_size": len(turns),
            "max_turns": self.max_turns,
        }
        if len(turns) >= self.max_turns:
            response["flush"] = self.flush_turn_buffer(agent=agent, session_id=session_id, dry_run=dry_run)
        log_injection_event(
            store_path=self.engine.store.path,
            hook="turn_complete",
            agent=agent,
            session_id=session_id,
            task=user_message,
            context=context or {},
            source="hook",
            reason="turn_buffered",
            extra={"buffer_size": len(turns), "max_turns": self.max_turns},
        )
        return response

    def on_action_executed(
        self,
        action: str,
        result: str = "",
        agent: str = "agent",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        signal = _behavior_signal(action, result, metadata or {})
        if not signal:
            log_injection_event(
                store_path=self.engine.store.path,
                hook="action_executed",
                agent=agent,
                session_id=session_id,
                task=action,
                context=metadata or {},
                source="hook",
                reason="unsupported_action_signal",
            )
            return {
                "ok": True,
                "hook": "action_executed",
                "agent": agent,
                "session_id": session_id,
                "captured": False,
                "reason": "unsupported_action_signal",
            }
        record = PreferenceRecord(
            title=signal["title"],
            summary=signal["preference"],
            applies_to=signal["applies_to"],
            preference=signal["preference"],
            triggers=[action],
            confidence="low",
            status="needs_review",
            evidence=[
                Evidence(
                    source=f"behavior:{agent}:{session_id}:{action}",
                    quote=result or json.dumps(metadata or {}, ensure_ascii=False),
                    role="system",
                    source_type="action_signal",
                )
            ],
        )
        capture = self.engine.capture_records(
            [record],
            source=f"behavior:{agent}:{session_id}:{action}",
            dry_run=dry_run,
        )
        if not dry_run:
            sync_injection_artifacts(self.engine.store.path)
        log_injection_event(
            store_path=self.engine.store.path,
            hook="action_executed",
            agent=agent,
            session_id=session_id,
            task=action,
            context=metadata or {},
            source="hook",
            reason="captured_action_signal",
            extra={"capture": capture.to_dict()},
        )
        return {
            "ok": True,
            "hook": "action_executed",
            "agent": agent,
            "session_id": session_id,
            "captured": True,
            "capture": capture.to_dict(),
        }

    def on_session_end(
        self,
        agent: str,
        session_id: str,
        messages: list[dict[str, Any]] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        flush = self.flush_turn_buffer(agent=agent, session_id=session_id, dry_run=dry_run)
        full_capture = None
        normalized = _messages_from_payload(messages or [])
        if len(normalized) >= _env_int("PREFERENCE_SESSION_END_MIN_MESSAGES", DEFAULT_MIN_SESSION_MESSAGES):
            session = Session(
                source=f"session-end:{agent}:{session_id}",
                session_id=session_id,
                messages=normalized,
            )
            full_capture = self.engine.capture_session(session, dry_run=dry_run).to_dict()
        sync = None
        if not dry_run:
            sync = sync_injection_artifacts(self.engine.store.path).to_dict()
        log_injection_event(
            store_path=self.engine.store.path,
            hook="session_end",
            agent=agent,
            session_id=session_id,
            source="hook",
            reason="session_end",
            extra={
                "buffer_flush": flush,
                "full_capture": full_capture,
                "sync": sync,
            },
        )
        return {
            "ok": True,
            "hook": "session_end",
            "agent": agent,
            "session_id": session_id,
            "buffer_flush": flush,
            "full_capture": full_capture,
            "sync": sync,
        }

    def flush_turn_buffer(self, agent: str, session_id: str, dry_run: bool = False) -> dict[str, Any]:
        turns = self._load_turns(agent, session_id)
        if not turns:
            log_injection_event(
                store_path=self.engine.store.path,
                hook="turn_buffer_flush",
                agent=agent,
                session_id=session_id,
                source="hook",
                reason="empty_buffer",
            )
            return {
                "ok": True,
                "hook": "turn_buffer_flush",
                "agent": agent,
                "session_id": session_id,
                "flushed": False,
                "reason": "empty_buffer",
            }
        session = Session(
            source=f"turn-buffer:{agent}:{session_id}",
            session_id=session_id,
            messages=_messages_from_turns(turns),
        )
        capture = self.engine.capture_session(session, dry_run=dry_run)
        if not dry_run:
            self._buffer_path(agent, session_id).unlink(missing_ok=True)
            sync = sync_injection_artifacts(self.engine.store.path).to_dict()
        else:
            sync = None
        log_injection_event(
            store_path=self.engine.store.path,
            hook="turn_buffer_flush",
            agent=agent,
            session_id=session_id,
            source="hook",
            reason="flushed",
            extra={"turns": len(turns), "capture": capture.to_dict(), "sync": sync},
        )
        return {
            "ok": True,
            "hook": "turn_buffer_flush",
            "agent": agent,
            "session_id": session_id,
            "flushed": True,
            "turns": len(turns),
            "capture": capture.to_dict(),
            "sync": sync,
        }

    def _load_turns(self, agent: str, session_id: str) -> list[TurnEntry]:
        path = self._buffer_path(agent, session_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        items = data.get("turns", []) if isinstance(data, dict) else []
        return [TurnEntry.from_dict(item) for item in items if isinstance(item, dict)]

    def _save_turns(self, agent: str, session_id: str, turns: list[TurnEntry]) -> None:
        path = self._buffer_path(agent, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "agent": agent,
                    "session_id": session_id,
                    "updated_at": now_iso(),
                    "turns": [turn.to_dict() for turn in turns],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _buffer_path(self, agent: str, session_id: str) -> Path:
        key = hashlib.sha256(f"{agent}:{session_id}".encode("utf-8")).hexdigest()[:16]
        return self.hooks_dir / "turn-buffers" / f"{key}.json"


def default_hooks_dir(store_path: str | Path) -> Path:
    return default_hooks_dir_path()


def _should_buffer_turn(user_message: str, assistant_response: str) -> bool:
    user = str(user_message or "")
    assistant = str(assistant_response or "")
    return (
        should_recall_user_text(user)
        or _has_any(user, TURN_SIGNAL_MARKERS)
        or _has_any(assistant, ASSISTANT_SIGNAL_MARKERS)
    )


def _messages_from_turns(turns: list[TurnEntry]) -> list[SessionMessage]:
    messages: list[SessionMessage] = []
    for turn in turns:
        if turn.user_message.strip():
            messages.append(SessionMessage(role="user", content=turn.user_message, created_at=turn.created_at))
        if turn.assistant_response.strip():
            messages.append(SessionMessage(role="assistant", content=turn.assistant_response, created_at=turn.created_at))
    return messages


def _messages_from_payload(items: list[dict[str, Any]]) -> list[SessionMessage]:
    messages: list[SessionMessage] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("text") or item.get("message") or "").strip()
        if not content:
            continue
        role = str(item.get("role") or "user").strip().lower()
        if role not in {"user", "assistant", "system"}:
            role = "user"
        messages.append(SessionMessage(role=role, content=content, created_at=str(item.get("created_at") or "")))
    return messages


def _behavior_signal(action: str, result: str, metadata: dict[str, Any]) -> dict[str, str] | None:
    text = " ".join([action, result, json.dumps(metadata, ensure_ascii=False)]).casefold()
    if _has_any(text, ("test", "pytest", "验证", "测试")) and _has_any(text, ("commit", "done", "完成", "提交", "汇报")):
        return {
            "title": "代码完成前验证",
            "applies_to": "当 agent 修改代码、准备提交或汇报完成时",
            "preference": "当 agent 修改代码、准备提交或汇报完成时，优先运行相关测试或验证。",
        }
    if _has_any(text, ("format", "formatter", "格式化")):
        return {
            "title": "代码格式化偏好",
            "applies_to": "当 agent 修改代码后",
            "preference": "当 agent 修改代码后，保持代码格式化并遵循项目现有格式。",
        }
    return None


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
