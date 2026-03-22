from __future__ import annotations

import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import yaml

from config.config import agentSettings, reveal_secret
from utils.logger import get_logger
from tools.aggre_mcp_market.mcp_clients import MCPClientBase, SSEMCPClient, StreamableHttpMCPClient
from tools.aggre_mcp_market.models import (
    MCPServerConfig,
    Protocol,
    RegistrySnapshot,
    ToolCallResult,
    ToolInfo,
)


logger = get_logger(__name__)


class MCPRegistry:
    """Aggregates tools from multiple MCP servers and caches them."""

    def __init__(
        self,
        servers: List[MCPServerConfig],
        refresh_interval_seconds: int = 60,
        start_background: bool = True,
    ) -> None:
        self._refresh_interval_seconds = max(5, int(refresh_interval_seconds))
        self._clients: List[MCPClientBase] = [self._make_client(cfg) for cfg in servers]
        self._lock = threading.RLock()
        self._snapshot: RegistrySnapshot = RegistrySnapshot()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        if start_background:
            self._thread = threading.Thread(target=self._run_loop, name="mcp-refresh", daemon=True)
            self._thread.start()

    @staticmethod
    def _make_client(cfg: MCPServerConfig) -> MCPClientBase:
        if cfg.protocol == Protocol.SSE:
            return SSEMCPClient(cfg.url, reveal_secret(cfg.authorization), cfg.tool_prefix)
        if cfg.protocol == Protocol.STREAMABLE_HTTP:
            return StreamableHttpMCPClient(cfg.url, reveal_secret(cfg.authorization), cfg.tool_prefix)
        raise ValueError(f"Unsupported protocol: {cfg.protocol}")

    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run_loop(self) -> None:
        time.sleep(2)
        while not self._stop_event.is_set():
            try:
                self.refresh()
            except Exception:
                pass
            self._stop_event.wait(self._refresh_interval_seconds)

    # ------------------------------------------------------------------
    def refresh(self, keep_last_on_failure: bool = True) -> None:
        tools: List[ToolInfo] = []
        errors: List[str] = []
        for client in self._clients:
            try:
                tools.extend(client.list_tools())
            except Exception as exc:
                err_msg = f"{client.__class__.__name__}@{client.url} error: {exc}"
                errors.append(err_msg)
                logger.error(f"refresh tools error: {err_msg}")
                continue

        if not tools and keep_last_on_failure and self._snapshot.tools:
            logger.warning(
                "refresh skipped snapshot update because no tools were fetched; "
                f"keeping previous {len(self._snapshot.tools)} tools. errors={'; '.join(errors) if errors else 'none'}"
            )
            return

        index = {tool.full_name: tool for tool in tools}
        with self._lock:
            self._snapshot = RegistrySnapshot(tools=tools, index_by_full_name=index)

    def blocking_refresh(self, timeout_seconds: float = 10, interval_seconds: float = 1.0) -> None:
        """Block until tools are fetched or timeout.

        解决启动时 mcp server 尚未就绪导致初始快照为空的问题，
        避免等待下一次刷新间隔才拿到工具列表。
        """
        deadline = time.time() + max(timeout_seconds, 0)
        attempt = 0
        while True:
            attempt += 1
            self.refresh(keep_last_on_failure=False)
            with self._lock:
                tool_count = len(self._snapshot.tools)
            if tool_count:
                logger.info(
                    "MCP registry ready with %d tools after %d attempt(s)",
                    tool_count,
                    attempt,
                )
                return
            if time.time() >= deadline:
                logger.warning(
                    "MCP registry still empty after %.1f seconds; continuing with empty snapshot",
                    timeout_seconds,
                )
                return
            time.sleep(max(interval_seconds, 0.1))

    def list_tools(self) -> List[ToolInfo]:
        with self._lock:
            return list(self._snapshot.tools)

    def get_tool(self, full_name: str) -> Optional[ToolInfo]:
        with self._lock:
            return self._snapshot.index_by_full_name.get(full_name)

    def call_tool(self, full_name: str, arguments: dict) -> ToolCallResult:
        tool = self.get_tool(full_name)
        if not tool:
            raise KeyError(f"Tool not found: {full_name}")

        client = next(
            (
                c
                for c in self._clients
                if c.url == tool.server_url and c.tool_prefix == tool.tool_prefix
            ),
            None,
        )
        if client is None:
            raise RuntimeError(f"No client found for tool: {full_name}")

        return client.call_tool(tool.name, arguments)

    async def call_tool_stream(self, full_name: str, arguments: dict) -> AsyncIterator[Dict[str, Any]]:
        tool = self.get_tool(full_name)
        if not tool:
            raise KeyError(f"Tool not found: {full_name}")

        client = next(
            (
                c
                for c in self._clients
                if c.url == tool.server_url and c.tool_prefix == tool.tool_prefix
            ),
            None,
        )
        if client is None:
            raise RuntimeError(f"No client found for tool: {full_name}")
        if not client.supports_streaming():
            raise RuntimeError(f"Client {client.__class__.__name__} does not support streaming")

        return await client.call_tool_stream(tool.name, arguments)


def load_registry_from_yaml(start_background: bool = True) -> MCPRegistry:
    refresh_interval_seconds = int(agentSettings.mcp.mcp_market.refresh_interval_seconds)
    servers: List[MCPServerConfig] = []
    for item in agentSettings.mcp.mcp_market.mcp_servers:
        servers.append(
            MCPServerConfig(
                url=item.url,
                protocol=Protocol(item.transport),
                authorization=reveal_secret(item.authorization),
                tool_prefix=item.tool_prefix,
            )
        )

    return MCPRegistry(
        servers=servers,
        refresh_interval_seconds=refresh_interval_seconds,
        start_background=start_background,
    )
