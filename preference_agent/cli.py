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
from .integrations import DEFAULT_SERVER_NAME, run_integrations
from .kimi_hooks import install_kimi_hooks
from .onboarding import get_onboarding_status, run_onboarding
from .paths import default_store_path
from .preference_actions import apply_preference_feedback
from .snapshots import list_store_snapshots, restore_store_snapshot
from .store import MarkdownPreferenceStore
from .fitting import apply_fitting_plan, get_fitting_job, list_fitting_jobs, run_fitting
from .fitting_background import mark_fitting_plan_reviewed, run_fitting_for_mode


def build_engine(args: argparse.Namespace) -> PreferenceEngine:
    backend = build_backend(getattr(args, "backend", None))
    fallback = HeuristicBackend() if backend.__class__.__name__ != "HeuristicBackend" else None
    return PreferenceEngine(MarkdownPreferenceStore(args.store), backend=backend, fallback_backend=fallback)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from .config_env import load_local_env

    load_local_env()
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(prog="datailor")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=argparse.SUPPRESS, help="Markdown preference store path")
    common.add_argument("--backend", default=argparse.SUPPRESS)
    parser.add_argument("--store", default=str(default_store_path()), help="Markdown preference store path")
    parser.add_argument("--backend", default=os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"))
    sub = parser.add_subparsers(dest="command", required=False)

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
    onboard.add_argument("--no-agent-rules", action="store_true", help="Do not auto-install the Datailor AGENTS.md managed block")
    onboard.add_argument("--agent-rules-target", default="", help="Override the AGENTS.md target path")
    onboard.add_argument("--no-kimi-hooks", action="store_true", help="Do not auto-install Kimi CLI lifecycle hooks when --agent kimi")
    onboard.add_argument("--kimi-hooks-target", default="", help="Override the Kimi config.toml path")
    onboard.add_argument("--integrate-client", action="append", default=[], choices=["all", "codex", "claude", "kimi"], help="Explicitly install client integration during onboarding; repeatable")
    onboard.add_argument("--integrate-scope", default="default", choices=["default", "user", "project", "local"], help="Scope used for --integrate-client")
    onboard.add_argument("--integrate-project-root", default="", help="Project root used for project/local integration scope")
    onboard.add_argument("--integrate-dry-run", action="store_true", help="Preview --integrate-client changes without writing client config")
    onboard.add_argument("--json", action="store_true", help="Print structured JSON instead of the human summary")
    onboard.add_argument("--quiet", action="store_true", help="Print only the final status and next command")
    onboard.add_argument(
        "--interactive",
        dest="interactive",
        action="store_true",
        default=None,
        help="Force the interactive setup wizard",
    )
    onboard.add_argument(
        "--no-interactive",
        dest="interactive",
        action="store_false",
        help="Force the classic non-interactive onboarding flow",
    )

    install_rules = sub.add_parser("install-agent-rules", help="Install or update the Datailor managed block in AGENTS.md")
    install_rules.add_argument("--target", default="", help="Target AGENTS.md path; defaults to ~/AGENTS.md")
    install_rules.add_argument("--snippet", default="", help="Override snippet path")

    install_kimi = sub.add_parser("install-kimi-hooks", parents=[common], help="Install or update Datailor hooks in Kimi CLI config.toml")
    install_kimi.add_argument("--agent", default="kimi")
    install_kimi.add_argument("--target", default="", help="Target Kimi config.toml path; defaults to ~/.kimi/config.toml")
    install_kimi.add_argument("--command", dest="hook_command", default="datailor-kimi-hook", help="Hook command written into config.toml")
    install_kimi.add_argument("--timeout", type=int, default=20)

    mcp_config = sub.add_parser("mcp-config", parents=[common], help="Print MCP client config for installed Datailor")
    mcp_config.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", "codex"))
    mcp_config.add_argument("--server-name", default=DEFAULT_SERVER_NAME)
    mcp_config.add_argument("--command", dest="mcp_command", default="datailor-mcp")
    mcp_config.add_argument("--no-agent-rules", action="store_true", help="Do not auto-install the Datailor AGENTS.md managed block")
    mcp_config.add_argument("--agent-rules-target", default="", help="Override the AGENTS.md target path")
    mcp_config.add_argument("--no-kimi-hooks", action="store_true", help="Do not auto-install Kimi CLI lifecycle hooks when --agent kimi")
    mcp_config.add_argument("--kimi-hooks-target", default="", help="Override the Kimi config.toml path")

    integrate = sub.add_parser("integrate", parents=[common], help="Install, remove, inspect, or export Datailor client integrations")
    integrate_sub = integrate.add_subparsers(dest="integrate_action", required=True)
    for action_name in ["status", "install", "remove", "doctor", "export-plugin"]:
        action = integrate_sub.add_parser(action_name, help=f"{action_name} Datailor client integration")
        action.add_argument("--client", default="all", choices=["all", "codex", "claude", "kimi"])
        action.add_argument("--scope", default="default", choices=["default", "user", "project", "local"])
        action.add_argument("--project-root", default="")
        action.add_argument("--server-name", default=DEFAULT_SERVER_NAME)
        action.add_argument("--command", dest="mcp_command", default="datailor-mcp")
        action.add_argument("--dry-run", action="store_true")
        action.add_argument("--output", default="", help="Output directory for export-plugin")
        action.add_argument("--json", action="store_true", help="Print structured JSON")

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

    fitting = sub.add_parser("fitting", parents=[common], help="Run review-first Datailor Fitting")
    fitting.add_argument("--agent", default=os.getenv("PREFERENCE_CALLER_AGENT", "codex"))
    fitting.add_argument("--source", default="", help="Optional history/session file or directory")
    fitting.add_argument("--instructions", default="", help="Natural-language focus/ignore instructions")
    fitting.add_argument("--instructions-file", default="", help="Read Fitting instructions from a UTF-8 text file")
    fitting.add_argument("--fitting-dir", default="", help="Override the local Fitting artifact directory")
    fitting.add_argument("--max-files", type=int, default=0)
    review_group = fitting.add_mutually_exclusive_group()
    review_group.add_argument("--review", action="store_true", dest="review", help="Generate drafts/report without applying changes")
    review_group.add_argument("--no-review", action="store_false", dest="review", help="Mark the Fitting run as non-review metadata")
    fitting.set_defaults(review=True)
    fitting.add_argument("--dry-run", action="store_true", help="Build result in memory without writing job artifacts")
    fitting.add_argument("--auto-apply", action="store_true", help="Apply high-confidence preference changes after Fitting")
    fitting.add_argument("--json", action="store_true", help="Print structured JSON instead of the human summary")
    fitting.add_argument("--quiet", action="store_true", help="Print only final status and report path")

    fitting_list = sub.add_parser("fitting-list", parents=[common], help="List recent Datailor Fitting jobs")
    fitting_list.add_argument("--fitting-dir", default="")
    fitting_list.add_argument("--limit", type=int, default=20)

    fitting_show = sub.add_parser("fitting-show", parents=[common], help="Show a Datailor Fitting job")
    fitting_show.add_argument("job_id")
    fitting_show.add_argument("--fitting-dir", default="")
    fitting_show.add_argument("--report", action="store_true", help="Print the Markdown report")
    fitting_show.add_argument("--json", action="store_true")

    fitting_apply = sub.add_parser("fitting-apply", parents=[common], help="Apply accepted changes from a Datailor Fitting job")
    fitting_apply.add_argument("job_id")
    fitting_apply.add_argument("--fitting-dir", default="")
    fitting_apply.add_argument("--accept", action="append", default=[], help="Change id to apply; repeatable")

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
    if not args.command:
        return _print_welcome_hint(Path(args.store))
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
        if _should_run_setup_tui(args, raw_argv):
            from .setup_tui import run_setup_wizard

            return run_setup_wizard(store_path=args.store, agent_hint=args.agent, backend=args.backend)
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
            install_agent_rules_enabled=not args.no_agent_rules,
            agent_rules_target=args.agent_rules_target or None,
            install_kimi_hooks_enabled=False if args.no_kimi_hooks else None,
            kimi_hooks_target=args.kimi_hooks_target or None,
            progress=progress,
        )
        if args.integrate_client:
            integrations = []
            integration_store = args.store if _arg_was_provided(raw_argv, "--store") or os.getenv("PREFERENCE_STORE_PATH") else None
            for integrate_client in args.integrate_client:
                integrations.append(
                    run_integrations(
                        action="install",
                        client=integrate_client,
                        scope=args.integrate_scope,
                        project_root=args.integrate_project_root or None,
                        dry_run=args.dry_run or args.integrate_dry_run,
                        backend=args.backend,
                        store=integration_store,
                    )
                )
            result["integrations"] = integrations
        if args.json:
            return _print(result)
        return _print_text(render_onboarding_summary(result, quiet=args.quiet))
    if args.command == "install-agent-rules":
        result = install_agent_rules(target=args.target or None, snippet=args.snippet or None)
        return _print(result.to_dict())
    if args.command == "install-kimi-hooks":
        result = install_kimi_hooks(
            target=args.target or None,
            command=args.hook_command,
            agent=args.agent,
            backend=args.backend,
            store=args.store,
            timeout=args.timeout,
        )
        return _print(result.to_dict())
    if args.command == "mcp-config":
        if not args.no_agent_rules:
            agent_rules = install_agent_rules(target=args.agent_rules_target or None)
            _print_agent_rules_notice(agent_rules.to_dict())
        if args.agent.strip().casefold() == "kimi" and not args.no_kimi_hooks:
            kimi_hooks = install_kimi_hooks(
                target=args.kimi_hooks_target or None,
                agent=args.agent,
                backend=args.backend,
                store=args.store,
            )
            _print_kimi_hooks_notice(kimi_hooks.to_dict())
        return _print(_build_mcp_config(args, raw_argv))
    if args.command == "integrate":
        result = run_integrations(
            action=args.integrate_action,
            client=args.client,
            scope=args.scope,
            project_root=args.project_root or None,
            dry_run=args.dry_run,
            output_dir=args.output or None,
            server_name=args.server_name,
            mcp_command=args.mcp_command,
            backend=args.backend,
            store=args.store if _arg_was_provided(raw_argv, "--store") or os.getenv("PREFERENCE_STORE_PATH") else None,
        )
        return _print(result)
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
    if args.command == "fitting":
        if args.auto_apply and not args.dry_run:
            payload = run_fitting_for_mode(
                store_path=args.store,
                mode="auto",
                agent=args.agent,
                source=args.source or None,
                instructions=args.instructions,
                instructions_file=args.instructions_file or None,
                fitting_dir=args.fitting_dir or None,
                max_files=args.max_files,
            )
            if args.json:
                return _print(payload)
            return _print_text(_render_fitting_cli_summary(payload.get("result") or {}, quiet=args.quiet))
        result = run_fitting(
            store_path=args.store,
            instructions=args.instructions,
            instructions_file=args.instructions_file or None,
            source=args.source or None,
            agent=args.agent,
            fitting_dir=args.fitting_dir or None,
            dry_run=args.dry_run,
            review=args.review,
            max_files=args.max_files,
        )
        if args.json:
            return _print({"ok": True, **result.to_dict()})
        return _print_text(_render_fitting_cli_summary(result.to_dict(), quiet=args.quiet))
    if args.command == "fitting-list":
        return _print(list_fitting_jobs(fitting_dir=args.fitting_dir or None, limit=args.limit))
    if args.command == "fitting-show":
        try:
            result = get_fitting_job(args.job_id, fitting_dir=args.fitting_dir or None)
        except ValueError as exc:
            return _print({"ok": False, "error": str(exc), "job_id": args.job_id})
        if args.report:
            report_path = str(result.get("report_file") or "").strip()
            report_file = Path(report_path) if report_path else None
            return _print_text(report_file.read_text(encoding="utf-8") if report_file and report_file.exists() and report_file.is_file() else "")
        if args.json:
            return _print({"ok": True, **result})
        return _print_text(_render_fitting_cli_summary(result, quiet=False))
    if args.command == "fitting-apply":
        try:
            result = apply_fitting_plan(
                job_id=args.job_id,
                accepted_change_ids=args.accept,
                store_path=args.store,
                fitting_dir=args.fitting_dir or None,
            )
        except ValueError as exc:
            return _print({"ok": False, "error": str(exc), "job_id": args.job_id})
        if result.get("ok") and not result.get("remaining_pending"):
            mark_fitting_plan_reviewed(args.job_id, accepted=len(result.get("applied") or []))
        return _print(result)
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


