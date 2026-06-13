from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator, Dict, List, Optional

from tools.aggre_mcp_market.models import MCPServerStatus, ToolCallResult, ToolInfo
from tools.aggre_mcp_market.service.registry import MCPRegistry, load_registry_from_yaml
from utils.logger import get_logger


logger = get_logger(__name__)
_registry: Optional[MCPRegistry] = None


def get_registry() -> Optional[MCPRegistry]:
    return _registry


def set_registry(registry: Optional[MCPRegistry]) -> None:
    global _registry
    old_registry = _registry
    _registry = registry
    if old_registry is not None and old_registry is not registry:
        with contextlib.suppress(Exception):
            old_registry.stop()


async def init_registry(*, start_background: bool = True) -> MCPRegistry:
    registry = await asyncio.to_thread(load_registry_from_yaml, start_background)
    set_registry(registry)
    try:
        await asyncio.to_thread(registry.blocking_refresh, 10, 1.0)
    except Exception as exc:
        logger.error("initial MCP registry refresh failed: %s", exc)
    return registry


def require_registry() -> MCPRegistry:
    registry = get_registry()
    if registry is None:
        raise RuntimeError("MCP registry not initialised")
    return registry


def list_tools() -> List[ToolInfo]:
    registry = get_registry()
    return registry.list_tools() if registry else []


def list_servers() -> List[MCPServerStatus]:
    registry = get_registry()
    return registry.list_servers() if registry else []


async def refresh_tools(keep_last_on_failure: bool = True) -> None:
    await asyncio.to_thread(require_registry().refresh, keep_last_on_failure)


async def refresh_server(server_id: str, keep_last_on_failure: bool = True) -> bool:
    return await asyncio.to_thread(require_registry().refresh_server, server_id, keep_last_on_failure)


async def call_tool(tool_name: str, arguments: Dict[str, Any]) -> ToolCallResult:
    return await asyncio.to_thread(require_registry().call_tool, tool_name, arguments)


async def call_tool_stream(tool_name: str, arguments: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
    return await require_registry().call_tool_stream(tool_name, arguments)
