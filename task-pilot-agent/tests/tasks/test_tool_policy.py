from brain.core.tool_policy import (
    matches_any_tool_pattern,
    matches_tool_pattern,
    matches_tool_selection,
    normalize_tool_selection,
    tool_name_variants,
)


def test_normalize_tool_selection_preserves_none_and_filters_empty_values():
    assert normalize_tool_selection(None) is None
    assert normalize_tool_selection("file_read") == ["file_read"]
    assert normalize_tool_selection(["file_read", "", "  web_search  "]) == ["file_read", "web_search"]
    assert normalize_tool_selection({"bad": "shape"}) == []


def test_tool_name_variants_support_mcp_colon_and_hyphen_aliases():
    assert tool_name_variants("mcp_local:web_search") == ["mcp_local:web_search", "mcp_local-web_search"]
    assert tool_name_variants("mcp_local-web_search") == ["mcp_local-web_search", "mcp_local:web_search"]


def test_tool_pattern_matching_accepts_aliases_and_wildcards():
    assert matches_tool_pattern("mcp_local-web_search", "mcp_local:web_*") is True
    assert matches_tool_pattern("mcp_local:web_search", "mcp_local-web_*") is True
    assert matches_tool_pattern("file_read", "*") is True
    assert matches_any_tool_pattern("file_write", ["web_*", "file_*"]) is True
    assert matches_tool_selection(None, "shell_exec") is True
    assert matches_tool_selection([], "shell_exec") is False
    assert matches_tool_selection(["mcp_local:web_*"], "mcp_local-web_search") is True
