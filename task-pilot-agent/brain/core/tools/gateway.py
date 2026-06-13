from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.core.agent_registry import AgentConfig, AgentRegistry, AgentToolSpec
from brain.core.context import AgentContext
from brain.core.planning_policy import PLAN_TOOL_NAME, should_use_plan
from brain.core.tool_policy import (
    matches_tool_selection,
    normalize_tool_selection,
)
from brain.core.tools.builtin_handoff_tool import BuiltinHandoffTool, HandoffStarter
from brain.core.tools.builtin_plan_tool import BuiltinPlanTool
from brain.core.tools.builtin_request_input_tool import BuiltinRequestInputTool
from brain.core.tools.builtin_todo_tool import BuiltinTodoTool
from brain.core.tools.collection import ToolCollection
from brain.core.tools.mcp_tool import MCPToolFetcher
from utils.logger import get_logger

logger = get_logger(__name__)


def find_agent_tool_spec(agent_config: Optional[AgentConfig], tool_name: str) -> Optional[AgentToolSpec]:
    if not agent_config:
        return None
    exact = next((tool for tool in agent_config.tools if tool.name == tool_name), None)
    if exact:
        return exact
    return next(
        (
            tool
            for tool in agent_config.tools
            if tool.name and matches_tool_selection([tool.name], tool_name)
        ),
        None,
    )


def blocked_tool_reasons(
    blocked_tools: List[str],
    agent_config: Optional[AgentConfig],
    selected_tools: Optional[List[str]],
    approved_tools: Optional[List[str]] = None,
) -> Dict[str, str]:
    normalized_selected = normalize_tool_selection(selected_tools)
    normalized_approved = normalize_tool_selection(approved_tools)
    reasons: Dict[str, str] = {}
    for tool_name in blocked_tools:
        if normalized_selected is not None and not matches_tool_selection(normalized_selected, tool_name):
            reasons[tool_name] = "not_selected"
            continue
        if agent_config:
            reasons[tool_name] = (
                agent_config.tool_block_reason(tool_name, approved_tools=normalized_approved)
                or "blocked_by_policy"
            )
        else:
            reasons[tool_name] = "blocked_by_policy"
    return reasons


def approval_requests_from_blocked_tools(
    blocked_tools: List[str],
    blocked_reasons: Dict[str, str],
    agent_config: Optional[AgentConfig],
    selected_tools: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    normalized_selected = normalize_tool_selection(selected_tools)
    requests: List[Dict[str, Any]] = []
    for tool_name in blocked_tools:
        reason = blocked_reasons.get(tool_name) or ""
        if reason != "high_risk_requires_approval":
            continue
        if normalized_selected is None or not matches_tool_selection(normalized_selected, tool_name):
            continue
        tool_spec = find_agent_tool_spec(agent_config, tool_name)
        policy = dict(getattr(tool_spec, "policy", None) or {})
        requests.append(
            {
                "tool": tool_name,
                "reason": reason,
                "approvalType": "high_risk_tools",
                "riskLevel": str(policy.get("risk") or "high"),
                "description": str(getattr(tool_spec, "description", "") or ""),
                "policy": policy,
            }
        )
    return requests


def approval_waiting_message(approval_requests: List[Dict[str, Any]], language: Optional[str] = None) -> str:
    tool_names = [
        str(item.get("tool") or "").strip()
        for item in approval_requests
        if isinstance(item, dict) and str(item.get("tool") or "").strip()
    ]
    names = ", ".join(tool_names) or "high risk tools"
    if str(language or "").lower().startswith("en"):
        return f"Approval is required before using: {names}"
    return f"需要审批工具：{names}"


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
            if agent_config.allows_tool(PLAN_TOOL_NAME, approved_tools=approved_tools) and await _should_expose_plan_tool(
                ctx,
                selected_tools,
            ):
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


async def _should_expose_plan_tool(ctx: AgentContext, selected_tools: Optional[List[str]]) -> bool:
    if selected_tools is not None:
        return matches_tool_selection(selected_tools, PLAN_TOOL_NAME)
    if not str(getattr(ctx, "query", "") or "").strip():
        return True
    return await should_use_plan(ctx)
