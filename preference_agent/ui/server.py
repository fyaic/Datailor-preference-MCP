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

from ..backends import semantic_similarity
from ..capacity import count_cap_records, enforce_preference_cap, preference_limit
from ..executive_summary import read_executive_summary
from ..feedback import feedback_report, record_feedback
from ..fitting import apply_fitting_plan, latest_fitting, reject_fitting_plan
from ..fitting_trigger import default_mode_settings, mark_fitting_reviewed, normalize_mode, read_mode_settings, write_mode_settings
from ..injection import sync_injection_artifacts
from ..injection_log import default_injection_log, injection_log_summary, read_injection_log
from ..live_confidence import live_confidence
from ..models import Evidence, PreferenceRecord, now_iso
from ..paths import default_feedback_log as default_feedback_log_path
from ..paths import default_store_path as default_preference_store_path
from ..paths import default_ui_dir as default_user_ui_dir
from ..preference_actions import apply_preference_feedback
from ..privacy import redact_sensitive
from ..store import MarkdownPreferenceStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_SETTINGS = default_mode_settings()
VALID_MODES = {"auto", "curate"}
VALID_THEMES = {"light", "dark"}
VALID_LANGUAGES = {"en", "zh"}

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
    injection_log: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UiConfig:
    store_path: Path
    feedback_log: Path
    settings_path: Path
    event_log: Path
    injection_log: Path


def default_store_path() -> Path:
    return default_preference_store_path()


def default_ui_dir(store_path: str | Path | None = None) -> Path:
    return default_user_ui_dir()


def default_feedback_log(store_path: str | Path | None = None) -> Path:
    return default_feedback_log_path()