def _print_welcome_hint(store_path: Path) -> int:
    """First-run banner shown when `datailor` is run without a subcommand."""

    initialized = store_path.exists()
    primary = "datailor doctor" if initialized else "datailor onboard"
    headline = "Welcome back to Datailor" if initialized else "Welcome to Datailor"
    try:
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                try:
                    reconfigure(encoding="utf-8")
                except (ValueError, OSError):
                    pass
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        console = Console(legacy_windows=False)
        body = Table.grid(padding=(0, 2))
        body.add_column(style="bold cyan", no_wrap=True)
        body.add_column()
        body.add_row("› RUN", f"[bold green]{primary}[/]")
        if not initialized:
            body.add_row("", "[dim]Guided setup: model API, AGENTS.md, scan, integrations[/]")
        body.add_row("datailor ui", "Review and curate your preferences")
        body.add_row("datailor --help", "All commands")
        console.print(
            Panel(body, title=Text(headline, style="bold cyan"), border_style="cyan", padding=(1, 2))
        )
    except Exception:
        # Never let a rendering hiccup break the bare invocation.
        print(f"{headline}\n  RUN: {primary}\n  datailor --help for all commands")
    return 0


def _print(data: dict[str, Any]) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _print_text(text: str) -> int:
    print(text)
    return 0


