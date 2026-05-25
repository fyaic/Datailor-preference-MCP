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
from .models import Session, SessionMessage
from .privacy import redact_sensitive
from .quality import should_recall_user_text
from .recall import MultiRouteRecallEngine, RecallCandidate, RecallConfig, RecallInput
from .refinement import conflict_likely, refine_records
from .store import MarkdownPreferenceStore


SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
AGENT_RULE_NAMES = {"AGENTS.md", "agents.md", "CLAUDE.md", "claude.md"}
AGENT_RULE_SUFFIXES = {".md", ".yaml", ".yml"}
USER_ROLES = {"user", "human", "用户", "我"}
IGNORED_ROLES = {"assistant", "model", "ai", "tool", "system", "助手", "系统", "kimi", "codex", "openclaw"}
RECALL_MARKERS = (
    "以后",
    "默认",
    "每次",
    "总是",
    "我希望",
    "我偏好",
    "我倾向",
    "我喜欢",
    "我不喜欢",
    "不要",
    "别",
    "禁止",
    "必须",
    "一定要",
    "不对",
    "不是这个意思",
    "你应该",
    "下次",
    "回读",
    "沉淀",
    "从现在开始",
    "以后默认",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class CaptureConfig:
    project_root: Path
    candidate_dir: Path
    checkpoint_dir: Path
    store_path: Path = field(default_factory=lambda: Path("data") / "个人偏好.md")
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
        app_root = Path(__file__).resolve().parents[1]
        root = Path(
            project_root
            or os.getenv("PREFERENCE_PROJECT_ROOT")
            or app_root
        )
        data_dir = app_root / "data"
        return cls(
            project_root=root,
            store_path=Path(os.getenv("PREFERENCE_STORE_PATH") or data_dir / "个人偏好.md"),
            candidate_dir=Path(os.getenv("PREFERENCE_CANDIDATE_DIR") or data_dir / ".debug-capture"),
            checkpoint_dir=Path(os.getenv("PREFERENCE_CHECKPOINT_DIR") or data_dir / ".capture-state"),
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
    merge_action: str = "pending"
    context_hint: str = ""
    routes: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
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
    preferences_added: int = 0
    preferences_merged: int = 0
    preferences_replaced: int = 0
    preferences_conflicted: int = 0
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
                    applies_to=f"当 agent 在 {scope} 范围内回复或执行任务时",
                    evidence_quote=block,
                    confidence="high",
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
        elif action == "replace":
            target.merge_from(record)
            target.status = "active"
            result.preferences_replaced += 1
        elif action == "merge":
            target.add_evidence_from(record)
            result.preferences_merged += 1
        else:
            record.status = "needs_review"
            records_store.append(record)
            result.preferences_conflicted += 1

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
    direct = _message_from_object(data)
    if direct:
        return [direct]
    for key in ("messages", "conversation", "chat", "items"):
        value = data.get(key)
        if isinstance(value, list):
            messages: list[dict[str, str]] = []
            for item in value:
                messages.extend(_extract_messages(item))
            if messages:
                return messages
    return []


def _message_from_object(data: dict[str, Any]) -> dict[str, str] | None:
    role = data.get("role") or data.get("sender") or data.get("author") or data.get("from")
    content = (
        data.get("content")
        or data.get("text")
        or data.get("message")
        or data.get("markdown")
        or data.get("value")
    )
    if isinstance(content, list):
        content = "\n".join(str(item) for item in content if str(item).strip())
    if role and isinstance(content, str) and content.strip():
        return {"role": str(role), "content": content.strip()}
    if not role and isinstance(content, str) and content.strip() and set(data.keys()).issubset({"content", "text", "message", "markdown", "value"}):
        return {"role": "user", "content": content.strip()}
    return None


def _normalize_role(role: str) -> str:
    lowered = role.strip().casefold()
    if lowered in USER_ROLES:
        return "user"
    if lowered in IGNORED_ROLES:
        return "assistant" if lowered in {"assistant", "model", "ai", "助手", "kimi", "codex", "openclaw"} else "ignored"
    return "user" if "user" in lowered or "human" in lowered else "ignored"


def _should_recall(text: str) -> bool:
    return should_recall_user_text(text)


def _candidate_from_user_message(source: str, content: str, context_hint: str = "") -> CaptureCandidate:
    content = redact_sensitive(content)
    return CaptureCandidate(
        candidate_id=str(uuid5(NAMESPACE_URL, f"{source}:{content}")),
        source=source,
        source_type="user_message",
        scope="global",
        preference=_truncate(content, 1000),
        applies_to="从用户历史输入召回的候选偏好，需模型精提或人工抽检后合并",
        evidence_quote=_truncate(content, 1000),
        confidence="low",
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
        context_hint=candidate.context_hint,
        routes=candidate.routes,
        scores=candidate.scores,
        final_score=candidate.final_score,
    )


def _context_hint(content: str, previous_assistant: str) -> str:
    if not previous_assistant:
        return ""
    markers = ("按你刚才", "这个不对", "不是这个意思", "要", "不要", "可以")
    if any(marker in content for marker in markers) and len(content) <= 80:
        return previous_assistant
    return ""


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def session_from_candidates(candidates: list[CaptureCandidate], source: str) -> Session:
    return Session(
        source=source,
        session_id=str(uuid5(NAMESPACE_URL, source)),
        messages=[
            SessionMessage(role="user", content=item.preference.strip())
            for item in candidates
        ],
    )
