from __future__ import annotations

import fnmatch
from typing import Any, List, Optional

from brain.core.agent_registry import AgentRegistry
from brain.core.context import AgentContext
from brain.core.tools.builtin_handoff_tool import BuiltinHandoffTool, HandoffStarter
from brain.core.tools.builtin_plan_tool import BuiltinPlanTool
from brain.core.tools.builtin_request_input_tool import BuiltinRequestInputTool
from brain.core.tools.builtin_todo_tool import BuiltinTodoTool
from brain.core.tools.collection import ToolCollection
from brain.core.tools.mcp_tool import MCPToolFetcher
from utils.logger import get_logger

logger = get_logger(__name__)


def normalize_tool_selection(selected_tools: Any) -> Optional[List[str]]:
    if selected_tools is None:
        return None
    if isinstance(selected_tools, str):
        selected_tools = [selected_tools]
    if not isinstance(selected_tools, list):
        return []
    return [str(tool).strip() for tool in selected_tools if str(tool).strip()]


def matches_tool_selection(selected_patterns: Optional[List[str]], tool_name: str) -> bool:
    if selected_patterns is None:
        return True
    if not selected_patterns:
        return False
    for pattern in selected_patterns:
        if pattern in {"*", "all"}:
            return True
        for tool_candidate in _tool_name_variants(tool_name):
            for pattern_candidate in _tool_name_variants(pattern):
                if fnmatch.fnmatch(tool_candidate, pattern_candidate):
                    return True
    return False


def _tool_name_variants(value: str) -> List[str]:
    text = str(value or "")
    variants = [text]
    if ":" in text:
        variants.append(text.replace(":", "-", 1))
    if "-" in text:
        variants.append(text.replace("-", ":", 1))
    return list(dict.fromkeys(item for item in variants if item))


class ToolGateway:
    """Build task-scoped tool collections through one policy-aware path."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        *,
        mcp_market_url: str,
        handoff_starter: Optional[HandoffStarter] = None,
        mcp_fetcher_cls: type[MCPToolFetcher] = MCPToolFetcher,
    ) -> None:
        self.agent_registry = agent_registry
        self.mcp_market_url = mcp_market_url
        self.handoff_starter = handoff_starter
        self.mcp_fetcher_cls = mcp_fetcher_cls

    async def build_collection(self, ctx: AgentContext) -> ToolCollection:
        tc = ToolCollection()
        tc.agentContext = ctx
        selected_tools = normalize_tool_selection(getattr(ctx, "selected_tools", None))
        approved_tools = normalize_tool_selection(getattr(ctx, "approved_tools", None))
        agent_config = self.agent_registry.get(ctx.agent_id)
        if agent_config:
            tc.set_allowed_tool_patterns(
                selected_tools if selected_tools is not None else agent_config.tool_patterns()
            )
            tc.set_tool_timeout_patterns(
                {
                    tool.name: tool.timeout_seconds
                    for tool in agent_config.tools
                    if tool.timeout_seconds
                }
            )
            tc.set_tool_allowed_checker(
                lambda tool_name: agent_config.allows_tool(tool_name, approved_tools=approved_tools)
                and matches_tool_selection(selected_tools, tool_name)
            )
            if agent_config.allows_tool("builtin:plan_tool", approved_tools=approved_tools):
                tc.add_tool(BuiltinPlanTool(ctx))
            if agent_config.allows_tool("builtin:set_todo_list", approved_tools=approved_tools):
                tc.add_tool(BuiltinTodoTool(ctx))
            if self.handoff_starter and agent_config.allows_tool("builtin:handoff", approved_tools=approved_tools):
                tc.add_tool(BuiltinHandoffTool(ctx, self.handoff_starter))
            if agent_config.allows_tool("builtin:request_input", approved_tools=approved_tools):
                tc.add_tool(BuiltinRequestInputTool(ctx))
        elif selected_tools is not None:
            tc.set_allowed_tool_patterns(selected_tools)

        try:
            mcp_fetcher = self.mcp_fetcher_cls(ctx, self.mcp_market_url)
            mcp_tools = await mcp_fetcher.fetch_tools()
            for mcp_tool in mcp_tools:
                tc.add_tool(mcp_tool)
                logger.debug("add mcp tools: %s - %s", mcp_tool.name, mcp_tool.description)
            logger.debug("load %s mcp tools", len(mcp_tools))
        except Exception as exc:
            logger.error("load mcp tools error: %s", exc)

        if not tc.tool_map:
            logger.warning("No MCP tools loaded; executor will have no available tools for this request.")
        return tc