def _render_fitting_cli_summary(result: dict[str, Any], quiet: bool = False) -> str:
    stats = result.get("stats") or {}
    report_file = str(result.get("report_file") or "")
    if quiet:
        return f"{result.get('status', 'unknown')} {report_file}".strip()
    lines = [
        "Datailor Fitting completed.",
        f"- Job: {result.get('job_id', '')}",
        f"- Report: {report_file}",
        f"- Insights proposed: {stats.get('insights_proposed', 0)}",
        f"- Memory rot suggestions: {stats.get('rot_suggestions', 0)}",
        f"- Ignored by instructions: {stats.get('ignored_by_instruction', 0)}",
    ]
    next_commands = result.get("next_commands") or []
    if next_commands:
        lines.append(f"- Next: {next_commands[0]}")
    return "\n".join(lines)


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


def _print_agent_rules_notice(result: dict[str, Any]) -> None:
    action = "updated" if result.get("changed") else "already current"
    print(f"Datailor AGENTS managed block: {action}", file=sys.stderr)
    print(f"Target: {result.get('target', '')}", file=sys.stderr)
    print(f"Markers: {result.get('managed_start', '')} ... {result.get('managed_end', '')}", file=sys.stderr)


def _print_kimi_hooks_notice(result: dict[str, Any]) -> None:
    action = "updated" if result.get("changed") else "already current"
    print(f"Datailor Kimi hooks: {action}", file=sys.stderr)
    print(f"Target: {result.get('target', '')}", file=sys.stderr)
    print(f"Events: {', '.join(result.get('events') or [])}", file=sys.stderr)


