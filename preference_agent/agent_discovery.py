from __future__ import annotations

import os
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import Any, Callable

from .capture_runner import CaptureConfig
from .cold_start_summary import render_cold_start_summary, summarize_cold_start_result
from .incremental import IncrementalScanResult, scan_incremental
from .models import now_iso


@dataclass(frozen=True)
class AgentHistorySpec:
    name: str
    display_name: str
    root_templates: dict[str, str]
    history_templates: dict[str, list[str]]
    supported: bool = True
    format: str = "jsonl"
    unsupported_reason: str = ""


@dataclass
class AgentHistorySource:
    agent: str
    display_name: str
    path: str
    format: str
    latest_mtime: float
    size_bytes: int
    supported: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["latest_mtime_iso"] = _mtime_iso(self.latest_mtime)
        return data


@dataclass
class DiscoveredAgent:
    name: str
    display_name: str
    installed: bool
    supported: bool
    root_paths: list[str] = field(default_factory=list)
    sources: list[AgentHistorySource] = field(default_factory=list)
    unsupported_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sources"] = [source.to_dict() for source in self.sources]
        return data


@dataclass
class ColdStartScanResult:
    ok: bool
    store: str
    dry_run: bool
    agent_hint: str = ""
    discovered_agents: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    scan_results: list[dict[str, Any]] = field(default_factory=list)
    scanned_sources: int = 0
    changed_files: int = 0
    total_files_seen: int = 0
    skipped_sources: list[dict[str, str]] = field(default_factory=list)
    state_file: str = ""
    stopped_reason: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    summary_text: str = ""
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


AGENT_HISTORY_SPECS = [
    AgentHistorySpec(
        name="claude",
        display_name="Claude",
        root_templates={
            "windows": "~/.claude",
            "darwin": "~/.claude",
            "linux": "~/.config/claude",
        },
        history_templates={
            "windows": ["~/.claude/history.jsonl"],
            "darwin": ["~/.claude/history.jsonl"],
            "linux": ["~/.config/claude/history.jsonl", "~/.claude/history.jsonl"],
        },
    ),
    AgentHistorySpec(
        name="codex",
        display_name="Codex",
        root_templates={
            "windows": "~/.codex",
            "darwin": "~/.codex",
            "linux": "~/.config/codex",
        },
        history_templates={
            "windows": ["~/.codex/history.jsonl"],
            "darwin": ["~/.codex/history.jsonl"],
            "linux": ["~/.config/codex/history.jsonl", "~/.codex/history.jsonl"],
        },
    ),
    AgentHistorySpec(
        name="kimi",
        display_name="Kimi CLI",
        root_templates={
            "windows": "~/.kimi",
            "darwin": "~/.kimi",
            "linux": "~/.kimi",
        },
        history_templates={
            "windows": ["~/.kimi/user-history/*.jsonl"],
            "darwin": ["~/.kimi/user-history/*.jsonl"],
            "linux": ["~/.kimi/user-history/*.jsonl"],
        },
    ),
    AgentHistorySpec(
        name="openclaw",
        display_name="OpenClaw",
        root_templates={
            "windows": "~/.openclaw",
            "darwin": "~/.openclaw",
            "linux": "~/.openclaw",
        },
        history_templates={
            "windows": ["~/.openclaw/agents/*/sessions/*.jsonl"],
            "darwin": ["~/.openclaw/agents/*/sessions/*.jsonl"],
            "linux": ["~/.openclaw/agents/*/sessions/*.jsonl"],
        },
    ),
    AgentHistorySpec(
        name="cursor",
        display_name="Cursor",
        root_templates={
            "windows": "%APPDATA%/Cursor",
            "darwin": "~/Library/Application Support/Cursor",
            "linux": "~/.config/Cursor",
        },
        history_templates={"windows": [], "darwin": [], "linux": []},
        supported=False,
        format="indexeddb-leveldb",
        unsupported_reason="Cursor stores chat history in IndexedDB/LevelDB; JSONL adapter is not implemented yet.",
    ),
    AgentHistorySpec(
        name="trae",
        display_name="Trae",
        root_templates={
            "windows": "%APPDATA%/Trae",
            "darwin": "~/Library/Application Support/Trae",
            "linux": "~/.config/Trae",
        },
        history_templates={"windows": [], "darwin": [], "linux": []},
        supported=False,
        format="indexeddb-leveldb",
        unsupported_reason="Trae stores chat history in IndexedDB/LevelDB; JSONL adapter is not implemented yet.",
    ),
]


