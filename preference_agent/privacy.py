from __future__ import annotations

import re


SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"\b[A-Fa-f0-9]{24,}\.[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|密钥)\s*[:=：]?\s*([A-Za-z0-9._/-]{16,})"),
)
BROKEN_REPLACEMENT_CHARS = re.compile(r"\uFFFD+")


def redact_sensitive(text: str) -> str:
    result = str(text)
    result = BROKEN_REPLACEMENT_CHARS.sub("", result)
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED_SECRET]", result)
        else:
            result = pattern.sub("[REDACTED_SECRET]", result)
    return result
