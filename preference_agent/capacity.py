from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .backends import semantic_similarity
from .models import PreferenceRecord, now_iso, unique_strings
from .refinement import conflict_likely, quality_score


DEFAULT_MAX_PREFERENCES = 50
MAX_EVIDENCE_PER_RECORD = 8
COUNTED_STATUSES = {"active", "needs_review", "pending", "low", "draft"}
ARCHIVED_STATUSES = {"archived", "rejected", "deleted"}


@dataclass
class CapReviewCandidate:
    id: str
    title: str
    preference: str
    status: str
    confidence: str
    score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapResult:
    records: list[PreferenceRecord]
    limit: int
    before_count: int
    after_count: int
    merged: list[dict[str, str]] = field(default_factory=list)
    review_required: bool = False
    review_candidates: list[CapReviewCandidate] = field(default_factory=list)
    overflow_records: list[PreferenceRecord] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    artifact_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "before_count": self.before_count,
            "after_count": self.after_count,
            "merged": self.merged,
            "review_required": self.review_required,
            "review_candidates": [item.to_dict() for item in self.review_candidates],
            "overflow_record_count": len(self.overflow_records),
            "overflow_record_ids": [record.id for record in self.overflow_records],
            "dropped": self.dropped,
            "artifact_file": self.artifact_file,
        }


def preference_limit() -> int:
    raw = os.getenv("PREFERENCE_STORE_MAX_RECORDS", "").strip()
    if not raw:
        return DEFAULT_MAX_PREFERENCES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PREFERENCES
    return max(1, value)


def count_cap_records(records: list[PreferenceRecord]) -> int:
    return sum(1 for record in records if is_counted_record(record))


def is_counted_record(record: PreferenceRecord) -> bool:
    status = str(record.status or "active").casefold()
    return status not in ARCHIVED_STATUSES


def enforce_preference_cap(
    records: list[PreferenceRecord],
    limit: int | None = None,
    mode: str = "review-first",
) -> CapResult:
    cap = preference_limit() if limit is None else max(1, int(limit))
    before = count_cap_records(records)
    merged_records, merged = merge_duplicate_records(records)
    counted = [record for record in merged_records if is_counted_record(record)]
    uncounted = [record for record in merged_records if not is_counted_record(record)]
    if len(counted) <= cap:
        final = [*counted, *uncounted]
        return CapResult(
            records=final,
            limit=cap,
            before_count=before,
            after_count=len(counted),
            merged=merged,
        )

    ranked = quality_rank_records(counted)
    keep = ranked[:cap]
    overflow = ranked[cap:]
    review_candidates = [
        CapReviewCandidate(
            id=record.id,
            title=record.title,
            preference=record.preference,
            status=record.status,
            confidence=record.confidence,
            score=record_value_score(record),
            reason=_overflow_reason(mode),
        )
        for record in overflow
    ]
    return CapResult(
        records=[*keep, *uncounted],
        limit=cap,
        before_count=before,
        after_count=len(keep),
        merged=merged,
        review_required=True,
        review_candidates=review_candidates,
        overflow_records=overflow,
    )


def merge_duplicate_records(records: list[PreferenceRecord]) -> tuple[list[PreferenceRecord], list[dict[str, str]]]:
    kept: list[PreferenceRecord] = []
    merged: list[dict[str, str]] = []
    for record in records:
        if not is_counted_record(record):
            kept.append(record)
            continue
        target = _find_merge_target(record, kept)
        if target is None:
            kept.append(record)
            continue
        keeper = target
        merged_id = record.id
        if record_value_score(record) > record_value_score(keeper):
            _merge_into(record, keeper)
            merged_id = keeper.id
            kept[kept.index(keeper)] = record
            keeper = record
        else:
            _merge_into(keeper, record)
        merged.append({"target_id": keeper.id, "merged_id": merged_id})
    return kept, merged


def quality_rank_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    return sorted(
        records,
        key=lambda record: (
            record_value_score(record),
            _status_weight(record),
            _confidence_weight(record.confidence),
            record.updated_at,
            record.created_at,
            record.id,
        ),
        reverse=True,
    )


def record_value_score(record: PreferenceRecord) -> float:
    score = quality_score(record).overall
    score += _status_weight(record) * 0.08
    score += min(0.12, len(record.evidence) * 0.02)
    if record.conflict_notes:
        score -= 0.18
    if _is_generic(record):
        score -= 0.15
    return round(max(0.0, min(1.5, score)), 4)


