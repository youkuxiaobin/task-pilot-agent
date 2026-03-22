from __future__ import annotations

import abc
from typing import Any, AsyncIterator, Dict, List, Optional

from tools.aggre_mcp_market.models import ToolCallResult, ToolInfo


class MCPClientBase(abc.ABC):
    """Abstract base class wrapping different MCP transport clients."""

    def __init__(self, url: str, authorization: Optional[str], tool_prefix: str) -> None:
        self.url = url
        self.authorization = authorization
        self.tool_prefix = tool_prefix

    @abc.abstractmethod
    def list_tools(self) -> List[ToolInfo]:
        """Return available tools from the upstream MCP server."""

    @abc.abstractmethod
    def call_tool(self, name: str, arguments: Dict[str, Any]) -> ToolCallResult:
        """Invoke a tool and return its full result payload."""

    # ------------------------------------------------------------------
    def supports_streaming(self) -> bool:
        """Whether this client can expose streaming call events."""
        return False

    async def call_tool_stream(
        self,
        name: str,
        arguments: Dict[str, Any],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream tool execution events as dictionaries.

        Implementations must yield JSON-serialisable dict objects containing
        either MCP notifications (e.g. ``{"method": "notifications/message", ...}``)
        or final ``{"result": ...}`` / ``{"error": ...}`` payloads.
        """
        raise NotImplementedError(
            f"Streaming not supported by {self.__class__.__name__}"
        )
