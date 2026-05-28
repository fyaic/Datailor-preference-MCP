from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backends import HeuristicBackend, OpenAICompatibleBackend, PreferenceModelBackend, build_backend
from .models import PreferenceRecord, now_iso
from .privacy import redact_sensitive
from .store import MarkdownPreferenceStore


SUMMARY_TITLE = "# Executive Summary"
MAX_SUMMARY_PREFERENCES = 60
MAX_CHANGED_PREFERENCES = 20


@dataclass(frozen=True)
class ExecutiveSummary:
    path: str
    text: str
    status: str
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutiveSummaryUpdate:
    path: str
    updated: bool
    skipped_reason: str = ""
    reason: str = ""
    used_model: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def default_summary_path(store_path: str | Path) -> Path:
    configured = os.getenv("PREFERENCE_EXECUTIVE_SUMMARY_PATH")
    if configured:
        return Path(configured)
    return Path(store_path).parent / ".summary" / "executive-summary.md"


def read_executive_summary(
    store_path: str | Path,
    summary_path: str | Path | None = None,
) -> ExecutiveSummary:
    path = Path(summary_path) if summary_path else default_summary_path(store_path)
    if not path.exists():
        return ExecutiveSummary(path=str(path), text="", status="missing")
    text = _strip_title(path.read_text(encoding="utf-8", errors="replace")).strip()
    updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone().isoformat(timespec="seconds")
    return ExecutiveSummary(path=str(path), text=text, status="ready" if text else "empty", updated_at=updated_at)


def refresh_executive_summary(
    store_path: str | Path,
    changed_records: list[PreferenceRecord] | None = None,
    backend: PreferenceModelBackend | None = None,
    force: bool = False,
    summary_path: str | Path | None = None,
) -> ExecutiveSummaryUpdate:
    path = Path(summary_path) if summary_path else default_summary_path(store_path)
    if not _summary_enabled():
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="disabled")

    records = MarkdownPreferenceStore(store_path).load()
    changed = _unique_records(changed_records or [])
    if not force and not changed:
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="no_delta")
    if not records:
        return _write_summary(path, _cold_start_summary(), reason="cold_start", used_model=False)

    current = read_executive_summary(store_path, path).text
    payload = {
        "current_summary": current,
        "new_or_changed_preferences": [_record_view(record) for record in changed[:MAX_CHANGED_PREFERENCES]],
        "all_preferences": [_record_view(record) for record in _rank_records(records)[:MAX_SUMMARY_PREFERENCES]],
    }
    try:
        model_update = _llm_summary_update(payload, backend=backend, force=force)
    except Exception as exc:
        fallback = _heuristic_summary(records)
        result = _write_summary(path, fallback, reason="fallback_after_model_error", used_model=False)
        return ExecutiveSummaryUpdate(
            path=result.path,
            updated=result.updated,
            reason=result.reason,
            used_model=False,
            error=str(exc),
        )

    if model_update is not None:
        should_update = bool(model_update.get("should_update", force))
        summary = str(model_update.get("executive_summary") or "").strip()
        reason = str(model_update.get("reason") or "model_update")
        if should_update and summary:
            return _write_summary(path, _clean_summary(summary), reason=reason, used_model=True)
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="model_kept_current", reason=reason, used_model=True)

    summary = _heuristic_summary(records)
    if not force and current.strip() == summary.strip():
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="unchanged")
    return _write_summary(path, summary, reason="heuristic_update", used_model=False)


def _llm_summary_update(
    payload: dict[str, Any],
    backend: PreferenceModelBackend | None,
    force: bool,
) -> dict[str, Any] | None:
    selected = _select_llm_backend(backend)
    if not selected:
        return None
    prompt_payload = dict(payload)
    prompt_payload["force_update"] = force
    return selected._chat_json(  # noqa: SLF001 - the local backend already owns OpenAI-compatible JSON transport.
        system=EXECUTIVE_SUMMARY_SYSTEM_PROMPT,
        user=json.dumps(prompt_payload, ensure_ascii=False),
    )


def _select_llm_backend(backend: PreferenceModelBackend | None) -> OpenAICompatibleBackend | None:
    if isinstance(backend, OpenAICompatibleBackend):
        return backend
    configured = os.getenv("PREFERENCE_EXECUTIVE_SUMMARY_BACKEND")
    if configured:
        selected = build_backend(configured)
        return selected if isinstance(selected, OpenAICompatibleBackend) else None
    return None if isinstance(backend, HeuristicBackend) else None


def _write_summary(path: Path, summary: str, reason: str, used_model: bool) -> ExecutiveSummaryUpdate:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join([SUMMARY_TITLE, "", _clean_summary(summary), ""])
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if existing.strip() == content.strip():
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="unchanged", reason=reason, used_model=used_model)
    path.write_text(content, encoding="utf-8")
    return ExecutiveSummaryUpdate(path=str(path), updated=True, reason=reason, used_model=used_model)


