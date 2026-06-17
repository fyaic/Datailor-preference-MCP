from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid5, NAMESPACE_URL

from .backends import HeuristicBackend, PreferenceModelBackend, build_backend
from .capture_governance import CaptureDiagnostic, classify_capture_candidate, evaluate_capture_candidate
from .executive_summary import refresh_executive_summary
from .models import Session, SessionMessage
from .paths import default_candidate_dir, default_checkpoint_dir, default_store_path
from .privacy import redact_sensitive
from .recall import MultiRouteRecallEngine, RecallCandidate, RecallConfig, RecallInput
from .refinement import conflict_likely, refine_records
from .store import MarkdownPreferenceStore


SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
AGENT_RULE_NAMES = {"AGENTS.md", "agents.md", "CLAUDE.md", "claude.md"}
AGENT_RULE_SUFFIXES = {".md", ".yaml", ".yml"}
USER_ROLES = {"user", "human", "me"}
IGNORED_ROLES = {"assistant", "model", "ai", "tool", "system", "kimi", "codex", "openclaw"}
RECALL_MARKERS = (
    "from now on",
    "default",
    "every time",
    "always",
    "I want",
    "I prefer",
    "I tend to",
    "I like",
    "I dislike",
    "do not",
    "don't",
    "forbid",
    "must",
    "wrong",
    "not what I meant",
    "you should",
    "next time",
    "read back",
    "save as documentation",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class CaptureConfig:
    project_root: Path
    candidate_dir: Path
    checkpoint_dir: Path
    store_path: Path = field(default_factory=default_store_path)
    mode: str = "recall-only"
    batch_size: int = 50
    max_minutes: float = 0.0
    max_lines: int = 0
    max_candidates: int = 0
    max_retries: int = 3
    profile_sample_lines: int = 1000
    include_agent_rules: bool = True
    debug_output: bool = False
    recall_strategy: str = "keyword"
    recall_batch_size: int = 64
    model_batch_size: int = 8

    @classmethod
    def from_env(cls, project_root: str | Path | None = None) -> "CaptureConfig":
        root = Path(
            project_root
            or os.getenv("PREFERENCE_PROJECT_ROOT")
            or Path.cwd()
        )
        return cls(
            project_root=root,
            store_path=default_store_path(),
            candidate_dir=default_candidate_dir(),
            checkpoint_dir=default_checkpoint_dir(),
            mode=os.getenv("PREFERENCE_CAPTURE_MODE", "recall-only"),
            batch_size=_env_int("PREFERENCE_CAPTURE_BATCH_SIZE", 50),
            max_minutes=_env_float("PREFERENCE_CAPTURE_MAX_MINUTES", 0.0),
            max_lines=_env_int("PREFERENCE_CAPTURE_MAX_LINES", 0),
            max_candidates=_env_int("PREFERENCE_CAPTURE_MAX_CANDIDATES", 0),
            max_retries=_env_int("PREFERENCE_CAPTURE_MAX_RETRIES", 3),
            profile_sample_lines=_env_int("PREFERENCE_CAPTURE_PROFILE_SAMPLE_LINES", 1000),
            include_agent_rules=os.getenv("PREFERENCE_CAPTURE_AGENT_RULES", "1") not in {"0", "false", "False"},
            debug_output=os.getenv("PREFERENCE_CAPTURE_DEBUG", "0") in {"1", "true", "True"},
            recall_strategy=os.getenv("PREFERENCE_RECALL_STRATEGY", "keyword"),
            recall_batch_size=_env_int("PREFERENCE_RECALL_BATCH_SIZE", 64),
            model_batch_size=_env_int("PREFERENCE_MODEL_BATCH_SIZE", 8),
        )

    def ensure_dirs(self) -> None:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        if self.debug_output:
            self.candidate_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class CaptureCandidate:
    candidate_id: str
    source: str
    source_type: str
    scope: str
    preference: str
    applies_to: str
    evidence_quote: str
    confidence: str
    session_id: str = ""
    merge_action: str = "pending"
    context_hint: str = ""
    routes: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    signal_type: str = ""
    diagnostic: dict[str, Any] = field(default_factory=dict)
    final_score: float = 0.0
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaptureJobResult:
    source: str
    mode: str
    store_file: str
    candidate_file: str
    extracted_file: str
    failure_file: str
    summary_file: str
    checkpoint_file: str
    lines_seen: int = 0
    bad_lines: int = 0
    user_messages_seen: int = 0
    candidates_written: int = 0
    extracted_records_written: int = 0
    agent_rule_files_seen: int = 0
    extract_errors: int = 0
    recall_errors: int = 0
    classified_candidates: int = 0
    filtered_candidates: int = 0
    model_fallbacks: int = 0
    preferences_added: int = 0
    preferences_merged: int = 0
    preferences_replaced: int = 0
    preferences_conflicted: int = 0
    executive_summary_file: str = ""
    executive_summary_updated: bool = False
    executive_summary_error: str = ""
    stopped_reason: str = "completed"
    profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CaptureRunner:
    def __init__(self, config: CaptureConfig, backend: PreferenceModelBackend | None = None):
        self.config = config
        self.config.ensure_dirs()
        self.backend = backend
        self.recall_engine: MultiRouteRecallEngine | None = None

    def run(self, source: str | Path) -> CaptureJobResult:
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(str(source_path))
        self._changed_preference_ids: set[str] = set()
        job_id = _safe_job_id(source_path)
        candidate_file = self.config.candidate_dir / f"{job_id}-candidates.jsonl"
        extracted_file = self.config.candidate_dir / f"{job_id}-extracted.jsonl"
        failure_file = self.config.candidate_dir / f"{job_id}-failures.jsonl"
        summary_file = self.config.candidate_dir / f"{job_id}-summary.md"
        checkpoint_file = self.config.checkpoint_dir / f"{job_id}.checkpoint.json"
        result = CaptureJobResult(
            source=str(source_path),
            mode=self.config.mode,
            store_file=str(self.config.store_path),
            candidate_file=str(candidate_file) if self.config.debug_output else "",
            extracted_file=str(extracted_file) if self.config.debug_output and self._use_model_extract() else "",
            failure_file=str(failure_file) if self.config.debug_output and self._use_model_extract() else "",
            summary_file=str(summary_file) if self.config.debug_output else "",
            checkpoint_file=str(checkpoint_file),
        )
        start = time.monotonic()
        result.profile = self.profile(source_path)
        checkpoint = self._load_checkpoint(checkpoint_file)
        store = MarkdownPreferenceStore(self.config.store_path)
        store.ensure()
        records = store.load()
        if self.config.debug_output:
            candidate_file.parent.mkdir(parents=True, exist_ok=True)

        pending_extract: list[CaptureCandidate] = []
        recall_buffer: list[RecallInput] = []
        committed_line = int(checkpoint.get("line", 0))
        if self.config.include_agent_rules and not checkpoint.get("agent_rules_done"):
            for candidate in self._agent_rule_candidates(self.config.project_root):
                self._write_candidate(candidate_file, candidate)
                result.candidates_written += 1
                pending_extract.append(candidate)
                if len(pending_extract) >= self.config.batch_size:
                    self._extract_batch(pending_extract, extracted_file, result, records)
                    store.save(records)
                    pending_extract.clear()
            result.agent_rule_files_seen = checkpoint.get("agent_rule_files_seen", 0) or self._last_agent_rule_count
            checkpoint["agent_rules_done"] = True
            checkpoint["agent_rule_files_seen"] = result.agent_rule_files_seen
            self._save_checkpoint(checkpoint_file, checkpoint, result)

        for item in self._iter_jsonl_user_messages(source_path, start_line=int(checkpoint.get("line", 0))):
            result.lines_seen = item["line"]
            result.bad_lines += item.get("bad_line", 0)
            if "message" not in item:
                if self._should_stop(start, result):
                    break
                continue
            result.user_messages_seen += 1
            message = item["message"]
            if self._use_multi_recall():
                recall_buffer.append(
                    RecallInput(
                        source=f"{source_path}:{item['line']}",
                    content=message["content"],
                    context_hint=message.get("context_hint", ""),
                    session_id=message.get("session_id", ""),
                        turn_index=int(message.get("turn_index", item["line"])),
                    )
                )
                if len(recall_buffer) >= self.config.recall_batch_size:
                    for candidate in self._recall_batch(recall_buffer, result):
                        self._write_candidate(candidate_file, candidate)
                        result.candidates_written += 1
                        pending_extract.append(candidate)
                        if len(pending_extract) >= self.config.batch_size:
                            self._extract_batch(pending_extract, extracted_file, result, records)
                            store.save(records)
                            pending_extract.clear()
                            committed_line = result.lines_seen
                            self._save_checkpoint(checkpoint_file, {"line": committed_line}, result)
                    recall_buffer.clear()
            elif _should_recall(message["content"]):
                candidate = _candidate_from_user_message(
                    source=f"{source_path}:{item['line']}",
                    session_id=message.get("session_id", ""),
                    content=message["content"],
                    context_hint=message.get("context_hint", ""),
                )
                self._write_candidate(candidate_file, candidate)
                result.candidates_written += 1
                pending_extract.append(candidate)
                if len(pending_extract) >= self.config.batch_size:
                    self._extract_batch(pending_extract, extracted_file, result, records)
                    store.save(records)
                    pending_extract.clear()
                    committed_line = result.lines_seen
                    self._save_checkpoint(checkpoint_file, {"line": committed_line}, result)
            if result.lines_seen % max(1, self.config.batch_size) == 0 and not pending_extract and not recall_buffer:
                committed_line = result.lines_seen
                self._save_checkpoint(checkpoint_file, {"line": committed_line}, result)
            if self._should_stop(start, result):
                break
        if recall_buffer:
            for candidate in self._recall_batch(recall_buffer, result):
                self._write_candidate(candidate_file, candidate)
                result.candidates_written += 1
                pending_extract.append(candidate)
                if len(pending_extract) >= self.config.batch_size:
                    self._extract_batch(pending_extract, extracted_file, result, records)
                    store.save(records)
                    pending_extract.clear()
                    committed_line = result.lines_seen
                    self._save_checkpoint(checkpoint_file, {"line": committed_line}, result)
            recall_buffer.clear()
        if self._use_multi_recall() and self.recall_engine:
            for candidate in self._behavior_candidates():
                self._write_candidate(candidate_file, candidate)
                result.candidates_written += 1
                pending_extract.append(candidate)
                if len(pending_extract) >= self.config.batch_size:
                    self._extract_batch(pending_extract, extracted_file, result, records)
                    store.save(records)
                    pending_extract.clear()
                    committed_line = result.lines_seen
                    self._save_checkpoint(checkpoint_file, {"line": committed_line}, result)
        if pending_extract:
            self._extract_batch(pending_extract, extracted_file, result, records)
            store.save(records)
            committed_line = result.lines_seen
        elif not recall_buffer:
            committed_line = result.lines_seen
        self._save_checkpoint(
            checkpoint_file,
            {"line": committed_line, "completed": result.stopped_reason == "completed"},
            result,
        )
        self._refresh_executive_summary(records, result)
        if self.config.debug_output:
            self._write_summary(summary_file, result)
        return result

    def profile(self, source_path: Path) -> dict[str, Any]:
        if source_path.suffix.lower() != ".jsonl":
            return {"format": source_path.suffix.lower().lstrip("."), "streaming": False}
        role_paths: dict[str, int] = {}
        content_paths: dict[str, int] = {}
        session_paths: dict[str, int] = {}
        lines_seen = 0
        bad_lines = 0
        with source_path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.strip():
                    continue
                if lines_seen >= self.config.profile_sample_lines:
                    break
                lines_seen += 1
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                for path, value in _walk_json(data):
                    lowered = path.lower()
                    if lowered.endswith(("role", "sender", "author")):
                        role_paths[path] = role_paths.get(path, 0) + 1
                    if lowered.endswith(("content", "text", "message", "markdown")) and isinstance(value, str):
                        content_paths[path] = content_paths.get(path, 0) + 1
                    if lowered.endswith(("session_id", "conversation_id", "chat_id", "id")):
                        session_paths[path] = session_paths.get(path, 0) + 1
        return {
            "format": "jsonl",
            "streaming": True,
            "sample_lines": lines_seen,
            "bad_lines": bad_lines,
            "role_path": _best_path(role_paths),
            "content_path": _best_path(content_paths),
            "session_id_path": _best_path(session_paths),
        }

    def _iter_jsonl_user_messages(self, source_path: Path, start_line: int = 0) -> Iterable[dict[str, Any]]:
        if source_path.suffix.lower() != ".jsonl":
            for index, message in enumerate(_messages_from_text_file(source_path), start=1):
                yield {"line": index, "message": message}
            return
        previous_assistant = ""
        with source_path.open("r", encoding="utf-8-sig") as handle:
            for line_no, line in enumerate(handle, start=1):
                if line_no <= start_line:
                    continue
                if not line.strip():
                    continue
                if self.config.max_lines and line_no > self.config.max_lines:
                    return
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    yield {"line": line_no, "bad_line": 1}
                    continue
                for message in _extract_messages(data):
                    role = _normalize_role(message.get("role", ""))
                    content = str(message.get("content", "")).strip()
                    if not content:
                        continue
                    if role == "assistant":
                        previous_assistant = _truncate(content, 500)
                        continue
                    if role == "user":
                        context_hint = _context_hint(content, previous_assistant)
                        yield {
                            "line": line_no,
                            "message": {
                                "role": "user",
                                "content": content,
                                "context_hint": context_hint,
                                "session_id": str(message.get("session_id") or source_path),
                            },
                        }

    def _agent_rule_candidates(self, root: Path) -> Iterable[CaptureCandidate]:
        self._last_agent_rule_count = 0
        for path in _find_agent_rule_files(root):
            self._last_agent_rule_count += 1
            scope = _scope_for_rule(root, path)
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for index, block in enumerate(_rule_blocks(text), start=1):
                if not block:
                    continue
                yield CaptureCandidate(
                    candidate_id=str(uuid5(NAMESPACE_URL, f"{path}:{index}:{block}")),
                    source=str(path),
                    source_type="agent_rule",
                    scope=scope,
                    preference=block,
                    applies_to=f"When the agent replies or executes tasks within the {scope} scope",
                    evidence_quote=block,
                    confidence="high",
                    session_id=str(path),
                )

    def _should_stop(self, start: float, result: CaptureJobResult) -> bool:
        if self.config.max_candidates and result.candidates_written >= self.config.max_candidates:
            result.stopped_reason = "max_candidates"
            return True
        if self.config.max_minutes and (time.monotonic() - start) >= self.config.max_minutes * 60:
            result.stopped_reason = "max_minutes"
            return True
        return False

    def _load_checkpoint(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save_checkpoint(self, path: Path, update: dict[str, Any], result: CaptureJobResult) -> None:
        current = self._load_checkpoint(path)
        current.update(update)
        current.update(
            {
                "source": result.source,
                "mode": result.mode,
                "store_file": result.store_file,
                "updated_at": now_iso(),
                "candidates_written": result.candidates_written,
                "extracted_records_written": result.extracted_records_written,
                "extract_errors": result.extract_errors,
                "recall_errors": result.recall_errors,
                "preferences_added": result.preferences_added,
                "preferences_merged": result.preferences_merged,
                "preferences_replaced": result.preferences_replaced,
                "preferences_conflicted": result.preferences_conflicted,
                "user_messages_seen": result.user_messages_seen,
                "stopped_reason": result.stopped_reason,
            }
        )
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_summary(self, path: Path, result: CaptureJobResult) -> None:
        text = "\n".join(
            [
                "# Capture Job Summary",
                "",
                f"- Source: `{result.source}`",
                f"- Mode: `{result.mode}`",
                f"- Store file: `{result.store_file}`",
                f"- Lines seen: {result.lines_seen}",
                f"- Bad lines: {result.bad_lines}",
                f"- User messages seen: {result.user_messages_seen}",
                f"- Agent rule files seen: {result.agent_rule_files_seen}",
                f"- Candidates written: {result.candidates_written}",
                f"- Extracted records written: {result.extracted_records_written}",
                f"- Extract errors: {result.extract_errors}",
                f"- Recall errors: {result.recall_errors}",
                f"- Stopped reason: {result.stopped_reason}",
                f"- Candidate file: `{result.candidate_file}`",
                f"- Extracted file: `{result.extracted_file or 'N/A'}`",
                f"- Failure file: `{result.failure_file or 'N/A'}`",
                f"- Checkpoint file: `{result.checkpoint_file}`",
                "",
                "## Profile",
                "",
                "```json",
                json.dumps(result.profile, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
        path.write_text(text, encoding="utf-8")

    def _extract_batch(
        self,
        candidates: list[CaptureCandidate],
        extracted_file: Path,
        result: CaptureJobResult,
        records_store: list[Any],
    ) -> None:
        if not candidates:
            return
        if self._use_model_extract() and self.config.model_batch_size > 0 and len(candidates) > self.config.model_batch_size:
            for index in range(0, len(candidates), self.config.model_batch_size):
                self._extract_batch(candidates[index : index + self.config.model_batch_size], extracted_file, result, records_store)
            return
        backend = self.backend or (build_backend("auto") if self._use_model_extract() else HeuristicBackend())
        candidates, diagnostics = self._classify_candidates_before_extract(candidates, backend, result)
        if not candidates:
            return
        session = session_from_candidates(candidates, f"{result.source}#candidates")
        extracted_records = None
        last_error = ""
        for attempt in range(1, max(1, self.config.max_retries) + 1):
            try:
                extracted_records = backend.extract_preferences(session)
                break
            except Exception as exc:
                last_error = str(exc)
                if attempt >= max(1, self.config.max_retries):
                    if self._use_model_extract() and len(candidates) > 1:
                        midpoint = max(1, len(candidates) // 2)
                        self._extract_batch(candidates[:midpoint], extracted_file, result, records_store)
                        self._extract_batch(candidates[midpoint:], extracted_file, result, records_store)
                        return
                    result.extract_errors += 1
                    self._write_extract_failure(Path(result.failure_file), candidates, last_error)
                    return
                time.sleep(min(2 ** (attempt - 1), 8))
        if self.config.debug_output and self._use_model_extract():
            extracted_file.parent.mkdir(parents=True, exist_ok=True)
        for record in extracted_records or []:
            _attach_candidate_evidence_context(record, candidates)
        for record in refine_records(extracted_records or []):
            self._merge_record(record, result, records_store=records_store)
            if self.config.debug_output and self._use_model_extract():
                data = record.to_dict()
                data["source_candidate_ids"] = [item.candidate_id for item in candidates]
                with extracted_file.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(data, ensure_ascii=False) + "\n")
            result.extracted_records_written += 1

    def _merge_record(self, record: Any, result: CaptureJobResult, records_store: list[Any]) -> None:
        merger = HeuristicBackend()
        if not records_store:
            records_store.append(record)
            result.preferences_added += 1
            self._mark_summary_changed(record)
            return
        decision = merger.merge_decision(record, records_store)
        action = str(decision.get("action", "new"))
        target_id = decision.get("target_id")
        target = next((item for item in records_store if item.id == target_id), None)
        if target and action in {"merge", "replace"} and conflict_likely(record, target):
            action = "conflict"
        if action == "new" or not target:
            records_store.append(record)
            result.preferences_added += 1
            self._mark_summary_changed(record)
        elif action == "replace":
            target.merge_from(record)
            target.status = "active"
            result.preferences_replaced += 1
            self._mark_summary_changed(target)
        elif action == "merge":
            target.add_evidence_from(record)
            result.preferences_merged += 1
            self._mark_summary_changed(target)
        else:
            record.status = "needs_review"
            records_store.append(record)
            result.preferences_conflicted += 1
            self._mark_summary_changed(record)

    def _mark_summary_changed(self, record: Any) -> None:
        if not hasattr(self, "_changed_preference_ids"):
            self._changed_preference_ids = set()
        record_id = str(getattr(record, "id", "") or "")
        if record_id:
            self._changed_preference_ids.add(record_id)

    def _refresh_executive_summary(self, records: list[Any], result: CaptureJobResult) -> None:
        changed_ids = getattr(self, "_changed_preference_ids", set())
        changed_records = [record for record in records if getattr(record, "id", "") in changed_ids]
        backend = self.backend or (build_backend("auto") if self._use_model_extract() else None)
        try:
            update = refresh_executive_summary(
                self.config.store_path,
                changed_records=changed_records,
                backend=backend,
                force=False,
            )
        except Exception as exc:
            result.executive_summary_error = str(exc)
            return
        result.executive_summary_file = update.path
        result.executive_summary_updated = update.updated
        result.executive_summary_error = update.error

    def _use_multi_recall(self) -> bool:
        strategy = self.config.recall_strategy.strip().lower()
        return strategy in {"multi", "semantic", "semantic-recall", "hybrid"} or self.config.mode in {
            "semantic-recall",
            "semantic-extract",
        }

    def _use_model_extract(self) -> bool:
        return self.config.mode in {"recall-extract", "semantic-extract"}

    def _get_recall_engine(self) -> MultiRouteRecallEngine:
        if not self.recall_engine:
            self.recall_engine = MultiRouteRecallEngine(config=RecallConfig.from_env())
        return self.recall_engine

    def _recall_batch(self, inputs: list[RecallInput], result: CaptureJobResult) -> list[CaptureCandidate]:
        try:
            recalled = self._get_recall_engine().recall_batch(inputs)
        except Exception:
            result.recall_errors += 1
            return [
                _candidate_from_user_message(
                    source=item.source,
                    content=item.content,
                    context_hint=item.context_hint,
                    session_id=item.session_id,
                )
                for item in inputs
                if _should_recall(item.content)
            ]
        return [_capture_candidate_from_recall(item) for item in recalled]

    def _behavior_candidates(self) -> list[CaptureCandidate]:
        if not self.recall_engine:
            return []
        return [_capture_candidate_from_recall(item) for item in self.recall_engine.flush_behavior_candidates()]

    def _write_candidate(self, candidate_file: Path, candidate: CaptureCandidate) -> None:
        if not self.config.debug_output:
            return
        candidate_file.parent.mkdir(parents=True, exist_ok=True)
        with candidate_file.open("a", encoding="utf-8") as out:
            out.write(json.dumps(candidate.to_dict(), ensure_ascii=False) + "\n")

    def _write_extract_failure(
        self,
        failure_file: Path,
        candidates: list[CaptureCandidate],
        error: str,
    ) -> None:
        if not self.config.debug_output or not str(failure_file):
            return
        failure_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "failed_at": now_iso(),
            "error": error,
            "candidate_ids": [item.candidate_id for item in candidates],
            "sources": [item.source for item in candidates],
            "candidate_count": len(candidates),
        }
        with failure_file.open("a", encoding="utf-8") as out:
            out.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _classify_candidates_before_extract(
        self,
        candidates: list[CaptureCandidate],
        backend: PreferenceModelBackend,
        result: CaptureJobResult,
    ) -> tuple[list[CaptureCandidate], list[CaptureDiagnostic]]:
        classified: list[CaptureCandidate] = []
        diagnostics: list[CaptureDiagnostic] = []
        for candidate in candidates:
            diagnostic = classify_capture_candidate(
                candidate.preference,
                context_hint=candidate.context_hint,
                backend=backend if self._use_model_extract() else None,
            )
            diagnostics.append(diagnostic)
            candidate.reason_codes = diagnostic.reason_codes
            candidate.signal_type = diagnostic.signal_type
            candidate.diagnostic = diagnostic.to_dict()
            result.classified_candidates += 1
            if "model_failed_fallback" in diagnostic.reason_codes:
                result.model_fallbacks += 1
            if diagnostic.should_extract:
                classified.append(candidate)
        result.filtered_candidates += len(candidates) - len(classified)
        return classified, diagnostics


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


def _safe_job_id(path: Path) -> str:
    base = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in path.stem)
    suffix = uuid5(NAMESPACE_URL, str(path.resolve())).hex[:8]
    return f"{base}-{suffix}"


def _walk_json(data: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, value
            yield from _walk_json(value, path)
    elif isinstance(data, list):
        for index, value in enumerate(data[:5]):
            path = f"{prefix}[{index}]"
            yield path, value
            yield from _walk_json(value, path)


def _best_path(paths: dict[str, int]) -> str:
    if not paths:
        return ""
    return sorted(paths.items(), key=lambda item: item[1], reverse=True)[0][0]


def _find_agent_rule_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.name in AGENT_RULE_NAMES:
            yield path
            continue
        if path.suffix.lower() in AGENT_RULE_SUFFIXES and path.parent.name.casefold() == "agents":
            yield path


def _scope_for_rule(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return "global"
    parts = relative.parts
    if len(parts) <= 1:
        return "global"
    return f"project:{parts[0]}"


def _rule_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cleaned = stripped.lstrip("-*0123456789. )").strip()
        if _should_recall(cleaned):
            blocks.append(cleaned)
    if not blocks and len(text.strip()) <= 2000:
        blocks.append(text.strip())
    return blocks[:200]


def _messages_from_text_file(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return [{"role": "user", "content": text}]


def _extract_messages(data: Any) -> list[dict[str, str]]:
    if isinstance(data, list):
        messages: list[dict[str, str]] = []
        for item in data:
            messages.extend(_extract_messages(item))
        return messages
    if not isinstance(data, dict):
        return []
    if data.get("type") == "message" and isinstance(data.get("message"), dict):
        nested = dict(data["message"])
        for key in ("session_id", "sessionId", "conversation_id", "chat_id"):
            if key in data and key not in nested:
                nested[key] = data[key]
        return _extract_messages(nested)
    # 优先探测内部消息列表，避免 content + messages 的 wrapper 只返回顶层 content
    for key in ("messages", "conversation", "chat", "items"):
        value = data.get(key)
        if isinstance(value, list):
            messages: list[dict[str, str]] = []
            for item in value:
                messages.extend(_extract_messages(item))
            if messages:
                return messages
    direct = _message_from_object(data)
    if direct:
        return [direct]
    return []


def _message_from_object(data: dict[str, Any]) -> dict[str, str] | None:
    role = (
        data.get("role")
        or data.get("sender")
        or data.get("author")
        or data.get("from")
        or data.get("type")
        or data.get("kind")
        or data.get("source")
    )
    session_id = data.get("session_id") or data.get("sessionId") or data.get("conversation_id") or data.get("chat_id") or data.get("id")
    content_value = (
        data.get("content")
        or data.get("text")
        or data.get("message")
        or data.get("display")
        or data.get("markdown")
        or data.get("value")
    )
    content = _text_from_content(content_value)
    if isinstance(content, str) and content.strip():
        # 收紧默认 user：如果没有任何 role 标识且 dict 含有 type/kind/source 等字段，
        # 说明可能是 assistant/tool/system，不应默认 user
        if not role:
            has_structural_hint = any(k in data for k in ("type", "kind", "source"))
            if has_structural_hint:
                return None
        return {
            "role": str(role or "user"),
            "content": content.strip(),
            "session_id": str(session_id or ""),
        }
    return None


def _text_from_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
                continue
            if isinstance(item, dict):
                part_type = str(item.get("type") or "").casefold()
                if part_type and part_type != "text":
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _normalize_role(role: str) -> str:
    lowered = role.strip().casefold()
    if lowered in USER_ROLES:
        return "user"
    if lowered in IGNORED_ROLES:
        return "assistant" if lowered in {"assistant", "model", "ai", "kimi", "codex", "openclaw"} else "ignored"
    return "user" if "user" in lowered or "human" in lowered else "ignored"


def _should_recall(text: str) -> bool:
    return evaluate_capture_candidate(text).is_candidate


def _candidate_from_user_message(source: str, content: str, context_hint: str = "", session_id: str = "") -> CaptureCandidate:
    content = redact_sensitive(content)
    return CaptureCandidate(
        candidate_id=str(uuid5(NAMESPACE_URL, f"{source}:{content}")),
        source=source,
        source_type="user_message",
        scope="global",
        preference=_truncate(content, 1000),
        applies_to="Candidate preference recalled from user history; merge only after model extraction or manual review",
        evidence_quote=_truncate(content, 1000),
        confidence="low",
        session_id=session_id,
        context_hint=context_hint,
    )


def _capture_candidate_from_recall(candidate: RecallCandidate) -> CaptureCandidate:
    return CaptureCandidate(
        candidate_id=candidate.candidate_id,
        source=candidate.source,
        source_type=candidate.source_type,
        scope=candidate.scope,
        preference=candidate.content,
        applies_to=candidate.applies_to,
        evidence_quote=candidate.evidence_quote,
        confidence=candidate.confidence,
        session_id=candidate.session_id,
        context_hint=candidate.context_hint,
        routes=candidate.routes,
        scores=candidate.scores,
        final_score=candidate.final_score,
    )


def _attach_candidate_evidence_context(record: Any, candidates: list[CaptureCandidate]) -> None:
    if not candidates or not hasattr(record, "evidence"):
        return
    for evidence in record.evidence:
        candidate = _matching_candidate_for_evidence(record, evidence, candidates)
        if not candidate:
            continue
        if not getattr(evidence, "source", "") or "#candidates" in str(getattr(evidence, "source", "")):
            evidence.source = candidate.source
        if not getattr(evidence, "session_id", ""):
            evidence.session_id = candidate.session_id or candidate.source


def _matching_candidate_for_evidence(record: Any, evidence: Any, candidates: list[CaptureCandidate]) -> CaptureCandidate | None:
    quote = str(getattr(evidence, "quote", "") or "").casefold()
    preference = str(getattr(record, "preference", "") or "").casefold()
    best = candidates[0] if len(candidates) == 1 else None
    for candidate in candidates:
        candidate_text = f"{candidate.preference} {candidate.evidence_quote}".casefold()
        if quote and quote in candidate_text:
            return candidate
        if preference and preference in candidate_text:
            return candidate
    return best


def _context_hint(content: str, previous_assistant: str) -> str:
    if not previous_assistant:
        return ""
    markers = ("as you said", "that is wrong", "not what I meant", "do", "do not", "can")
    lowered = content.casefold()
    if any(marker in lowered for marker in markers) and len(content) <= 80:
        return previous_assistant
    return ""


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def session_from_candidates(candidates: list[CaptureCandidate], source: str) -> Session:
    return Session(
        source=source,
        session_id=str(uuid5(NAMESPACE_URL, source)),
        messages=[
            SessionMessage(role="user", content=item.preference.strip())
            for item in candidates
        ],
    )
