from __future__ import annotations

from pathlib import Path
import threading
import traceback
from typing import Any

from .fitting import run_fitting
from .fitting_auto import auto_apply_fitting_plan
from .fitting_report import render_fitting_report
from .fitting_store import FittingJobStore
from .fitting_trigger import (
    FittingTriggerDecision,
    fitting_settings_path,
    mark_fitting_reviewed,
    normalize_mode,
    record_session_and_decide,
    read_mode_settings,
    update_fitting_state,
)
from .injection import sync_injection_artifacts
from .injection_log import log_injection_event
from .models import now_iso


_BACKGROUND_LOCK = threading.Lock()
_BACKGROUND_RUNNING: set[str] = set()


def maybe_run_fitting_after_capture(
    store_path: str | Path,
    changed_records: int,
    agent: str,
    session_id: str = "",
    settings_path: str | Path | None = None,
    fitting_dir: str | Path | None = None,
    dry_run: bool = False,
    background: bool = False,
) -> dict[str, Any]:
    decision = record_session_and_decide(
        store_path=store_path,
        changed_records=changed_records,
        settings_path=settings_path,
        fitting_dir=fitting_dir,
    )
    if not decision.should_trigger or dry_run:
        payload = {"ok": True, "triggered": False, "decision": decision.to_dict()}
        _log_trigger(store_path, agent, session_id, payload)
        return payload
    if background:
        return queue_fitting_for_mode(
            store_path=store_path,
            mode=decision.mode,
            agent=agent,
            session_id=session_id,
            settings_path=settings_path,
            fitting_dir=fitting_dir,
            max_files=decision.config.max_files,
            trigger=decision,
        )
    return run_fitting_for_mode(
        store_path=store_path,
        mode=decision.mode,
        agent=agent,
        session_id=session_id,
        settings_path=settings_path,
        fitting_dir=fitting_dir,
        max_files=decision.config.max_files,
        trigger=decision,
    )


def queue_fitting_for_mode(
    store_path: str | Path,
    mode: str,
    agent: str,
    session_id: str = "",
    settings_path: str | Path | None = None,
    fitting_dir: str | Path | None = None,
    source: str | Path | None = None,
    instructions: str = "",
    instructions_file: str | Path | None = None,
    max_files: int = 0,
    trigger: FittingTriggerDecision | None = None,
) -> dict[str, Any]:
    key = f"{Path(fitting_dir) if fitting_dir else 'default'}:{Path(store_path)}"
    with _BACKGROUND_LOCK:
        if key in _BACKGROUND_RUNNING:
            payload = {
                "ok": True,
                "triggered": False,
                "queued": False,
                "reason": "fitting_already_queued",
                "decision": trigger.to_dict() if trigger else None,
            }
            _log_trigger(store_path, agent, session_id, payload)
            return payload
        _BACKGROUND_RUNNING.add(key)

    def _run() -> None:
        try:
            run_fitting_for_mode(
                store_path=store_path,
                mode=mode,
                agent=agent,
                session_id=session_id,
                settings_path=settings_path,
                fitting_dir=fitting_dir,
                source=source,
                instructions=instructions,
                instructions_file=instructions_file,
                max_files=max_files,
                trigger=trigger,
            )
        except Exception as exc:
            log_injection_event(
                store_path=store_path,
                hook="fitting_background_failed",
                agent=agent,
                session_id=session_id,
                source="fitting",
                reason=str(exc),
                extra={"traceback": traceback.format_exc()},
            )
        finally:
            with _BACKGROUND_LOCK:
                _BACKGROUND_RUNNING.discard(key)

    thread = threading.Thread(target=_run, name="datailor-fitting", daemon=False)
    thread.start()
    payload = {
        "ok": True,
        "triggered": True,
        "queued": True,
        "background": True,
        "mode": normalize_mode(mode),
        "decision": trigger.to_dict() if trigger else None,
    }
    log_injection_event(
        store_path=store_path,
        hook="fitting_queued",
        agent=agent,
        session_id=session_id,
        source="fitting",
        reason="queued_background_fitting",
        extra=payload,
    )
    return payload


def run_fitting_for_mode(
    store_path: str | Path,
    mode: str,
    agent: str,
    session_id: str = "",
    settings_path: str | Path | None = None,
    fitting_dir: str | Path | None = None,
    source: str | Path | None = None,
    instructions: str = "",
    instructions_file: str | Path | None = None,
    max_files: int = 0,
    trigger: FittingTriggerDecision | None = None,
) -> dict[str, Any]:
    mode = normalize_mode(mode)
    effective_settings_path = fitting_settings_path(settings_path, fitting_dir)
    review = mode == "curate"
    result = run_fitting(
        store_path=store_path,
        instructions=instructions,
        instructions_file=instructions_file,
        source=source,
        agent=agent,
        fitting_dir=fitting_dir,
        dry_run=False,
        review=review,
        max_files=max_files,
    )
    auto_apply = None
    pending_plan = None
    if result.status == "completed" and mode == "auto":
        auto_apply = auto_apply_fitting_plan(
            job_id=result.job_id,
            plan=result.apply_plan,
            store_path=str(store_path),
            fitting_dir=str(fitting_dir) if fitting_dir else None,
        )
        if auto_apply.get("applied"):
            sync_injection_artifacts(store_path)
        _update_state_after_run(
            settings_path=effective_settings_path,
            result=result.to_dict(),
            pending_plan_job_id=None,
            auto_applied_count=len(auto_apply.get("applied") or []),
        )
        hook = "auto_fitting_completed"
    elif result.status == "completed" and mode == "curate":
        pending_plan = _mark_pending_review(result, fitting_dir=fitting_dir)
        _update_state_after_run(
            settings_path=effective_settings_path,
            result=pending_plan,
            pending_plan_job_id=result.job_id if _pending_changes(pending_plan) else None,
            auto_applied_count=0,
        )
        hook = "fitting_pending_review" if _pending_changes(pending_plan) else "fitting_completed_no_changes"
    else:
        _update_state_after_run(
            settings_path=effective_settings_path,
            result=result.to_dict(),
            pending_plan_job_id=None,
            auto_applied_count=0,
            reset_counters=False,
        )
        hook = "fitting_failed"
    payload = {
        "ok": result.status != "failed",
        "triggered": True,
        "mode": mode,
        "job_id": result.job_id,
        "status": pending_plan.get("status") if pending_plan else result.status,
        "result": pending_plan or result.to_dict(),
        "auto_apply": auto_apply,
        "decision": trigger.to_dict() if trigger else None,
    }
    result_data = pending_plan or result.to_dict()
    log_extra = _terminal_log_extra(
        mode=mode,
        result=result_data,
        status=str(payload.get("status") or ""),
        auto_apply=auto_apply,
    )
    log_injection_event(
        store_path=store_path,
        hook=hook,
        agent=agent,
        session_id=session_id,
        source="fitting",
        reason=hook,
        extra=log_extra,
    )
    return payload


