from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any


RULES_START = "<!-- datailor-preference:start -->"
RULES_END = "<!-- datailor-preference:end -->"
LEGACY_RULES_START = "<!-- bondie-preference:start -->"
LEGACY_RULES_END = "<!-- bondie-preference:end -->"
SNIPPET_RESOURCE_PACKAGE = "preference_agent.resources"
SNIPPET_RESOURCE_NAME = "AGENTS-snippet.md"


@dataclass(frozen=True)
class AgentRulesInstallResult:
    ok: bool
    target: str
    snippet: str
    changed: bool
    action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_agents_target() -> Path:
    return Path.home() / "AGENTS.md"


def default_snippet_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "AGENTS-snippet.md"


def default_snippet_label() -> str:
    return f"package:{SNIPPET_RESOURCE_PACKAGE}/{SNIPPET_RESOURCE_NAME}"


def default_snippet_text() -> str:
    try:
        return files(SNIPPET_RESOURCE_PACKAGE).joinpath(SNIPPET_RESOURCE_NAME).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, ModuleNotFoundError):
        return default_snippet_path().read_text(encoding="utf-8").strip()


def install_agent_rules(
    target: str | Path | None = None,
    snippet: str | Path | None = None,
) -> AgentRulesInstallResult:
    target_path = Path(target) if target else default_agents_target()
    if snippet:
        snippet_path = Path(snippet)
        snippet_text = snippet_path.read_text(encoding="utf-8").strip()
        snippet_label = str(snippet_path)
    else:
        snippet_text = default_snippet_text()
        snippet_label = default_snippet_label()
    block = "\n".join([RULES_START, "", snippet_text, "", RULES_END, ""])
    existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    action = "append"
    existing_start, existing_end = _find_existing_markers(existing)
    if existing_start and existing_end:
        before, rest = existing.split(existing_start, 1)
        _old, after = rest.split(existing_end, 1)
        updated = before.rstrip() + "\n\n" + block + after.lstrip()
        action = "replace"
    else:
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
    changed = updated != existing
    if changed:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(updated, encoding="utf-8")
    return AgentRulesInstallResult(
        ok=True,
        target=str(target_path),
        snippet=snippet_label,
        changed=changed,
        action=action,
    )


def _find_existing_markers(existing: str) -> tuple[str, str] | tuple[None, None]:
    if RULES_START in existing and RULES_END in existing:
        return RULES_START, RULES_END
    if LEGACY_RULES_START in existing and LEGACY_RULES_END in existing:
        return LEGACY_RULES_START, LEGACY_RULES_END
    return None, None
