"""Interactive first-run setup wizard for Datailor.

Renders an OpenClaw-style terminal experience on top of the existing
``run_onboarding`` building blocks:

    welcome -> model API config (or skip) -> AGENTS.md rules ->
    curate/auto mode -> MCP client integration -> full history scan -> summary

It is reached via a bare, interactive ``datailor onboard`` (see
``preference_agent.cli``). When ``rich``/``questionary`` are unavailable or the
session is non-interactive, callers fall back to the classic non-interactive
onboarding flow.

The wizard never raises on a cancelled prompt: Esc/Ctrl-C aborts cleanly with a
non-zero exit code so scripts can detect the abort.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from .agent_discovery import cold_start_scan
from .agent_rules import default_agents_target, default_snippet_text, install_agent_rules
from .capture_runner import CaptureConfig
from .cold_start_summary import render_cold_start_progress, summarize_cold_start_result
from .config_env import update_env_file
from .fitting_trigger import normalize_mode, read_mode_settings, write_mode_settings
from .integrations import DEFAULT_SERVER_NAME, run_integrations
from .integrations.registry import PROFILES
from .onboarding import PRODUCT_NAME, get_onboarding_status
from .paths import package_version


# ---------------------------------------------------------------------------
# Dependency handling
# ---------------------------------------------------------------------------


class _CancelledError(Exception):
    """Raised internally when the user aborts a prompt."""


def tui_available() -> bool:
    try:
        import questionary  # noqa: F401
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_setup_wizard(
    store_path: str | Path,
    agent_hint: str = "",
    backend: str = "",
) -> int:
    if not tui_available():
        print(
            "The interactive setup wizard needs the 'rich' and 'questionary' packages.\n"
            "Install them with:  pip install rich questionary\n"
            "Or run non-interactively:  datailor onboard --no-interactive",
        )
        return 2

    import questionary
    from rich.console import Console

    # On Windows consoles with a non-UTF-8 code page (e.g. GBK), rich's legacy
    # win32 renderer crashes on box-drawing/✓/• glyphs. Force UTF-8 output and
    # ANSI/VT rendering (supported on Windows 10+/11 and all POSIX terminals).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    console = Console(legacy_windows=False)
    store_path = Path(store_path)
    try:
        wizard = _Wizard(console, questionary, store_path, agent_hint=agent_hint, backend=backend)
        return wizard.run()
    except (_CancelledError, KeyboardInterrupt):
        console.print()
        console.print("[yellow]Setup cancelled.[/] You can resume any time with [bold]datailor onboard[/].")
        return 130


class _Wizard:
    def __init__(self, console, questionary, store_path: Path, agent_hint: str, backend: str) -> None:
        self.console = console
        self.q = questionary
        self.store_path = store_path
        self.agent_hint = agent_hint or os.getenv("PREFERENCE_CALLER_AGENT", "")
        self.backend = backend
        self.actions: list[str] = []

    # -- prompt helpers -----------------------------------------------------

    def _ask(self, prompt) -> Any:
        answer = prompt.ask()
        if answer is None:
            raise _CancelledError
        return answer

    def _select(self, message: str, choices: list, default=None) -> Any:
        return self._ask(self.q.select(message, choices=choices, default=default, qmark="›"))

    def _confirm(self, message: str, default: bool = True) -> bool:
        return self._ask(self.q.confirm(message, default=default, qmark="›"))

    def _text(self, message: str, default: str = "") -> str:
        return self._ask(self.q.text(message, default=default, qmark="›")).strip()

    def _password(self, message: str) -> str:
        return self._ask(self.q.password(message, qmark="›")).strip()

    # -- flow ---------------------------------------------------------------

    def run(self) -> int:
        status = self._welcome()
        self._step_model_backend()
        self._step_agent_rules()
        self._step_mode()
        self._step_integrations(status)
        scan_summary = self._step_scan()
        self._summary(scan_summary)
        return 0

    # -- step 0: welcome ----------------------------------------------------

    def _welcome(self):
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        status = get_onboarding_status(self.store_path, agent_hint=self.agent_hint, backend=self.backend)
        data = status.to_dict()
        discovered = [a for a in data.get("discovered_agents", []) if a.get("installed")]

        title = Text(f"{PRODUCT_NAME} Setup", style="bold cyan")
        subtitle = Text("Local-first personal preference layer for your AI agents", style="dim")

        meta = Table.grid(padding=(0, 2))
        meta.add_column(style="bold")
        meta.add_column()
        meta.add_row("Version", package_version())
        meta.add_row("Store", str(self.store_path))
        meta.add_row("State", _state_label(data.get("state", "")))
        meta.add_row("Install", str(data.get("install", {}).get("kind", "unknown")))
        meta.add_row(
            "Detected agents",
            ", ".join(a.get("display_name") or a.get("name") or "?" for a in discovered) or "[dim]none yet[/]",
        )
        meta.add_row("History sources", str(data.get("supported_sources", 0)))

        body = Table.grid()
        body.add_row(subtitle)
        body.add_row("")
        body.add_row(meta)
        self.console.print(Panel(body, title=title, border_style="cyan", padding=(1, 2)))
        self.console.print("[dim]Use ↑/↓ to move · Enter to select · Ctrl-C to quit[/]\n")
        return status

    # -- step 1: model backend ---------------------------------------------

    def _step_model_backend(self) -> None:
        self._rule("1 · Model backend")
        current = os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic")
        self.console.print(
            "Datailor extracts and consolidates preferences. "
            "The local [bold]heuristic[/] backend needs no config; an "
            "[bold]OpenAI-compatible[/] API gives higher-quality extraction.\n"
        )
        choice = self._select(
            "How should Datailor extract preferences?",
            choices=[
                self.q.Choice("Local heuristic — zero config, fully offline", value="heuristic"),
                self.q.Choice("OpenAI-compatible API — cloud or local LLM", value="openai-compatible"),
                self.q.Choice(f"Skip — keep current ({current})", value="skip"),
            ],
            default="heuristic" if current == "heuristic" else "openai-compatible",
        )
        if choice == "skip":
            self.console.print("[dim]Kept existing model configuration.[/]\n")
            return
        if choice == "heuristic":
            update_env_file({"PREFERENCE_MODEL_BACKEND": "heuristic"})
            self.backend = "heuristic"
            self.actions.append("Model backend set to local heuristic.")
            self.console.print("[green]✓[/] Using the offline heuristic backend.\n")
            return

        base_url = self._text(
            "API base URL",
            default=os.getenv("PREFERENCE_MODEL_BASE_URL", "https://api.openai.com/v1"),
        )
        api_key = self._password("API key (stored locally, hidden)")
        model_name = self._text("Model name", default=os.getenv("PREFERENCE_MODEL_NAME", "gpt-4o-mini"))
        values = {
            "PREFERENCE_MODEL_BACKEND": "openai-compatible",
            "PREFERENCE_MODEL_BASE_URL": base_url,
            "PREFERENCE_MODEL_NAME": model_name,
        }
        if api_key:
            values["PREFERENCE_MODEL_API_KEY"] = api_key
        target = update_env_file(values)
        self.backend = "openai-compatible"

        verify = self._confirm("Verify the API connection now?", default=True)
        if verify:
            ok, detail = _verify_backend()
            if ok:
                self.console.print(f"[green]✓[/] API reachable. {detail}")
            else:
                self.console.print(f"[yellow]![/] Could not verify: {detail}")
                if not self._confirm("Keep this configuration anyway?", default=True):
                    raise _CancelledError
        self.actions.append("Model backend set to OpenAI-compatible API.")
        self.console.print(f"[green]✓[/] Saved model config to [dim]{target}[/]\n")

    # -- step 2: AGENTS.md rules -------------------------------------------

    def _step_agent_rules(self) -> None:
        self._rule("2 · AGENTS.md rules")
        target = default_agents_target()
        self.console.print(
            "Datailor can install a managed block into your global "
            f"[bold]AGENTS.md[/] ([dim]{target}[/]) so agents call the preference "
            "tools before acting.\n"
        )
        if self._confirm("Preview the snippet?", default=False):
            from rich.syntax import Syntax

            preview = "\n".join(default_snippet_text().splitlines()[:18])
            self.console.print(Syntax(preview, "markdown", theme="ansi_dark", word_wrap=True))
            self.console.print("[dim]… truncated[/]\n")
        if not self._confirm("Install / update the AGENTS.md managed block?", default=True):
            self.console.print("[dim]Skipped AGENTS.md install.[/]\n")
            return
        result = install_agent_rules()
        action = "Updated" if result.changed else "Already current"
        self.actions.append(f"AGENTS.md managed block: {action.lower()}.")
        self.console.print(f"[green]✓[/] {action}: [dim]{result.target}[/]\n")

    # -- step 3: curate / auto mode ----------------------------------------

    def _step_mode(self) -> None:
        self._rule("3 · Fitting mode")
        settings = read_mode_settings()
        current = normalize_mode(settings.get("mode"))
        self.console.print(
            "Datailor Fitting periodically consolidates your preferences.\n"
            "[bold]Curate[/] proposes changes for your review first. "
            "[bold]Auto[/] applies high-confidence changes automatically.\n"
        )
        choice = self._select(
            "Choose a fitting mode",
            choices=[
                self.q.Choice("Curate — review changes before they apply (recommended)", value="curate"),
                self.q.Choice("Auto — apply high-confidence changes automatically", value="auto"),
            ],
            default=current,
        )
        if choice != current:
            settings["mode"] = choice
            write_mode_settings(settings)
        self.actions.append(f"Fitting mode set to {choice}.")
        self.console.print(f"[green]✓[/] Fitting mode: [bold]{choice}[/]\n")

    # -- step 4: MCP client integration ------------------------------------

    def _step_integrations(self, status) -> None:
        self._rule("4 · Agent integrations")
        data = status.to_dict() if hasattr(status, "to_dict") else {}
        installed_names = {
            str(a.get("name") or "").casefold()
            for a in data.get("discovered_agents", [])
            if a.get("installed")
        }
        self.console.print(
            "Register Datailor as an MCP server so your agents can call it. "
            "Detected clients are pre-selected.\n"
        )
        choices = []
        for name, profile in PROFILES.items():
            detected = name.casefold() in installed_names
            label = f"{profile.display_name}" + ("  [detected]" if detected else "")
            choices.append(self.q.Choice(label, value=name, checked=detected))
        selected = self._ask(
            self.q.checkbox("Select clients to configure (space to toggle)", choices=choices)
        )
        if not selected:
            self.console.print("[dim]No clients selected.[/]\n")
            return

        store_arg = self.store_path if os.getenv("PREFERENCE_STORE_PATH") else None
        from rich.table import Table

        table = Table(show_header=True, header_style="bold", box=None)
        table.add_column("Client")
        table.add_column("Result")
        table.add_column("Target", overflow="fold", style="dim")
        for client in selected:
            outcome = run_integrations(
                action="install",
                client=client,
                scope="default",
                backend=self.backend or os.getenv("PREFERENCE_MODEL_BACKEND", "heuristic"),
                store=store_arg,
                server_name=DEFAULT_SERVER_NAME,
            )
            for item in outcome.get("results", []):
                ok = item.get("ok")
                changed = item.get("changed")
                state = "installed" if changed else item.get("state", "")
                mark = "[green]✓[/]" if ok else "[red]✗[/]"
                table.add_row(PROFILES[client].display_name, f"{mark} {state}", item.get("target_path", ""))
        self.actions.append(f"Configured MCP clients: {', '.join(selected)}.")
        self.console.print(table)
        self.console.print()

    # -- step 5: full history scan -----------------------------------------

    def _step_scan(self) -> dict[str, Any] | None:
        self._rule("5 · History scan")
        self.console.print(
            "Datailor can scan your existing agent histories now to seed your "
            "preference store (a full cold-start capture).\n"
        )
        choice = self._select(
            "Run a history scan?",
            choices=[
                self.q.Choice("Full scan — extract and save preferences now", value="full"),
                self.q.Choice("Dry run — preview what would be captured", value="dry"),
                self.q.Choice("Skip — I'll run it later", value="skip"),
            ],
            default="full",
        )
        if choice == "skip":
            self.console.print("[dim]Skipped. Run later with[/] [bold]datailor cold-start-scan[/].\n")
            return None

        dry_run = choice == "dry"
        config = CaptureConfig.from_env()
        config.store_path = self.store_path
        summary: dict[str, Any] = {}
        from rich.progress import Progress, SpinnerColumn, TextColumn

        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
            transient=True,
        ) as progress:
            task = progress.add_task("Discovering agent histories…", total=None)

            def on_event(event: dict[str, Any]) -> None:
                kind = str(event.get("event") or "")
                if kind == "source_start":
                    progress.update(
                        task,
                        description=f"[{event.get('index')}/{event.get('total')}] "
                        f"Scanning {_source_name(event.get('source'))}…",
                    )
                elif kind == "capture_stage":
                    progress.update(task, description=f"  extracting from {event.get('file', '')}…")
                elif kind in {"source_done", "source_skipped", "scan_stopped"}:
                    line = render_cold_start_progress(event)
                    if line:
                        progress.console.print(f"[dim]{line}[/]")

            result = cold_start_scan(
                store_path=self.store_path,
                agent_hint=self.agent_hint,
                config=config,
                dry_run=dry_run,
                state_file=None,
                progress=on_event,
            )
            summary = summarize_cold_start_result(result.to_dict())

        self.console.print(f"[green]✓[/] {summary.get('status', 'Completed')}")
        self.actions.append(
            ("Dry-run scan: " if dry_run else "Scan: ")
            + f"{summary.get('preferences_added', 0)} added, "
            f"{summary.get('preferences_merged', 0)} merged, "
            f"{summary.get('preferences_conflicted', 0)} conflicts."
        )
        self.console.print()
        return summary

    # -- summary ------------------------------------------------------------

    def _summary(self, scan_summary: dict[str, Any] | None) -> None:
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        done = Table.grid(padding=(0, 1))
        done.add_column()
        for item in self.actions or ["Nothing changed."]:
            done.add_row(Text("• ", style="green") + Text(item))

        self.console.print(
            Panel(done, title=Text("Setup complete", style="bold green"), border_style="green", padding=(1, 2))
        )

        nxt = Table.grid(padding=(0, 2))
        nxt.add_column(style="bold cyan")
        nxt.add_column()
        nxt.add_row("datailor ui", "Review, confirm, reject, or correct preferences")
        nxt.add_row("datailor doctor", "Check onboarding & integration status")
        nxt.add_row("datailor integrate status --client all", "Inspect MCP client setup")
        self.console.print(Panel(nxt, title="Next steps", border_style="dim", padding=(1, 2)))

        if self._confirm("Open the Datailor UI now?", default=False):
            from .ui.server import serve_preference_panel

            def _on_ready(info) -> None:
                self.console.print(
                    Panel(
                        f"[bold green]{info.url}[/]\n[dim]Press Ctrl-C to stop the server and finish setup.[/]",
                        title=Text("Datailor UI", style="bold cyan"),
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )

            self.console.print("[dim]Starting the local UI…[/]")
            # Foreground server: keeps this process alive so the opened link works.
            serve_preference_panel(store_path=self.store_path, open_browser=True, on_ready=_on_ready)
            self.console.print("\n[dim]UI stopped. Reopen any time with[/] [bold]datailor ui[/].")
        else:
            self.console.print("[dim]Open the panel any time with[/] [bold]datailor ui[/].")

    # -- misc ---------------------------------------------------------------

    def _rule(self, label: str) -> None:
        self.console.rule(f"[bold]{label}", style="cyan")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _state_label(state: str) -> str:
    return {
        "not_initialized": "[yellow]not initialized[/]",
        "needs_capture": "[yellow]ready to capture[/]",
        "empty_no_sources": "[yellow]empty (no sources)[/]",
        "ready": "[green]ready[/]",
    }.get(state, state or "unknown")


def _source_name(source: Any) -> str:
    if isinstance(source, dict):
        return str(source.get("display_name") or source.get("agent") or "source")
    return "source"


def _verify_backend() -> tuple[bool, str]:
    """Best-effort connectivity check against the configured model endpoint.

    Issues a short-timeout GET to ``{base_url}/models`` with the API key. A
    2xx/4xx HTTP status (anything that is *not* a connection failure) is treated
    as "reachable" since auth/permission errors still prove the endpoint exists.
    """

    import urllib.error
    import urllib.request

    base_url = (os.getenv("PREFERENCE_MODEL_BASE_URL") or "").rstrip("/")
    api_key = os.getenv("PREFERENCE_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    if not base_url:
        return False, "no base URL configured."
    url = f"{base_url}/models"
    request = urllib.request.Request(url, method="GET")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return True, f"HTTP {response.status} from {url}"
    except urllib.error.HTTPError as exc:
        return True, f"endpoint reachable (HTTP {exc.code})."
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"{getattr(exc, 'reason', exc)}"
