from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .fitting_store import FittingJobStore
from .models import now_iso
from .paths import default_ui_dir


VALID_FITTING_MODES = {"auto", "curate"}
DEFAULT_FITTING_MODE = "curate"


@dataclass(frozen=True)
class FittingTriggerConfig:
    enabled: bool = True
    trigger_records: int = 5
    trigger_days: float = 7.0
    cooldown_minutes: float = 60.0
    max_files: int = 0
    max_minutes: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FittingTriggerDecision:
    should_trigger: bool
    reason: str
    mode: str
    review: bool
    auto_apply: bool
    config: FittingTriggerConfig
    state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["config"] = self.config.to_dict()
        return data


def default_fitting_state() -> dict[str, Any]:
    return {
        "auto_enabled": True,
        "last_fitting_at": "",
        "last_job_id": "",
        "pending_plan_job_id": None,
        "records_since_last": 0,
        "total_sessions_since_last": 0,
        "auto_applied_count": 0,
        "user_reviewed_count": 0,
        "last_result": {},
    }


def default_mode_settings() -> dict[str, Any]:
    return {
        "mode": DEFAULT_FITTING_MODE,
        "theme": "light",
        "language": "en",
        "fitting": default_fitting_state(),
    }


def default_settings_path() -> Path:
    return default_ui_dir() / "settings.json"


def read_mode_settings(path: str | Path | None = None) -> dict[str, Any]:
    settings = default_mode_settings()
    settings_path = Path(path) if path else default_settings_path()
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            for key in ("mode", "theme", "language"):
                if key in data:
                    settings[key] = str(data[key])
            if isinstance(data.get("fitting"), dict):
                fitting = default_fitting_state()
                fitting.update(data["fitting"])
                settings["fitting"] = fitting
    settings["mode"] = normalize_mode(settings.get("mode"))
    fitting_state = default_fitting_state()
    if isinstance(settings.get("fitting"), dict):
        fitting_state.update(settings["fitting"])
    settings["fitting"] = fitting_state
    return settings


