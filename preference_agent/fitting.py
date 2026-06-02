from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .agent_discovery import auto_discover_sources
from .backends import semantic_similarity
from .capacity import count_cap_records, enforce_preference_cap, preference_limit, quality_rank_records, write_cap_review_artifact
from .feedback import feedback_report
from .live_confidence import live_confidence
from .models import Evidence, PreferenceRecord
from .quality import has_guidance_terms, has_stable_signal, looks_like_one_off_task, should_recall_user_text
from .refinement import conflict_likely
from .session_loader import load_sessions
from .store import MarkdownPreferenceStore
from .fitting_models import ApplyChange, ApplyPlan, InsightRecord, RotSuggestion, FittingInstruction, FittingJobResult
from .fitting_report import render_fitting_report
from .fitting_store import FittingJobStore


@dataclass(frozen=True)
class FittingInput:
    source: str
    text: str
    role: str = "user"
    session_id: str = ""


def run_fitting(
    store_path: str | Path,
    instructions: str = "",
    instructions_file: str | Path | None = None,
    source: str | Path | None = None,
    agent: str = "agent",
    fitting_dir: str | Path | None = None,
    dry_run: bool = False,
    review: bool = True,
    max_files: int = 0,
) -> FittingJobResult:
    instruction_text = _instruction_text(instructions, instructions_file)
    instruction = FittingInstruction.from_text(instruction_text, source="file" if instructions_file else "cli")
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    job_store = FittingJobStore(fitting_dir)
    paths = job_store.new_job_paths()
    if not dry_run:
        job_store.write_status(paths, "running", {"agent": agent, "review": review, "dry_run": dry_run})

    inputs: list[FittingInput] = []
    ignored_inputs: list[dict[str, str]] = []
    try:
        records = store.load()
        inputs, source_errors, auto_source_count = _collect_inputs(
            records=records,
            source=source,
            max_files=max_files,
            agent=agent,
        )
        filtered_inputs, instruction_ignored = _apply_instruction_filter(inputs, instruction)
        ignored_inputs = [*source_errors, *instruction_ignored]
        insights = extract_insights(filtered_inputs)
        rot_suggestions = analyze_memory_rot(records, feedback_report())
        apply_plan = build_apply_plan(paths.job_id, insights, rot_suggestions)
        stats = _stats(inputs, ignored_inputs, insights, rot_suggestions, auto_source_count)
        result = FittingJobResult(
            job_id=paths.job_id,
            status="completed",
            store_file=str(store.path),
            job_dir=str(paths.job_dir),
            report_file=str(paths.report_file),
            result_file=str(paths.result_file),
            instructions=instruction,
            insights=insights,
            rot_suggestions=rot_suggestions,
            apply_plan=apply_plan,
            stats=stats,
            inputs=_input_labels(inputs),
            ignored_inputs=ignored_inputs,
            next_commands=[
                f"datailor fitting-show {paths.job_id}",
                f"datailor fitting-apply {paths.job_id} --accept <change-id>",
            ],
        )
        _write_job_artifacts(job_store, paths, result, dry_run=dry_run)
        return result
    except Exception as exc:
        result = FittingJobResult(
            job_id=paths.job_id,
            status="failed",
            store_file=str(store.path),
            job_dir=str(paths.job_dir),
            report_file=str(paths.report_file),
            result_file=str(paths.result_file),
            instructions=instruction,
            stats={"inputs_seen": len(inputs), "source_errors": len(ignored_inputs)},
            inputs=_input_labels(inputs),
            ignored_inputs=ignored_inputs,
            error=str(exc),
            next_commands=["Review the Fitting report and rerun `datailor fitting` after fixing the error."],
        )
        _write_job_artifacts(job_store, paths, result, dry_run=dry_run)
        if not dry_run:
            job_store.write_status(
                paths,
                "failed",
                {"error": str(exc), "report_file": str(paths.report_file), "result_file": str(paths.result_file)},
            )
        return result


def extract_insights(inputs: list[FittingInput]) -> list[InsightRecord]:
    insights: list[InsightRecord] = []
    for item in inputs:
        for sentence in _sentences(item.text):
            cleaned = _clean(sentence)
            if not _looks_like_durable_insight(cleaned):
                continue
            kind = _classify_kind(cleaned)
            insight = _insight_from_text(kind, cleaned, item)
            if not _has_similar_insight(insight, insights):
                insights.append(insight)
    return insights


