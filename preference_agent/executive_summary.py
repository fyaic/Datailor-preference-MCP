from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backends import HeuristicBackend, OpenAICompatibleBackend, PreferenceModelBackend, build_backend
from .models import PreferenceRecord, now_iso
from .privacy import redact_sensitive
from .store import MarkdownPreferenceStore


SUMMARY_TITLE = "# Executive Summary"
MAX_SUMMARY_PREFERENCES = 60
MAX_CHANGED_PREFERENCES = 20


@dataclass(frozen=True)
class ExecutiveSummary:
    path: str
    text: str
    status: str
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutiveSummaryUpdate:
    path: str
    updated: bool
    skipped_reason: str = ""
    reason: str = ""
    used_model: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def default_summary_path(store_path: str | Path) -> Path:
    configured = os.getenv("PREFERENCE_EXECUTIVE_SUMMARY_PATH")
    if configured:
        return Path(configured)
    return Path(store_path).parent / ".summary" / "executive-summary.md"


def read_executive_summary(
    store_path: str | Path,
    summary_path: str | Path | None = None,
) -> ExecutiveSummary:
    path = Path(summary_path) if summary_path else default_summary_path(store_path)
    if not path.exists():
        return ExecutiveSummary(path=str(path), text="", status="missing")
    text = _strip_title(path.read_text(encoding="utf-8", errors="replace")).strip()
    updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone().isoformat(timespec="seconds")
    return ExecutiveSummary(path=str(path), text=text, status="ready" if text else "empty", updated_at=updated_at)


def refresh_executive_summary(
    store_path: str | Path,
    changed_records: list[PreferenceRecord] | None = None,
    backend: PreferenceModelBackend | None = None,
    force: bool = False,
    summary_path: str | Path | None = None,
) -> ExecutiveSummaryUpdate:
    path = Path(summary_path) if summary_path else default_summary_path(store_path)
    if not _summary_enabled():
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="disabled")

    records = MarkdownPreferenceStore(store_path).load()
    changed = _unique_records(changed_records or [])
    if not force and not changed:
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="no_delta")
    if not records:
        return _write_summary(path, _cold_start_summary(), reason="cold_start", used_model=False)

    current = read_executive_summary(store_path, path).text
    payload = {
        "current_summary": current,
        "new_or_changed_preferences": [_record_view(record) for record in changed[:MAX_CHANGED_PREFERENCES]],
        "all_preferences": [_record_view(record) for record in _rank_records(records)[:MAX_SUMMARY_PREFERENCES]],
    }
    try:
        model_update = _llm_summary_update(payload, backend=backend, force=force)
    except Exception as exc:
        fallback = _heuristic_summary(records)
        result = _write_summary(path, fallback, reason="fallback_after_model_error", used_model=False)
        return ExecutiveSummaryUpdate(
            path=result.path,
            updated=result.updated,
            reason=result.reason,
            used_model=False,
            error=str(exc),
        )

    if model_update is not None:
        should_update = bool(model_update.get("should_update", force))
        summary = str(model_update.get("executive_summary") or "").strip()
        reason = str(model_update.get("reason") or "model_update")
        if should_update and summary:
            return _write_summary(path, _clean_summary(summary), reason=reason, used_model=True)
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="model_kept_current", reason=reason, used_model=True)

    summary = _heuristic_summary(records)
    if not force and current.strip() == summary.strip():
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="unchanged")
    return _write_summary(path, summary, reason="heuristic_update", used_model=False)


def _llm_summary_update(
    payload: dict[str, Any],
    backend: PreferenceModelBackend | None,
    force: bool,
) -> dict[str, Any] | None:
    selected = _select_llm_backend(backend)
    if not selected:
        return None
    prompt_payload = dict(payload)
    prompt_payload["force_update"] = force
    return selected._chat_json(  # noqa: SLF001 - the local backend already owns OpenAI-compatible JSON transport.
        system=EXECUTIVE_SUMMARY_SYSTEM_PROMPT,
        user=json.dumps(prompt_payload, ensure_ascii=False),
    )


def _select_llm_backend(backend: PreferenceModelBackend | None) -> OpenAICompatibleBackend | None:
    if isinstance(backend, OpenAICompatibleBackend):
        return backend
    configured = os.getenv("PREFERENCE_EXECUTIVE_SUMMARY_BACKEND")
    if configured:
        selected = build_backend(configured)
        return selected if isinstance(selected, OpenAICompatibleBackend) else None
    return None if isinstance(backend, HeuristicBackend) else None


def _write_summary(path: Path, summary: str, reason: str, used_model: bool) -> ExecutiveSummaryUpdate:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join([SUMMARY_TITLE, "", _clean_summary(summary), ""])
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if existing.strip() == content.strip():
        return ExecutiveSummaryUpdate(path=str(path), updated=False, skipped_reason="unchanged", reason=reason, used_model=used_model)
    path.write_text(content, encoding="utf-8")
    return ExecutiveSummaryUpdate(path=str(path), updated=True, reason=reason, used_model=used_model)


