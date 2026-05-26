from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from typing import Any

from .live_confidence import LiveConfidence, live_confidence, should_inject_live_confidence
from .models import Evidence, PreferenceRecord, Session, unique_strings
from .quality import looks_like_one_off_task, looks_like_raw_user_fragment, should_recall_user_text


class PreferenceModelBackend(ABC):
    @abstractmethod
    def extract_preferences(self, session: Session) -> list[PreferenceRecord]:
        raise NotImplementedError

    @abstractmethod
    def merge_decision(
        self, candidate: PreferenceRecord, existing: list[PreferenceRecord]
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def decide(
        self,
        task: str,
        context: dict[str, Any],
        records: list[PreferenceRecord],
        agent: str = "agent",
    ) -> dict[str, Any]:
        raise NotImplementedError


def build_backend(name: str | None = None) -> PreferenceModelBackend:
    backend = (name or os.getenv("PREFERENCE_MODEL_BACKEND") or "heuristic").strip().lower()
    if backend in {"openai", "openai-compatible", "llm", "local"}:
        return OpenAICompatibleBackend()
    if backend in {"auto"}:
        if os.getenv("PREFERENCE_MODEL_BASE_URL") or os.getenv("OPENAI_API_KEY"):
            return OpenAICompatibleBackend()
        return HeuristicBackend()
    return HeuristicBackend()


class HeuristicBackend(PreferenceModelBackend):
    """Offline backend for deterministic POC tests.

    This is deliberately not the target intelligence layer. The production-quality
    semantic behavior is expected from OpenAI-compatible cloud/local models.
    """

    preference_markers = (
        "以后",
        "默认",
        "每次",
        "总是",
        "必须",
        "不要",
        "不用问",
        "别问",
        "我偏好",
        "我希望",
        "我要求",
        "你应该",
        "优先",
        "倾向",
        "回读",
    )
    confirmation_markers = ("要不要", "是否", "需不需要", "要不", "可以吗", "确认", "?")
    change_markers = ("以后", "从现在开始", "改成", "默认改为", "以后默认")

    def extract_preferences(self, session: Session) -> list[PreferenceRecord]:
        candidates: list[PreferenceRecord] = []
        messages = session.messages
        for index, message in enumerate(messages):
            content = _clean(message.content)
            if not content:
                continue
            if message.role == "assistant" and _has_any(content, self.confirmation_markers):
                next_user = _next_user_message(messages, index + 1)
                if next_user:
                    generalized = _generalize_preference(_clean(next_user.content), context=content)
                    if not generalized:
                        continue
                    candidates.append(
                        _record(
                            title=f"用户偏好：{_short(generalized, 32)}",
                            applies_to=_infer_applies_to(generalized),
                            preference=generalized,
                            triggers=[content],
                            exceptions=["上下文明显变化、风险升高或近期回答不一致时升级给真人确认"],
                            source=session.source,
                            quote=next_user.content,
                            confidence="medium",
                        )
                    )
            if message.role == "user":
                for sentence in _sentences(content):
                    if not _has_any(sentence, self.preference_markers) and not should_recall_user_text(sentence):
                        continue
                    generalized = _generalize_preference(sentence)
                    if generalized and len(generalized) >= 8:
                        candidates.append(
                            _record(
                                title=f"用户偏好：{_short(generalized, 32)}",
                                applies_to=_infer_applies_to(generalized),
                                preference=generalized,
                                triggers=_infer_triggers(generalized),
                                exceptions=_infer_exceptions(generalized),
                                source=session.source,
                                quote=sentence,
                                confidence="medium",
                            )
                        )
        return _dedupe_records(candidates)

    def merge_decision(
        self, candidate: PreferenceRecord, existing: list[PreferenceRecord]
    ) -> dict[str, Any]:
        best: tuple[float, PreferenceRecord | None] = (0.0, None)
        candidate_scope = _scope_text(candidate)
        candidate_category = _category(candidate.preference or candidate_scope)
        for record in existing:
            record_category = _category(record.preference or _scope_text(record))
            if candidate_category and record_category and candidate_category != record_category:
                continue
            score = semantic_similarity(candidate_scope, _scope_text(record))
            if candidate_category and record_category == candidate_category:
                if candidate.preference in record.preference or record.preference in candidate.preference:
                    score = max(score, 0.6)
            if score > best[0]:
                best = (score, record)
        score, record = best
        if not record or score < 0.27:
            return {"action": "new", "reason": "没有足够相似的既有偏好", "score": round(score, 3)}
        preference_score = semantic_similarity(candidate.preference, record.preference)
        if candidate.preference in record.preference or record.preference in candidate.preference:
            preference_score = max(preference_score, 0.6)
        if _has_any(candidate.preference, self.change_markers):
            return {
                "action": "replace",
                "target_id": record.id,
                "reason": "候选偏好包含明确变化信号，按持续更新处理",
                "score": round(score, 3),
            }
        if preference_score >= 0.55:
            return {
                "action": "merge",
                "target_id": record.id,
                "reason": "同一意图下的新证据强化既有偏好",
                "score": round(score, 3),
            }
        return {
            "action": "conflict",
            "target_id": record.id,
            "reason": "语境相近但回答不一致，需要保留冲突证据",
            "score": round(score, 3),
        }

    def decide(
        self,
        task: str,
        context: dict[str, Any],
        records: list[PreferenceRecord],
        agent: str = "agent",
    ) -> dict[str, Any]:
        if not records:
            return _no_preference(task)
        scored = _live_scored_records(task=task, context=context, records=records)
        matches = [
            {
                "id": record.id,
                "title": record.title,
                "confidence": live.label,
                "stored_confidence": record.confidence,
                "live_confidence": live.score,
                "confidence_factors": {
                    "base": live.base,
                    "evidence_boost": live.evidence_boost,
                    "recency_decay": live.recency_decay,
                    "consistency_penalty": live.consistency_penalty,
                    "relevance_bonus": live.relevance_bonus,
                },
                "confidence_reasons": live.reasons,
                "score": round(relevance, 3),
                "instruction": record.preference,
                "applies_to": record.applies_to,
            }
            for _live_score, relevance, live, record in scored[:5]
        ]
        if not matches:
            return _no_preference(task)
        conflict_group = _find_conflicts_among([record for _score, _relevance, _live, record in scored[:3]])
        if conflict_group:
            return _backend_conflict_response(agent=agent, matches=matches, conflicts=conflict_group)
        combined = "；".join(match["instruction"] for match in matches[:3])
        return {
            "decision": "apply",
            "agent": agent,
            "matched_preferences": matches,
            "agent_instruction": f"在回复或执行前应用这些用户偏好：{combined}",
            "escalate": False,
            "reason": "找到语义相关的用户偏好",
        }


class OpenAICompatibleBackend(PreferenceModelBackend):
    def __init__(self) -> None:
        self.base_url = (
            os.getenv("PREFERENCE_MODEL_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "http://localhost:11434/v1"
        ).rstrip("/")
        self.api_key = os.getenv("PREFERENCE_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY") or "local"
        self.model = os.getenv("PREFERENCE_MODEL_NAME") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        self.timeout = float(os.getenv("PREFERENCE_MODEL_TIMEOUT", "90"))
        self.temperature = float(os.getenv("PREFERENCE_MODEL_TEMPERATURE", "0.1"))

    def extract_preferences(self, session: Session) -> list[PreferenceRecord]:
        payload = {
            "source": session.source,
            "messages": [message.__dict__ for message in session.messages],
        }
        result = self._chat_json(
            system=EXTRACT_SYSTEM_PROMPT,
            user=json.dumps(payload, ensure_ascii=False),
        )
        candidates = result.get("preferences", []) if isinstance(result, dict) else []
        records: list[PreferenceRecord] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            item.setdefault("evidence", [])
            if not item["evidence"]:
                item["evidence"] = [
                    {
                        "source": session.source,
                        "quote": item.get("evidence_quote") or item.get("preference") or "",
                        "role": "user",
                        "source_type": "user_explicit",
                    }
                ]
            records.append(PreferenceRecord.from_dict(item))
        return records

    def merge_decision(
        self, candidate: PreferenceRecord, existing: list[PreferenceRecord]
    ) -> dict[str, Any]:
        result = self._chat_json(
            system=MERGE_SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "candidate": candidate.to_dict(),
                    "existing": [record.to_dict() for record in existing],
                },
                ensure_ascii=False,
            ),
        )
        return result if isinstance(result, dict) else {"action": "new", "reason": "model returned invalid JSON"}

    def decide(
        self,
        task: str,
        context: dict[str, Any],
        records: list[PreferenceRecord],
        agent: str = "agent",
    ) -> dict[str, Any]:
        if not records:
            return _no_preference(task)
        candidates = _live_scored_preference_payloads(
            task=task,
            context=context,
            records=records,
            require_injection_threshold=False,
        )
        if not candidates:
            return _no_preference(task)
        result = self._chat_json(
            system=DECIDE_SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "agent": agent,
                    "task": task,
                    "context": context,
                    "preferences": candidates,
                },
                ensure_ascii=False,
            ),
        )
        if not isinstance(result, dict):
            return _no_preference(task)
        result.setdefault("agent", agent)
        result.setdefault("escalate", result.get("decision") != "apply")
        return result

    def _chat_json(self, system: str, user: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model request failed: HTTP {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"model request failed: {exc}") from exc
        content = data["choices"][0]["message"]["content"]
        return _json_from_text(content)


EXTRACT_SYSTEM_PROMPT = """你是个人偏好精提器。请从召回候选中提取稳定、可复用、对未来 agent 行为有指导价值的用户偏好。
只提取和 agent 行为、回复方式、执行习惯、验证标准、升级条件、工具选择、语言风格、信息组织有关的偏好。
严禁把一次性业务任务、项目事实、路径/URL/issue 编号、临时指令、调试请求、让 agent 当前去做的事情提取为偏好。
如果一条候选只是“帮我改/查/录/创建/推/测试某个具体项目或 issue”，输出空数组。
如果候选能反映偏好，必须把它改写成可复用规则，不要照抄用户原文。
`applies_to` 必须是具体场景，禁止使用“当 agent 准备回复、执行任务或做确认决策时”这种对所有场景都成立的假条件。
`preference` 必须是可执行的短句，不能包含本地路径、URL、AIC/issue 编号、分支名、具体文件名、一次性对象名。
输出 JSON 对象：{"preferences": [...]}。
每个 preference 字段：
- title: 简短标题。
- summary: 一句话概括。
- applies_to: 适用场景，尽量写成“当...时”。
- preference: 可直接给 agent 执行的偏好陈述，第三人称或祈使句均可，但要简洁。
- triggers[]: 触发该偏好的意图或场景。
- exceptions[]: 不适用或需要升级确认的情况。
- confidence: high/medium/low。
- evidence[]: 每条必须包含 source, quote, role。
可额外输出 category、is_negation、quality 等字段，但不要编造证据。
否定表达要保留语义，例如“不要长文”可以提炼为“偏好简洁回复”，证据 quote 仍保留原文。
高置信只给明确出现“以后、默认、每次、总是、我希望、我偏好、我说过、记住”等稳定信号，或多条候选反复表达的习惯。
"""

MERGE_SYSTEM_PROMPT = """你是偏好库合并器。判断 candidate 与 existing 是否是同一个用户偏好意图。
输出 JSON：{"action":"new|merge|replace|conflict","target_id":null或id,"reason":"..."}。
规则：
- new: 没有同类偏好。
- merge: 同类且一致，追加证据。
- replace: 明确体现用户偏好发生变化，应更新主偏好。
- conflict: 同类但不确定是否变化，应保留冲突证据，不自动覆盖。
"""

DECIDE_SYSTEM_PROMPT = """你是偏好代言决策器。给定 agent 当前任务和偏好库，判断回复或做事前应应用哪些偏好。
语义和意图优先，不要求字面一致。
输入偏好已包含 live_confidence：这是当前任务下由基础置信度、证据、时间、一致性和场景相关性计算出的动态置信度。
优先使用 live_confidence.score 高、且确实适合当前任务的偏好；不要因为 stored confidence 高就强行应用低相关偏好。
status 不是 active 的偏好不能直接注入；如果它与当前任务强相关，只能用于解释不确定性或触发澄清。
输出 JSON：
{
  "decision": "apply|no_preference|escalate",
  "matched_preferences": [{"id":"...","title":"...","confidence":"high|medium|low","instruction":"...","reason":"..."}],
  "agent_instruction": "给 agent 的可执行指令",
  "escalate": false,
  "reason": "..."
}
只有在偏好确实适用时 apply；不确定就 no_preference 或 escalate。
"""


def _record(
    title: str,
    applies_to: str,
    preference: str,
    triggers: list[str],
    exceptions: list[str],
    source: str,
    quote: str,
    confidence: str,
) -> PreferenceRecord:
    return PreferenceRecord(
        title=title,
        summary=preference[:120],
        applies_to=applies_to,
        preference=preference,
        triggers=unique_strings(triggers, limit=8),
        exceptions=unique_strings(exceptions, limit=8),
        confidence=confidence,
        evidence=[Evidence(source=source, quote=quote, role="user", source_type="user_explicit")],
    )


def semantic_similarity(left: str, right: str) -> float:
    left_norm = _normalize_for_similarity(left)
    right_norm = _normalize_for_similarity(right)
    if not left_norm or not right_norm:
        return 0.0
    seq = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_grams = _char_ngrams(left_norm)
    right_grams = _char_ngrams(right_norm)
    jaccard = len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
    keyword = _keyword_overlap(left_norm, right_norm)
    return max(seq * 0.45 + jaccard * 0.35 + keyword * 0.2, keyword * 0.9)


def _normalize_for_similarity(text: str) -> str:
    text = text.casefold()
    replacements = {
        "需不需要": "要不要",
        "是否": "要不要",
        "验证": "测试",
        "test": "测试",
        "tests": "测试",
        "review": "审查",
        "代码审查": "审查",
        "检查": "审查",
        "回读": "读取确认",
        "确认中文": "中文正常",
        "agent": "智能体",
        "ai": "智能体",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"\s+", "", text)


def _char_ngrams(text: str, size: int = 2) -> set[str]:
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _keyword_overlap(left: str, right: str) -> float:
    keywords = [
        "测试",
        "代码",
        "代码完成",
        "代码改动",
        "审查",
        "验证",
        "覆盖率",
        "边界",
        "安全",
        "中文",
        "回复",
        "回答",
        "用户问题",
        "简洁",
        "详细",
        "回读",
        "linear",
        "沉淀",
        "文档",
        "不要问",
        "自动",
        "本地",
        "偏好",
        "codex",
        "openclaw",
    ]
    left_hits = {word for word in keywords if word in left}
    right_hits = {word for word in keywords if word in right}
    if not left_hits or not right_hits:
        return 0.0
    return len(left_hits & right_hits) / len(left_hits | right_hits)


def _scope_text(record: PreferenceRecord) -> str:
    return " ".join([record.title, record.summary, record.applies_to, record.preference, *record.triggers])


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _short(text: str, limit: int) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s*|\n+", text)
    return [_clean(part) for part in parts if _clean(part)]


def _generalize_preference(sentence: str, context: str = "") -> str:
    text = _clean(sentence)
    joined = _clean(" ".join([context, text]))
    lowered = joined.casefold()
    if not text:
        return ""
    if any(word in joined for word in ("回读", "中文", "乱码")) and any(
        word in joined for word in ("必须", "一定要", "不要破坏", "检查", "确认")
    ):
        return "写入包含中文的外部系统后，必须回读确认中文正常且 Markdown 结构未损坏。"
    if any(word in lowered for word in ("测试", "test", "pytest", "验证")) and any(
        word in joined for word in ("以后", "默认", "每次", "不要问", "不用问", "别问")
    ):
        return "代码或脚本改动完成后，默认运行相关测试或验证；无法运行时必须明确说明原因和替代验证方式。"
    if any(word in lowered for word in ("review", "审查")) and (
        any(word in joined for word in ("代码", "实现", "改动", "边界", "异常", "回归"))
    ):
        return "做代码 review 时优先指出 bug、回归风险和缺失测试，再补充摘要。"
    if any(word in joined for word in ("沉淀", "文档", "复盘")) and any(
        word in joined for word in ("方案", "设计", "深度", "讨论", "复用", "复杂", "决策", "整理")
    ):
        return "深度研讨、方案设计或复盘形成可复用结论时，主动询问是否沉淀为 Markdown 文档。"
    if "Linear" in joined or "linear" in lowered or "issue" in lowered:
        if any(word in joined for word in ("完成", "阶段性", "记录", "更新", "回读", "中文")) and not looks_like_one_off_task(text):
            return "完成阶段性成果后，询问是否需要更新 Linear issue；写入中文后必须回读校验。"
        return ""
    if any(word in lowered for word in ("git", "commit", "push", "分支")) and any(
        word in joined for word in ("不要", "别", "先不要", "本地", "留在本地")
    ):
        return "代码修改默认先保留在本地，不主动 commit 或 push，除非用户明确要求。"
    if any(word in lowered for word in ("edit", "编辑")) and any(word in joined for word in ("不要覆盖", "别覆盖", "覆盖")):
        return "修改已有文件时优先使用增量编辑，避免覆盖用户本地未同步修改。"
    if any(word in joined for word in ("结论句", "问句")) and any(word in joined for word in ("标题", "title")):
        return "撰写标题时使用结论句，避免使用问句形式。"
    if "中文" in joined and any(word in joined for word in ("回复", "回答", "输出")):
        return "默认使用中文回复用户。"
    if "动画" in joined and any(word in joined for word in ("慢", "较慢", "过快", "速度")):
        return "设计交互动画或过渡效果时，使用较慢的动画速度，避免过快的跳跃或涟漪效果。"
    if any(word in joined for word in ("反问", "不用问", "不要问", "别问", "说了做其实没做", "说了做")):
        return "用户已经明确要求执行时，避免反复确认；在风险可控时直接推进并汇报结果。"
    if any(word in joined for word in ("简洁", "短一点", "两句话", "少废话", "不要长文", "眼花", "子弹点", "分点", "不要全部平铺")):
        return "回复和文档输出偏好简洁、分点、易扫读，避免大段平铺和冗长解释。"
    if any(word in joined for word in ("先给", "结论", "大纲")) and any(word in joined for word in ("以后", "默认", "每次", "先")):
        return "回复复杂问题时先给结论或大纲，再展开必要细节。"
    if looks_like_one_off_task(text) and not should_recall_user_text(text):
        return ""
    if looks_like_raw_user_fragment(text):
        return ""
    if not should_recall_user_text(text):
        return ""
    return ""


def _category(text: str) -> str:
    lowered = text.casefold()
    if any(word in lowered for word in ("review", "审查", "边界情况", "异常路径", "回归风险", "bug")):
        return "review"
    if any(word in lowered for word in ("测试", "验证", "覆盖率", "test", "依赖", "跑不了", "代码完成", "代码改动", "改代码")):
        return "test"
    if any(word in lowered for word in ("文档", "沉淀", "方案设计", "复盘", "markdown")):
        return "docs"
    if any(word in lowered for word in ("回复", "回答", "用户问题", "简洁", "详细", "长文", "展开", "concise", "brief", "detailed")):
        return "reply"
    if any(word in lowered for word in ("linear", "issue")):
        return "linear"
    if any(word in lowered for word in ("中文", "回读", "乱码")):
        return "chinese-write"
    return ""


def _next_user_message(messages: list[Any], start: int) -> Any | None:
    for message in messages[start : start + 4]:
        if message.role == "user" and _clean(message.content):
            return message
    return None


def _infer_applies_to(sentence: str) -> str:
    lowered = sentence.casefold()
    if any(word in lowered for word in ("git", "commit", "push", "分支", "代码", "测试", "review", "验证", "coverage", "覆盖率")):
        return "当 agent 修改代码、完成实现、询问测试或 review 标准时"
    if "Linear" in sentence or "issue" in sentence.casefold():
        return "当 agent 完成阶段性成果并可能需要更新 Linear issue 时"
    if "中文" in sentence and any(word in sentence for word in ("回复", "回答", "输出")):
        return "当 agent 回复用户时"
    if "中文" in sentence or "回读" in sentence:
        return "当 agent 把包含中文的内容写入外部系统后"
    if "文档" in sentence or "沉淀" in sentence:
        return "当讨论有复用价值、形成方案或决策时"
    if any(word in sentence for word in ("简洁", "分点", "子弹点", "结论", "大纲", "回复", "长文")):
        return "当 agent 回复问题、汇报结果或组织长内容时"
    if any(word in sentence for word in ("反复确认", "反问", "直接推进")):
        return "当用户已经明确要求 agent 执行任务时"
    return "当 agent 需要选择回复方式或执行节奏时"


def _infer_triggers(sentence: str) -> list[str]:
    triggers = []
    if any(word in sentence.casefold() for word in ("代码", "测试", "review", "验证")):
        triggers.append("代码完成、测试、review、验证")
    if "Linear" in sentence or "issue" in sentence.casefold():
        triggers.append("完成任务或阶段性成果")
    if "中文" in sentence or "回读" in sentence:
        triggers.append("写入包含中文的外部内容")
    if "沉淀" in sentence or "文档" in sentence:
        triggers.append("深度讨论、方案设计、复盘")
    return triggers or [sentence[:60]]


def _infer_exceptions(sentence: str) -> list[str]:
    exceptions = ["用户明确给出相反指令时，以最新指令为准"]
    if "不要" in sentence or "不用" in sentence:
        exceptions.append("高风险或上下文不足时仍需升级给真人")
    return exceptions


def _dedupe_records(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    deduped: list[PreferenceRecord] = []
    for record in records:
        if not any(
            semantic_similarity(_scope_text(record), _scope_text(item)) > 0.82
            and semantic_similarity(record.preference, item.preference) > 0.72
            for item in deduped
        ):
            deduped.append(record)
    return deduped


def _live_scored_records(
    task: str,
    context: dict[str, Any],
    records: list[PreferenceRecord],
    require_injection_threshold: bool = True,
) -> list[tuple[float, float, LiveConfidence, PreferenceRecord]]:
    query = " ".join([task, json.dumps(context, ensure_ascii=False)])
    scored: list[tuple[float, float, LiveConfidence, PreferenceRecord]] = []
    for record in records:
        if str(record.status or "").casefold() in {"rejected", "archived", "deleted"}:
            continue
        if require_injection_threshold and str(record.status or "").casefold() != "active":
            continue
        relevance = _relevance_score(query, record)
        live = live_confidence(record, relevance_score=relevance)
        if not require_injection_threshold or should_inject_live_confidence(live):
            scored.append((live.score, relevance, live, record))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored


def _live_scored_preference_payloads(
    task: str,
    context: dict[str, Any],
    records: list[PreferenceRecord],
    require_injection_threshold: bool = True,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for _score, relevance, live, record in _live_scored_records(
        task=task,
        context=context,
        records=records,
        require_injection_threshold=require_injection_threshold,
    ):
        item = record.to_dict()
        item["relevance_score"] = round(relevance, 4)
        item["live_confidence"] = live.to_dict()
        payloads.append(item)
    return payloads


def _relevance_score(query: str, record: PreferenceRecord) -> float:
    record_scope = _scope_text(record)
    score = semantic_similarity(query, record_scope)
    query_category = _category(query)
    record_category = _category(record_scope)
    if query_category and record_category == query_category:
        score = max(score, 0.26)
    return score


DETAIL_CONFLICT_WORDS = ("详细", "展开", "完整", "充分解释", "长文", "exhaustive", "detailed")
CONCISE_CONFLICT_WORDS = ("简洁", "简短", "短一点", "不要长文", "别啰嗦", "少废话", "concise", "brief", "short")
TEST_REQUIRED_WORDS = ("测试", "验证", "pytest", "test", "verify", "verification", "回归")
TEST_SKIP_WORDS = ("不用测试", "不要测试", "跳过测试", "不测", "无需验证", "no test", "skip test", "without verification")
ASK_WORDS = ("确认", "询问", "反问", "ask", "confirm", "manual")
NO_ASK_WORDS = ("不用问", "不要问", "无需确认", "直接", "自动", "不反问", "do not ask", "no confirmation")


def _find_conflicts_among(records: list[PreferenceRecord]) -> list[PreferenceRecord]:
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            if _records_conflict_for_injection(left, right):
                return [left, right]
    return []


def _records_conflict_for_injection(left: PreferenceRecord, right: PreferenceRecord) -> bool:
    left_text = _scope_text(left)
    right_text = _scope_text(right)
    return (
        _opposes(left_text, right_text, DETAIL_CONFLICT_WORDS, CONCISE_CONFLICT_WORDS)
        or _opposes(left_text, right_text, TEST_REQUIRED_WORDS, TEST_SKIP_WORDS)
        or _opposes(left_text, right_text, ASK_WORDS, NO_ASK_WORDS)
    )


def _opposes(left: str, right: str, first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    return (_has_any(left, first) and _has_any(right, second)) or (_has_any(left, second) and _has_any(right, first))


def _backend_conflict_response(
    agent: str,
    matches: list[dict[str, Any]],
    conflicts: list[PreferenceRecord],
) -> dict[str, Any]:
    options = "；".join(f"{index + 1}. {record.preference}" for index, record in enumerate(conflicts))
    conflict_ids = [record.id for record in conflicts]
    return {
        "decision": "escalate",
        "agent": agent,
        "matched_preferences": matches,
        "conflict": {
            "preference_ids": conflict_ids,
            "options": [
                {
                    "id": record.id,
                    "title": record.title,
                    "instruction": record.preference,
                    "applies_to": record.applies_to,
                }
                for record in conflicts
            ],
        },
        "conflict_preference_ids": sorted(conflict_ids),
        "agent_instruction": f"检测到本轮命中的用户偏好互相冲突，不能同时应用。请简短反问用户本次采用哪一种，或是否两个都不适用：{options}。",
        "escalate": True,
        "clarification_required": True,
        "reason": "top-3 命中的偏好之间存在冲突，不能拼接注入。",
    }


def _no_preference(task: str) -> dict[str, Any]:
    return {
        "decision": "no_preference",
        "matched_preferences": [],
        "agent_instruction": "",
        "escalate": True,
        "reason": "偏好库为空或没有找到足够相关的偏好",
        "cold_start_hint": "可以先捕获当前 session 或导入历史对话，再增量更新 Markdown 偏好库。",
        "task": task,
    }


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))
