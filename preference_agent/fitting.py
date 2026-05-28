from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .backends import semantic_similarity
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
    records = store.load()
    job_store = FittingJobStore(fitting_dir)
    paths = job_store.new_job_paths()
    job_store.write_status(paths, "running", {"agent": agent, "review": review, "dry_run": dry_run})

    inputs = _collect_inputs(records=records, source=source, max_files=max_files)
    filtered_inputs, ignored_inputs = _apply_instruction_filter(inputs, instruction)
    insights = extract_insights(filtered_inputs)
    rot_suggestions = analyze_memory_rot(records, feedback_report())
    apply_plan = build_apply_plan(paths.job_id, insights, rot_suggestions)
    stats = _stats(inputs, ignored_inputs, insights, rot_suggestions)
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
    report = render_fitting_report(result)
    if not dry_run:
        job_store.ensure_job_dir(paths)
        paths.report_file.write_text(report, encoding="utf-8")
        job_store.write_insights(paths, insights)
        job_store.write_rot_suggestions(paths, rot_suggestions)
        job_store.write_apply_plan(paths, apply_plan)
        job_store.write_result(result)
        job_store.write_status(paths, "completed", {"report_file": str(paths.report_file), "result_file": str(paths.result_file)})
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
                            reason="两条偏好语义相近但表达了相反或不一致的行为要求，需要人工审查。",
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
                            reason="两条偏好高度相似，可以合并证据后保留一条。",
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
                            reason="两条偏好覆盖相近场景，可能需要改写边界或合并适用范围。",
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
                    reason="这条偏好被用户拒绝或多次纠正，建议转入待审。",
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
                    reason="这条偏好长期未更新且动态置信度偏低，建议降权或转入待审。",
                    proposed_action="downgrade",
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
) -> dict[str, Any]:
    if not accepted_change_ids:
        return {"ok": False, "error": "accepted_change_ids_required", "job_id": job_id, "applied": []}
    job_store = FittingJobStore(fitting_dir)
    result = job_store.read_result(job_id)
    plan = job_store.read_apply_plan(job_id)
    accepted = set(accepted_change_ids)
    insights = {item.id: item for item in result.insights}
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    records = store.load()
    applied: list[dict[str, str]] = []
    for change in plan.changes:
        if change.change_id not in accepted:
            continue
        if change.type == "add_preference":
            insight_data = change.payload.get("insight") if isinstance(change.payload, dict) else {}
            insight = insights.get(change.record_id) or (
                InsightRecord.from_dict(insight_data) if isinstance(insight_data, dict) else None
            )
            if not insight:
                continue
            records.append(insight.to_preference_record())
            applied.append({"change_id": change.change_id, "action": "add_preference"})
            continue
        suggestion_data = change.payload.get("suggestion") if isinstance(change.payload, dict) else {}
        suggestion = RotSuggestion.from_dict(suggestion_data) if isinstance(suggestion_data, dict) else None
        if not suggestion:
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
            elif change.type == "merge":
                record.status = "needs_review"
            record.touch()
            applied.append({"change_id": change.change_id, "action": change.type, "target_id": target_id})
    if applied:
        store.save(records)
    readback = MarkdownPreferenceStore(store.path).load()
    return {
        "ok": True,
        "job_id": job_id,
        "store": str(store.path),
        "applied": applied,
        "readback_records": len(readback),
        "readback_ok": bool(readback or not records),
    }


def get_fitting_job(job_id: str, fitting_dir: str | Path | None = None) -> dict[str, Any]:
    return FittingJobStore(fitting_dir).read_result(job_id).to_dict()


def list_fitting_jobs(fitting_dir: str | Path | None = None, limit: int = 20) -> dict[str, Any]:
    return {"ok": True, "jobs": FittingJobStore(fitting_dir).list_jobs(limit=limit)}


def latest_fitting(fitting_dir: str | Path | None = None) -> dict[str, Any]:
    result = FittingJobStore(fitting_dir).latest_result()
    return {"ok": True, "job": result.to_dict() if result else None}


def _instruction_text(instructions: str, instructions_file: str | Path | None) -> str:
    parts = [str(instructions or "").strip()]
    if instructions_file:
        parts.append(Path(instructions_file).read_text(encoding="utf-8-sig").strip())
    return "\n".join(part for part in parts if part)


