from __future__ import annotations

import re

from .models import PreferenceRecord


GENERIC_APPLIES_TO = "当 agent 准备回复、执行任务或做确认决策时"
GENERIC_APPLIES_TO_VALUES = {
    GENERIC_APPLIES_TO,
    "当 agent 需要选择回复方式或执行节奏时",
}

STABLE_SIGNAL_PATTERNS = (
    re.compile(r"(以后|以后默认|从现在开始|默认|每次|总是|我说过|记住|一定要)"),
    re.compile(r"(我希望|我偏好|我倾向|我更喜欢|我喜欢|我不喜欢|我讨厌)"),
    re.compile(r"(简洁|短一点|少废话|别啰嗦|不要长文|两句话|分点|子弹点|不要全部平铺)"),
    re.compile(r"(主动).{0,12}(问|沉淀|文档)"),
    re.compile(r"(不要|别|不用|不必|无需|避免|禁止).{0,12}(每次|总是|反问|问我|啰嗦|长文|推|commit|覆盖|破坏)"),
    re.compile(r"(必须|需要|应该).{0,18}(回读|测试|验证|review|沉淀|分点|子弹点|本地|无头|中文)"),
)

ONE_OFF_PATTERNS = (
    re.compile(r"\b(lin_api|sk-|xox[baprs]-|api[_-]?key|token|secret|密钥)\b", re.I),
    re.compile(r"https?://", re.I),
    re.compile(r"\b[A-Z]{2,10}-\d+\b"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\b(github\.com|linear\.app)\b", re.I),
    re.compile(r"(今天|昨天|刚才|现在|当前|这次|本次|一会儿|临时|先暂时|过去一周)"),
    re.compile(r"(帮我|请你|辛苦你|麻烦你|给我).{0,24}(看|查|改|修|录|创建|新建|安装|拉|推|上传|重启|测试|验证|检查)"),
    re.compile(r"(看这个|这个仓库|这个文件|这个 issue|这个项目|这个 PRD|这个页面|这张图|这段代码)"),
    re.compile(r"(录入|创建|新建|批量修改|推上来|拉下来|上传|重启|删除|挪动|黏贴|贴一份)"),
    re.compile(r"(请问|检查以下代码|一直没有加载|预览但是|合成一个文件夹)"),
    re.compile(r"(\.plugin|微信ide|obsidian本地目录|同一个代码仓库)"),
)

RAW_FRAGMENT_PATTERNS = (
    re.compile(r"(^|[，。；\s])(我希望|我倾向|我觉得|我说了|我写在|我会|我要|我的|我刚|咱们|我们)"),
    re.compile(r"(请问|检查以下代码|我稍微调整|请保留|找的是|读一遍metadata|每张卡片都问我|我要睡觉|刚做了一些修改)"),
    re.compile(r"(^|[\s，。])=="),
    re.compile(r"(^|[\s，。])>"),
    re.compile(r"(\.plugin|微信ide|obsidian本地目录|这个配色|这个挺好看|这个文件夹|这张卡片)"),
    re.compile(r"(后者估计|前者估计|那一段|不需要\*|^\s*B\s+而且必须无头)"),
)

GUIDANCE_TERMS = (
    "回复",
    "输出",
    "解释",
    "结论",
    "简洁",
    "短一点",
    "少废话",
    "长文",
    "分点",
    "子弹点",
    "文档",
    "沉淀",
    "复盘",
    "代码",
    "测试",
    "验证",
    "review",
    "回读",
    "中文",
    "乱码",
    "Linear",
    "issue",
    "本地",
    "git",
    "commit",
    "push",
    "主动",
    "反问",
    "确认",
    "无头",
    "工具",
    "模型",
    "edit",
    "覆盖",
    "交互",
    "视觉",
    "界面",
    "UI",
    "动画",
    "卡片",
    "全宽",
    "voice",
    "answer",
    "answers",
)


def has_stable_signal(text: str) -> bool:
    return any(pattern.search(text) for pattern in STABLE_SIGNAL_PATTERNS)


def has_guidance_terms(text: str) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in GUIDANCE_TERMS)


def looks_like_one_off_task(text: str) -> bool:
    return any(pattern.search(text) for pattern in ONE_OFF_PATTERNS)


def looks_like_raw_user_fragment(text: str) -> bool:
    cleaned = " ".join(str(text).split())
    return any(pattern.search(cleaned) for pattern in RAW_FRAGMENT_PATTERNS)


def should_recall_user_text(text: str) -> bool:
    cleaned = " ".join(str(text).split())
    if len(cleaned) < 6:
        return False
    stable = has_stable_signal(cleaned)
    guidance = has_guidance_terms(cleaned)
    one_off = looks_like_one_off_task(cleaned)
    if one_off and not re.search(r"(以后|以后默认|从现在开始|每次|总是|我说过|记住)", cleaned):
        return False
    if stable and guidance:
        return True
    if "我说过" in cleaned or "记住" in cleaned:
        return guidance
    return False


def record_has_guidance_value(record: PreferenceRecord) -> bool:
    preference = " ".join(record.preference.split()).strip()
    applies_to = " ".join(record.applies_to.split()).strip()
    combined = " ".join([record.title, record.summary, applies_to, preference, *record.triggers])
    if len(preference) < 8 or len(preference) > 220:
        return False
    if applies_to in GENERIC_APPLIES_TO_VALUES or _has_generic_scope_prefix(preference):
        return False
    if looks_like_raw_user_fragment(preference):
        return False
    if looks_like_one_off_task(preference) or looks_like_one_off_task(applies_to):
        return False
    if re.search(r"https?://|[A-Za-z]:\\|\b[A-Z]{2,10}-\d+\b", preference, re.I):
        return False
    if not has_guidance_terms(combined):
        return False
    if preference.startswith(("帮我", "请你", "辛苦你", "麻烦你", "给我")):
        return False
    return True


def _has_generic_scope_prefix(preference: str) -> bool:
    return any(
        preference == scope
        or preference.startswith(f"{scope}，")
        or preference.startswith(f"{scope},")
        for scope in GENERIC_APPLIES_TO_VALUES
    )
