from __future__ import annotations

from typing import Any


def summarize_cold_start_result(result: Any) -> dict[str, Any]:
    data = _as_dict(result)
    scan_results = _list(data.get("scan_results"))
    captures = [capture for scan in scan_results for capture in _list(scan.get("capture_results")) if isinstance(capture, dict)]
    skipped = _list(data.get("skipped_sources"))
    unsupported = _unsupported_agents(data)
    stopped_reason = str(data.get("stopped_reason") or "")
    capture_stops = [
        str(capture.get("stopped_reason") or "")
        for capture in captures
        if str(capture.get("stopped_reason") or "completed") != "completed"
    ]
    extract_errors = sum(_int(capture.get("extract_errors")) for capture in captures)
    recall_errors = sum(_int(capture.get("recall_errors")) for capture in captures)
    added = sum(_int(capture.get("preferences_added")) for capture in captures)
    merged = sum(_int(capture.get("preferences_merged")) for capture in captures)
    replaced = sum(_int(capture.get("preferences_replaced")) for capture in captures)
    conflicted = sum(_int(capture.get("preferences_conflicted")) for capture in captures)
    candidates = sum(_int(capture.get("candidates_written")) for capture in captures)
    changed_files = _int(data.get("changed_files"))
    dry_run = bool(data.get("dry_run"))
    store_updated = (not dry_run) and (added + merged + replaced + conflicted > 0)
    warnings = len(skipped) + extract_errors + recall_errors
    status_line = _status_line(
        ok=bool(data.get("ok", True)),
        stopped_reason=stopped_reason or (capture_stops[0] if capture_stops else ""),
        warnings=warnings,
        store_updated=store_updated,
        changed_files=changed_files,
    )
    return {
        "status": status_line,
        "store_updated": store_updated,
        "sources_discovered": len(_list(data.get("sources"))),
        "unsupported_sources": len(unsupported),
        "scanned_sources": _int(data.get("scanned_sources")),
        "changed_files": changed_files,
        "total_files_seen": _int(data.get("total_files_seen")),
        "candidates_written": candidates,
        "preferences_added": added,
        "preferences_merged": merged,
        "preferences_replaced": replaced,
        "preferences_conflicted": conflicted,
        "extract_errors": extract_errors,
        "recall_errors": recall_errors,
        "skipped_sources": len(skipped),
        "stopped_reason": stopped_reason,
    }


def render_cold_start_summary(result: Any, quiet: bool = False) -> str:
    data = _as_dict(result)
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else summarize_cold_start_result(data)
    if quiet:
        return "\n".join(
            [
                str(summary["status"]),
                f"Store updated: {_yes_no(summary['store_updated'])}",
                "Next: datailor ui",
            ]
        )

    lines = [
        "Datailor cold-start scan",
        "",
        "Store",
        f"  {data.get('store', '')}",
        "",
        "Sources discovered",
    ]
    source_lines = _source_lines(data)
    lines.extend(source_lines or ["  No supported local history source found."])
    lines.extend(["", "Scanning"])
    scan_lines = _scan_lines(data)
    lines.extend(scan_lines or ["  No supported sources scanned."])
    lines.extend(
        [
            "",
            "Result",
            f"  {summary['status']}",
            f"  Sources scanned: {summary['scanned_sources']}",
            f"  Changed files: {summary['changed_files']}",
            f"  Candidates: {summary['candidates_written']}",
            f"  Preferences added: {summary['preferences_added']}",
            f"  Preferences merged: {summary['preferences_merged']}",
            f"  Preferences replaced: {summary['preferences_replaced']}",
            f"  Conflicts: {summary['preferences_conflicted']}",
            f"  Extract errors: {summary['extract_errors']}",
            f"  Skipped sources: {summary['skipped_sources']}",
            f"  Store updated: {_yes_no(summary['store_updated'])}",
            "",
            "Next",
            "  Run: datailor ui",
            "  Review pending/conflicts in the UI.",
        ]
    )
    return "\n".join(lines)


def render_onboarding_summary(result: dict[str, Any], quiet: bool = False) -> str:
    scan = result.get("scan") if isinstance(result.get("scan"), dict) else None
    if not scan:
        before = result.get("before") if isinstance(result.get("before"), dict) else {}
        after = result.get("after") if isinstance(result.get("after"), dict) else {}
        state = after.get("state") or before.get("state") or "unknown"
        store = (after.get("store") or before.get("store") or {}).get("path", "")
        if quiet:
            return f"Datailor onboard: {state}"
        return "\n".join(
            [
                "Datailor onboard",
                "",
                "Store",
                f"  {store}",
                "",
                "Result",
                f"  Completed: {state}",
                "",
                "Next",
                "  Run: datailor ui",
            ]
        )
    title = "Datailor onboard"
    body = render_cold_start_summary(scan, quiet=quiet)
    if quiet:
        return body.replace("Next: datailor ui", "Next: datailor ui")
    return body.replace("Datailor cold-start scan", title, 1)


