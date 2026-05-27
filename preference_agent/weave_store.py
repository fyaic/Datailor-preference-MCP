from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import default_weave_dir
from .weave_models import ApplyPlan, InsightRecord, RotSuggestion, WeaveJobResult


@dataclass(frozen=True)
class WeaveJobPaths:
    job_id: str
    job_dir: Path
    status_file: Path
    result_file: Path
    report_file: Path
    draft_insights_file: Path
    rot_suggestions_file: Path
    apply_plan_file: Path


class WeaveJobStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else default_weave_dir()
        self.jobs_dir = self.root / "jobs"

    def new_job_paths(self, job_id: str | None = None) -> WeaveJobPaths:
        job_id = job_id or _new_job_id()
        job_dir = self.jobs_dir / job_id
        return WeaveJobPaths(
            job_id=job_id,
            job_dir=job_dir,
            status_file=job_dir / "status.json",
            result_file=job_dir / "result.json",
            report_file=job_dir / "report.md",
            draft_insights_file=job_dir / "draft-insights.jsonl",
            rot_suggestions_file=job_dir / "rot-suggestions.jsonl",
            apply_plan_file=job_dir / "apply-plan.json",
        )

    def ensure_job_dir(self, paths: WeaveJobPaths) -> None:
        paths.job_dir.mkdir(parents=True, exist_ok=True)

    def write_status(self, paths: WeaveJobPaths, status: str, extra: dict[str, Any] | None = None) -> None:
        self.ensure_job_dir(paths)
        payload = {
            "job_id": paths.job_id,
            "status": status,
            "updated_at": _now_compact(),
            **(extra or {}),
        }
        _write_json(paths.status_file, payload)

    def write_result(self, result: WeaveJobResult) -> None:
        _write_json(Path(result.result_file), result.to_dict())

    def write_insights(self, paths: WeaveJobPaths, insights: list[InsightRecord]) -> None:
        _write_jsonl(paths.draft_insights_file, [item.to_dict() for item in insights])

    def write_rot_suggestions(self, paths: WeaveJobPaths, suggestions: list[RotSuggestion]) -> None:
        _write_jsonl(paths.rot_suggestions_file, [item.to_dict() for item in suggestions])

    def write_apply_plan(self, paths: WeaveJobPaths, plan: ApplyPlan) -> None:
        _write_json(paths.apply_plan_file, plan.to_dict())

    def read_result(self, job_id: str) -> WeaveJobResult:
        path = self.jobs_dir / job_id / "result.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return WeaveJobResult.from_dict(data)

    def read_apply_plan(self, job_id: str) -> ApplyPlan:
        path = self.jobs_dir / job_id / "apply-plan.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return ApplyPlan.from_dict(data)

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.jobs_dir.exists():
            return []
        items: list[dict[str, Any]] = []
        for job_dir in sorted((item for item in self.jobs_dir.iterdir() if item.is_dir()), reverse=True):
            status_file = job_dir / "status.json"
            result_file = job_dir / "result.json"
            status = {}
            if status_file.exists():
                try:
                    status = json.loads(status_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    status = {}
            items.append(
                {
                    "job_id": job_dir.name,
                    "status": status.get("status", "unknown"),
                    "updated_at": status.get("updated_at", ""),
                    "result_file": str(result_file),
                    "report_file": str(job_dir / "report.md"),
                }
            )
            if len(items) >= limit:
                break
        return items

    def latest_result(self) -> WeaveJobResult | None:
        for item in self.list_jobs(limit=1):
            result_file = Path(str(item.get("result_file") or ""))
            if result_file.exists():
                return WeaveJobResult.from_dict(json.loads(result_file.read_text(encoding="utf-8")))
        return None


def _new_job_id() -> str:
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    return f"weave-{timestamp}"


def _now_compact() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")