def _collect_inputs(records: list[PreferenceRecord], source: str | Path | None, max_files: int) -> list[FittingInput]:
    inputs: list[FittingInput] = []
    if source:
        sessions = load_sessions(source)
        files_seen: set[str] = set()
        for session in sessions:
            file_key = str(session.source).split("#", 1)[0]
            files_seen.add(file_key)
            if max_files and len(files_seen) > max_files:
                break
            for message in session.messages:
                if message.role == "user":
                    inputs.append(FittingInput(source=session.source, text=message.content, role=message.role))
    for record in records:
        inputs.append(FittingInput(source=f"store:{record.id}", text=_record_text(record), role="preference"))
    return inputs


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
    wants_one_off_filter = any(term in ignore_blob for term in ("one-off", "install command", "安装命令", "一次性"))
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
        or (has_stable_signal(text) and has_guidance_terms(text))
    )


def _classify_kind(text: str) -> str:
    if _looks_like_error_pattern(text):
        return "error_pattern"
    if _looks_like_tool_quirk(text):
        return "tool_quirk"
    if re.search(r"(流程|步骤|工作流|长任务|Harness|PRD|自检|验收)", text, re.I):
        return "workflow"
    if should_recall_user_text(text):
        return "preference"
    if _looks_like_workflow(text):
        return "workflow"
    return "preference"


def _looks_like_workflow(text: str) -> bool:
    return bool(
        re.search(r"(先).{0,18}(再|然后|之后)", text)
        or re.search(r"(流程|步骤|工作流|长任务|Harness|PRD|自检|验收)", text, re.I)
    )


def _looks_like_error_pattern(text: str) -> bool:
    return bool(re.search(r"(经常|反复|总是).{0,24}(错|失败|漏|忘|没)|error pattern|常犯|恢复策略", text, re.I))


def _looks_like_tool_quirk(text: str) -> bool:
    return bool(re.search(r"(PowerShell|MCP|CLI|pipx|终端|工具|Obsidian|Linear|Kimi|Codex|Windows|UI|quirk)", text, re.I))


def _insight_from_text(kind: str, text: str, item: FittingInput) -> InsightRecord:
    title_prefix = {
        "preference": "偏好",
        "workflow": "工作流",
        "error_pattern": "错误模式",
        "tool_quirk": "工具特性",
    }.get(kind, "模式")
    applies_to = {
        "preference": "agent 回复、执行任务或做确认决策",
        "workflow": "同类任务执行流程",
        "error_pattern": "错误恢复与交付前检查",
        "tool_quirk": "工具调用、终端、MCP 或 UI 操作",
    }.get(kind, "长期行为模式")
    return InsightRecord(
        id=f"insight-{uuid5(NAMESPACE_URL, f'{kind}:{item.source}:{text}').hex[:8]}",
        kind=kind,
        title=f"{title_prefix}：{_short(text, 36)}",
        summary=_short(text, 160),
        guidance=text,
        applies_to=applies_to,
        triggers=_infer_triggers(kind, text),
        exceptions=["用户明确给出相反要求时，以当前任务要求为准"],
        confidence="medium",
        status="draft",
        evidence=[
            Evidence(
                source=item.source,
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
            return True
    return False


def _stats(
    inputs: list[FittingInput],
    ignored: list[dict[str, str]],
    insights: list[InsightRecord],
    suggestions: list[RotSuggestion],
) -> dict[str, int]:
    return {
        "inputs_seen": len(inputs),
        "insights_proposed": len(insights),
        "preferences_proposed": sum(1 for item in insights if item.kind == "preference"),
        "rot_suggestions": len(suggestions),
        "conflicts": sum(1 for item in suggestions if item.type == "conflict"),
        "ignored_by_instruction": len(ignored),
    }


def _input_labels(inputs: list[FittingInput]) -> list[str]:
    labels: list[str] = []
    for item in inputs:
        label = item.source
        if label not in labels:
            labels.append(label)
    return labels[:100]


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[。！？!?；;\n]+", text) if part.strip()]
    return parts or [text.strip()]


def _clean(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _short(text: str, limit: int) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _infer_triggers(kind: str, text: str) -> list[str]:
    if kind == "workflow":
        return ["同类任务", "长程任务", "交付前"]
    if kind == "error_pattern":
        return ["类似错误再次出现", "交付前自检"]
    if kind == "tool_quirk":
        return ["使用相关工具时"]
    triggers = []
    for marker in ("回复", "测试", "文档", "回读", "Linear", "中文", "UI"):
        if marker.casefold() in text.casefold():
            triggers.append(marker)
    return triggers[:5]


def _record_text(record: PreferenceRecord) -> str:
    return " ".join(part for part in [record.title, record.summary, record.applies_to, record.preference, *record.triggers] if part)


def _statement(record: PreferenceRecord) -> str:
    return _clean(record.preference or record.summary or record.title)


def _normalize(text: str) -> str:
    return re.sub(r"\W+", "", text.casefold())
