from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import webbrowser
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..feedback import feedback_report, record_feedback
from ..models import PreferenceRecord, now_iso
from ..privacy import redact_sensitive
from ..store import MarkdownPreferenceStore


STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_SETTINGS = {"mode": "auto", "theme": "light"}
VALID_MODES = {"auto", "autonomous", "curated"}
VALID_THEMES = {"light", "dark"}

_BACKGROUND_LOCK = threading.Lock()
_BACKGROUND_SERVER: ThreadingHTTPServer | None = None
_BACKGROUND_THREAD: threading.Thread | None = None
_BACKGROUND_INFO: "UiServerInfo | None" = None


@dataclass
class UiServerInfo:
    url: str
    host: str
    port: int
    store: str
    settings_file: str
    event_log: str
    feedback_log: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UiConfig:
    store_path: Path
    feedback_log: Path
    settings_path: Path
    event_log: Path


def default_store_path() -> Path:
    return Path(os.getenv("PREFERENCE_STORE_PATH") or Path(__file__).resolve().parents[2] / "data" / "\u4e2a\u4eba\u504f\u597d.md")


def default_ui_dir(store_path: str | Path | None = None) -> Path:
    configured = os.getenv("PREFERENCE_UI_DIR")
    if configured:
        return Path(configured)
    base = Path(store_path) if store_path else default_store_path()
    return base.parent / ".ui"


def default_feedback_log(store_path: str | Path | None = None) -> Path:
    configured = os.getenv("PREFERENCE_FEEDBACK_LOG")
    if configured:
        return Path(configured)
    base = Path(store_path) if store_path else default_store_path()
    return base.parent / ".feedback" / "feedback-log.jsonl"


def build_manifesto(
    store_path: str | Path | None = None,
    feedback_log: str | Path | None = None,
    settings_path: str | Path | None = None,
    event_log: str | Path | None = None,
) -> dict[str, Any]:
    store_path = Path(store_path) if store_path else default_store_path()
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    ui_dir = default_ui_dir(store_path)
    settings_path = Path(settings_path) if settings_path else ui_dir / "settings.json"
    event_log = Path(event_log) if event_log else ui_dir / "events.jsonl"
    feedback_log = Path(feedback_log) if feedback_log else default_feedback_log(store_path)

    records = store.load()
    settings = _read_settings(settings_path)
    events = _read_jsonl(event_log)
    feedback = feedback_report(feedback_log)
    feedback_by_preference = {
        str(item.get("preference")): item
        for item in feedback.get("by_preference", [])
        if item.get("preference")
    }
    preferences = [_record_view(record, feedback_by_preference) for record in records]
    active = [item for item in preferences if item["status"] == "active"]
    pending = [item for item in preferences if item["status"] != "active"]
    conflicts = [item for item in preferences if item["attention"]]
    mode = _effective_mode(settings, events)

    return {
        "generated_at": now_iso(),
        "store": str(store_path),
        "mode": mode,
        "configured_mode": settings["mode"],
        "theme": settings["theme"],
        "summary": {
            "active": len(active),
            "pending": len(pending),
            "conflicts": len(conflicts),
            "feedback_total": int(feedback.get("total", 0)),
            "last_updated": _latest_update(preferences),
        },
        "preferences": preferences,
        "recent": sorted(preferences, key=lambda item: item["updated_at"], reverse=True)[:5],
        "attention": conflicts[:10],
        "feedback": feedback,
        "events": {
            "panel_opens": sum(1 for item in events if item.get("event_type") == "panel_open"),
            "review_clicks": sum(1 for item in events if item.get("event_type") == "review_click"),
        },
    }


def open_preference_panel(
    store_path: str | Path | None = None,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = False,
) -> dict[str, Any]:
    info = ensure_ui_server(store_path=store_path, host=host, port=port)
    if open_browser:
        webbrowser.open(info.url)
    return {"ok": True, **info.to_dict()}


