from __future__ import annotations

import fnmatch
from typing import Any, List, Optional


def normalize_tool_selection(selected_tools: Any) -> Optional[List[str]]:
    """Normalize request-level tool selection into ordered, non-empty patterns."""
    if selected_tools is None:
        return None
    if isinstance(selected_tools, str):
        selected_tools = [selected_tools]
    if not isinstance(selected_tools, list):
        return []
    return [str(tool).strip() for tool in selected_tools if str(tool).strip()]


def tool_name_variants(value: str) -> List[str]:
    """Return equivalent local/remote tool spellings used by configs and MCP."""
    text = str(value or "")
    variants = [text]
    if ":" in text:
        variants.append(text.replace(":", "-", 1))
    if "-" in text:
        variants.append(text.replace("-", ":", 1))
    return list(dict.fromkeys(item for item in variants if item))


def matches_tool_pattern(tool_name: str, pattern: str) -> bool:
    """Match a tool name against a config pattern, accepting ':' and '-' aliases."""
    if pattern in {"*", "all"}:
        return True
    return any(
        fnmatch.fnmatch(tool_candidate, pattern_candidate)
        for tool_candidate in tool_name_variants(tool_name)
        for pattern_candidate in tool_name_variants(pattern)
    )


def matches_any_tool_pattern(tool_name: str, patterns: List[str]) -> bool:
    return any(matches_tool_pattern(tool_name, pattern) for pattern in patterns)


def matches_tool_selection(selected_patterns: Optional[List[str]], tool_name: str) -> bool:
    if selected_patterns is None:
        return True
    if not selected_patterns:
        return False
    return matches_any_tool_pattern(tool_name, selected_patterns)