def mark_fitting_plan_reviewed(
    job_id: str,
    settings_path: str | Path | None = None,
    fitting_dir: str | Path | None = None,
    accepted: int = 0,
    rejected: bool = False,
) -> dict[str, Any]:
    return mark_fitting_reviewed(
        job_id=job_id,
        path=fitting_settings_path(settings_path, fitting_dir),
        accepted=accepted,
        rejected=rejected,
    )


def _mark_pending_review(result: Any, fitting_dir: str | Path | None = None) -> dict[str, Any]:
    data = result.to_dict()
    if not _pending_changes(data):
        return data
    result.status = "pending_review"
    store = FittingJobStore(fitting_dir)
    paths = store.paths_for_job(result.job_id)
    paths.report_file.write_text(render_fitting_report(result), encoding="utf-8")
    store.write_result(result)
    store.write_status(
        paths,
        "pending_review",
        {
            "report_file": result.report_file,
            "result_file": result.result_file,
            "pending_changes": len(result.apply_plan.changes) if result.apply_plan else 0,
        },
    )
    return result.to_dict()


def _update_state_after_run(
    settings_path: str | Path | None,
    result: dict[str, Any],
    pending_plan_job_id: str | None,
    auto_applied_count: int,
    reset_counters: bool = True,
) -> None:
    settings = read_mode_settings(settings_path)
    fitting = dict(settings.get("fitting") or {})
    fitting["last_fitting_at"] = now_iso()
    fitting["last_job_id"] = result.get("job_id", "")
    fitting["pending_plan_job_id"] = pending_plan_job_id
    fitting["auto_applied_count"] = int(fitting.get("auto_applied_count") or 0) + int(auto_applied_count or 0)
    fitting["last_result"] = {
        "job_id": result.get("job_id", ""),
        "status": result.get("status", ""),
        "stats": result.get("stats", {}),
        "report_file": result.get("report_file", ""),
        "updated_at": now_iso(),
    }
    if reset_counters:
        fitting["records_since_last"] = 0
        fitting["total_sessions_since_last"] = 0
    settings["fitting"] = fitting
    update_fitting_state(settings_path, **fitting)


def _pending_changes(result: dict[str, Any]) -> int:
    plan = result.get("apply_plan") if isinstance(result, dict) else {}
    changes = plan.get("changes") if isinstance(plan, dict) else []
    return len(changes) if isinstance(changes, list) else 0


def _terminal_log_extra(
    mode: str,
    result: dict[str, Any],
    status: str,
    auto_apply: dict[str, Any] | None,
) -> dict[str, Any]:
    pending_changes = _pending_changes(result)
    applied_count = len(auto_apply.get("applied") or []) if isinstance(auto_apply, dict) else _applied_changes(result)
    return {
        "mode": mode,
        "job_id": str(result.get("job_id") or ""),
        "status": status,
        "report_file": str(result.get("report_file") or ""),
        "result_file": str(result.get("result_file") or ""),
        "pending_changes": pending_changes,
        "applied_count": applied_count,
        "no_benefit_reason": _no_benefit_reason(
            result=result,
            mode=mode,
            status=status,
            pending_changes=pending_changes,
            applied_count=applied_count,
        ),
        "auto_applied": bool(auto_apply and auto_apply.get("applied")),
        "stats": result.get("stats", {}),
    }


def _applied_changes(result: dict[str, Any]) -> int:
    plan = result.get("apply_plan") if isinstance(result, dict) else {}
    changes = plan.get("changes") if isinstance(plan, dict) else []
    if not isinstance(changes, list):
        return 0
    return sum(1 for change in changes if isinstance(change, dict) and str(change.get("status") or "").casefold() == "applied")


def _no_benefit_reason(
    result: dict[str, Any],
    mode: str,
    status: str,
    pending_changes: int,
    applied_count: int,
) -> str:
    if status == "failed":
        return "fitting_failed"
    if pending_changes == 0 and applied_count == 0:
        return "no_pending_changes"
    if mode == "auto" and applied_count == 0:
        return "no_high_confidence_auto_apply"
    return ""


def _log_trigger(store_path: str | Path, agent: str, session_id: str, payload: dict[str, Any]) -> None:
    log_injection_event(
        store_path=store_path,
        hook="fitting_trigger_check",
        agent=agent,
        session_id=session_id,
        source="fitting",
        reason=str((payload.get("decision") or {}).get("reason") or ""),
        extra=payload,
    )