def ensure_ui_server(
    store_path: str | Path | None = None,
    host: str | None = None,
    port: int | None = None,
    feedback_log: str | Path | None = None,
    ui_dir: str | Path | None = None,
) -> UiServerInfo:
    global _BACKGROUND_INFO, _BACKGROUND_SERVER, _BACKGROUND_THREAD
    with _BACKGROUND_LOCK:
        if _BACKGROUND_SERVER is not None and _BACKGROUND_INFO is not None:
            return _BACKGROUND_INFO
        server, info = _create_server(
            store_path=store_path,
            host=host,
            port=port,
            feedback_log=feedback_log,
            ui_dir=ui_dir,
        )
        thread = threading.Thread(target=server.serve_forever, name="preference-ui", daemon=True)
        thread.start()
        _BACKGROUND_SERVER = server
        _BACKGROUND_THREAD = thread
        _BACKGROUND_INFO = info
        return info


def shutdown_ui_server() -> None:
    global _BACKGROUND_INFO, _BACKGROUND_SERVER, _BACKGROUND_THREAD
    with _BACKGROUND_LOCK:
        server = _BACKGROUND_SERVER
        thread = _BACKGROUND_THREAD
        _BACKGROUND_SERVER = None
        _BACKGROUND_THREAD = None
        _BACKGROUND_INFO = None
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None:
        thread.join(timeout=2)


def run_ui_server(
    store_path: str | Path | None = None,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = True,
) -> int:
    server, info = _create_server(store_path=store_path, host=host, port=port)
    print(json.dumps({"ok": True, **info.to_dict()}, ensure_ascii=False, indent=2), flush=True)
    if open_browser:
        webbrowser.open(info.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="preference-agent-ui")
    parser.add_argument("--store", default=str(default_store_path()))
    parser.add_argument("--host", default=os.getenv("PREFERENCE_UI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PREFERENCE_UI_PORT", "8080")))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    return run_ui_server(args.store, args.host, args.port, open_browser=not args.no_open)


def _create_server(
    store_path: str | Path | None = None,
    host: str | None = None,
    port: int | None = None,
    feedback_log: str | Path | None = None,
    ui_dir: str | Path | None = None,
) -> tuple[ThreadingHTTPServer, UiServerInfo]:
    store_path = Path(store_path) if store_path else default_store_path()
    ui_dir = Path(ui_dir) if ui_dir else default_ui_dir(store_path)
    ui_dir.mkdir(parents=True, exist_ok=True)
    config = UiConfig(
        store_path=store_path,
        feedback_log=Path(feedback_log) if feedback_log else default_feedback_log(store_path),
        settings_path=ui_dir / "settings.json",
        event_log=ui_dir / "events.jsonl",
    )
    host = host or os.getenv("PREFERENCE_UI_HOST", "127.0.0.1")
    requested_port = int(port if port is not None else os.getenv("PREFERENCE_UI_PORT", "8080"))
    available_port = _find_available_port(host, requested_port)
    handler = _make_handler(config)
    server = ThreadingHTTPServer((host, available_port), handler)
    info = UiServerInfo(
        url=f"http://{host}:{available_port}",
        host=host,
        port=available_port,
        store=str(store_path),
        settings_file=str(config.settings_path),
        event_log=str(config.event_log),
        feedback_log=str(config.feedback_log),
    )
    return server, info


def _make_handler(config: UiConfig) -> type[BaseHTTPRequestHandler]:
    class PreferenceUiHandler(BaseHTTPRequestHandler):
        server_version = "PreferenceAgentUI/0.1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._serve_static("index.html", "text/html; charset=utf-8")
                return
            if path == "/static/style.css":
                self._serve_static("style.css", "text/css; charset=utf-8")
                return
            if path == "/static/app.js":
                self._serve_static("app.js", "application/javascript; charset=utf-8")
                return
            if path == "/api/manifesto":
                _append_event(config.event_log, "panel_open")
                payload = build_manifesto(
                    config.store_path,
                    feedback_log=config.feedback_log,
                    settings_path=config.settings_path,
                    event_log=config.event_log,
                )
                self._json(HTTPStatus.OK, payload)
                return
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                body = self._read_json()
                if path == "/api/settings":
                    settings = _update_settings(config.settings_path, body)
                    _append_event(config.event_log, "settings_update", {"settings": settings})
                    self._json(HTTPStatus.OK, {"ok": True, "settings": settings})
                    return
                if path == "/api/feedback":
                    result = record_feedback(
                        feedback_type=str(body.get("feedback_type") or body.get("type") or "correction"),
                        user_feedback=str(body.get("user_feedback") or ""),
                        preference_id=str(body.get("preference_id") or ""),
                        preference_text=str(body.get("preference_text") or ""),
                        agent=str(body.get("agent") or "ui"),
                        task=str(body.get("task") or "preference-panel"),
                        source="ui",
                        context=body.get("context") if isinstance(body.get("context"), dict) else {},
                        log_path=config.feedback_log,
                    )
                    _append_event(config.event_log, "manual_action", {"action": "feedback"})
                    self._json(HTTPStatus.OK, result)
                    return
                if path == "/api/event":
                    event_type = str(body.get("event_type") or "event").strip() or "event"
                    _append_event(config.event_log, event_type, body.get("metadata") if isinstance(body.get("metadata"), dict) else {})
                    self._json(HTTPStatus.OK, {"ok": True})
                    return
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _serve_static(self, name: str, content_type: str) -> None:
            path = STATIC_DIR / name
            if not path.exists():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "static_not_found"})
                return
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return PreferenceUiHandler


