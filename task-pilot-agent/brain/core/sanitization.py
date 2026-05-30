from __future__ import annotations

import re
from typing import Any, Dict

REDACTED = "***"

SENSITIVE_KEYWORDS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)

SECRET_VALUE_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{12,}"),
)
SENSITIVE_QUERY_PARAM_PATTERN = re.compile(
    r"([?&][^=&#\s]*(?:api_key|apikey|authorization|cookie|password|secret|token)[^=&#\s]*=)([^&#\s]+)",
    re.IGNORECASE,
)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(keyword in normalized_key for keyword in SENSITIVE_KEYWORDS):
                sanitized[key] = REDACTED
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_text(value: str) -> str:
    sanitized = SENSITIVE_QUERY_PARAM_PATTERN.sub(lambda match: match.group(1) + REDACTED, value)
    for pattern in SECRET_VALUE_PATTERNS:
        sanitized = pattern.sub(_replace_secret_match, sanitized)
    return sanitized


def _replace_secret_match(match: re.Match[str]) -> str:
    text = match.group(0)
    if text.lower().startswith("bearer "):
        return "Bearer " + REDACTED
    return REDACTED