def analyze_memory_rot(records: list[PreferenceRecord], feedback: dict[str, Any] | None = None) -> list[RotSuggestion]:
    suggestions: list[RotSuggestion] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    feedback_by_key = {
        str(item.get("preference")): item
        for item in (feedback or {}).get("by_preference", [])
        if item.get("preference")
    }
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            pair_key = tuple(sorted((left.id, right.id)))
            left_text = _record_text(left)
            right_text = _record_text(right)
            similarity = semantic_similarity(left_text, right_text)
            if conflict_likely(left, right):
                key = (*pair_key, "conflict")
                if key not in seen_pairs:
                    suggestions.append(
                        RotSuggestion(
                            type="conflict",
                            target_ids=[left.id, right.id],
                            risk="high",
                            reason="Two preferences are semantically close but express opposite or inconsistent behavior requirements, so they need manual review.",
                            proposed_action="manual_review",
                            evidence=[_statement(left), _statement(right)],
                        )
                    )
                    seen_pairs.add(key)
            elif similarity >= 0.88 or _normalize(left_text) == _normalize(right_text):
                key = (*pair_key, "duplicate")
                if key not in seen_pairs:
                    suggestions.append(
                        RotSuggestion(
                            type="duplicate",
                            target_ids=[left.id, right.id],
                            risk="low",
                            reason="Two preferences are highly similar and can be consolidated after their evidence is merged.",
                            proposed_action="merge",
                            evidence=[_statement(left), _statement(right)],
                        )
                    )
                    seen_pairs.add(key)
            elif similarity >= 0.68:
                key = (*pair_key, "overlap")
                if key not in seen_pairs:
                    suggestions.append(
                        RotSuggestion(
                            type="overlap",
                            target_ids=[left.id, right.id],
                            risk="medium",
                            reason="Two preferences cover similar scenarios and may need clearer boundaries or a merged scope.",
                            proposed_action="manual_review",
                            evidence=[_statement(left), _statement(right)],
                        )
                    )
                    seen_pairs.add(key)

    for record in records:
        feedback_bucket = feedback_by_key.get(record.id) or feedback_by_key.get(_statement(record)) or {}
        if int(feedback_bucket.get("rejection", 0)) or int(feedback_bucket.get("correction", 0)) >= 3:
            suggestions.append(
                RotSuggestion(
                    type="negative_feedback",
                    target_ids=[record.id],
                    risk="high",
                    reason="This preference was rejected or corrected multiple times, so it should move back to review.",
                    proposed_action="mark_needs_review",
                    evidence=[_statement(record)],
                )
            )
            continue
        live = live_confidence(record, relevance_score=0.16)
        if record.status == "active" and live.score < 0.40 and live.recency_decay > 0:
            suggestions.append(
                RotSuggestion(
                    type="stale",
                    target_ids=[record.id],
                    risk="medium",
                    reason="This active preference is stale and has low live confidence, so it should be downgraded or reviewed.",
                    proposed_action="downgrade",
                    evidence=[_statement(record)],
                )
            )
    cap = preference_limit()
    if count_cap_records(records) > cap:
        overflow = quality_rank_records([record for record in records if str(record.status or "").casefold() not in {"archived", "rejected", "deleted"}])[cap:]
        for record in overflow:
            suggestions.append(
                RotSuggestion(
                    type="archive_candidate",
                    target_ids=[record.id],
                    risk="medium",
                    reason=f"The canonical preference store exceeds the {cap}-record cap; this lower-value preference should be reviewed, merged, or archived.",
                    proposed_action="mark_needs_review",
                    evidence=[_statement(record)],
                )
            )
    return suggestions[:100]


def build_apply_plan(job_id: str, insights: list[InsightRecord], suggestions: list[RotSuggestion]) -> ApplyPlan:
    changes: list[ApplyChange] = []
    for insight in insights:
        if insight.kind != "preference":
            continue
        changes.append(
            ApplyChange(
                change_id=f"chg-{len(changes) + 1:03d}",
                type="add_preference",
                target_store="preference",
                record_id=insight.id,
                risk="low",
                payload={"insight": insight.to_dict()},
            )
        )
    for suggestion in suggestions:
        if suggestion.proposed_action not in {"archive", "downgrade", "mark_needs_review", "merge"}:
            continue
        changes.append(
            ApplyChange(
                change_id=f"chg-{len(changes) + 1:03d}",
                type=suggestion.proposed_action,
                target_store="preference",
                record_id=suggestion.id,
                risk=suggestion.risk,
                payload={"suggestion": suggestion.to_dict()},
            )
        )
    return ApplyPlan(job_id=job_id, changes=changes)