def detect_installed_agents(
    home: str | Path | None = None,
    system: str | None = None,
) -> list[DiscoveredAgent]:
    platform_key = _platform_key(system)
    return [
        _discover_agent(spec, platform_key=platform_key, home=home)
        for spec in AGENT_HISTORY_SPECS
    ]


def auto_discover_sources(
    agent_hint: str = "",
    home: str | Path | None = None,
    system: str | None = None,
) -> list[AgentHistorySource]:
    agents = detect_installed_agents(home=home, system=system)
    sources = [
        source
        for agent in agents
        if agent.supported
        for source in agent.sources
    ]
    return _sort_sources(sources, agent_hint=agent_hint)


def cold_start_scan(
    store_path: str | Path,
    agent_hint: str = "",
    config: CaptureConfig | None = None,
    dry_run: bool = False,
    max_files: int = 0,
    state_file: str | Path | None = None,
    home: str | Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> ColdStartScanResult:
    config = config or CaptureConfig.from_env()
    config.store_path = Path(store_path)
    config.include_agent_rules = False
    state_path = Path(state_file) if state_file else _default_discovery_state(config)
    discovered = detect_installed_agents(home=home)
    sources = _sort_sources(
        [
            source
            for agent in discovered
            if agent.supported
            for source in agent.sources
        ],
        agent_hint=agent_hint,
    )
    result = ColdStartScanResult(
        ok=True,
        store=str(config.store_path),
        dry_run=dry_run,
        agent_hint=agent_hint,
        discovered_agents=[agent.to_dict() for agent in discovered],
        sources=[source.to_dict() for source in sources],
        state_file=str(state_path),
    )
    _emit(
        progress,
        {
            "event": "scan_start",
            "store": str(config.store_path),
            "dry_run": dry_run,
            "sources": result.sources,
            "discovered_agents": result.discovered_agents,
        },
    )
    remaining = max_files
    total = len(sources)
    for index, source in enumerate(sources, start=1):
        if remaining == 0 and max_files:
            result.stopped_reason = "max_files"
            _emit(progress, {"event": "scan_stopped", "reason": "max_files"})
            break
        _emit(progress, {"event": "source_start", "index": index, "total": total, "source": source.to_dict()})
        try:
            scan = scan_incremental(
                source.path,
                config=config,
                state_file=state_path,
                dry_run=dry_run,
                max_files=1 if max_files else 0,
                progress=_source_progress(progress, index=index, total=total, source=source),
            )
        except Exception as exc:
            result.skipped_sources.append({"source": source.path, "reason": str(exc)})
            _emit(
                progress,
                {
                    "event": "source_skipped",
                    "index": index,
                    "total": total,
                    "source": source.to_dict(),
                    "reason": str(exc),
                },
            )
            continue
        result.scan_results.append(scan.to_dict())
        result.scanned_sources += 1
        result.changed_files += len(scan.changed_files)
        result.total_files_seen += scan.scanned_files
        _emit(
            progress,
            {
                "event": "source_done",
                "index": index,
                "total": total,
                "source": source.to_dict(),
                "scan": scan.to_dict(),
            },
        )
        if max_files and scan.changed_files:
            remaining -= len(scan.changed_files)
            if remaining <= 0:
                result.stopped_reason = "max_files"
                _emit(progress, {"event": "scan_stopped", "reason": "max_files"})
                break
    result.summary = summarize_cold_start_result(result)
    result.summary_text = render_cold_start_summary(result)
    return result


def _source_progress(
    progress: Callable[[dict[str, Any]], None] | None,
    index: int,
    total: int,
    source: AgentHistorySource,
) -> Callable[[dict[str, Any]], None] | None:
    if not progress:
        return None

    def _progress(event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("index", index)
        payload.setdefault("total", total)
        payload.setdefault("source", source.to_dict())
        progress(payload)

    return _progress


def _emit(progress: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if progress:
        progress(event)


def discovery_report(
    agent_hint: str = "",
    home: str | Path | None = None,
) -> dict[str, Any]:
    agents = detect_installed_agents(home=home)
    sources = _sort_sources(
        [source for agent in agents if agent.supported for source in agent.sources],
        agent_hint=agent_hint,
    )
    return {
        "ok": True,
        "agent_hint": agent_hint,
        "agents": [agent.to_dict() for agent in agents],
        "sources": [source.to_dict() for source in sources],
        "supported_sources": len(sources),
        "updated_at": now_iso(),
    }


def _discover_agent(
    spec: AgentHistorySpec,
    platform_key: str,
    home: str | Path | None,
) -> DiscoveredAgent:
    root_templates = _templates_for_platform(spec.root_templates, platform_key)
    history_templates = _templates_for_platform(spec.history_templates, platform_key)
    root_paths = [_expand_path(template, home=home) for template in root_templates]
    installed = any(path.exists() for path in root_paths)
    sources = _history_sources(spec, history_templates, home=home) if spec.supported else []
    installed = installed or bool(sources)
    return DiscoveredAgent(
        name=spec.name,
        display_name=spec.display_name,
        installed=installed,
        supported=spec.supported,
        root_paths=[str(path) for path in root_paths],
        sources=sources,
        unsupported_reason=spec.unsupported_reason,
    )


def _history_sources(
    spec: AgentHistorySpec,
    history_templates: list[str],
    home: str | Path | None,
) -> list[AgentHistorySource]:
    sources: list[AgentHistorySource] = []
    seen: set[Path] = set()
    for template in history_templates:
        pattern = _expand_pattern(template, home=home)
        matches = [Path(item) for item in sorted(glob(str(pattern)))] if _has_glob(pattern) else [pattern]
        for path in matches:
            if not path.exists() or not path.is_file():
                continue
            if spec.name == "openclaw" and path.name.endswith(".trajectory.jsonl"):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            stat = path.stat()
            sources.append(
                AgentHistorySource(
                    agent=spec.name,
                    display_name=spec.display_name,
                    path=str(path),
                    format=spec.format,
                    latest_mtime=stat.st_mtime,
                    size_bytes=stat.st_size,
                )
            )
    return sources


def _sort_sources(
    sources: list[AgentHistorySource],
    agent_hint: str = "",
) -> list[AgentHistorySource]:
    hint = agent_hint.strip().casefold()
    return sorted(
        sources,
        key=lambda source: (
            0 if hint and source.agent.casefold() == hint else 1,
            -source.latest_mtime,
            source.agent,
            source.path,
        ),
    )


def _templates_for_platform(mapping: dict[str, str] | dict[str, list[str]], platform_key: str) -> list[str]:
    value = mapping.get(platform_key) or mapping.get("linux") or []
    if isinstance(value, list):
        return value
    return [value]


def _platform_key(system: str | None = None) -> str:
    name = (system or platform.system()).strip().lower()
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "darwin"
    return "linux"


def _expand_path(template: str, home: str | Path | None = None) -> Path:
    return Path(_expand_template(template, home=home))


def _expand_pattern(template: str, home: str | Path | None = None) -> Path:
    return Path(_expand_template(template, home=home))


def _expand_template(template: str, home: str | Path | None = None) -> str:
    base_home = Path(home or os.getenv("PREFERENCE_DISCOVERY_HOME") or Path.home())
    expanded = template.replace("~", str(base_home), 1) if template.startswith("~") else template
    return os.path.expandvars(expanded)


def _has_glob(path: Path) -> bool:
    return any(char in str(path) for char in "*?[")


def _default_discovery_state(config: CaptureConfig) -> Path:
    return config.checkpoint_dir / "agent-discovery-incremental.json"


def _mtime_iso(mtime: float) -> str:
    return now_iso() if not mtime else datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds")
