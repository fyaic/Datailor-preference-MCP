from __future__ import annotations

import re

from .models import PreferenceRecord


GENERIC_APPLIES_TO = "When the agent is about to reply, execute a task, or make a confirmation decision"
GENERIC_APPLIES_TO_VALUES = {
    GENERIC_APPLIES_TO,
    "When the agent chooses response style or execution cadence",
    "When the agent prepares to reply, execute tasks, or make confirmation decisions",
}

STABLE_SIGNAL_PATTERNS = (
    re.compile(r"(from now on|default|every time|always|i said before|remember|must)", re.I),
    re.compile(r"(i want|i prefer|i tend to|i like|i dislike|i hate)", re.I),
    re.compile(r"(concise|short|less verbose|no long answers|two sentences|bullets|scannable)", re.I),
    re.compile(r"(proactively).{0,24}(ask|document)", re.I),
    re.compile(r"(do not|avoid|forbid|never).{0,24}(every time|always|ask me|verbose|long answer|push|commit|overwrite|break)", re.I),
    re.compile(r"(must|need|should).{0,32}(read back|test|verify|review|document|bullets|local|headless|encoding)", re.I),
)

ONE_OFF_PATTERNS = (
    re.compile(r"\b(lin_api|sk-|xox[baprs]-|api[_-]?key|token|secret)\b", re.I),
    re.compile(r"https?://", re.I),
    re.compile(r"\b[A-Z]{2,10}-\d+\b"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\b(github\.com|linear\.app)\b", re.I),
    re.compile(r"(today|yesterday|just now|right now|current|this time|temporary|for now|past week)", re.I),
    re.compile(r"(help me|please).{0,40}(look|check|edit|fix|record|create|install|pull|push|upload|restart|test|verify|inspect)", re.I),
    re.compile(r"(this repo|this file|this issue|this project|this PRD|this page|this screenshot|this code)", re.I),
    re.compile(r"(create|batch edit|push up|pull down|upload|restart|delete|move|paste a copy)", re.I),
    re.compile(r"(\bquestion\b|inspect the following code|not loading|preview but|merge into one folder)", re.I),
    re.compile(r"(\.plugin|wechat ide|obsidian local directory|same code repository)", re.I),
)

RAW_FRAGMENT_PATTERNS = (
    re.compile(r"(^|[,\s])(i want|i tend|i think|i said|i wrote|i will|my|we just)", re.I),
    re.compile(r"(\bquestion\b|inspect the following code|i adjusted|please keep|looking for|read metadata|ask me about every card|i need sleep|just changed)", re.I),
    re.compile(r"(^|[\s,])=="),
    re.compile(r"(^|[\s,])>"),
    re.compile(r"(\.plugin|wechat ide|obsidian local directory|this palette|this looks good|this folder|this card)", re.I),
    re.compile(r"(the latter|the former|that section|do not need\*|^\s*B\s+and must be headless)", re.I),
)

GUIDANCE_TERMS = (
    "reply",
    "output",
    "explain",
    "conclusion",
    "concise",
    "short",
    "less verbose",
    "long answer",
    "bullets",
    "document",
    "retrospective",
    "code",
    "test",
    "verify",
    "review",
    "read back",
    "encoding",
    "Linear",
    "issue",
    "local",
    "git",
    "commit",
    "push",
    "proactive",
    "ask",
    "confirm",
    "headless",
    "tool",
    "model",
    "edit",
    "overwrite",
    "interaction",
    "visual",
    "interface",
    "UI",
    "animation",
    "card",
    "full width",
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
    if one_off and not re.search(r"(from now on|default|every time|always|i said before|remember)", cleaned, re.I):
        return False
    if stable and guidance:
        return True
    if re.search(r"(i said before|remember)", cleaned, re.I):
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
    if preference.casefold().startswith(("help me", "please")):
        return False
    return True


def _has_generic_scope_prefix(preference: str) -> bool:
    return any(
        preference == scope
        or preference.startswith(f"{scope},")
        for scope in GENERIC_APPLIES_TO_VALUES
    )