def apply_fitting_plan(
    job_id: str,
    accepted_change_ids: list[str],
    store_path: str | Path,
    fitting_dir: str | Path | None = None,
    activate_added: bool = False,
) -> dict[str, Any]:
    if not accepted_change_ids:
        return {"ok": False, "error": "accepted_change_ids_required", "job_id": job_id, "applied": []}
    job_store = FittingJobStore(fitting_dir)
    result = job_store.read_result(job_id)
    plan = job_store.read_apply_plan(job_id)
    accepted = set(accepted_change_ids)
    known = {change.change_id for change in plan.changes}
    unknown = sorted(accepted - known)
    if unknown:
        return {"ok": False, "error": "unknown_change_ids", "job_id": job_id, "unknown_change_ids": unknown, "applied": []}
    insights = {item.id: item for item in result.insights}
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    records = store.load()
    applied: list[dict[str, str]] = []
    for change in plan.changes:
        if change.change_id not in accepted:
            continue
        if change.status != "pending":
            continue
        if change.type == "add_preference":
            insight_data = change.payload.get("insight") if isinstance(change.payload, dict) else {}
            insight = insights.get(change.record_id) or (
                InsightRecord.from_dict(insight_data) if isinstance(insight_data, dict) else None
            )
            if not insight:
                continue
            record = insight.to_preference_record()
            if activate_added:
                record.status = "active"
            records.append(record)
            change.status = "applied"
            applied.append({"change_id": change.change_id, "action": "add_preference"})
            continue
        suggestion_data = change.payload.get("suggestion") if isinstance(change.payload, dict) else {}
        suggestion = RotSuggestion.from_dict(suggestion_data) if isinstance(suggestion_data, dict) else None
        if not suggestion:
            continue
        if change.type == "merge":
            merged = _merge_suggestion_targets(records, suggestion.target_ids)
            if merged:
                change.status = "applied"
                applied.append({"change_id": change.change_id, "action": "merge", "target_id": merged})
            continue
        for target_id in suggestion.target_ids:
            record = next((item for item in records if item.id == target_id), None)
            if not record:
                continue
            if change.type == "archive":
                record.status = "archived"
            elif change.type == "downgrade":
                record.confidence = "low"
                record.status = "needs_review"
            elif change.type == "mark_needs_review":
                record.status = "needs_review"
            record.touch()
            change.status = "applied"
            applied.append({"change_id": change.change_id, "action": change.type, "target_id": target_id})
    cap_result = enforce_preference_cap(records, mode="review-first")
    records = cap_result.records
    cap_artifact = ""
    if cap_result.review_required or cap_result.merged:
        cap_artifact = write_cap_review_artifact(store.path, cap_result)
    if applied:
        store.save(records)
        _write_review_state(job_store, result, plan)
    readback = MarkdownPreferenceStore(store.path).load()
    remaining = _pending_change_ids(plan)
    return {
        "ok": True,
        "job_id": job_id,
        "store": str(store.path),
        "applied": applied,
        "remaining_pending": len(remaining),
        "pending_change_ids": remaining,
        "readback_records": len(readback),
        "readback_ok": bool(readback or not records),
        "cap": cap_result.to_dict(),
        "cap_review_file": cap_artifact,
    }


def _merge_suggestion_targets(records: list[PreferenceRecord], target_ids: list[str]) -> str:
    targets = [record for record in records if record.id in set(target_ids)]
    if len(targets) < 2:
        return targets[0].id if targets else ""
    keeper = quality_rank_records(targets)[0]
    for record in targets:
        if record.id == keeper.id:
            continue
        keeper.add_evidence_from(record)
        keeper.conflict_notes = list(dict.fromkeys([*keeper.conflict_notes, *record.conflict_notes]))
        record.status = "archived"
        record.touch()
    keeper.touch()
    return keeper.id


def reject_fitting_plan(job_id: str, fitting_dir: str | Path | None = None) -> dict[str, Any]:
    job_store = FittingJobStore(fitting_dir)
    result = job_store.read_result(job_id)
    plan = job_store.read_apply_plan(job_id)
    rejected: list[str] = []
    for change in plan.changes:
        if change.status == "pending":
            change.status = "rejected"
            rejected.append(change.change_id)
    _write_review_state(job_store, result, plan, force_reviewed=True)
    return {
        "ok": True,
        "job_id": job_id,
        "rejected": rejected,
        "remaining_pending": 0,
        "pending_change_ids": [],
    }


