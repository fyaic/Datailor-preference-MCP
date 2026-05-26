from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .agent_rules import install_agent_rules
from .agent_discovery import cold_start_scan, discovery_report
from .backends import HeuristicBackend, build_backend
from .capture_runner import CaptureConfig, CaptureRunner
from .cold_start_summary import render_cold_start_progress, render_cold_start_summary, render_onboarding_summary
from .engine import PreferenceEngine, parse_context
from .executive_summary import read_executive_summary, refresh_executive_summary
from .feedback import feedback_report, record_feedback
from .incremental import scan_incremental
from .injection import prewarm_session, sync_injection_artifacts
from .onboarding import get_onboarding_status, run_onboarding
from .paths import default_store_path
from .preference_actions import apply_preference_feedback
from .snapshots import list_store_snapshots, restore_store_snapshot
from .store import MarkdownPreferenceStore


def build_engine(args: argparse.Namespace) -> PreferenceEngine:
    backend = build_backend(getattr(args, "backend", None))
    fallback = HeuristicBackend() if backend.__class__.__name__ != "HeuristicBackend" else None
    return PreferenceEngine(MarkdownPreferenceStore(args.store), backend=backend, fallback_backend=fallback)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(prog="datailor")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=argparse.SUPPRESS, help="Markdown preference store path")
    common.add_argument("--backend", default=argparse.SUPPRESS)
    parser.add_argument("--store", default=str(default_store_path()), help="Markdown preference store path")
    parser.add_argument("--backend", default=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", parents=[common], help="Check Datailor onboarding status")
    doctor.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", "codex"))

    onboard = sub.add_parser("onboard", parents=[common], help="Run first-run onboarding: init, discover histories, capture, and optionally open UI")
    onboard.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", "codex"))
    onboard.add_argument("--mode", default=os.getenv("PREFERENCE_AUTO_DISCOVERY_MODE", "recall-extract"))
    onboard.add_argument("--dry-run", action="store_true")
    onboard.add_argument("--no-capture", action="store_true", help="Only show onboarding status; do not scan histories")
    onboard.add_argument("--max-files", type=int, default=0)
    onboard.add_argument("--max-minutes", type=float, default=0.0)
    onboard.add_argument("--state", default="")
    onboard.add_argument("--open-ui", action="store_true", help="Open the Manifesto UI after onboarding")
    onboard.add_argument("--host", default=os.getenv("PREFERENCE_UI_HOST", "127.0.0.1"))
    onboard.add_argument("--port", type=int, default=int(os.getenv("PREFERENCE_UI_PORT", "8080")))
    onboard.add_argument("--json", action="store_true", help="Print structured JSON instead of the human summary")
    onboard.add_argument("--quiet", action="store_true", help="Print only the final status and next command")

    install_rules = sub.add_parser("install-agent-rules", help="Install or update the Datailor managed block in AGENTS.md")
    install_rules.add_argument("--target", default="", help="Target AGENTS.md path; defaults to ~/AGENTS.md")
    install_rules.add_argument("--snippet", default="", help="Override snippet path")

    mcp_config = sub.add_parser("mcp-config", parents=[common], help="Print MCP client config for installed Datailor")
    mcp_config.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", "codex"))
    mcp_config.add_argument("--server-name", default="datailor-preference")
    mcp_config.add_argument("--command", dest="mcp_command", default="datailor-mcp")

    sub.add_parser("init", parents=[common], help="Create an empty cold-start preference store")

    capture = sub.add_parser("capture", parents=[common], help="Capture preferences from a session file or directory")
    capture.add_argument("--source", required=True, help="Session file or directory")
    capture.add_argument("--dry-run", action="store_true", help="Extract and merge in memory without writing")

    capture_job = sub.add_parser(
        "capture-job",
        parents=[common],
        help="Run resumable large-file capture into candidates/checkpoints without polluting the preference store",
    )
    capture_job.add_argument("--source", required=True, help="Large JSONL/session file to scan")
    capture_job.add_argument("--project-root", default="", help="Project root used for agent rule discovery")
    capture_job.add_argument("--mode", default="", help="Override PREFERENCE_CAPTURE_MODE, e.g. recall-only")
    capture_job.add_argument("--max-lines", type=int, default=None, help="Optional emergency/debug stop after this JSONL line number")
    capture_job.add_argument("--max-candidates", type=int, default=None, help="Optional emergency/debug stop after writing this many candidates")
    capture_job.add_argument("--max-minutes", type=float, default=None, help="Optional emergency/debug stop after this many minutes")

    decide = sub.add_parser("decide", parents=[common], help="Decide which preferences apply before an agent acts")
    decide.add_argument("--task", required=True, help="Current task, question, or planned agent action")
    decide.add_argument("--context", default="", help="JSON object or key=value,key=value context")
    decide.add_argument("--agent", default="agent", help="Agent name, for example codex or openclaw")

    sync_injection = sub.add_parser("sync-injection", parents=[common], help="Generate static rules, fallback files, and snapshots for agent injection")
    sync_injection.add_argument("--output-dir", default="", help="Directory for generated injection artifacts")
    sync_injection.add_argument("--target", action="append", default=[], help="Optional AGENTS.md/.cursorrules-style file to update with a managed block")
    sync_injection.add_argument("--max-rules", type=int, default=0)
    sync_injection.add_argument("--max-fallback", type=int, default=0)

    prewarm = sub.add_parser("prewarm", parents=[common], help="Prewarm session-level preference cache")
    prewarm.add_argument("--task", required=True)
    prewarm.add_argument("--agent", default="agent")
    prewarm.add_argument("--context", default="")
    prewarm.add_argument("--output-dir", default="")

    feedback = sub.add_parser("feedback", help="Record preference usage feedback")
    feedback.add_argument("--type", required=True, choices=["correction", "confirmation", "usage", "rejection"])
    feedback.add_argument("--user-feedback", required=True)
    feedback.add_argument("--preference-id", default="")
    feedback.add_argument("--preference", default="")
    feedback.add_argument("--agent", default="agent")
    feedback.add_argument("--task", default="")
    feedback.add_argument("--context", default="")
    feedback.add_argument("--source", default="cli")
    feedback.add_argument("--log", default="")

    report = sub.add_parser("feedback-report", help="Summarize preference feedback")
    report.add_argument("--log", default="")

    incremental = sub.add_parser("incremental-scan", parents=[common], help="Scan new or modified session files and run capture-job incrementally")
    incremental.add_argument("--source", required=True)
    incremental.add_argument("--state", default="")
    incremental.add_argument("--dry-run", action="store_true")
    incremental.add_argument("--mode", default="")
    incremental.add_argument("--max-files", type=int, default=0)

    discover_agents = sub.add_parser("discover-agents", parents=[common], help="Detect installed agent history sources")
    discover_agents.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", ""), help="Current caller agent, used for source ordering")

    cold_start = sub.add_parser("cold-start-scan", parents=[common], help="Auto-discover installed agent histories and scan them incrementally")
    cold_start.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", ""), help="Current caller agent, used for source ordering")
    cold_start.add_argument("--dry-run", action="store_true")
    cold_start.add_argument("--mode", default="")
    cold_start.add_argument("--max-files", type=int, default=0)
    cold_start.add_argument("--max-minutes", type=float, default=0.0)
    cold_start.add_argument("--state", default="")
    cold_start.add_argument("--json", action="store_true", help="Print structured JSON instead of the human summary")
    cold_start.add_argument("--quiet", action="store_true", help="Print only the final status and next command")

    ui = sub.add_parser("ui", parents=[common], help="Start the local preference manifesto panel")
    ui.add_argument("--host", default=os.getenv("PREFERENCE_UI_HOST", "127.0.0.1"))
    ui.add_argument("--port", type=int, default=int(os.getenv("PREFERENCE_UI_PORT", "8080")))
    ui.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")

    snapshots = sub.add_parser("snapshots", parents=[common], help="List automatic Markdown store snapshots")
    snapshots.add_argument("--limit", type=int, default=20)

    restore_snapshot = sub.add_parser("restore-snapshot", parents=[common], help="Restore the Markdown store from a snapshot")
    restore_snapshot.add_argument("--snapshot", required=True, help="Snapshot .md file to restore")

    summary_refresh = sub.add_parser("summary-refresh", parents=[common], help="Generate or refresh the UX Executive Summary")
    summary_refresh.add_argument("--force", action="store_true", help="Refresh even when there is no captured delta")

    sub.add_parser("summary-show", parents=[common], help="Show the current UX Executive Summary")

    args = parser.parse_args(argv)
    args.store = Path(args.store)
    engine = build_engine(args)

    if args.command == "doctor":
        status = get_onboarding_status(
            store_path=args.store,
            agent_hint=args.agent,
            backend=args.backend,
        )
        return _print(status.to_dict())
    if args.command == "onboard":
        progress = None if args.json or args.quiet else _cold_start_progress_printer()
        result = run_onboarding(
            store_path=args.store,
            agent_hint=args.agent,
            backend=args.backend,
            mode=args.mode,
            dry_run=args.dry_run,
            capture=not args.no_capture,
            max_files=args.max_files,
            max_minutes=args.max_minutes,
            state_file=args.state or None,
            open_ui=args.open_ui,
            host=args.host,
            port=args.port,
            progress=progress,
        )
        if args.json:
            return _print(result)
        return _print_text(render_onboarding_summary(result, quiet=args.quiet))
    if args.command == "install-agent-rules":
        result = install_agent_rules(target=args.target or None, snippet=args.snippet or None)
        return _print(result.to_dict())
    if args.command == "mcp-config":
        return _print(_build_mcp_config(args, raw_argv))
    if args.command == "init":
        engine.init_store()
        return _print({"ok": True, "store": str(args.store), "state": "cold_start"})
    if args.command == "capture":
        result = engine.capture_path(args.source, dry_run=args.dry_run)
        return _print({"ok": True, **result.to_dict(), "store": str(args.store)})
    if args.command == "capture-job":
        config = CaptureConfig.from_env(args.project_root or None)
        config.store_path = args.store
        if args.mode:
            config.mode = args.mode
        if args.max_lines is not None:
            config.max_lines = args.max_lines
        if args.max_candidates is not None:
            config.max_candidates = args.max_candidates
        if args.max_minutes is not None:
            config.max_minutes = args.max_minutes
        result = CaptureRunner(config).run(args.source)
        return _print({"ok": True, **result.to_dict()})
    if args.command == "decide":
        result = engine.decide(task=args.task, context=parse_context(args.context), agent=args.agent)
        return _print({"ok": True, **result})
    if args.command == "sync-injection":
        result = sync_injection_artifacts(
            store_path=args.store,
            output_dir=args.output_dir or None,
            target_files=args.target,
            max_rules=args.max_rules or None,
            max_fallback=args.max_fallback or None,
        )
        return _print({"ok": True, **result.to_dict()})
    if args.command == "prewarm":
        result = prewarm_session(
            store_path=args.store,
            task=args.task,
            agent=args.agent,
            context=parse_context(args.context),
            output_dir=args.output_dir or None,
        )
        return _print({"ok": True, **result.to_dict()})
    if args.command == "feedback":
        result = record_feedback(
            feedback_type=args.type,
            user_feedback=args.user_feedback,
            preference_id=args.preference_id,
            preference_text=args.preference,
            agent=args.agent,
            task=args.task,
            source=args.source,
            context=parse_context(args.context),
            log_path=args.log or None,
        )
        result["preference_update"] = apply_preference_feedback(
            store_path=args.store,
            feedback_type=args.type,
            user_feedback=args.user_feedback,
            preference_id=args.preference_id,
            preference_text=args.preference,
        ).to_dict()
        return _print(result)
    if args.command == "feedback-report":
        return _print({"ok": True, **feedback_report(args.log or None)})
    if args.command == "incremental-scan":
        config = CaptureConfig.from_env()
        config.store_path = args.store
        if args.mode:
            config.mode = args.mode
        result = scan_incremental(
            source=args.source,
            config=config,
            state_file=args.state or None,
            dry_run=args.dry_run,
            max_files=args.max_files,
        )
        return _print({"ok": True, **result.to_dict()})
    if args.command == "discover-agents":
        return _print(discovery_report(agent_hint=args.agent))
    if args.command == "cold-start-scan":
        config = CaptureConfig.from_env()
        config.store_path = args.store
        if args.mode:
            config.mode = args.mode
        if args.max_minutes:
            config.max_minutes = args.max_minutes
        progress = None if args.json or args.quiet else _cold_start_progress_printer()
        result = cold_start_scan(
            store_path=args.store,
            agent_hint=args.agent,
            config=config,
            dry_run=args.dry_run,
            max_files=args.max_files,
            state_file=args.state or None,
            progress=progress,
        )
        if args.json:
            return _print(result.to_dict())
        return _print_text(render_cold_start_summary(result, quiet=args.quiet))
    if args.command == "ui":
        from .ui.server import run_ui_server

        return run_ui_server(args.store, args.host, args.port, open_browser=not args.no_open)
    if args.command == "snapshots":
        items = list_store_snapshots(args.store, limit=args.limit)
        return _print({"ok": True, "store": str(args.store), "snapshots": [item.to_dict() for item in items]})
    if args.command == "restore-snapshot":
        result = restore_store_snapshot(args.snapshot, args.store)
        return _print({"ok": True, **result})
    if args.command == "summary-refresh":
        update = refresh_executive_summary(
            args.store,
            changed_records=MarkdownPreferenceStore(args.store).load(),
            backend=engine.backend,
            force=args.force,
        )
        return _print({"ok": True, **update.to_dict()})
    if args.command == "summary-show":
        summary = read_executive_summary(args.store)
        return _print({"ok": True, **summary.to_dict()})
    parser.error("unknown command")
    return 2


def _print(data: dict[str, Any]) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _print_text(text: str) -> int:
    print(text)
    return 0


def _build_mcp_config(args: argparse.Namespace, raw_argv: list[str]) -> dict[str, Any]:
    env = {"PREFERENCE_MODEL_BACKEND": args.backend}
    if _arg_was_provided(raw_argv, "--store") or os.getenv("PREFERENCE_STORE_PATH"):
        env["PREFERENCE_STORE_PATH"] = str(args.store)
    return {
        "mcpServers": {
            args.server_name: {
                "command": args.mcp_command,
                "args": ["--agent", args.agent],
                "env": env,
            }
        }
    }


def _arg_was_provided(argv: list[str], name: str) -> bool:
    prefix = f"{name}="
    return any(item == name or item.startswith(prefix) for item in argv)


def _cold_start_progress_printer():
    def _print_progress(event: dict[str, Any]) -> None:
        text = render_cold_start_progress(event)
        if text:
            print(text, flush=True)

    return _print_progress


if __name__ == "__main__":
    raise SystemExit(main())
