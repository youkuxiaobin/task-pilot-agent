from __future__ import annotations

import copy
from typing import Any, List


DEFAULT_CONTEXT_TEXT_LIMIT = 320
DEFAULT_MESSAGE_CONTEXT_MAX_CHARS = 16_000
MAX_MESSAGE_CONTEXT_MAX_CHARS = 64_000
OMISSION_MARKER = "..."


def normalize_context_text(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split()) if text else ""


def truncate_context_text(value: Any, limit: int = DEFAULT_CONTEXT_TEXT_LIMIT) -> str:
    text = normalize_context_text(value)
    if not text:
        return ""
    safe_limit = max(int(limit or 0), 0)
    if safe_limit <= 0:
        return ""
    if len(text) <= safe_limit:
        return text
    if safe_limit <= len(OMISSION_MARKER):
        return text[:safe_limit]
    return text[: safe_limit - len(OMISSION_MARKER)] + OMISSION_MARKER


def message_content_chars(message: Any) -> int:
    return len(str(getattr(message, "content", "") or ""))


def _copy_message_with_content(message: Any, content: str) -> Any:
    if str(getattr(message, "content", "") or "") == content:
        return message
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": content})
    if hasattr(message, "copy"):
        return message.copy(update={"content": content})
    cloned = copy.copy(message)
    setattr(cloned, "content", content)
    return cloned


def fit_messages_to_char_budget(
    messages: List[Any],
    *,
    max_chars: int = DEFAULT_MESSAGE_CONTEXT_MAX_CHARS,
) -> List[Any]:
    items = list(messages or [])
    if not items:
        return []

    safe_max_chars = max(min(int(max_chars or 0), MAX_MESSAGE_CONTEXT_MAX_CHARS), 0)
    if safe_max_chars <= 0:
        return []

    kept_reversed: List[Any] = []
    used_chars = 0
    for message in reversed(items):
        remaining = safe_max_chars - used_chars
        if remaining <= 0:
            break

        content = str(getattr(message, "content", "") or "")
        if len(content) <= remaining:
            kept_reversed.append(message)
            used_chars += len(content)
            continue

        clipped_content = truncate_context_text(content, remaining)
        if clipped_content:
            kept_reversed.append(_copy_message_with_content(message, clipped_content))
        break

    return list(reversed(kept_reversed))
