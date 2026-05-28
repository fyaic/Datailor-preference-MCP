from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .executive_summary import refresh_executive_summary
from .models import Evidence, PreferenceRecord
from .privacy import redact_sensitive
from .store import MarkdownPreferenceStore


@dataclass(frozen=True)
class PreferenceStoreUpdate:
    ok: bool
    action: str
    changed: bool
    removed: bool
    store: str
    preference_id: str = ""
    current_id: str = ""
    previous_text: str = ""
    current_text: str = ""
    error: str = ""
    executive_summary: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def apply_preference_feedback(
    store_path: str | Path,
    feedback_type: str,
    user_feedback: str = "",
    preference_id: str = "",
    preference_text: str = "",
    refresh_summary: bool = True,
) -> PreferenceStoreUpdate:
    action = feedback_type.strip().lower()
    store = MarkdownPreferenceStore(store_path)
    store.ensure()

    if action == "usage":
        return PreferenceStoreUpdate(ok=True, action=action, changed=False, removed=False, store=str(store.path))
    if action not in {"confirmation", "rejection", "correction"}:
        return PreferenceStoreUpdate(
            ok=False,
            action=action,
            changed=False,
            removed=False,
            store=str(store.path),
            error="unsupported_feedback_type",
        )

    records = store.load()
    index = _find_record_index(records, preference_id=preference_id, preference_text=preference_text)
    if index is None:
        return PreferenceStoreUpdate(
            ok=False,
            action=action,
            changed=False,
            removed=False,
            store=str(store.path),
            preference_id=preference_id.strip(),
            previous_text=preference_text.strip(),
            error="preference_not_found",
        )

    record = records[index]
    previous_text = _statement(record)
    previous_id = record.id
    previous_status = record.status
    previous_confidence = record.confidence

    if action == "confirmation":
        changed = True
        record.status = "active"
        record.confidence = "high"
        record.evidence.append(
            Evidence(
                source="ui_feedback:confirmation",
                quote=user_feedback.strip() or previous_text,
                role="user",
                source_type="user_confirm",
            )
        )
        record.touch()
        records[index] = record
        if changed:
            store.save(records)
        summary = _refresh_summary(store.path, [record], force=False, enabled=refresh_summary) if changed else None
        current = _find_saved_record(store.load() if changed else records, previous_id, previous_text)
        return PreferenceStoreUpdate(
            ok=True,
            action=action,
            changed=changed,
            removed=False,
            store=str(store.path),
            preference_id=previous_id,
            current_id=current.id if current else previous_id,
            previous_text=previous_text,
            current_text=_statement(current or record),
            executive_summary=summary,
        )

    if action == "rejection":
        del records[index]
        store.save(records)
        summary = _refresh_summary(store.path, [], force=True, enabled=refresh_summary)
        return PreferenceStoreUpdate(
            ok=True,
            action=action,
            changed=True,
            removed=True,
            store=str(store.path),
            preference_id=previous_id,
            previous_text=previous_text,
            current_text="",
            executive_summary=summary,
        )

    corrected_text = _clean_correction(user_feedback)
    if not corrected_text:
        return PreferenceStoreUpdate(
            ok=False,
            action=action,
            changed=False,
            removed=False,
            store=str(store.path),
            preference_id=previous_id,
            previous_text=previous_text,
            error="empty_correction",
        )

    record.title = corrected_text[:48]
    record.summary = corrected_text
    record.applies_to = corrected_text
    record.preference = corrected_text
    record.status = "active"
    record.confidence = "high"
    record.evidence.append(
        Evidence(
            source="ui_feedback:correction",
            quote=corrected_text,
            role="user",
            source_type="user_confirm",
        )
    )
    record.version += 1
    record.touch()
    records[index] = record
    store.save(records)
    saved_records = store.load()
    current = _find_saved_record(saved_records, previous_id, corrected_text) or record
    summary = _refresh_summary(store.path, [current], force=False, enabled=refresh_summary)
    return PreferenceStoreUpdate(
        ok=True,
        action=action,
        changed=(
            _normalize(previous_text) != _normalize(corrected_text)
            or previous_id != current.id
            or previous_status != "active"
            or previous_confidence != "high"
        ),
        removed=False,
        store=str(store.path),
        preference_id=previous_id,
        current_id=current.id,
        previous_text=previous_text,
        current_text=_statement(current),
        executive_summary=summary,
    )


def _find_record_index(
    records: list[PreferenceRecord],
    preference_id: str = "",
    preference_text: str = "",
) -> int | None:
    preference_id = preference_id.strip()
    if preference_id:
        for index, record in enumerate(records):
            if record.id == preference_id:
                return index
    normalized_text = _normalize(preference_text)
    if normalized_text:
        for index, record in enumerate(records):
            if _normalize(_statement(record)) == normalized_text:
                return index
    return None


def _find_saved_record(
    records: list[PreferenceRecord],
    preference_id: str = "",
    preference_text: str = "",
) -> PreferenceRecord | None:
    index = _find_record_index(records, preference_id=preference_id, preference_text=preference_text)
    return records[index] if index is not None else None


def _refresh_summary(
    store_path: Path,
    changed_records: list[PreferenceRecord],
    force: bool,
    enabled: bool,
) -> dict[str, object] | None:
    if not enabled:
        return None
    return refresh_executive_summary(store_path, changed_records=changed_records, force=force).to_dict()


def _statement(record: PreferenceRecord) -> str:
    return redact_sensitive(" ".join((record.preference or record.summary or record.title).split()).strip())


def _clean_correction(text: str) -> str:
    return redact_sensitive(" ".join(str(text).split()).strip())


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).strip().rstrip(".!?").casefold()
