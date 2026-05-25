from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backends import PreferenceModelBackend, build_backend
from .models import PreferenceRecord, now_iso
from .session_loader import load_sessions
from .store import MarkdownPreferenceStore


@dataclass
class CaptureResult:
    source: str
    sessions_seen: int = 0
    candidates_seen: int = 0
    added: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sessions_seen": self.sessions_seen,
            "candidates_seen": self.candidates_seen,
            "added": self.added,
            "merged": self.merged,
            "replaced": self.replaced,
            "conflicts": self.conflicts,
            "dry_run": self.dry_run,
        }


class PreferenceEngine:
    def __init__(
        self,
        store: MarkdownPreferenceStore,
        backend: PreferenceModelBackend | None = None,
        fallback_backend: PreferenceModelBackend | None = None,
    ):
        self.store = store
        self.backend = backend or build_backend("auto")
        self.fallback_backend = fallback_backend

    def init_store(self) -> None:
        self.store.ensure()

    def capture_path(self, source: str | Path, dry_run: bool = False) -> CaptureResult:
        self.store.ensure()
        records = self.store.load()
        result = CaptureResult(source=str(source), dry_run=dry_run)
        sessions = load_sessions(source)
        result.sessions_seen = len(sessions)
        for session in sessions:
            candidates = self._extract(session)
            result.candidates_seen += len(candidates)
            for candidate in candidates:
                self._apply_candidate(candidate, records, result)
        if not dry_run:
            self.store.save(records)
        return result

    def decide(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        agent: str = "agent",
    ) -> dict[str, Any]:
        self.store.ensure()
        records = self.store.load()
        backend = self.backend
        try:
            decision = backend.decide(task=task, context=context or {}, records=records, agent=agent)
        except Exception as exc:
            if not self.fallback_backend:
                raise
            decision = self.fallback_backend.decide(task=task, context=context or {}, records=records, agent=agent)
            decision["backend_error"] = str(exc)
        decision.setdefault("store", str(self.store.path))
        decision.setdefault("checked_at", now_iso())
        return decision

    def _extract(self, session: Any) -> list[PreferenceRecord]:
        try:
            return self.backend.extract_preferences(session)
        except Exception:
            if self.fallback_backend:
                return self.fallback_backend.extract_preferences(session)
            raise

    def _apply_candidate(
        self,
        candidate: PreferenceRecord,
        records: list[PreferenceRecord],
        result: CaptureResult,
    ) -> None:
        decision = self.backend.merge_decision(candidate, records) if records else {"action": "new"}
        action = str(decision.get("action", "new"))
        target_id = decision.get("target_id")
        target = next((record for record in records if record.id == target_id), None)
        if action == "new" or not target:
            records.append(candidate)
            result.added.append(candidate.id)
            return
        if action == "merge":
            target.add_evidence_from(candidate)
            result.merged.append(target.id)
            return
        if action == "replace":
            target.merge_from(candidate)
            result.replaced.append(target.id)
            return
        if action == "conflict":
            note = decision.get("reason") or "发现同类场景下的不一致偏好"
            target.conflict_notes.append(
                f"{now_iso()} | {note} | candidate={candidate.preference}"
            )
            target.evidence.extend(candidate.evidence)
            target.status = "needs_review"
            target.touch()
            result.conflicts.append(target.id)
            return
        records.append(candidate)
        result.added.append(candidate.id)


def parse_context(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    text = text.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        pairs = {}
        for part in text.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                pairs[key.strip()] = value.strip()
        return pairs
    return value if isinstance(value, dict) else {"value": value}

