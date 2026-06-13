from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlparse
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import yaml

from config.config import agentSettings, reveal_secret
from utils.logger import get_logger
from tools.aggre_mcp_market.mcp_clients import MCPClientBase, SSEMCPClient, StreamableHttpMCPClient
from tools.aggre_mcp_market.models import (
    MCPServerConfig,
    MCPServerStatus,
    Protocol,
    RegistrySnapshot,
    ToolCallResult,
    ToolInfo,
)


logger = get_logger(__name__)


HIGH_RISK_TOOL_TOKENS = {
    "shell",
    "command_start",
    "command_write",
    "process_command",
    "code_interpreter",
    "file_write",
    "file_edit",
    "file_delete",
    "file_move",
    "file_copy",
    "directory_create",
    "config_update",
    "mcp_manager_add_server",
    "message_send",
    "create_subagent",
    "skill_install",
    "skill_set_enabled",
    "memory_delete",
}

MEDIUM_RISK_TOOL_TOKENS = {
    "browser_agent",
    "browser",
    "text_to_image",
    "memory_add",
    "mcp_manager_write_manifest",
}


class MCPRegistry:
    """Aggregates tools from multiple MCP servers and caches them."""

    def __init__(
        self,
        servers: List[MCPServerConfig],
        refresh_interval_seconds: int = 60,
        start_background: bool = True,
        snapshot_path: Optional[Path] = None,
    ) -> None:
        self._refresh_interval_seconds = max(5, int(refresh_interval_seconds))
        self._clients: List[MCPClientBase] = [self._make_client(cfg) for cfg in servers]
        self._lock = threading.RLock()
        self._snapshot: RegistrySnapshot = RegistrySnapshot()
        self._snapshot_path = snapshot_path or self._default_snapshot_path()
        self._server_statuses: Dict[Tuple[str, str], MCPServerStatus] = {
            (client.url, client.tool_prefix): MCPServerStatus(
                url=client.url,
                protocol=getattr(client, "protocol", Protocol.SSE),
                tool_prefix=client.tool_prefix,
                authorization_configured=bool(client.authorization),
            )
            for client in self._clients
        }
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._load_snapshot_cache()

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

    @staticmethod
    def _default_snapshot_path() -> Path:
        explicit = os.getenv("TASKPILOT_MCP_REGISTRY_SNAPSHOT")
        if explicit:
            return Path(explicit).expanduser().resolve()
        seed = os.getenv("APP_CONFIG_FILE") or os.getcwd()
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / f"taskpilot_mcp_registry_{digest}.json"

    @classmethod
    def _server_id_for(cls, url: str, tool_prefix: str) -> str:
        if tool_prefix:
            return tool_prefix
        parsed = urlparse(url)
        candidate = f"{parsed.netloc}{parsed.path}".strip("/") or url
        return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in candidate)

    @staticmethod
    def _risk_level_for_tool(tool_name: str) -> str:
        canonical = str(tool_name or "").lower().replace(":", "_").replace("-", "_")
        if any(token in canonical for token in HIGH_RISK_TOOL_TOKENS):
            return "high"
        if any(token in canonical for token in MEDIUM_RISK_TOOL_TOKENS):
            return "medium"
        return "low"

    def _enrich_tool(self, client: MCPClientBase, tool: ToolInfo) -> ToolInfo:
        server_id = self._server_id_for(client.url, client.tool_prefix)
        risk_level = self._risk_level_for_tool(tool.full_name or tool.name)
        tool.source = tool.source or "mcp"
        tool.server_id = tool.server_id or server_id
        tool.risk_level = tool.risk_level or risk_level
        if tool.risk_level == "low":
            tool.risk_level = risk_level
        tool.requires_approval = bool(tool.requires_approval or tool.risk_level in {"high", "critical"})
        metadata = dict(tool.metadata or {})
        metadata.update(
            {
                "serverId": server_id,
                "serverUrl": client.url,
                "toolPrefix": client.tool_prefix,
                "transport": str(getattr(client, "protocol", "")),
            }
        )
        tool.metadata = metadata
        return tool

    def _tools_from_client(self, client: MCPClientBase) -> List[ToolInfo]:
        return [self._enrich_tool(client, tool) for tool in client.list_tools()]

    def _save_snapshot_cache(self) -> None:
        if not self._snapshot_path:
            return
        with self._lock:
            tools = [asdict(tool) for tool in self._snapshot.tools]
            statuses = [asdict(status) for status in self._server_statuses.values()]
        payload = {
            "updatedAt": time.time(),
            "tools": tools,
            "serverStatuses": statuses,
        }
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._snapshot_path.with_suffix(f"{self._snapshot_path.suffix}.tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(self._snapshot_path)
        except Exception:
            logger.debug("failed to save MCP registry snapshot cache", exc_info=True)

    def _load_snapshot_cache(self) -> None:
        if not self._snapshot_path or not self._snapshot_path.exists():
            return
        try:
            payload = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            tools = [
                ToolInfo(
                    full_name=item["full_name"],
                    name=item["name"],
                    description=item.get("description"),
                    input_schema=item.get("input_schema"),
                    server_url=item.get("server_url", ""),
                    protocol=Protocol(item.get("protocol")),
                    tool_prefix=item.get("tool_prefix", ""),
                    output_schema=item.get("output_schema"),
                    source=item.get("source", "mcp"),
                    server_id=item.get("server_id", ""),
                    risk_level=item.get("risk_level", "low"),
                    requires_approval=bool(item.get("requires_approval", False)),
                    metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                )
                for item in payload.get("tools", [])
                if isinstance(item, dict) and item.get("full_name") and item.get("name")
            ]
            statuses = {}
            for item in payload.get("serverStatuses", []):
                if not isinstance(item, dict):
                    continue
                status = MCPServerStatus(
                    url=item.get("url", ""),
                    protocol=Protocol(item.get("protocol")),
                    tool_prefix=item.get("tool_prefix", ""),
                    authorization_configured=bool(item.get("authorization_configured", False)),
                    status=item.get("status", "unknown"),
                    tool_count=int(item.get("tool_count") or 0),
                    error=item.get("error", ""),
                    last_checked_at=item.get("last_checked_at"),
                    duration_ms=item.get("duration_ms"),
                )
                statuses[(status.url, status.tool_prefix)] = status
            with self._lock:
                if tools:
                    self._snapshot = RegistrySnapshot(
                        tools=tools,
                        index_by_full_name={tool.full_name: tool for tool in tools},
                    )
                self._server_statuses.update(statuses)
        except Exception:
            logger.debug("failed to load MCP registry snapshot cache", exc_info=True)

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
            started_at = time.time()
            client_tools: List[ToolInfo] = []
            try:
                client_tools = self._tools_from_client(client)
                tools.extend(client_tools)
                status = MCPServerStatus(
                    url=client.url,
                    protocol=getattr(client, "protocol", Protocol.SSE),
                    tool_prefix=client.tool_prefix,
                    authorization_configured=bool(client.authorization),
                    status="ok",
                    tool_count=len(client_tools),
                    last_checked_at=started_at,
                    duration_ms=max(0, int((time.time() - started_at) * 1000)),
                )
            except Exception as exc:
                err_msg = f"{client.__class__.__name__}@{client.url} error: {exc}"
                errors.append(err_msg)
                logger.error(f"refresh tools error: {err_msg}")
                status = MCPServerStatus(
                    url=client.url,
                    protocol=getattr(client, "protocol", Protocol.SSE),
                    tool_prefix=client.tool_prefix,
                    authorization_configured=bool(client.authorization),
                    status="error",
                    error=str(exc),
                    last_checked_at=started_at,
                    duration_ms=max(0, int((time.time() - started_at) * 1000)),
                )
            with self._lock:
                self._server_statuses[(client.url, client.tool_prefix)] = status

        if not tools and keep_last_on_failure and self._snapshot.tools:
            logger.warning(
                "refresh skipped snapshot update because no tools were fetched; "
                f"keeping previous {len(self._snapshot.tools)} tools. errors={'; '.join(errors) if errors else 'none'}"
            )
            return

        index = {tool.full_name: tool for tool in tools}
        with self._lock:
            self._snapshot = RegistrySnapshot(tools=tools, index_by_full_name=index)
        self._save_snapshot_cache()

    def _client_matches_server_id(self, client: MCPClientBase, server_id: str) -> bool:
        normalized = str(server_id or "").strip()
        if not normalized:
            return False
        return normalized in {
            client.url,
            client.tool_prefix,
            self._server_id_for(client.url, client.tool_prefix),
        }

    def refresh_server(self, server_id: str, keep_last_on_failure: bool = True) -> bool:
        matched_clients = [
            client
            for client in self._clients
            if self._client_matches_server_id(client, server_id)
        ]
        if not matched_clients:
            return False

        refreshed_tools: List[ToolInfo] = []
        successful_keys = set()
        failed_keys = set()
        for client in matched_clients:
            started_at = time.time()
            key = (client.url, client.tool_prefix)
            try:
                client_tools = self._tools_from_client(client)
                refreshed_tools.extend(client_tools)
                successful_keys.add(key)
                status = MCPServerStatus(
                    url=client.url,
                    protocol=getattr(client, "protocol", Protocol.SSE),
                    tool_prefix=client.tool_prefix,
                    authorization_configured=bool(client.authorization),
                    status="ok",
                    tool_count=len(client_tools),
                    last_checked_at=started_at,
                    duration_ms=max(0, int((time.time() - started_at) * 1000)),
                )
            except Exception as exc:
                failed_keys.add(key)
                logger.error("refresh server tools error: %s@%s error: %s", client.__class__.__name__, client.url, exc)
                status = MCPServerStatus(
                    url=client.url,
                    protocol=getattr(client, "protocol", Protocol.SSE),
                    tool_prefix=client.tool_prefix,
                    authorization_configured=bool(client.authorization),
                    status="error",
                    error=str(exc),
                    last_checked_at=started_at,
                    duration_ms=max(0, int((time.time() - started_at) * 1000)),
                )
            with self._lock:
                self._server_statuses[key] = status

        replaced_keys = set(successful_keys)
        if not keep_last_on_failure:
            replaced_keys.update(failed_keys)
        with self._lock:
            kept_tools = [
                tool
                for tool in self._snapshot.tools
                if (tool.server_url, tool.tool_prefix) not in replaced_keys
            ]
            tools = kept_tools + refreshed_tools
            self._snapshot = RegistrySnapshot(
                tools=tools,
                index_by_full_name={tool.full_name: tool for tool in tools},
            )
        self._save_snapshot_cache()
        return True

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

    def list_servers(self) -> List[MCPServerStatus]:
        with self._lock:
            return list(self._server_statuses.values())

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