def _heuristic_summary(records: list[PreferenceRecord]) -> str:
    active = [record for record in records if record.status == "active"]
    pending = [record for record in records if record.status != "active"]
    source = active or records
    lines: list[str] = []
    lines.append("The current preference profile indicates that you value direct, verifiable, low-noise collaboration that preserves context.")

    communication = _first_matching(source, ("concise", "conclusion", "outline", "reply", "bullets", "long-form", "detailed"))
    execution = _first_matching(source, ("test", "verify", "review", "code", "implementation", "tool", "edit", "coverage"))
    memory = _first_matching(source, ("document", "notes", "read back", "English", "Linear", "issue", "external system"))
    autonomy = _first_matching(source, ("confirm", "ask", "clarify", "direct", "automatic", "autonomous", "do not ask"))

    bullets = []
    if communication:
        bullets.append(f"Communication preference: {communication}")
    if execution:
        bullets.append(f"Execution preference: {execution}")
    if memory:
        bullets.append(f"Long-term collaboration preference: {memory}")
    if autonomy:
        bullets.append(f"Cadence preference: {autonomy}")
    for record in source:
        statement = _statement(record)
        if statement and statement not in bullets and len(bullets) < 5:
            bullets.append(statement)
    if bullets:
        lines.extend(f"- {item}" for item in bullets[:5])
    if pending:
        lines.append(f"{len(pending)} additional preference(s) are still under review, so the system will surface them cautiously instead of treating them as confirmed long-term profile data.")
    return "\n".join(lines)


def _cold_start_summary() -> str:
    return "The preference store is still in cold start. The system has not formed a stable profile yet, so it will observe explicit long-term preferences before updating this summary."


def _record_view(record: PreferenceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "title": redact_sensitive(record.title),
        "status": record.status,
        "confidence": record.confidence,
        "applies_to": redact_sensitive(record.applies_to),
        "preference": _statement(record),
        "triggers": [redact_sensitive(item) for item in record.triggers[:5]],
        "exceptions": [redact_sensitive(item) for item in record.exceptions[:5]],
    }


def _rank_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    confidence = {"high": 3, "medium": 2, "low": 1}
    return sorted(
        records,
        key=lambda record: (
            record.status == "active",
            confidence.get(record.confidence.lower(), 0),
            len(record.evidence),
            record.updated_at,
        ),
        reverse=True,
    )


def _unique_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    result: list[PreferenceRecord] = []
    seen: set[str] = set()
    for record in records:
        if record.id not in seen:
            seen.add(record.id)
            result.append(record)
    return result


def _first_matching(records: list[PreferenceRecord], markers: tuple[str, ...]) -> str:
    for record in records:
        text = _statement(record)
        lowered = text.casefold()
        if any(marker.casefold() in lowered for marker in markers):
            return text
    return ""


def _statement(record: PreferenceRecord) -> str:
    return redact_sensitive(" ".join((record.preference or record.summary or record.title).split()).strip())


def _clean_summary(summary: str) -> str:
    summary = redact_sensitive(summary)
    summary = _strip_title(summary)
    summary = re.sub(r"\n{3,}", "\n\n", summary).strip()
    return summary


def _strip_title(text: str) -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().casefold() in {SUMMARY_TITLE.casefold(), "# executive summary", "## executive summary"}:
        lines.pop(0)
    return "\n".join(lines).strip()


def _summary_enabled() -> bool:
    return os.getenv("PREFERENCE_EXECUTIVE_SUMMARY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


EXECUTIVE_SUMMARY_SYSTEM_PROMPT = f"""You generate an Executive Summary for a personal preference profile.

Your job is not to repeat every preference. The summary should make the user feel that the system understands and remembers their working style.

Input fields:
- current_summary: the current summary, which may be empty.
- new_or_changed_preferences: preferences added or changed in this run.
- all_preferences: important preferences currently in the store.
- force_update: whether to force a refresh.

First decide whether the new or changed preferences are enough to update current_summary. If not, return should_update=false.
If an update is needed, produce an English Markdown summary:
- Use 2 to 5 specific bullets.
- You may use second person, with a restrained and accurate tone.
- Summarize only long-term preferences, work habits, collaboration style, trust boundaries, and verification standards.
- Do not include paths, URLs, issue IDs, one-off tasks, secrets, or temporary object names.
- Do not output internal field names such as confidence, status, or metadata.
- Do not present pending or needs_review items as fully confirmed preferences.

Return a JSON object:
{{
  "should_update": true,
  "reason": "why the summary was or was not updated",
  "executive_summary": "Markdown summary text without {SUMMARY_TITLE}"
}}
"""