def _record_view(record: PreferenceRecord, feedback_by_preference: dict[str, dict[str, Any]]) -> dict[str, Any]:
    statement = _statement(record)
    feedback = feedback_by_preference.get(record.id) or feedback_by_preference.get(statement) or {}
    recommendation = str(feedback.get("recommendation") or "observe")
    source_count = len({item.source for item in record.evidence if item.source})
    occurrences = max(1, len(record.evidence)) + int(feedback.get("usage", 0)) + int(feedback.get("confirmation", 0))
    attention = bool(record.conflict_notes) or recommendation == "review_needed" or record.status != "active"
    return {
        "id": record.id,
        "title": record.title or statement[:64],
        "statement": statement,
        "applies_to": record.applies_to,
        "status": record.status,
        "confidence": record.confidence,
        "confidence_score": _confidence_score(record.confidence),
        "frequency": occurrences,
        "sessions": source_count,
        "triggers": record.triggers,
        "exceptions": record.exceptions,
        "conflict_notes": record.conflict_notes,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "evidence": [item.to_dict() for item in record.evidence[:5]],
        "feedback": feedback,
        "attention": attention,
    }


def _statement(record: PreferenceRecord) -> str:
    return redact_sensitive(" ".join((record.preference or record.summary or record.title).split()).strip())


def _confidence_score(confidence: str) -> float:
    return {"high": 0.95, "medium": 0.7, "low": 0.45}.get(str(confidence).lower(), 0.6)


def _latest_update(preferences: list[dict[str, Any]]) -> str:
    if not preferences:
        return ""
    return max(str(item.get("updated_at") or "") for item in preferences)


def _read_settings(path: Path) -> dict[str, str]:
    settings = dict(DEFAULT_SETTINGS)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                settings.update({key: str(value) for key, value in data.items()})
        except json.JSONDecodeError:
            pass
    if settings.get("mode") not in VALID_MODES:
        settings["mode"] = "auto"
    if settings.get("theme") not in VALID_THEMES:
        settings["theme"] = "light"
    return settings


def _update_settings(path: Path, patch: dict[str, Any]) -> dict[str, str]:
    settings = _read_settings(path)
    if "mode" in patch and str(patch["mode"]) in VALID_MODES:
        settings["mode"] = str(patch["mode"])
    if "theme" in patch and str(patch["theme"]) in VALID_THEMES:
        settings["theme"] = str(patch["theme"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


def _effective_mode(settings: dict[str, str], events: list[dict[str, Any]]) -> str:
    configured = settings.get("mode", "auto")
    if configured in {"autonomous", "curated"}:
        return configured
    if any(item.get("event_type") in {"review_click", "manual_action"} for item in events):
        return "curated"
    if sum(1 for item in events if item.get("event_type") == "panel_open") <= 1:
        return "autonomous"
    return "curated"


def _append_event(path: Path, event_type: str, metadata: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "event_type": event_type,
        "metadata": metadata or {},
        "created_at": now_iso(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def _find_available_port(host: str, requested_port: int) -> int:
    if requested_port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])
    for port in range(requested_port, requested_port + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise OSError(f"no available port found from {requested_port} to {requested_port + 99}")
