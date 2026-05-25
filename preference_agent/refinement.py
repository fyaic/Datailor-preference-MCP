from __future__ import annotations

from dataclasses import dataclass

from .backends import semantic_similarity
from .models import PreferenceRecord
from .quality import record_has_guidance_value


@dataclass
class QualityScore:
    confidence: float
    clarity: float
    evidence: float
    overall: float


NEGATION_WORDS = ("不要", "别", "不必", "无需", "避免", "禁止", "不能", "不喜欢")
DETAIL_WORDS = ("详细", "展开", "完整", "解释", "长文")
CONCISE_WORDS = ("简洁", "简短", "短一点", "不要长文", "别啰嗦", "少废话")


def refine_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    normalized: list[PreferenceRecord] = []
    for record in records:
        if not _has_content(record):
            continue
        _normalize_applies_to(record)
        if not record_has_guidance_value(record):
            continue
        record.confidence = _confidence_label(quality_score(record).overall, record.confidence)
        match = _find_normalization_target(record, normalized)
        if match:
            match.add_evidence_from(record)
            if len(record.preference) < len(match.preference) or record.confidence == "high":
                match.preference = record.preference
                match.summary = record.summary or record.preference[:120]
                match.title = record.title or match.title
                match.touch()
            continue
        normalized.append(record)
    return normalized


def conflict_likely(candidate: PreferenceRecord, existing: PreferenceRecord) -> bool:
    candidate_text = _record_text(candidate)
    existing_text = _record_text(existing)
    if semantic_similarity(candidate_text, existing_text) < 0.24:
        return False
    if _has_negation(candidate_text) != _has_negation(existing_text):
        return True
    if _has_any(candidate_text, DETAIL_WORDS) and _has_any(existing_text, CONCISE_WORDS):
        return True
    if _has_any(candidate_text, CONCISE_WORDS) and _has_any(existing_text, DETAIL_WORDS):
        return True
    return False


def quality_score(record: PreferenceRecord) -> QualityScore:
    confidence = {"high": 0.9, "medium": 0.65, "low": 0.38}.get(record.confidence, 0.55)
    text = _record_text(record)
    clarity = 0.25
    if len(record.preference) >= 8:
        clarity += 0.25
    if record.applies_to:
        clarity += 0.20
    if record.triggers:
        clarity += 0.10
    if any(word in text for word in ("当", "如果", "默认", "不要", "必须", "优先", "以后")):
        clarity += 0.15
    evidence = min(1.0, 0.35 + 0.2 * len(record.evidence))
    overall = round(0.45 * confidence + 0.35 * min(1.0, clarity) + 0.20 * evidence, 4)
    return QualityScore(confidence=confidence, clarity=min(1.0, clarity), evidence=evidence, overall=overall)


def _find_normalization_target(
    candidate: PreferenceRecord,
    records: list[PreferenceRecord],
) -> PreferenceRecord | None:
    best_score = 0.0
    best_record: PreferenceRecord | None = None
    for record in records:
        score = semantic_similarity(_record_text(candidate), _record_text(record))
        if score > best_score:
            best_score = score
            best_record = record
    return best_record if best_record and best_score >= 0.82 and not conflict_likely(candidate, best_record) else None


def _has_content(record: PreferenceRecord) -> bool:
    return bool(record.preference.strip() or record.summary.strip() or record.title.strip())


def _record_text(record: PreferenceRecord) -> str:
    return " ".join([record.title, record.summary, record.applies_to, record.preference, *record.triggers])


def _normalize_applies_to(record: PreferenceRecord) -> None:
    applies_to = " ".join(record.applies_to.split()).strip()
    if not applies_to:
        return
    if applies_to.startswith("当"):
        record.applies_to = applies_to
        return
    if applies_to.endswith("时"):
        record.applies_to = f"当{applies_to}"
    else:
        record.applies_to = f"当{applies_to}时"


def _has_negation(text: str) -> bool:
    return _has_any(text, NEGATION_WORDS)


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(word.casefold() in lowered for word in words)


def _confidence_label(score: float, current: str) -> str:
    if current == "high" or score >= 0.82:
        return "high"
    if current == "medium" or score >= 0.58:
        return "medium"
    return "low"
