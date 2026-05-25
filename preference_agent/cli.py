from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .backends import HeuristicBackend, build_backend
from .capture_runner import CaptureConfig, CaptureRunner
from .engine import PreferenceEngine, parse_context
from .feedback import feedback_report, record_feedback
from .incremental import scan_incremental
from .injection import prewarm_session, sync_injection_artifacts
from .store import MarkdownPreferenceStore


def default_store_path() -> Path:
    return Path(os.getenv("PREFERENCE_STORE_PATH") or Path(__file__).resolve().parents[1] / "data" / "个人偏好.md")


def build_engine(args: argparse.Namespace) -> PreferenceEngine:
    backend = build_backend(getattr(args, "backend", None))
    fallback = HeuristicBackend() if backend.__class__.__name__ != "HeuristicBackend" else None
    return PreferenceEngine(MarkdownPreferenceStore(args.store), backend=backend, fallback_backend=fallback)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="preference-agent")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=str(default_store_path()), help="Markdown preference store path")
    common.add_argument("--backend", default=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
    parser.add_argument("--store", default=str(default_store_path()), help="Markdown preference store path")
    parser.add_argument("--backend", default=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
    sub = parser.add_subparsers(dest="command", required=True)

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

    ui = sub.add_parser("ui", parents=[common], help="Start the local preference manifesto panel")
    ui.add_argument("--host", default=os.getenv("PREFERENCE_UI_HOST", "127.0.0.1"))
    ui.add_argument("--port", type=int, default=int(os.getenv("PREFERENCE_UI_PORT", "8080")))
    ui.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")

    args = parser.parse_args(argv)
    args.store = Path(args.store)
    engine = build_engine(args)

    if args.command == "init":
        engine.init_store()
        return _print({"ok": True, "store": str(args.store), "state": "cold_start"})
    if args.command == "capture":
        result = engine.capture_path(args.source, dry_run=args.dry_run)
        return _print({"ok": True, **result.to_dict(), "store": str(args.store)})
    if args.command == "capture-job":
        config = CaptureConfig.from_env(args.project_root or None)
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
    if args.command == "ui":
        from .ui.server import run_ui_server

        return run_ui_server(args.store, args.host, args.port, open_browser=not args.no_open)
    parser.error("unknown command")
    return 2


def _print(data: dict[str, Any]) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
