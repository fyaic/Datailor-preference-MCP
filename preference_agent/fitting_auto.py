from __future__ import annotations

from typing import Any

from .fitting import apply_fitting_plan
from .fitting_models import ApplyPlan


CONFIDENCE_SCORES = {
    "high": 0.9,
    "medium": 0.7,
    "low": 0.4,
}


def auto_apply_change_ids(plan: ApplyPlan | None, threshold: float = 0.8) -> list[str]:
    if not plan:
        return []
    accepted: list[str] = []
    for change in plan.changes:
        if change.type != "add_preference" or change.risk != "low":
            continue
        insight = change.payload.get("insight") if isinstance(change.payload, dict) else {}
        if not isinstance(insight, dict):
            continue
        if str(insight.get("kind") or "") != "preference":
            continue
        if _confidence_score(insight) >= threshold:
            accepted.append(change.change_id)
    return accepted


def auto_apply_fitting_plan(
    job_id: str,
    plan: ApplyPlan | None,
    store_path: str,
    fitting_dir: str | None = None,
    threshold: float = 0.8,
) -> dict[str, Any]:
    accepted = auto_apply_change_ids(plan, threshold=threshold)
    if not accepted:
        return {"ok": True, "job_id": job_id, "auto_applied": [], "applied": [], "reason": "no_high_confidence_preferences"}
    result = apply_fitting_plan(
        job_id=job_id,
        accepted_change_ids=accepted,
        store_path=store_path,
        fitting_dir=fitting_dir,
        activate_added=True,
    )
    result["auto_applied"] = accepted
    return result


def _confidence_score(insight: dict[str, Any]) -> float:
    raw = insight.get("confidence_score")
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    label = str(insight.get("confidence") or "").casefold()
    score = CONFIDENCE_SCORES.get(label, 0.0)
    evidence = insight.get("evidence")
    if isinstance(evidence, list) and len(evidence) >= 3:
        score = max(score, 0.85)
    return score
