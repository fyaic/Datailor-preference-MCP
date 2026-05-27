from __future__ import annotations

import os
import platform
import sys
from importlib import metadata
from pathlib import Path
from shutil import which
from typing import Any


PACKAGE_NAME = "datailor-preference-mcp"
PRODUCT_DIR_NAME = "Datailor"


def default_data_dir() -> Path:
    configured = os.getenv("DATAILOR_DATA_DIR") or os.getenv("PREFERENCE_DATA_DIR")
    if configured:
        return Path(configured)
    system = platform.system().lower()
    if system.startswith("win"):
        base = os.getenv("APPDATA")
        return Path(base) / PRODUCT_DIR_NAME if base else Path.home() / "AppData" / "Roaming" / PRODUCT_DIR_NAME
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / PRODUCT_DIR_NAME
    base = os.getenv("XDG_DATA_HOME")
    return Path(base) / "datailor" if base else Path.home() / ".local" / "share" / "datailor"


def default_store_path() -> Path:
    configured = os.getenv("PREFERENCE_STORE_PATH")
    return Path(configured) if configured else default_data_dir() / "个人偏好.md"


def default_checkpoint_dir() -> Path:
    configured = os.getenv("PREFERENCE_CHECKPOINT_DIR")
    return Path(configured) if configured else default_data_dir() / ".capture-state"


def default_candidate_dir() -> Path:
    configured = os.getenv("PREFERENCE_CANDIDATE_DIR")
    return Path(configured) if configured else default_data_dir() / ".debug-capture"


def default_incremental_state() -> Path:
    configured = os.getenv("PREFERENCE_INCREMENTAL_STATE")
    return Path(configured) if configured else default_checkpoint_dir() / "incremental-scan.json"


def default_injection_dir() -> Path:
    configured = os.getenv("PREFERENCE_INJECTION_DIR")
    return Path(configured) if configured else default_data_dir() / ".injection"


def default_feedback_log() -> Path:
    configured = os.getenv("PREFERENCE_FEEDBACK_LOG")
    return Path(configured) if configured else default_data_dir() / ".feedback" / "feedback-log.jsonl"


def default_ui_dir() -> Path:
    configured = os.getenv("PREFERENCE_UI_DIR")
    return Path(configured) if configured else default_data_dir() / ".ui"


def default_weave_dir() -> Path:
    configured = os.getenv("DATAILOR_WEAVE_DIR") or os.getenv("PREFERENCE_WEAVE_DIR")
    return Path(configured) if configured else default_data_dir() / ".weave"


def default_hooks_dir() -> Path:
    configured = os.getenv("PREFERENCE_HOOKS_DIR")
    return Path(configured) if configured else default_data_dir() / ".hooks"


def default_embedding_cache_path() -> Path:
    configured = os.getenv("PREFERENCE_EMBEDDING_CACHE_PATH")
    return Path(configured) if configured else default_checkpoint_dir() / "embedding-cache.json"


def install_kind() -> str:
    executable = Path(sys.executable).as_posix().casefold()
    if "pipx" in executable:
        return "pipx"
    direct_url = _distribution_direct_url()
    if direct_url:
        if _direct_url_is_editable(direct_url):
            return "editable"
        if '"vcs_info"' in direct_url:
            return "git"
        return "wheel"
    package_root = Path(__file__).resolve().parents[1]
    if (package_root / ".git").exists() or (package_root.parent / ".git").exists():
        return "editable"
    return "unknown"


def package_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return "0.1.0"


def command_available(name: str) -> bool:
    return which(name) is not None


def diagnostics(store_path: str | Path | None = None) -> dict[str, Any]:
    store = Path(store_path) if store_path else default_store_path()
    return {
        "cli": {
            "executable": sys.executable,
            "version": package_version(),
        },
        "install": {
            "kind": install_kind(),
            "package": PACKAGE_NAME,
        },
        "paths": {
            "data_dir": str(default_data_dir()),
            "store": str(store),
            "checkpoint_dir": str(default_checkpoint_dir()),
            "candidate_dir": str(default_candidate_dir()),
            "feedback_log": str(default_feedback_log()),
            "hooks_dir": str(default_hooks_dir()),
            "injection_dir": str(default_injection_dir()),
            "ui_dir": str(default_ui_dir()),
            "weave_dir": str(default_weave_dir()),
        },
        "mcp": {
            "command": "datailor-mcp",
            "command_available": command_available("datailor-mcp"),
        },
    }


def _distribution_direct_url() -> str:
    try:
        return metadata.distribution(PACKAGE_NAME).read_text("direct_url.json") or ""
    except metadata.PackageNotFoundError:
        return ""


def _direct_url_is_editable(text: str) -> bool:
    return '"editable": true' in text.casefold()