def _arg_was_provided(argv: list[str], name: str) -> bool:
    prefix = f"{name}="
    return any(item == name or item.startswith(prefix) for item in argv)


def _should_run_setup_tui(args: argparse.Namespace, raw_argv: list[str]) -> bool:
    """Decide whether a bare, interactive `onboard` should launch the TUI wizard.

    The classic non-interactive flow is preserved whenever the user passes
    onboard-specific flags, requests JSON/quiet output, or the session is not a
    real TTY. `--interactive`/`--no-interactive` force the decision.
    """

    interactive = getattr(args, "interactive", None)
    if interactive is False:
        return False
    if args.json or args.quiet:
        return False
    if interactive is True:
        return True
    if not _onboard_is_bare(raw_argv):
        return False
    return bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stdout, "isatty", lambda: False)())


def _onboard_is_bare(raw_argv: list[str]) -> bool:
    """True when `onboard` carries no flags beyond global store/backend/agent."""

    if "onboard" not in raw_argv:
        return True
    rest = raw_argv[raw_argv.index("onboard") + 1 :]
    passthrough = {"--store", "--backend", "--agent"}
    index = 0
    while index < len(rest):
        token = rest[index]
        if token in passthrough:
            index += 2
            continue
        if any(token.startswith(f"{name}=") for name in passthrough):
            index += 1
            continue
        return False
    return True


def _cold_start_progress_printer():
    def _print_progress(event: dict[str, Any]) -> None:
        text = render_cold_start_progress(event)
        if text:
            print(text, flush=True)

    return _print_progress


if __name__ == "__main__":
    raise SystemExit(main())
