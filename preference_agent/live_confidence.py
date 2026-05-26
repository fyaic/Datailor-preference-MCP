from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .models import Evidence, PreferenceRecord, infer_evidence_source_type


LIVE_CONFIDENCE_THRESHOLD = 0.40
LIVE_RELEVANCE_THRESHOLD = 0.16


@dataclass(frozen=True)
class LiveConfidence:
    score: float
    label: str
    base: float
    evidence_boost: float
    recency_decay: float
    consistency_penalty: float
    relevance_bonus: float
    relevance_score: float
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["factors"] = {
            "base": self.base,
            "evidence_boost": self.evidence_boost,
            "recency_decay": self.recency_decay,
            "consistency_penalty": self.consistency_penalty,
            "relevance_bonus": self.relevance_bonus,
        }
        return data


def live_confidence(
    record: PreferenceRecord,
    relevance_score: float,
    now: datetime | None = None,
) -> LiveConfidence:
    now = _aware(now or datetime.now(timezone.utc))
    base = _base_confidence(record)
    evidence_boost = _evidence_boost(record.evidence)
    recency_decay = _recency_decay(record.updated_at, now)
    consistency_penalty = _consistency_penalty(record)
    relevance_bonus = _relevance_bonus(relevance_score)
    raw = base + evidence_boost - recency_decay - consistency_penalty + relevance_bonus
    score = round(max(0.0, min(1.0, raw)), 4)
    return LiveConfidence(
        score=score,
        label=label_for_live_confidence(score),
        base=round(base, 4),
        evidence_boost=round(evidence_boost, 4),
        recency_decay=round(recency_decay, 4),
        consistency_penalty=round(consistency_penalty, 4),
        relevance_bonus=round(relevance_bonus, 4),
        relevance_score=round(max(0.0, min(1.0, relevance_score)), 4),
        reasons=_factor_reasons(
            record=record,
            evidence_boost=evidence_boost,
            recency_decay=recency_decay,
            consistency_penalty=consistency_penalty,
            relevance_bonus=relevance_bonus,
        ),
    )


def should_inject_live_confidence(live: LiveConfidence) -> bool:
    return live.score >= LIVE_CONFIDENCE_THRESHOLD and live.relevance_score >= LIVE_RELEVANCE_THRESHOLD


def label_for_live_confidence(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= LIVE_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


def _base_confidence(record: PreferenceRecord) -> float:
    label = str(record.confidence or "").casefold()
    base = {"high": 0.85, "medium": 0.60, "low": 0.35}.get(label, 0.50)
    if str(record.status or "").casefold() in {"needs_review", "pending", "observed"}:
        return min(base, 0.35)
    if str(record.status or "").casefold() in {"rejected", "archived", "deleted"}:
        return 0.0
    return base


def _evidence_boost(evidence: list[Evidence]) -> float:
    total = 0.0
    for item in evidence:
        total += 0.05 * _evidence_weight(item)
    return min(0.30, total)


def _evidence_weight(item: Evidence) -> float:
    source_type = str(getattr(item, "source_type", "") or "").casefold()
    if not source_type:
        source_type = _infer_source_type(item)
    weights = {
        "user_confirm": 1.5,
        "confirmation": 1.5,
        "ui_confirm": 1.5,
        "user_explicit": 1.0,
        "user_message": 1.0,
        "multi_route_recall": 1.0,
        "context_inferred": 0.6,
        "agent_rule": 0.6,
        "action_signal": 0.4,
        "behavior_pattern": 0.4,
        "behavior": 0.4,
    }
    return weights.get(source_type, 0.5)


def _infer_source_type(item: Evidence) -> str:
    return infer_evidence_source_type(str(getattr(item, "source", "")), str(getattr(item, "role", "")))


def _recency_decay(updated_at: str, now: datetime) -> float:
    updated = _parse_datetime(updated_at)
    if not updated:
        return 0.0
    days = max(0, (now - updated).days)
    if days <= 7:
        return 0.0
    if days <= 30:
        return 0.10
    if days <= 90:
        return 0.20
    if days <= 180:
        return 0.30
    return 0.40


def _consistency_penalty(record: PreferenceRecord) -> float:
    status = str(record.status or "").casefold()
    if status in {"needs_review", "pending", "observed"}:
        return 0.30
    if record.conflict_notes:
        return 0.15
    return 0.0


def _relevance_bonus(relevance_score: float) -> float:
    if relevance_score >= 0.35:
        return 0.30
    if relevance_score >= 0.25:
        return 0.15
    if relevance_score >= LIVE_RELEVANCE_THRESHOLD:
        return 0.05
    return -0.20


def _factor_reasons(
    record: PreferenceRecord,
    evidence_boost: float,
    recency_decay: float,
    consistency_penalty: float,
    relevance_bonus: float,
) -> list[str]:
    reasons: list[str] = []
    if evidence_boost > 0:
        reasons.append("evidence_boost")
    if recency_decay > 0:
        reasons.append("recency_decay")
    if consistency_penalty > 0:
        reasons.append("consistency_penalty")
    if relevance_bonus > 0:
        reasons.append("context_relevant")
    elif relevance_bonus < 0:
        reasons.append("context_weak")
    if str(record.status or "").casefold() != "active":
        reasons.append(f"status_{record.status}")
    return reasons


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