def render_cold_start_progress(event: dict[str, Any]) -> str:
    kind = str(event.get("event") or "")
    if kind == "scan_start":
        lines = [
            "Datailor cold-start scan",
            "",
            "Store",
            f"  {event.get('store', '')}",
            "",
            "Sources discovered",
        ]
        lines.extend(_source_lines(event) or ["  No supported local history source found."])
        lines.extend(["", "Scanning"])
        return "\n".join(lines)
    if kind == "source_start":
        return f"[{event.get('index')}/{event.get('total')}] Scanning {_source_label(event.get('source'))} ..."
    if kind == "capture_stage":
        file_name = str(event.get("file") or "")
        return f"  capture pipeline: {file_name} (recall candidates -> extracting preferences -> merging into store) ..."
    if kind == "source_done":
        scan = event.get("scan") if isinstance(event.get("scan"), dict) else {}
        metrics = _scan_metrics(scan)
        status = "changed" if metrics["changed_files"] else "unchanged"
        return (
            f"[{event.get('index')}/{event.get('total')}] {_source_label(event.get('source'))} "
            f"... {status}, changed files {metrics['changed_files']}, "
            f"added {metrics['preferences_added']}, merged {metrics['preferences_merged']}, "
            f"conflicts {metrics['preferences_conflicted']}"
        )
    if kind == "source_skipped":
        return f"[{event.get('index')}/{event.get('total')}] {_source_label(event.get('source'))} ... skipped: {event.get('reason', '')}"
    if kind == "scan_stopped":
        reason = str(event.get("reason") or "")
        return f"Stopped: {_friendly_stop(reason)}"
    return ""


def _source_lines(data: dict[str, Any]) -> list[str]:
    sources = _list(data.get("sources"))
    scan_by_source = {
        str(scan.get("source")): scan
        for scan in _list(data.get("scan_results"))
        if isinstance(scan, dict)
    }
    lines: list[str] = []
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            continue
        scan = scan_by_source.get(str(source.get("path") or ""))
        if scan:
            status = "changed" if _list(scan.get("changed_files")) else "unchanged"
        else:
            status = "discovered"
        lines.append(
            f"  [{index}/{len(sources)}] {_source_label(source):<24} {status:<10} {_format_size(_int(source.get('size_bytes')))}"
        )
    for agent in _unsupported_agents(data):
        lines.append(
            f"  [-] {str(agent.get('display_name') or agent.get('name') or 'Unknown'):<24} unsupported: {agent.get('unsupported_reason', '')}"
        )
    return lines


def _scan_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for scan in _list(data.get("scan_results")):
        if not isinstance(scan, dict):
            continue
        metrics = _scan_metrics(scan)
        source = str(scan.get("source") or "")
        label = source.split("\\")[-1].split("/")[-1] or source
        status = "done" if metrics["changed_files"] else "skipped, unchanged"
        lines.extend(
            [
                f"  {label} ... {status}",
                f"    files changed: {metrics['changed_files']}",
                f"    candidates: {metrics['candidates_written']}",
                f"    added: {metrics['preferences_added']}",
                f"    merged: {metrics['preferences_merged']}",
                f"    replaced: {metrics['preferences_replaced']}",
                f"    conflicts: {metrics['preferences_conflicted']}",
                f"    extract errors: {metrics['extract_errors']}",
            ]
        )
    for skipped in _list(data.get("skipped_sources")):
        if isinstance(skipped, dict):
            lines.append(f"  {skipped.get('source', '')} ... skipped: {skipped.get('reason', '')}")
    return lines


def _scan_metrics(scan: dict[str, Any]) -> dict[str, int]:
    captures = [item for item in _list(scan.get("capture_results")) if isinstance(item, dict)]
    return {
        "changed_files": len(_list(scan.get("changed_files"))),
        "candidates_written": sum(_int(item.get("candidates_written")) for item in captures),
        "preferences_added": sum(_int(item.get("preferences_added")) for item in captures),
        "preferences_merged": sum(_int(item.get("preferences_merged")) for item in captures),
        "preferences_replaced": sum(_int(item.get("preferences_replaced")) for item in captures),
        "preferences_conflicted": sum(_int(item.get("preferences_conflicted")) for item in captures),
        "extract_errors": sum(_int(item.get("extract_errors")) for item in captures),
    }


def _status_line(
    ok: bool,
    stopped_reason: str,
    warnings: int,
    store_updated: bool,
    changed_files: int,
) -> str:
    if not ok:
        return "Failed: scan did not complete"
    if stopped_reason:
        return f"Stopped: {_friendly_stop(stopped_reason)}"
    if warnings:
        return "Completed with warnings"
    if store_updated:
        return "Completed: preferences updated"
    if changed_files == 0:
        return "Completed: no changes found"
    return "Completed: no preference changes"


def _friendly_stop(reason: str) -> str:
    return {
        "max_files": "max-files reached",
        "max_lines": "max-lines reached",
        "max_candidates": "max-candidates reached",
        "max_minutes": "max-minutes reached",
    }.get(reason, reason or "stopped")


def _unsupported_agents(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        agent
        for agent in _list(data.get("discovered_agents"))
        if isinstance(agent, dict)
        and bool(agent.get("installed"))
        and not bool(agent.get("supported", True))
    ]


def _source_label(source: Any) -> str:
    if isinstance(source, dict):
        display = str(source.get("display_name") or source.get("agent") or "Source")
    else:
        display = "Source"
    return f"{display} history" if "history" not in display.casefold() else display


def _format_size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def _yes_no(value: Any) -> str:
    return "yes" if value else "no"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        data = value.to_dict()
        return data if isinstance(data, dict) else {}
    return {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