def cap_summary(result: CapResult | None) -> dict[str, Any]:
    if result is None:
        return {}
    return result.to_dict()


def write_cap_review_artifact(store_path: str | Path, result: CapResult) -> str:
    if not result.review_required and not result.merged:
        return ""
    path = _review_path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": now_iso(),
        "store": str(store_path),
        **result.to_dict(),
    }
    payload.pop("artifact_file", None)
    if result.overflow_records:
        payload["overflow_records"] = [record.to_dict() for record in result.overflow_records]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    result.artifact_file = str(path)
    return str(path)


def _find_merge_target(record: PreferenceRecord, records: list[PreferenceRecord]) -> PreferenceRecord | None:
    record_key = _canonical_key(record)
    best: tuple[float, PreferenceRecord | None] = (0.0, None)
    for existing in records:
        if not is_counted_record(existing):
            continue
        if conflict_likely(record, existing):
            continue
        if record_key and record_key == _canonical_key(existing):
            return existing
        similarity = min(
            semantic_similarity(str(record.preference or ""), str(existing.preference or "")),
            semantic_similarity(_statement(record), _statement(existing)),
        )
        if similarity > best[0]:
            best = (similarity, existing)
    score, target = best
    if target and score >= 0.98:
        return target
    return None


def _merge_into(target: PreferenceRecord, duplicate: PreferenceRecord) -> None:
    if record_value_score(duplicate) > record_value_score(target):
        target.title = duplicate.title or target.title
        target.summary = duplicate.summary or target.summary
        target.applies_to = duplicate.applies_to or target.applies_to
        target.preference = duplicate.preference or target.preference
        target.confidence = _higher_confidence(target.confidence, duplicate.confidence)
    target.triggers = unique_strings([*target.triggers, *duplicate.triggers], limit=12)
    target.exceptions = unique_strings([*target.exceptions, *duplicate.exceptions], limit=12)
    target.conflict_notes = unique_strings([*target.conflict_notes, *duplicate.conflict_notes], limit=12)
    target.evidence = _merge_evidence(target.evidence, duplicate.evidence)
    target.touch()


def _merge_evidence(left: list[Any], right: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[tuple[str, str, str]] = set()
    for item in [*left, *right]:
        key = (
            str(getattr(item, "source", "")),
            str(getattr(item, "session_id", "")),
            str(getattr(item, "quote", "")),
            str(getattr(item, "role", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= MAX_EVIDENCE_PER_RECORD:
            break
    return merged


def _canonical_key(record: PreferenceRecord) -> str:
    text = _statement(record)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.casefold())
    return text


def _record_text(record: PreferenceRecord) -> str:
    return " ".join([record.title, record.summary, record.applies_to, record.preference, *record.triggers])


def _statement(record: PreferenceRecord) -> str:
    preference = " ".join(str(record.preference or "").split()).strip().rstrip(".")
    applies_to = " ".join(str(record.applies_to or "").split()).strip().rstrip(".")
    if preference.casefold().startswith("when "):
        return preference
    if applies_to.casefold().startswith("when "):
        return f"{applies_to}, {preference}"
    return preference


def _status_weight(record: PreferenceRecord) -> float:
    status = str(record.status or "active").casefold()
    if status == "active":
        return 1.0
    if status in {"needs_review", "pending"}:
        return 0.55
    if status in {"low", "draft"}:
        return 0.35
    return 0.0


def _confidence_weight(confidence: str) -> float:
    return {"high": 1.0, "medium": 0.65, "low": 0.25}.get(str(confidence or "").casefold(), 0.45)


def _higher_confidence(left: str, right: str) -> str:
    return left if _confidence_weight(left) >= _confidence_weight(right) else right


def _is_generic(record: PreferenceRecord) -> bool:
    text = _record_text(record).casefold()
    generic_markers = (
        "when the agent is about to reply",
        "when the agent chooses response style",
        "general preference",
    )
    return any(marker in text for marker in generic_markers)


def _overflow_reason(mode: str) -> str:
    if mode == "safe":
        return "Store safety cap kept this preference out of the canonical store; review and consolidate before restoring it."
    return "Preference cap exceeded; review, merge, or generalize this candidate before adding it back."


def _review_path(store_path: str | Path) -> Path:
    path = Path(store_path)
    return path.with_name(f"{path.stem}.cap-review.jsonl")
