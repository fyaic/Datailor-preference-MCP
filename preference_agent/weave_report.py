from __future__ import annotations

from .weave_models import InsightRecord, RotSuggestion, WeaveJobResult


def render_weave_report(result: WeaveJobResult) -> str:
    stats = result.stats
    lines: list[str] = [
        "# Datailor Weave Report",
        "",
        "## Summary",
        "",
        f"- Job: `{result.job_id}`",
        f"- Status: `{result.status}`",
        f"- Store: `{result.store_file}`",
        f"- Inputs seen: {stats.get('inputs_seen', 0)}",
        f"- Insights proposed: {stats.get('insights_proposed', 0)}",
        f"- Preference proposals: {stats.get('preferences_proposed', 0)}",
        f"- Memory rot suggestions: {stats.get('rot_suggestions', 0)}",
        f"- Conflicts needing review: {stats.get('conflicts', 0)}",
        f"- Ignored by instructions: {stats.get('ignored_by_instruction', 0)}",
        "",
        "Nothing has been stitched into the preference library unless an apply command is run.",
        "",
        "## Instructions",
        "",
        result.instructions.text or "No instructions were provided.",
        "",
        f"- Focus: {', '.join(result.instructions.focus) if result.instructions.focus else 'none'}",
        f"- Ignore: {', '.join(result.instructions.ignore) if result.instructions.ignore else 'none'}",
        f"- Truncated: {str(result.instructions.truncated).lower()}",
        "",
        "## Inputs",
        "",
    ]
    lines.extend(_bullet_list(result.inputs, empty="No external source was scanned; the current preference store was used."))
    lines.extend(["", "## New Patterns", ""])
    lines.extend(_insight_section([item for item in result.insights if item.kind != "preference"]))
    lines.extend(["", "## Preference Proposals", ""])
    lines.extend(_insight_section([item for item in result.insights if item.kind == "preference"]))
    lines.extend(["", "## Memory Rot Suggestions", ""])
    lines.extend(_rot_section(result.rot_suggestions))
    lines.extend(["", "## Conflicts Needing Review", ""])
    conflicts = [item for item in result.rot_suggestions if item.type == "conflict"]
    lines.extend(_rot_section(conflicts, empty="No conflicts were detected."))
    lines.extend(["", "## Feedback Effects", ""])
    feedback_related = [item for item in result.rot_suggestions if item.type == "negative_feedback"]
    lines.extend(_rot_section(feedback_related, empty="No strong feedback-based changes were detected."))
    lines.extend(["", "## Ignored By Instructions", ""])
    if result.ignored_inputs:
        for item in result.ignored_inputs:
            lines.append(f"- `{item.get('source', '')}`: {item.get('reason', '')}")
    else:
        lines.append("No inputs were ignored by instructions.")
    lines.extend(["", "## Next Commands", ""])
    lines.extend(_bullet_list(result.next_commands, empty="No next command is available."))
    lines.append("")
    return "\n".join(lines)


def _insight_section(items: list[InsightRecord]) -> list[str]:
    if not items:
        return ["No proposals in this section."]
    lines: list[str] = []
    for item in items:
        lines.extend(
            [
                f"### {item.title or item.kind}",
                "",
                f"- Kind: `{item.kind}`",
                f"- Confidence: `{item.confidence}`",
                f"- Applies to: {item.applies_to or 'not specified'}",
                f"- Guidance: {item.guidance or item.summary}",
                f"- Evidence: {len(item.evidence)} item(s)",
                "",
            ]
        )
    return lines


def _rot_section(items: list[RotSuggestion], empty: str = "No memory rot suggestions were generated.") -> list[str]:
    if not items:
        return [empty]
    lines: list[str] = []
    for item in items:
        lines.extend(
            [
                f"### {item.type}: {', '.join(item.target_ids) or item.id}",
                "",
                f"- Risk: `{item.risk}`",
                f"- Proposed action: `{item.proposed_action}`",
                f"- Reason: {item.reason}",
                "",
            ]
        )
    return lines


def _bullet_list(items: list[str], empty: str) -> list[str]:
    return [f"- {item}" for item in items] if items else [empty]
