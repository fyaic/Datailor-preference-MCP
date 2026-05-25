from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .capture_runner import CaptureConfig, CaptureRunner
from .models import now_iso
from .session_loader import SUPPORTED_EXTENSIONS


@dataclass
class IncrementalScanResult:
    source: str
    state_file: str
    dry_run: bool
    changed_files: list[str] = field(default_factory=list)
    capture_results: list[dict[str, Any]] = field(default_factory=list)
    scanned_files: int = 0
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_incremental_state() -> Path:
    path = os.getenv("PREFERENCE_INCREMENTAL_STATE")
    if path:
        return Path(path)
    return Path(__file__).resolve().parents[1] / "data" / ".capture-state" / "incremental-scan.json"


def scan_incremental(
    source: str | Path,
    config: CaptureConfig,
    state_file: str | Path | None = None,
    dry_run: bool = False,
    max_files: int = 0,
) -> IncrementalScanResult:
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(str(source_path))
    state_path = Path(state_file) if state_file else default_incremental_state()
    state = _load_state(state_path)
    files = _iter_session_files(source_path)
    result = IncrementalScanResult(source=str(source_path), state_file=str(state_path), dry_run=dry_run)
    runner = CaptureRunner(config)
    changed_state = dict(state.get("files", {}))
    for file_path in files:
        result.scanned_files += 1
        signature = _signature(file_path)
        key = str(file_path.resolve())
        if changed_state.get(key) == signature:
            continue
        result.changed_files.append(str(file_path))
        if not dry_run:
            capture = runner.run(file_path)
            result.capture_results.append(capture.to_dict())
            changed_state[key] = signature
        if max_files and len(result.changed_files) >= max_files:
            break
    if not dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"updated_at": now_iso(), "files": changed_state}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return result


def _iter_session_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    return sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"files": {}}
    return data if isinstance(data, dict) else {"files": {}}
