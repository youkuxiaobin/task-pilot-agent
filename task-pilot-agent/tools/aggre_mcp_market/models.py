from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Protocol(str, Enum):
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"


@dataclass(frozen=True)
class MCPServerConfig:
    url: str
    protocol: Protocol
    authorization: Optional[str] = None
    tool_prefix: str = ""


@dataclass
class ToolInfo:
    # Fully qualified tool name with prefix, e.g. "srv1:search"
    full_name: str
    # Raw tool name as provided by server
    name: str
    description: Optional[str]
    input_schema: Optional[Dict[str, Any]]
    server_url: str
    protocol: Protocol
    tool_prefix: str
    # Some SDKs expose an optional output schema; capture if available
    output_schema: Optional[Dict[str, Any]] = None


@dataclass
class ToolCallResult:
    name: str
    arguments: Dict[str, Any]
    # Result payload is server-defined; keep flexible
    result: Any
    server_url: str
    protocol: Protocol
    tool_prefix: str


@dataclass
class RegistrySnapshot:
    tools: List[ToolInfo] = field(default_factory=list)
    # Maps full tool name -> internal lookup key
    index_by_full_name: Dict[str, ToolInfo] = field(default_factory=dict)


@dataclass
class MCPServerStatus:
    url: str
    protocol: Protocol
    tool_prefix: str
    authorization_configured: bool = False
    status: str = "unknown"
    tool_count: int = 0
    error: str = ""
    last_checked_at: Optional[float] = None
    duration_ms: Optional[int] = None