def _heuristic_summary(records: list[PreferenceRecord]) -> str:
    active = [record for record in records if record.status == "active"]
    pending = [record for record in records if record.status != "active"]
    source = active or records
    lines: list[str] = []
    lines.append("系统目前理解到：你更看重能直接推进、可验证、少废话且不会破坏上下文的协作方式。")

    communication = _first_matching(source, ("简洁", "结论", "大纲", "回复", "回话", "分点", "长文", "展开"))
    execution = _first_matching(source, ("测试", "验证", "review", "代码", "实现", "工具", "编辑", "覆盖"))
    memory = _first_matching(source, ("文档", "沉淀", "回读", "中文", "Linear", "issue", "外部系统"))
    autonomy = _first_matching(source, ("确认", "询问", "反问", "直接", "自动", "自主", "不要问"))

    bullets = []
    if communication:
        bullets.append(f"沟通上，偏好 {communication}")
    if execution:
        bullets.append(f"执行上，偏好 {execution}")
    if memory:
        bullets.append(f"长期协作上，偏好 {memory}")
    if autonomy:
        bullets.append(f"节奏上，偏好 {autonomy}")
    for record in source:
        statement = _statement(record)
        if statement and statement not in bullets and len(bullets) < 5:
            bullets.append(statement)
    if bullets:
        lines.extend(f"- {item}" for item in bullets[:5])
    if pending:
        lines.append(f"另有 {len(pending)} 条偏好仍在观察或待确认，系统会先谨慎展示，不会把它们当作已完全生效的长期画像。")
    return "\n".join(lines)


def _cold_start_summary() -> str:
    return "偏好库仍处于冷启动状态。系统还没有形成稳定的用户画像，会先观察用户明确表达的长期偏好，再逐步更新这里。"


def _record_view(record: PreferenceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "title": redact_sensitive(record.title),
        "status": record.status,
        "confidence": record.confidence,
        "applies_to": redact_sensitive(record.applies_to),
        "preference": _statement(record),
        "triggers": [redact_sensitive(item) for item in record.triggers[:5]],
        "exceptions": [redact_sensitive(item) for item in record.exceptions[:5]],
    }


def _rank_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    confidence = {"high": 3, "medium": 2, "low": 1}
    return sorted(
        records,
        key=lambda record: (
            record.status == "active",
            confidence.get(record.confidence.lower(), 0),
            len(record.evidence),
            record.updated_at,
        ),
        reverse=True,
    )


def _unique_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    result: list[PreferenceRecord] = []
    seen: set[str] = set()
    for record in records:
        if record.id not in seen:
            seen.add(record.id)
            result.append(record)
    return result


def _first_matching(records: list[PreferenceRecord], markers: tuple[str, ...]) -> str:
    for record in records:
        text = _statement(record)
        lowered = text.casefold()
        if any(marker.casefold() in lowered for marker in markers):
            return text
    return ""


def _statement(record: PreferenceRecord) -> str:
    return redact_sensitive(" ".join((record.preference or record.summary or record.title).split()).strip())


def _clean_summary(summary: str) -> str:
    summary = redact_sensitive(summary)
    summary = _strip_title(summary)
    summary = re.sub(r"\n{3,}", "\n\n", summary).strip()
    return summary


def _strip_title(text: str) -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().casefold() in {SUMMARY_TITLE.casefold(), "# executive summary", "# 执行摘要", "## executive summary"}:
        lines.pop(0)
    return "\n".join(lines).strip()


def _summary_enabled() -> bool:
    return os.getenv("PREFERENCE_EXECUTIVE_SUMMARY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


EXECUTIVE_SUMMARY_SYSTEM_PROMPT = f"""你是个人偏好画像的 Executive Summary 生成器。

你的任务不是重复列出所有偏好，而是让用户打开面板时感觉“系统真的理解并记住了我”。

输入会包含：
- current_summary：当前摘要，可能为空。
- new_or_changed_preferences：本轮新增或变更的偏好。
- all_preferences：当前偏好库中较重要的偏好。
- force_update：是否强制刷新。

请先判断新增/变更的偏好是否足以更新 current_summary。若不足以更新，返回 should_update=false。
若需要更新，生成一份中文 Markdown 摘要：
- 2 到 5 条要点即可，宁可具体，不要空泛。
- 可以使用第二人称“你”，语气克制、准确、被理解，但不要肉麻。
- 只总结长期偏好、工作习惯、协作风格、信任边界和验证标准。
- 不要写路径、URL、issue 编号、一次性任务、密钥、临时对象名。
- 不要输出内部字段名、confidence、status、metadata。
- 不要把 pending/needs_review 写成已经绝对确认的偏好。

输出 JSON 对象：
{{
  "should_update": true,
  "reason": "为什么更新或不更新",
  "executive_summary": "Markdown 摘要文本，不要包含 {SUMMARY_TITLE}"
}}
"""