def get_fitting_job(job_id: str, fitting_dir: str | Path | None = None) -> dict[str, Any]:
    return FittingJobStore(fitting_dir).read_result(job_id).to_dict()


def list_fitting_jobs(fitting_dir: str | Path | None = None, limit: int = 20) -> dict[str, Any]:
    return {"ok": True, "jobs": FittingJobStore(fitting_dir).list_jobs(limit=limit)}


def latest_fitting(fitting_dir: str | Path | None = None) -> dict[str, Any]:
    result = FittingJobStore(fitting_dir).latest_result()
    return {"ok": True, "job": result.to_dict() if result else None}


def _pending_change_ids(plan: ApplyPlan) -> list[str]:
    return [change.change_id for change in plan.changes if change.status == "pending"]


def _write_review_state(
    job_store: FittingJobStore,
    result: FittingJobResult,
    plan: ApplyPlan,
    force_reviewed: bool = False,
) -> None:
    paths = job_store.paths_for_job(result.job_id)
    pending = _pending_change_ids(plan)
    if force_reviewed or (result.status == "pending_review" and not pending):
        result.status = "reviewed"
    result.apply_plan = plan
    job_store.write_apply_plan(paths, plan)
    job_store.write_result(result)
    job_store.write_status(
        paths,
        result.status,
        {
            "report_file": result.report_file,
            "result_file": result.result_file,
            "pending_changes": len(pending),
        },
    )


def _instruction_text(instructions: str, instructions_file: str | Path | None) -> str:
    parts = [str(instructions or "").strip()]
    if instructions_file:
        parts.append(Path(instructions_file).read_text(encoding="utf-8-sig").strip())
    return "\n".join(part for part in parts if part)


