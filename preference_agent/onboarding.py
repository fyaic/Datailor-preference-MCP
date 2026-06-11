from __future__ import annotations

import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent_rules import install_agent_rules
from .agent_discovery import cold_start_scan, discovery_report
from .backends import backend_status
from .capture_runner import CaptureConfig
from .cold_start_summary import render_onboarding_summary
from .kimi_hooks import install_kimi_hooks
from .models import PreferenceRecord, now_iso
from .paths import diagnostics
from .store import MarkdownPreferenceStore


PRODUCT_NAME = "Datailor"
DEFAULT_AGENT = "codex"


@dataclass
class PreferenceStoreStatus:
    path: str
    exists: bool
    empty: bool
    active: int = 0
    pending: int = 0
    total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OnboardingStatus:
    ok: bool
    product: str
    cli: str
    state: str
    store: PreferenceStoreStatus
    agent_hint: str = ""
    backend: str = ""
    backend_status: dict[str, Any] = field(default_factory=dict)
    python: str = ""
    platform: str = ""
    supported_sources: int = 0
    discovered_agents: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    install: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, Any] = field(default_factory=dict)
    mcp: dict[str, Any] = field(default_factory=dict)
    next_steps: list[str] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["store"] = self.store.to_dict()
        return data


def get_onboarding_status(
    store_path: str | Path,
    agent_hint: str = "",
    backend: str = "",
) -> OnboardingStatus:
    store = MarkdownPreferenceStore(store_path)
    store_status = _store_status(store)
    discovered = discovery_report(agent_hint=agent_hint)
    sources = discovered.get("sources", []) if isinstance(discovered, dict) else []
    state = _state(store_status=store_status, supported_sources=len(sources))
    diag = diagnostics(store.path)
    return OnboardingStatus(
        ok=True,
        product=PRODUCT_NAME,
        cli="datailor",
        state=state,
        store=store_status,
        agent_hint=agent_hint,
        backend=backend or os.getenv("PREFERENCE_MODEL_BACKEND") or "auto",
        backend_status=backend_status(backend or None),
        python=sys.version.split()[0],
        platform=platform.platform(),
        supported_sources=len(sources),
        discovered_agents=discovered.get("agents", []) if isinstance(discovered, dict) else [],
        sources=sources,
        install=diag.get("install", {}),
        paths=diag.get("paths", {}),
        mcp=diag.get("mcp", {}),
        next_steps=_next_steps(state, agent_hint=agent_hint),
        commands=_commands(agent_hint=agent_hint),
    )


def run_onboarding(
    store_path: str | Path,
    agent_hint: str = "",
    backend: str = "",
    mode: str = "",
    dry_run: bool = False,
    capture: bool = True,
    max_files: int = 0,
    max_minutes: float = 0.0,
    state_file: str | Path | None = None,
    open_ui: bool = False,
    host: str = "127.0.0.1",
    port: int = 8080,
    install_agent_rules_enabled: bool = True,
    agent_rules_target: str | Path | None = None,
    install_kimi_hooks_enabled: bool | None = None,
    kimi_hooks_target: str | Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    store = MarkdownPreferenceStore(store_path)
    store.ensure()
    before = get_onboarding_status(store.path, agent_hint=agent_hint, backend=backend).to_dict()
    scan = None
    if capture:
        config = CaptureConfig.from_env()
        config.store_path = store.path
        if mode:
            config.mode = mode
        if max_minutes:
            config.max_minutes = max_minutes
        scan_result = cold_start_scan(
            store_path=store.path,
            agent_hint=agent_hint,
            config=config,
            dry_run=dry_run,
            max_files=max_files,
            state_file=state_file,
            progress=progress,
        )
        scan = scan_result.to_dict()
    panel = None
    if open_ui and not dry_run:
        from .ui.server import open_preference_panel

        panel = open_preference_panel(store_path=store.path, host=host, port=port, open_browser=True)
    agent_rules = None
    if install_agent_rules_enabled and not dry_run:
        agent_rules = install_agent_rules(target=agent_rules_target).to_dict()
    kimi_hooks = None
    should_install_kimi_hooks = (
        agent_hint.strip().casefold() == "kimi"
        if install_kimi_hooks_enabled is None
        else install_kimi_hooks_enabled
    )
    if should_install_kimi_hooks and not dry_run:
        kimi_hooks = install_kimi_hooks(
            target=kimi_hooks_target,
            agent=agent_hint or "kimi",
            backend=backend or os.getenv("PREFERENCE_MODEL_BACKEND") or "auto",
            store=store.path,
        ).to_dict()
    after = get_onboarding_status(store.path, agent_hint=agent_hint, backend=backend).to_dict()
    result = {
        "ok": True,
        "product": PRODUCT_NAME,
        "cli": "datailor",
        "dry_run": dry_run,
        "capture_requested": capture,
        "before": before,
        "scan": scan,
        "after": after,
        "panel": panel,
        "agent_rules": agent_rules,
        "kimi_hooks": kimi_hooks,
    }
    result["summary_text"] = render_onboarding_summary(result)
    return result


def _store_status(store: MarkdownPreferenceStore) -> PreferenceStoreStatus:
    exists = store.exists()
    records = store.load() if exists else []
    active = [record for record in records if record.status == "active"]
    pending = [record for record in records if record.status != "active"]
    return PreferenceStoreStatus(
        path=str(store.path),
        exists=exists,
        empty=len(records) == 0,
        active=len(active),
        pending=len(pending),
        total=len(records),
    )


def _state(store_status: PreferenceStoreStatus, supported_sources: int) -> str:
    if not store_status.exists:
        return "not_initialized"
    if store_status.total > 0:
        return "ready"
    if supported_sources > 0:
        return "needs_capture"
    return "empty_no_sources"


def _next_steps(state: str, agent_hint: str = "") -> list[str]:
    agent = agent_hint or DEFAULT_AGENT
    if state == "not_initialized":
        return [
            f"Run `datailor onboard --agent {agent}` to create the preference store and scan discovered local histories.",
            "Run `datailor integrate status --client all` to inspect IDE/client MCP setup.",
            "Preview IDE/client setup with `datailor integrate install --client all --dry-run`.",
        ]
    if state == "needs_capture":
        return [
            f"Run `datailor onboard --agent {agent}` to start cold-start capture.",
            "Run `datailor ui` after capture to review the Manifesto.",
            "Preview IDE/client setup with `datailor integrate install --client all --dry-run`.",
        ]
    if state == "empty_no_sources":
        return [
            "No supported local history source was found. Import a session with `datailor capture --source <path>`.",
            "Run `datailor integrate status --client all` to inspect IDE/client MCP setup.",
            "Preview IDE/client setup with `datailor integrate install --client all --dry-run`.",
        ]
    return [
        "Run `datailor ui` to review, confirm, reject, or correct preferences.",
        "Keep the MCP configured so agents can call `get_preference_decision` before acting.",
        "Run `datailor integrate status --client all` to inspect IDE/client MCP setup.",
        "Preview IDE/client setup with `datailor integrate install --client all --dry-run`.",
    ]


def _commands(agent_hint: str) -> dict[str, str]:
    agent = agent_hint or DEFAULT_AGENT
    return {
        "doctor": f"datailor doctor --agent {agent}",
        "onboard": f"datailor onboard --agent {agent}",
        "open_ui": "datailor ui",
        "mcp_config": f"datailor mcp-config --agent {agent}",
        "mcp_stdio": f"datailor-mcp --agent {agent}",
        "integrate_status": "datailor integrate status --client all",
        "integrate_preview": "datailor integrate install --client all --dry-run",
    }