def build_manifesto(
    store_path: str | Path | None = None,
    feedback_log: str | Path | None = None,
    settings_path: str | Path | None = None,
    event_log: str | Path | None = None,
    injection_log: str | Path | None = None,
) -> dict[str, Any]:
    store_path = Path(store_path) if store_path else default_store_path()
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    ui_dir = default_ui_dir(store_path)
    settings_path = Path(settings_path) if settings_path else ui_dir / "settings.json"
    event_log = Path(event_log) if event_log else ui_dir / "events.jsonl"
    feedback_log = Path(feedback_log) if feedback_log else default_feedback_log(store_path)
    injection_log = Path(injection_log) if injection_log else default_injection_log(store_path)

    records = store.load()
    executive_summary = read_executive_summary(store_path)
    settings = _read_settings(settings_path)
    events = _read_jsonl(event_log)
    injection_events = read_injection_log(store_path, path=injection_log, limit=200)
    feedback = feedback_report(feedback_log)
    feedback_by_preference = {
        str(item.get("preference")): item
        for item in feedback.get("by_preference", [])
        if item.get("preference")
    }
    preferences = [_record_view(record, feedback_by_preference) for record in records]
    active = [item for item in preferences if item["status"] == "active"]
    pending = [item for item in preferences if item["status"] != "active"]
    attention = [item for item in preferences if item["attention"]]
    cap = enforce_preference_cap([PreferenceRecord.from_dict(record.to_dict()) for record in records], mode="review-first")
    conflict_groups = _build_conflict_groups(records, preferences)
    mode = _effective_mode(settings, events)
    fitting_state = settings.get("fitting") if isinstance(settings.get("fitting"), dict) else {}
    fitting_view = _latest_fitting_view()
    if fitting_view and fitting_state.get("pending_plan_job_id") != fitting_view.get("job_id"):
        fitting_view["pending_changes"] = 0
        fitting_view["review_state"] = "reviewed"

    return {
        "generated_at": now_iso(),
        "store": str(store_path),
        "mode": mode,
        "configured_mode": settings["mode"],
        "theme": settings["theme"],
        "language": settings["language"],
        "fitting_state": fitting_state,
        "summary": {
            "active": len(active),
            "pending": len(pending),
            "conflicts": len(conflict_groups),
            "attention": len(attention),
            "feedback_total": int(feedback.get("total", 0)),
            "last_updated": _latest_update(preferences),
            "preference_count": count_cap_records(records),
            "preference_limit": preference_limit(),
            "over_limit": count_cap_records(records) > preference_limit(),
            "cap_review_required": cap.review_required,
            "cap_review_candidates": len(cap.review_candidates),
        },
        "executive_summary": executive_summary.to_dict(),
        "preferences": preferences,
        "recent": sorted(preferences, key=lambda item: item["updated_at"], reverse=True)[:5],
        "attention": attention[:10],
        "conflict_groups": conflict_groups,
        "feedback": feedback,
        "events": {
            "panel_opens": sum(1 for item in events if item.get("event_type") == "panel_open"),
            "review_clicks": sum(1 for item in events if item.get("event_type") == "review_click"),
        },
        "injection_log": {
            "path": str(injection_log),
            "summary": injection_log_summary(injection_events),
            "items": injection_events,
        },
        "fitting": fitting_view,
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
        injection_log=default_injection_log(store_path),
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
        injection_log=str(config.injection_log),
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
            if path == "/static/brand-logo.png":
                self._serve_static("brand-logo.png", "image/png")
                return
            if path in {"/favicon.png", "/static/favicon.png"}:
                self._serve_static("favicon.png", "image/png")
                return
            if path == "/api/manifesto":
                _append_event(config.event_log, "panel_open")
                payload = build_manifesto(
                    config.store_path,
                    feedback_log=config.feedback_log,
                    settings_path=config.settings_path,
                    event_log=config.event_log,
                    injection_log=config.injection_log,
                )
                self._json(HTTPStatus.OK, payload)
                return
            if path == "/api/fitting/latest":
                self._json(HTTPStatus.OK, {"ok": True, "job": _latest_fitting_view()})
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
                    feedback_type = str(body.get("feedback_type") or body.get("type") or "correction")
                    result = record_feedback(
                        feedback_type=feedback_type,
                        user_feedback=str(body.get("user_feedback") or ""),
                        preference_id=str(body.get("preference_id") or ""),
                        preference_text=str(body.get("preference_text") or ""),
                        agent=str(body.get("agent") or "ui"),
                        task=str(body.get("task") or "preference-panel"),
                        source="ui",
                        context=body.get("context") if isinstance(body.get("context"), dict) else {},
                        log_path=config.feedback_log,
                    )
                    update = apply_preference_feedback(
                        store_path=config.store_path,
                        feedback_type=feedback_type,
                        user_feedback=str(body.get("user_feedback") or ""),
                        preference_id=str(body.get("preference_id") or ""),
                        preference_text=str(body.get("preference_text") or ""),
                    )
                    result["preference_update"] = update.to_dict()
                    _append_event(
                        config.event_log,
                        "manual_action",
                        {
                            "action": "feedback",
                            "feedback_type": feedback_type,
                            "preference_update": update.to_dict(),
                        },
                    )
                    self._json(HTTPStatus.OK, result)
                    return
                if path == "/api/event":
                    event_type = str(body.get("event_type") or "event").strip() or "event"
                    _append_event(config.event_log, event_type, body.get("metadata") if isinstance(body.get("metadata"), dict) else {})
                    self._json(HTTPStatus.OK, {"ok": True})
                    return
                if path == "/api/fitting/apply":
                    job_id = str(body.get("job_id") or "").strip()
                    accepted = body.get("accepted_change_ids") if isinstance(body.get("accepted_change_ids"), list) else []
                    result = apply_fitting_plan(
                        job_id=job_id,
                        accepted_change_ids=[str(item) for item in accepted],
                        store_path=config.store_path,
                    )
                    if result.get("ok"):
                        rejected = reject_fitting_plan(job_id)
                        result["rejected"] = rejected.get("rejected", [])
                        result["remaining_pending"] = rejected.get("remaining_pending", result.get("remaining_pending", 0))
                        result["pending_change_ids"] = rejected.get("pending_change_ids", [])
                        mark_fitting_reviewed(job_id, path=config.settings_path, accepted=len(result.get("applied") or []))
                        sync_injection_artifacts(config.store_path)
                        _append_event(
                            config.event_log,
                            "manual_action",
                            {"action": "fitting_apply", "job_id": job_id, "accepted_change_ids": accepted},
                        )
                    self._json(HTTPStatus.OK, result)
                    return
                if path == "/api/fitting/reject":
                    job_id = str(body.get("job_id") or "").strip()
                    result = reject_fitting_plan(job_id)
                    settings = mark_fitting_reviewed(job_id, path=config.settings_path, rejected=True)
                    _append_event(config.event_log, "manual_action", {"action": "fitting_reject", "job_id": job_id})
                    self._json(HTTPStatus.OK, {"ok": True, "settings": settings, "result": result})
                    return
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            except ValueError as exc:
                status = HTTPStatus.NOT_FOUND if str(exc).startswith("job not found:") else HTTPStatus.BAD_REQUEST
                self._json(status, {"ok": False, "error": str(exc)})
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
    session_count = len({_evidence_session_key(item) for item in record.evidence if _evidence_session_key(item)})
    occurrences = max(1, len(record.evidence)) + int(feedback.get("usage", 0)) + int(feedback.get("confirmation", 0))
    live = live_confidence(record, relevance_score=0.26)
    attention = (
        bool(record.conflict_notes)
        or recommendation == "review_needed"
        or record.status != "active"
        or live.recency_decay > 0
        or live.consistency_penalty > 0
    )
    return {
        "id": record.id,
        "title": record.title or statement[:64],
        "statement": statement,
        "applies_to": record.applies_to,
        "status": record.status,
        "confidence": record.confidence,
        "confidence_score": _confidence_score(record.confidence),
        "live_confidence": live.score,
        "live_confidence_label": live.label,
        "live_confidence_factors": {
            "base": live.base,
            "evidence_boost": live.evidence_boost,
            "recency_decay": live.recency_decay,
            "consistency_penalty": live.consistency_penalty,
            "relevance_bonus": live.relevance_bonus,
        },
        "live_confidence_reasons": live.reasons,
        "frequency": occurrences,
        "sessions": session_count,
        "triggers": record.triggers,
        "exceptions": record.exceptions,
        "conflict_notes": record.conflict_notes,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "evidence": [item.to_dict() for item in record.evidence[:5]],
        "feedback": feedback,
        "attention": attention,
    }


def _evidence_session_key(item: Evidence) -> str:
    return str(getattr(item, "session_id", "") or getattr(item, "source", "") or "").strip()


def _latest_fitting_view() -> dict[str, Any] | None:
    job = latest_fitting().get("job")
    if not isinstance(job, dict):
        return None
    view = dict(job)
    report_file = Path(str(view.get("report_file") or ""))
    view["report_markdown"] = ""
    view["report_available"] = False
    if report_file.exists() and report_file.is_file():
        try:
            view["report_markdown"] = report_file.read_text(encoding="utf-8")
            view["report_available"] = True
        except OSError as exc:
            view["report_error"] = str(exc)
    plan_file = Path(str(view.get("job_dir") or "")) / "apply-plan.json"
    if plan_file.exists() and plan_file.is_file():
        try:
            view["apply_plan"] = json.loads(plan_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            view["apply_plan_error"] = str(exc)
    plan = view.get("apply_plan") if isinstance(view.get("apply_plan"), dict) else {}
    changes = plan.get("changes") if isinstance(plan.get("changes"), list) else []
    pending = [item for item in changes if isinstance(item, dict) and item.get("status", "pending") == "pending"]
    view["changes"] = changes
    view["pending_changes"] = len(pending) if view.get("status") == "pending_review" else 0
    view["review_state"] = "pending" if view["pending_changes"] else "none"
    return view


def _build_conflict_groups(
    records: list[PreferenceRecord],
    preferences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in preferences}
    groups: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for record in records:
        left = by_id.get(record.id)
        if not left:
            continue
        for index, note in enumerate(record.conflict_notes):
            right = _synthetic_conflict_candidate(record.id, index, _candidate_text_from_note(note))
            groups.append(
                _conflict_group(
                    group_id=f"{record.id}-note-{index}",
                    left=left,
                    right=right,
                    reason=_reason_from_conflict_note(note),
                    score=None,
                    right_label="B Conflicting candidate",
                )
            )

    active = [item for item in preferences if item["status"] == "active"]
    review = [item for item in preferences if item["status"] != "active"]
    for pending in review:
        counterpart, score = _best_conflict_counterpart(pending, active)
        if not counterpart or score < 0.34:
            continue
        pair_key = tuple(sorted((str(counterpart["id"]), str(pending["id"]))))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        groups.append(
            _conflict_group(
                group_id=f"{counterpart['id']}-vs-{pending['id']}",
                left=counterpart,
                right=pending,
                reason="Needs review against the closest active preference.",
                score=round(score, 3),
                right_label="B Needs review",
            )
        )
    return groups[:50]


def _conflict_group(
    group_id: str,
    left: dict[str, Any],
    right: dict[str, Any],
    reason: str,
    score: float | None,
    right_label: str,
) -> dict[str, Any]:
    return {
        "id": group_id,
        "title": _conflict_title(left, right),
        "reason": reason,
        "score": score,
        "left_label": "A Stored active preference",
        "right_label": right_label,
        "left": left,
        "right": right,
    }


def _best_conflict_counterpart(
    pending: dict[str, Any],
    active: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, float]:
    best: tuple[dict[str, Any] | None, float] = (None, 0.0)
    for candidate in active:
        score = _conflict_pair_score(pending, candidate)
        if score > best[1]:
            best = (candidate, score)
    return best


def _conflict_pair_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_blob = _conflict_blob(left)
    right_blob = _conflict_blob(right)
    semantic = semantic_similarity(left_blob, right_blob)
    overlap = _meaningful_overlap(left_blob, right_blob)
    shared_topic = _shared_topic(left_blob, right_blob)
    if _has_opposition(left_blob, right_blob) and (shared_topic or overlap >= 0.04):
        return max(0.52, min(1.0, overlap + semantic * 0.25))
    if _same_explicit_scope(left, right) and overlap >= 0.18:
        return max(0.42, min(1.0, overlap + semantic * 0.2))
    if shared_topic and overlap >= 0.22 and semantic >= 0.45:
        return max(0.36, min(1.0, overlap + semantic * 0.15))
    return min(overlap, semantic * 0.12)


def _conflict_blob(item: dict[str, Any]) -> str:
    parts = [
        item.get("title", ""),
        item.get("statement", ""),
        item.get("applies_to", ""),
        " ".join(item.get("triggers", []) or []),
    ]
    return " ".join(str(part) for part in parts if part).casefold()


def _same_explicit_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_scope = str(left.get("applies_to") or "").strip()
    right_scope = str(right.get("applies_to") or "").strip()
    if not left_scope or not right_scope or left_scope != right_scope:
        return False
    return left_scope != str(left.get("statement") or "").strip()


def _shared_topic(left: str, right: str) -> bool:
    topics = (
        ("reply", "response", "answer", "output"),
        ("test", "verify", "verification", "acceptance"),
        ("review", "code review", "audit"),
        ("linear", "issue"),
        ("markdown", "document", "notes"),
        ("local", "offline"),
        ("confirm", "ask", "clarify"),
    )
    return any(_has_any_term(left, topic) and _has_any_term(right, topic) for topic in topics)


def _has_opposition(left: str, right: str) -> bool:
    opposed = (
        (("concise", "brief", "short", "less verbose"), ("detail", "detailed", "exhaustive", "complete", "long-form")),
        (("ask", "confirm", "manual"), ("automatic", "auto", "do not ask", "no confirmation", "directly proceed")),
        (("local", "offline"), ("cloud", "api", "remote model")),
        (("commit", "push"), ("do not commit", "no push")),
    )
    for first, second in opposed:
        if (_has_any_term(left, first) and _has_any_term(right, second)) or (
            _has_any_term(left, second) and _has_any_term(right, first)
        ):
            return True
    return False


def _has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.casefold() in text for term in terms)


def _meaningful_overlap(left: str, right: str) -> float:
    left_text = _meaningful_text(left)
    right_text = _meaningful_text(right)
    if not left_text or not right_text:
        return 0.0
    left_grams = _char_grams(left_text)
    right_grams = _char_grams(right_text)
    return len(left_grams & right_grams) / max(1, len(left_grams | right_grams))


def _meaningful_text(text: str) -> str:
    normalized = "".join(ch for ch in text.casefold() if ch.isalnum())
    generic_terms = (
        "agent",
        "user",
        "when",
        "prefer",
        "preference",
        "default",
        "should",
        "need",
        "needs",
        "use",
        "avoid",
        "provide",
        "ensure",
        "content",
        "output",
        "task",
        "related",
        "current",
        "candidate",
        "active",
    )
    for term in generic_terms:
        normalized = normalized.replace(term, "")
    return normalized


def _char_grams(text: str, size: int = 2) -> set[str]:
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _conflict_title(left: dict[str, Any], right: dict[str, Any]) -> str:
    scope = str(left.get("applies_to") or right.get("applies_to") or "").strip()
    if scope and scope != str(left.get("statement") or "").strip():
        return _short_text(scope, 90)
    left_title = str(left.get("title") or left.get("statement") or "A").strip()
    right_title = str(right.get("title") or right.get("statement") or "B").strip()
    return _short_text(f"{left_title} vs {right_title}", 90)


def _candidate_text_from_note(note: str) -> str:
    if "candidate=" in note:
        return note.split("candidate=", 1)[1].strip()
    return note.strip()


def _reason_from_conflict_note(note: str) -> str:
    parts = [part.strip() for part in note.split("|")]
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return "Stored conflict note."


def _synthetic_conflict_candidate(record_id: str, index: int, text: str) -> dict[str, Any]:
    statement = _short_text(redact_sensitive(" ".join(text.split()).strip()), 500)
    return {
        "id": f"{record_id}-candidate-{index}",
        "title": _short_text(statement, 64),
        "statement": statement,
        "applies_to": "",
        "status": "candidate",
        "confidence": "unknown",
        "confidence_score": 0.0,
        "frequency": 1,
        "sessions": 0,
        "triggers": [],
        "exceptions": [],
        "conflict_notes": [],
        "created_at": "",
        "updated_at": "",
        "evidence": [],
        "feedback": {},
        "attention": True,
        "synthetic": True,
    }


def _short_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _statement(record: PreferenceRecord) -> str:
    return redact_sensitive(" ".join((record.preference or record.summary or record.title).split()).strip())


def _confidence_score(confidence: str) -> float:
    return {"high": 0.95, "medium": 0.7, "low": 0.45}.get(str(confidence).lower(), 0.6)


def _latest_update(preferences: list[dict[str, Any]]) -> str:
    if not preferences:
        return ""
    return max(str(item.get("updated_at") or "") for item in preferences)


def _read_settings(path: Path) -> dict[str, Any]:
    settings = read_mode_settings(path)
    if settings.get("theme") not in VALID_THEMES:
        settings["theme"] = "light"
    if settings.get("language") not in VALID_LANGUAGES:
        settings["language"] = "en"
    return settings


def _update_settings(path: Path, patch: dict[str, Any]) -> dict[str, Any]:
    settings = _read_settings(path)
    if "mode" in patch:
        settings["mode"] = normalize_mode(patch["mode"])
    if "theme" in patch and str(patch["theme"]) in VALID_THEMES:
        settings["theme"] = str(patch["theme"])
    if "language" in patch and str(patch["language"]) in VALID_LANGUAGES:
        settings["language"] = str(patch["language"])
    return write_mode_settings(settings, path)


def _effective_mode(settings: dict[str, Any], _events: list[dict[str, Any]] | None = None) -> str:
    """V1: explicit user choice only; default to conservative curate mode."""
    configured = settings.get("mode", "curate")
    return configured if configured in VALID_MODES else "curate"


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