def write_mode_settings(settings: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    settings_path = Path(path) if path else default_settings_path()
    payload = default_mode_settings()
    payload.update({key: settings.get(key, payload[key]) for key in ("theme", "language")})
    payload["mode"] = normalize_mode(settings.get("mode"))
    fitting_state = default_fitting_state()
    if isinstance(settings.get("fitting"), dict):
        fitting_state.update(settings["fitting"])
    payload["fitting"] = fitting_state
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def update_fitting_state(path: str | Path | None = None, **patch: Any) -> dict[str, Any]:
    settings = read_mode_settings(path)
    fitting = dict(settings.get("fitting") or {})
    fitting.update(patch)
    settings["fitting"] = fitting
    return write_mode_settings(settings, path)


def mark_fitting_reviewed(job_id: str, path: str | Path | None = None, accepted: int = 0, rejected: bool = False) -> dict[str, Any]:
    settings = read_mode_settings(path)
    fitting = dict(settings.get("fitting") or {})
    if fitting.get("pending_plan_job_id") == job_id:
        fitting["pending_plan_job_id"] = None
    fitting["user_reviewed_count"] = int(fitting.get("user_reviewed_count") or 0) + 1
    fitting["last_reviewed_job_id"] = job_id
    fitting["last_review_action"] = "rejected" if rejected else "accepted"
    fitting["last_reviewed_change_count"] = accepted
    settings["fitting"] = fitting
    return write_mode_settings(settings, path)


def record_session_and_decide(
    store_path: str | Path,
    changed_records: int,
    settings_path: str | Path | None = None,
    fitting_dir: str | Path | None = None,
) -> FittingTriggerDecision:
    settings = read_mode_settings(settings_path)
    fitting = dict(settings.get("fitting") or {})
    fitting["records_since_last"] = int(fitting.get("records_since_last") or 0) + max(0, int(changed_records or 0))
    fitting["total_sessions_since_last"] = int(fitting.get("total_sessions_since_last") or 0) + 1
    settings["fitting"] = fitting
    write_mode_settings(settings, settings_path)
    return should_trigger_fitting(store_path=store_path, settings=settings, fitting_dir=fitting_dir)


def should_trigger_fitting(
    store_path: str | Path,
    settings: dict[str, Any] | None = None,
    settings_path: str | Path | None = None,
    fitting_dir: str | Path | None = None,
    now: datetime | None = None,
) -> FittingTriggerDecision:
    settings = settings or read_mode_settings(settings_path)
    mode = normalize_mode(settings.get("mode"))
    fitting = dict(settings.get("fitting") or {})
    config = fitting_trigger_config(settings)
    review = mode == "curate"
    auto_apply = mode == "auto"
    if not config.enabled or not _bool(fitting.get("auto_enabled"), True):
        return _decision(False, "disabled", mode, review, auto_apply, config, fitting)
    running = _running_job(fitting_dir)
    if running:
        return _decision(False, "fitting_already_running", mode, review, auto_apply, config, fitting)
    if mode == "curate" and fitting.get("pending_plan_job_id"):
        return _decision(False, "pending_plan_requires_review", mode, review, auto_apply, config, fitting)
    current = now or datetime.now(timezone.utc).astimezone()
    last = _parse_time(str(fitting.get("last_fitting_at") or ""))
    if last and current - last < timedelta(minutes=config.cooldown_minutes):
        return _decision(False, "cooldown_active", mode, review, auto_apply, config, fitting)
    records_since_last = int(fitting.get("records_since_last") or 0)
    if config.trigger_records > 0 and records_since_last >= config.trigger_records:
        return _decision(True, "record_threshold", mode, review, auto_apply, config, fitting)
    if last and current - last >= timedelta(days=config.trigger_days):
        return _decision(True, "time_threshold", mode, review, auto_apply, config, fitting)
    if not last and _store_has_records(store_path) and config.trigger_records <= 0:
        return _decision(True, "first_run", mode, review, auto_apply, config, fitting)
    return _decision(False, "below_threshold", mode, review, auto_apply, config, fitting)


def fitting_trigger_config(settings: dict[str, Any] | None = None) -> FittingTriggerConfig:
    fitting = settings.get("fitting") if isinstance(settings, dict) else {}
    fitting = fitting if isinstance(fitting, dict) else {}
    return FittingTriggerConfig(
        enabled=_env_flag("PREFERENCE_FITTING_AUTO", _bool(fitting.get("auto_enabled"), True)),
        trigger_records=_env_int("PREFERENCE_FITTING_TRIGGER_RECORDS", _int(fitting.get("trigger_records"), 5)),
        trigger_days=_env_float("PREFERENCE_FITTING_TRIGGER_DAYS", _float(fitting.get("trigger_days"), 7.0)),
        cooldown_minutes=_env_float(
            "PREFERENCE_FITTING_COOLDOWN_MINUTES",
            _float(fitting.get("cooldown_minutes"), 60.0),
        ),
        max_files=_env_int("PREFERENCE_FITTING_MAX_FILES", _int(fitting.get("max_files"), 0)),
        max_minutes=_env_float("PREFERENCE_FITTING_MAX_MINUTES", _float(fitting.get("max_minutes"), 0.0)),
    )


def normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().casefold()
    if mode == "autonomous":
        return "auto"
    if mode == "curated":
        return "curate"
    return mode if mode in VALID_FITTING_MODES else DEFAULT_FITTING_MODE


def _decision(
    trigger: bool,
    reason: str,
    mode: str,
    review: bool,
    auto_apply: bool,
    config: FittingTriggerConfig,
    state: dict[str, Any],
) -> FittingTriggerDecision:
    return FittingTriggerDecision(
        should_trigger=trigger,
        reason=reason,
        mode=mode,
        review=review,
        auto_apply=auto_apply,
        config=config,
        state=dict(state),
    )


def _running_job(fitting_dir: str | Path | None) -> str:
    for item in FittingJobStore(fitting_dir).list_jobs(limit=5):
        if item.get("status") == "running":
            return str(item.get("job_id") or "")
    return ""


def _store_has_records(store_path: str | Path) -> bool:
    path = Path(store_path)
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("```json preference-record"):
                    return True
                if stripped.startswith("- ") and not stripped.casefold().startswith("- no "):
                    return True
    except OSError:
        return False
    return False


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}
