"""User-level environment persistence for Datailor.

The CLI and the MCP server both read configuration from environment variables
(``PREFERENCE_MODEL_BACKEND``, ``PREFERENCE_MODEL_API_KEY``, ...). Historically
those were only set by manually sourcing ``.env.local``. The interactive setup
wizard needs a place to persist API/model config so that later ``datailor`` and
``datailor-mcp`` runs pick it up automatically.

This module provides a small, dependency-free dotenv reader/writer:

* ``user_env_path()`` - the canonical user-level env file under the data dir.
* ``load_local_env()`` - load user env file + project ``.env.local`` into
  ``os.environ`` without clobbering values that are already set (real env wins).
* ``update_env_file()`` - upsert ``KEY=VALUE`` pairs while preserving comments
  and unrelated lines.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from .paths import default_data_dir


def user_env_path() -> Path:
    configured = os.getenv("DATAILOR_ENV_FILE")
    if configured:
        return Path(configured)
    return default_data_dir() / "datailor.env"


def project_env_path() -> Path:
    return Path.cwd() / ".env.local"


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_quotes(value.strip())
    return values


def load_env_file(path: str | Path, *, override: bool = False) -> dict[str, str]:
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values = parse_env_text(text)
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def load_local_env(*, override: bool = False) -> dict[str, str]:
    """Load persisted config into ``os.environ``.

    Real process environment takes precedence by default so that explicit
    ``KEY=value datailor ...`` invocations and parent-shell exports keep
    working. Project ``.env.local`` overrides the user-level file.
    """

    loaded: dict[str, str] = {}
    loaded.update(load_env_file(user_env_path(), override=override))
    loaded.update(load_env_file(project_env_path(), override=override))
    return loaded


def _needs_quoting(value: str) -> bool:
    return value == "" or any(ch.isspace() for ch in value) or "#" in value


def _format_value(value: str) -> str:
    text = str(value)
    if _needs_quoting(text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def update_env_file(
    values: Mapping[str, str],
    path: str | Path | None = None,
    *,
    apply_to_process: bool = True,
) -> Path:
    """Upsert ``values`` into the env file, preserving other lines/comments.

    When ``apply_to_process`` is true the values are also written into
    ``os.environ`` so the current process sees them immediately.
    """

    target = Path(path) if path else user_env_path()
    existing = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in existing:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key.lower().startswith("export "):
                key = key[len("export ") :].strip()
            if key in remaining:
                out.append(f"{key}={_format_value(remaining.pop(key))}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={_format_value(value)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(out).rstrip("\n") + "\n"
    target.write_text(text, encoding="utf-8")
    if apply_to_process:
        for key, value in values.items():
            os.environ[key] = str(value)
    return target


def is_datailor_enabled() -> bool:
    """Return whether Datailor preference injection is enabled.

    Re-reads from the persisted env file so that CLI toggles are picked up
    by long-running MCP server processes without a restart.
    """
    env_path = user_env_path()
    file_values: dict[str, str] = {}
    if env_path.exists():
        try:
            file_values = parse_env_text(env_path.read_text(encoding="utf-8"))
        except OSError:
            pass
    value = os.getenv("DATAILOR_ENABLED", file_values.get("DATAILOR_ENABLED", "true"))
    return str(value).strip().lower() not in {"false", "0", "no", "off", "disabled"}