def _write_job_artifacts(
    job_store: FittingJobStore,
    paths: Any,
    result: FittingJobResult,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    report = render_fitting_report(result)
    job_store.ensure_job_dir(paths)
    paths.report_file.write_text(report, encoding="utf-8")
    job_store.write_insights(paths, result.insights)
    job_store.write_rot_suggestions(paths, result.rot_suggestions)
    if result.apply_plan:
        job_store.write_apply_plan(paths, result.apply_plan)
    job_store.write_result(result)
    if result.status == "completed":
        job_store.write_status(
            paths,
            "completed",
            {"report_file": str(paths.report_file), "result_file": str(paths.result_file)},
        )


def _collect_inputs(
    records: list[PreferenceRecord],
    source: str | Path | None,
    max_files: int,
    agent: str,
) -> tuple[list[FittingInput], list[dict[str, str]], int]:
    inputs: list[FittingInput] = []
    skipped: list[dict[str, str]] = []
    auto_sources = 0
    source_paths = [str(source)] if source else [item.path for item in auto_discover_sources(agent_hint=agent)]
    auto_sources = 0 if source else len(source_paths)
    files_seen: set[str] = set()
    stop = False
    for source_path in source_paths:
        try:
            sessions = load_sessions(source_path)
        except Exception as exc:
            skipped.append({"source": str(source_path), "reason": f"source_read_error: {exc}"})
            continue
        for session in sessions:
            file_key = str(session.source).split("#", 1)[0]
            if file_key not in files_seen:
                if max_files and len(files_seen) >= max_files:
                    stop = True
                    break
                files_seen.add(file_key)
            for message in session.messages:
                if message.role == "user":
                    inputs.append(FittingInput(source=session.source, session_id=session.session_id, text=message.content, role=message.role))
        if stop:
            break
    for record in records:
        inputs.append(FittingInput(source=f"store:{record.id}", text=_record_text(record), role="preference"))
    return inputs, skipped, auto_sources


def _apply_instruction_filter(
    inputs: list[FittingInput],
    instruction: FittingInstruction,
) -> tuple[list[FittingInput], list[dict[str, str]]]:
    kept: list[FittingInput] = []
    ignored: list[dict[str, str]] = []
    for item in inputs:
        text = item.text
        if instruction.should_ignore(text) or _ignored_by_one_off_instruction(text, instruction):
            ignored.append({"source": item.source, "reason": "ignored_by_instruction"})
            continue
        if instruction.focus and not instruction.matches_focus(text):
            ignored.append({"source": item.source, "reason": "outside_focus"})
            continue
        kept.append(item)
    return kept, ignored


def _ignored_by_one_off_instruction(text: str, instruction: FittingInstruction) -> bool:
    ignore_blob = " ".join(instruction.ignore).casefold()
    if not ignore_blob:
        return False
    wants_one_off_filter = any(term in ignore_blob for term in ("one-off", "install command", "temporary command"))
    if not wants_one_off_filter:
        return False
    lowered = text.casefold()
    return looks_like_one_off_task(text) or any(term in lowered for term in ("pipx install", "git clone", "python -m pip install"))


def _looks_like_durable_insight(text: str) -> bool:
    if len(text) < 8 or len(text) > 500:
        return False
    if looks_like_one_off_task(text) and not has_stable_signal(text):
        return False
    return (
        should_recall_user_text(text)
        or _looks_like_workflow(text)
        or _looks_like_error_pattern(text)
        or _looks_like_tool_quirk(text)
        or _looks_like_recovery_strategy(text)
        or _looks_like_handoff_pattern(text)
        or _looks_like_app_usage(text)
        or _looks_like_personal_habit(text)
        or _looks_like_project_convention(text)
        or (has_stable_signal(text) and has_guidance_terms(text))
    )


def _classify_kind(text: str) -> str:
    if _looks_like_recovery_strategy(text):
        return "recovery_strategy"
    if _looks_like_error_pattern(text):
        return "error_pattern"
    if _looks_like_handoff_pattern(text):
        return "handoff_pattern"
    if _looks_like_project_convention(text):
        return "project_convention"
    if _looks_like_app_usage(text):
        return "app_usage"
    if _looks_like_tool_quirk(text):
        return "tool_quirk"
    if re.search(r"(process|step|workflow|long task|Harness|PRD|self-check|acceptance)", text, re.I):
        return "workflow"
    if _looks_like_personal_habit(text):
        return "personal_habit"
    if should_recall_user_text(text):
        return "preference"
    if _looks_like_workflow(text):
        return "workflow"
    return "preference"


def _looks_like_workflow(text: str) -> bool:
    return bool(
        re.search(r"(first|before).{0,24}(then|after|next)", text, re.I)
        or re.search(r"(process|step|workflow|long task|Harness|PRD|self-check|acceptance)", text, re.I)
    )


def _looks_like_error_pattern(text: str) -> bool:
    return bool(re.search(r"(often|repeatedly|always).{0,80}(wrong|fail|miss|forget)|error pattern|common mistake", text, re.I))


def _looks_like_tool_quirk(text: str) -> bool:
    return bool(re.search(r"(PowerShell|MCP|CLI|pipx|terminal|tool|Obsidian|Linear|Kimi|Codex|Windows|UI|quirk)", text, re.I))


def _looks_like_recovery_strategy(text: str) -> bool:
    return bool(
        re.search(r"(failed|blocked|fail|error|exception|cannot|unable).{0,36}(recover|retry|rollback|fix|fallback|rerun|degrade)", text, re.I)
        or re.search(r"(failed|error|exception|cannot|unable).{0,36}(explain|record|report).{0,18}(reason|blocker|limit)", text, re.I)
        or re.search(r"(recovery strategy|after failure|after an error|when blocked|when unable to complete)", text, re.I)
    )


def _looks_like_handoff_pattern(text: str) -> bool:
    return bool(
        re.search(r"(done|completed|phase|wrap up|deliver|report|summary|handoff).{0,40}(Linear|issue|update|ask|explain|report|comment)", text, re.I)
        or re.search(r"(update\s*Linear\s*issue|Linear\s*issue\s*update)", text, re.I)
    )


def _looks_like_app_usage(text: str) -> bool:
    return bool(
        re.search(r"(use|open|manage|record|write).{0,30}(Obsidian|Linear|Slack|Lark|Notion|Excel|GitHub|Kimi|Codex|Claude)", text, re.I)
    )


def _looks_like_personal_habit(text: str) -> bool:
    return bool(re.search(r"(my habit|user habit|I usually|I generally|I like|I prefer|by habit)", text, re.I))


def _looks_like_project_convention(text: str) -> bool:
    return bool(
        re.search(r"(project|repo|README|AGENTS|docs|product name|naming|directory|branch).{0,40}(consistent|must|should|convention|standard|Datailor|Fitting)", text, re.I)
        or re.search(r"(product name is\s*Datailor|use\s*Fitting\s*consistently|avoid old names)", text, re.I)
    )


def _insight_from_text(kind: str, text: str, item: FittingInput) -> InsightRecord:
    title_prefix = {
        "preference": "Preference",
        "workflow": "Workflow",
        "error_pattern": "Error pattern",
        "tool_quirk": "Tool quirk",
        "recovery_strategy": "Recovery strategy",
        "handoff_pattern": "Handoff pattern",
        "app_usage": "App usage",
        "personal_habit": "Personal habit",
        "project_convention": "Project convention",
    }.get(kind, "Pattern")
    applies_to = {
        "preference": "agent replies, task execution, or confirmation decisions",
        "workflow": "execution flow for similar tasks",
        "error_pattern": "error recovery and pre-delivery checks",
        "tool_quirk": "tool calls, terminal usage, MCP, or UI operations",
        "recovery_strategy": "failure recovery, retries, or fallback handling",
        "handoff_pattern": "phase delivery, wrap-up, and task handoff",
        "app_usage": "when using common applications or external systems",
        "personal_habit": "personal collaboration and expression habits",
        "project_convention": "current project, repository, or product documentation",
    }.get(kind, "long-term behavior pattern")
    return InsightRecord(
        id=f"insight-{uuid5(NAMESPACE_URL, f'{kind}:{item.source}:{text}').hex[:8]}",
        kind=kind,
        title=f"{title_prefix}: {_short(text, 36)}",
        summary=_short(text, 160),
        guidance=text,
        applies_to=applies_to,
        triggers=_infer_triggers(kind, text),
        exceptions=["When the user explicitly gives a conflicting instruction, follow the current task requirement."],
        confidence="medium",
        status="draft",
        evidence=[
            Evidence(
                source=item.source,
                session_id=item.session_id,
                quote=_short(text, 500),
                role=item.role,
                source_type="user_explicit" if item.role == "user" else "context_inferred",
            )
        ],
    )


def _has_similar_insight(candidate: InsightRecord, existing: list[InsightRecord]) -> bool:
    for item in existing:
        if item.kind != candidate.kind:
            continue
        if semantic_similarity(item.guidance, candidate.guidance) >= 0.90:
            item.evidence.extend(candidate.evidence)
            if item.kind == "preference" and len(item.evidence) >= 3:
                item.confidence = "high"
            return True
    return False


def _stats(
    inputs: list[FittingInput],
    ignored: list[dict[str, str]],
    insights: list[InsightRecord],
    suggestions: list[RotSuggestion],
    auto_source_count: int = 0,
) -> dict[str, int]:
    return {
        "inputs_seen": len(inputs),
        "auto_discovered_sources": auto_source_count,
        "sources_skipped": sum(1 for item in ignored if str(item.get("reason", "")).startswith("source_read_error")),
        "insights_proposed": len(insights),
        "preferences_proposed": sum(1 for item in insights if item.kind == "preference"),
        "rot_suggestions": len(suggestions),
        "conflicts": sum(1 for item in suggestions if item.type == "conflict"),
        "ignored_by_instruction": sum(
            1 for item in ignored if item.get("reason") in {"ignored_by_instruction", "outside_focus"}
        ),
    }


def _input_labels(inputs: list[FittingInput]) -> list[str]:
    labels: list[str] = []
    for item in inputs:
        label = item.source
        if label not in labels:
            labels.append(label)
    return labels[:100]


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[.!?;\n]+", text) if part.strip()]
    return parts or [text.strip()]


def _clean(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _short(text: str, limit: int) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _infer_triggers(kind: str, text: str) -> list[str]:
    if kind == "workflow":
        return ["similar task", "long-running task", "before delivery"]
    if kind == "error_pattern":
        return ["similar error appears again", "pre-delivery self-check"]
    if kind == "tool_quirk":
        return ["when using related tools"]
    if kind == "recovery_strategy":
        return ["when a failure or error occurs", "when recovering a task"]
    if kind == "handoff_pattern":
        return ["after phase completion", "during delivery or wrap-up"]
    if kind == "app_usage":
        return ["when using related applications"]
    if kind == "personal_habit":
        return ["collaboration-style tasks"]
    if kind == "project_convention":
        return ["within the current project or repository"]
    triggers = []
    for marker in ("reply", "test", "document", "read back", "Linear", "English", "UI"):
        if marker.casefold() in text.casefold():
            triggers.append(marker)
    return triggers[:5]


def _record_text(record: PreferenceRecord) -> str:
    return " ".join(part for part in [record.title, record.summary, record.applies_to, record.preference, *record.triggers] if part)


def _statement(record: PreferenceRecord) -> str:
    return _clean(record.preference or record.summary or record.title)


def _normalize(text: str) -> str:
    return re.sub(r"\W+", "", text.casefold())
