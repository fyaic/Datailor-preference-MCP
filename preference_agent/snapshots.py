from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SnapshotInfo:
    path: str
    name: str
    size: int
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SnapshotResult:
    created: bool
    path: str = ""
    reason: str = ""
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def snapshot_dir_for(store_path: str | Path, snapshot_dir: str | Path | None = None) -> Path:
    if snapshot_dir:
        return Path(snapshot_dir)
    configured = os.getenv("PREFERENCE_SNAPSHOT_DIR")
    if configured:
        return Path(configured)
    return Path(store_path).parent / ".snapshots"


def list_store_snapshots(
    store_path: str | Path,
    snapshot_dir: str | Path | None = None,
    limit: int | None = None,
) -> list[SnapshotInfo]:
    directory = snapshot_dir_for(store_path, snapshot_dir)
    if not directory.exists():
        return []
    paths = sorted(
        (path for path in directory.glob("*.md") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    if limit is not None:
        paths = paths[: max(0, limit)]
    return [_snapshot_info(path) for path in paths]


def create_store_snapshot(
    store_path: str | Path,
    reason: str = "pre-save",
    snapshot_dir: str | Path | None = None,
    max_snapshots: int | None = None,
) -> SnapshotResult:
    if not _snapshots_enabled():
        return SnapshotResult(created=False, reason=reason, skipped_reason="disabled")
    source = Path(store_path)
    if not source.exists() or not source.is_file():
        return SnapshotResult(created=False, reason=reason, skipped_reason="store_missing")
    content = source.read_bytes()
    if not content.strip():
        return SnapshotResult(created=False, reason=reason, skipped_reason="store_empty")

    directory = snapshot_dir_for(source, snapshot_dir)
    latest = _latest_snapshot_file(directory)
    if latest and latest.read_bytes() == content:
        return SnapshotResult(created=False, path=str(latest), reason=reason, skipped_reason="duplicate")

    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S-%f")
    target = directory / f"{stamp}-{_safe_reason(reason)}.md"
    target.write_bytes(content)
    _prune_snapshots(directory, _max_snapshots(max_snapshots))
    return SnapshotResult(created=True, path=str(target), reason=reason)


def restore_store_snapshot(
    snapshot_path: str | Path,
    store_path: str | Path,
    snapshot_dir: str | Path | None = None,
) -> dict[str, object]:
    snapshot = Path(snapshot_path)
    if not snapshot.exists() or not snapshot.is_file():
        raise FileNotFoundError(str(snapshot))
    target = Path(store_path)
    pre_restore = create_store_snapshot(target, reason="pre-restore", snapshot_dir=snapshot_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot, target)
    return {
        "restored": str(snapshot),
        "store": str(target),
        "pre_restore_snapshot": pre_restore.to_dict(),
    }


def _snapshot_info(path: Path) -> SnapshotInfo:
    stat = path.stat()
    created = datetime.fromtimestamp(stat.st_mtime, timezone.utc).astimezone().isoformat(timespec="seconds")
    return SnapshotInfo(path=str(path), name=path.name, size=stat.st_size, created_at=created)


def _latest_snapshot_file(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    paths = sorted(
        (path for path in directory.glob("*.md") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    return paths[0] if paths else None


def _prune_snapshots(directory: Path, max_snapshots: int) -> None:
    paths = sorted(
        (path for path in directory.glob("*.md") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    for old in paths[max_snapshots:]:
        old.unlink(missing_ok=True)


def _snapshots_enabled() -> bool:
    return os.getenv("PREFERENCE_SNAPSHOT_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _max_snapshots(value: int | None) -> int:
    if value is None:
        raw = os.getenv("PREFERENCE_SNAPSHOT_MAX", "50")
        try:
            value = int(raw)
        except ValueError:
            value = 50
    return max(1, value)


def _safe_reason(reason: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in reason.strip().lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:40] or "snapshot"
