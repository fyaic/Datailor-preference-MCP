from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backends import PreferenceModelBackend, build_backend
from .capacity import CapResult, enforce_preference_cap, write_cap_review_artifact
from .decision_conflicts import apply_decision_conflict_policy
from .executive_summary import refresh_executive_summary
from .injection_log import log_injection_event
from .models import PreferenceRecord, Session, now_iso
from .refinement import refine_records
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
    filtered_candidates: int = 0
    executive_summary_file: str = ""
    executive_summary_updated: bool = False
    executive_summary_error: str = ""
    cap: dict[str, Any] = field(default_factory=dict)

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
            "filtered_candidates": self.filtered_candidates,
            "executive_summary_file": self.executive_summary_file,
            "executive_summary_updated": self.executive_summary_updated,
            "executive_summary_error": self.executive_summary_error,
            "cap": self.cap,
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
        sessions = load_sessions(source)
        return self.capture_sessions(sessions, source=str(source), dry_run=dry_run)

    def capture_session(self, session: Session, dry_run: bool = False) -> CaptureResult:
        return self.capture_sessions([session], source=session.source, dry_run=dry_run)

    def capture_records(
        self,
        records: list[PreferenceRecord],
        source: str = "direct-records",
        dry_run: bool = False,
    ) -> CaptureResult:
        self.store.ensure()
        existing = self.store.load()
        result = CaptureResult(source=source, dry_run=dry_run)
        result.sessions_seen = 1
        result.candidates_seen = len(records)
        candidates = refine_records(records)
        result.filtered_candidates += len(records) - len(candidates)
        changed_records: list[PreferenceRecord] = []
        for candidate in candidates:
            changed = self._apply_candidate(candidate, existing, result)
            if changed is not None:
                changed_records.append(changed)
        cap = self._apply_cap(existing, result, dry_run=dry_run)
        existing = cap.records
        kept_ids = {record.id for record in existing}
        changed_records = [record for record in changed_records if record.id in kept_ids]
        if not dry_run:
            self.store.save(existing)
            self._refresh_executive_summary(changed_records, result)
        return result

    def capture_sessions(
        self,
        sessions: list[Session],
        source: str,
        dry_run: bool = False,
    ) -> CaptureResult:
        self.store.ensure()
        records = self.store.load()
        result = CaptureResult(source=source, dry_run=dry_run)
        result.sessions_seen = len(sessions)
        changed_records: list[PreferenceRecord] = []
        for session in sessions:
            extracted = self._extract(session)
            for candidate in extracted:
                self._attach_session_context(candidate, session)
            result.candidates_seen += len(extracted)
            candidates = refine_records(extracted)
            result.filtered_candidates += len(extracted) - len(candidates)
            for candidate in candidates:
                changed = self._apply_candidate(candidate, records, result)
                if changed is not None:
                    changed_records.append(changed)
        cap = self._apply_cap(records, result, dry_run=dry_run)
        records = cap.records
        kept_ids = {record.id for record in records}
        changed_records = [record for record in changed_records if record.id in kept_ids]
        if not dry_run:
            self.store.save(records)
            self._refresh_executive_summary(changed_records, result)
        return result

    def decide(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        agent: str = "agent",
        log_event: bool = True,
    ) -> dict[str, Any]:
        self.store.ensure()
        from .config_env import is_datailor_enabled

        if not is_datailor_enabled():
            decision = {
                "decision": "disabled",
                "agent_instruction": "",
                "reason": "Datailor preferences are currently disabled. Run `datailor enable` or set DATAILOR_ENABLED=true to re-enable.",
                "enabled": False,
                "matched_preferences": [],
                "escalate": False,
                "store": str(self.store.path),
                "checked_at": now_iso(),
            }
            if log_event:
                log_injection_event(
                    store_path=self.store.path,
                    hook=str((context or {}).get("hook") or "decide"),
                    agent=agent,
                    task=task,
                    context=context or {},
                    decision=decision,
                    source="engine",
                )
            return decision
        records = self.store.load()
        backend = self.backend
        try:
            decision = backend.decide(task=task, context=context or {}, records=records, agent=agent)
        except Exception as exc:
            if not self.fallback_backend:
                raise
            decision = self.fallback_backend.decide(task=task, context=context or {}, records=records, agent=agent)
            decision["backend_error"] = str(exc)
        decision = apply_decision_conflict_policy(
            decision,
            records=records,
            store_path=self.store.path,
            task=task,
            context=context or {},
        )
        if log_event:
            log_injection_event(
                store_path=self.store.path,
                hook=str((context or {}).get("hook") or "decide"),
                agent=agent,
                task=task,
                context=context or {},
                decision=decision,
                source="engine",
            )
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

    def _attach_session_context(self, record: PreferenceRecord, session: Session) -> None:
        for evidence in record.evidence:
            if not evidence.source:
                evidence.source = session.source
            if not evidence.session_id:
                evidence.session_id = session.session_id

    def _apply_candidate(
        self,
        candidate: PreferenceRecord,
        records: list[PreferenceRecord],
        result: CaptureResult,
    ) -> PreferenceRecord | None:
        decision = self.backend.merge_decision(candidate, records) if records else {"action": "new"}
        action = str(decision.get("action", "new"))
        target_id = decision.get("target_id")
        target = next((record for record in records if record.id == target_id), None)
        if action == "new" or not target:
            records.append(candidate)
            result.added.append(candidate.id)
            return candidate
        if action == "merge":
            target.add_evidence_from(candidate)
            result.merged.append(target.id)
            return target
        if action == "replace":
            target.merge_from(candidate)
            result.replaced.append(target.id)
            return target
        if action == "conflict":
            note = decision.get("reason") or "Found inconsistent preferences for a similar scenario."
            target.conflict_notes.append(
                f"{now_iso()} | {note} | candidate={candidate.preference}"
            )
            target.evidence.extend(candidate.evidence)
            target.status = "needs_review"
            target.touch()
            result.conflicts.append(target.id)
            return target
        records.append(candidate)
        result.added.append(candidate.id)
        return candidate

    def _apply_cap(self, records: list[PreferenceRecord], result: CaptureResult, dry_run: bool = False) -> CapResult:
        added_before_cap = list(result.added)
        cap = enforce_preference_cap(records, mode="review-first")
        records[:] = cap.records
        if not dry_run and (cap.review_required or cap.merged):
            write_cap_review_artifact(self.store.path, cap)
        kept_ids = {record.id for record in cap.records}
        result.added = [record_id for record_id in result.added if record_id in kept_ids]
        added_removed = [record_id for record_id in added_before_cap if record_id not in kept_ids]
        cap_data = cap.to_dict()
        if added_removed:
            cap_data["added_removed_by_cap"] = added_removed
        result.cap = cap_data
        return cap

    def _refresh_executive_summary(self, changed_records: list[PreferenceRecord], result: CaptureResult) -> None:
        try:
            update = refresh_executive_summary(
                self.store.path,
                changed_records=changed_records,
                backend=self.backend,
                force=False,
            )
        except Exception as exc:
            result.executive_summary_error = str(exc)
            return
        result.executive_summary_file = update.path
        result.executive_summary_updated = update.updated
        result.executive_summary_error = update.error


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
