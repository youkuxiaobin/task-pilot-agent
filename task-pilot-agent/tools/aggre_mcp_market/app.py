from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tools.aggre_mcp_market.models import MCPServerStatus, ToolCallResult, ToolInfo
from tools.aggre_mcp_market.service.prompt import assemble_prompt
from tools.aggre_mcp_market.service.registry import MCPRegistry
from tools.aggre_mcp_market.service import runtime as registry_runtime
from utils.logger import get_logger


class CallToolRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = {}


class ToolInfoModel(BaseModel):
    full_name: str
    name: str
    description: str | None = None
    input_schema: Dict[str, Any] | None = None
    output_schema: Dict[str, Any] | None = None
    server_url: str
    protocol: str
    tool_prefix: str
    source: str = "mcp"
    server_id: str = ""
    risk_level: str = "low"
    requires_approval: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def from_entity(t: ToolInfo) -> "ToolInfoModel":
        return ToolInfoModel(
            full_name=t.full_name,
            name=t.name,
            description=t.description,
            input_schema=t.input_schema,
            output_schema=t.output_schema,
            server_url=t.server_url,
            protocol=str(t.protocol.value),
            tool_prefix=t.tool_prefix,
            source=t.source,
            server_id=t.server_id,
            risk_level=t.risk_level,
            requires_approval=t.requires_approval,
            metadata=t.metadata,
        )


class MCPServerStatusModel(BaseModel):
    url: str
    protocol: str
    tool_prefix: str
    authorization_configured: bool = False
    status: str = "unknown"
    tool_count: int = 0
    error: str = ""
    last_checked_at: float | None = None
    duration_ms: int | None = None

    @staticmethod
    def from_entity(item: MCPServerStatus) -> "MCPServerStatusModel":
        return MCPServerStatusModel(
            url=item.url,
            protocol=str(item.protocol.value),
            tool_prefix=item.tool_prefix,
            authorization_configured=item.authorization_configured,
            status=item.status,
            tool_count=item.tool_count,
            error=item.error,
            last_checked_at=item.last_checked_at,
            duration_ms=item.duration_ms,
        )


class CallToolResponse(BaseModel):
    name: str
    arguments: Dict[str, Any]
    result: Any
    server_url: str
    protocol: str
    tool_prefix: str

    @staticmethod
    def from_entity(r: ToolCallResult) -> "CallToolResponse":
        return CallToolResponse(
            name=r.name,
            arguments=r.arguments,
            result=r.result,
            server_url=r.server_url,
            protocol=str(r.protocol.value),
            tool_prefix=r.tool_prefix,
        )


aggre_mcp_market_router = APIRouter()
registry: MCPRegistry | None = None
logger = get_logger(__name__)


async def init_mcp_market_registry() -> None:
    """初始化 MCP registry，阻塞刷新放到线程池，避免卡事件循环。"""
    global registry
    registry = await registry_runtime.init_registry(start_background=True)


def _active_registry() -> MCPRegistry | None:
    return registry or registry_runtime.get_registry()


@aggre_mcp_market_router.get("/tools", response_model=List[ToolInfoModel])
def get_tools() -> List[ToolInfoModel]:
    active_registry = _active_registry()
    tools = active_registry.list_tools() if active_registry else []
    return [ToolInfoModel.from_entity(t) for t in tools]


@aggre_mcp_market_router.get("/servers", response_model=List[MCPServerStatusModel])
def get_servers() -> List[MCPServerStatusModel]:
    active_registry = _active_registry()
    servers = active_registry.list_servers() if active_registry else []
    return [MCPServerStatusModel.from_entity(item) for item in servers]


@aggre_mcp_market_router.post("/refresh")
async def refresh_tools() -> Dict[str, Any]:
    active_registry = _active_registry()
    if active_registry is None:
        raise HTTPException(status_code=503, detail="MCP registry not initialised")
    await asyncio.to_thread(active_registry.refresh, True)
    return {
        "toolCount": len(active_registry.list_tools()),
        "servers": [MCPServerStatusModel.from_entity(item).model_dump() for item in active_registry.list_servers()],
    }


@aggre_mcp_market_router.get("/prompt", response_model=str)
def get_prompt() -> str:
    active_registry = _active_registry()
    tools = active_registry.list_tools() if active_registry else []
    return assemble_prompt(tools)


@aggre_mcp_market_router.post("/call_tool", response_model=CallToolResponse)
async def call_tool(req: CallToolRequest, request: Request):
    active_registry = _active_registry()
    if active_registry is None:
        raise HTTPException(status_code=503, detail="MCP registry not initialised")

    accept_header = request.headers.get("accept", "")
    wants_stream = "text/event-stream" in accept_header.lower()
    stream_flag = request.query_params.get("stream")
    if stream_flag:
        wants_stream = wants_stream or stream_flag.lower() in {"1", "true", "yes"}

    if wants_stream:
        async def event_iterator():
            try:
                stream_iter = await active_registry.call_tool_stream(req.tool_name, req.arguments)
                async for event in stream_iter:
                    payload = json.dumps(event, ensure_ascii=False, default=str)
                    yield f"data: {payload}\n\n"
            except Exception as exc:
                err_payload = {
                    "method": "notifications/message",
                    "params": {"data": str(exc)},
                    "error": str(exc),
                }
                yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"
            finally:
                yield "data: [DONE]\\n\\n"

        return StreamingResponse(event_iterator(), media_type="text/event-stream")

    try:
        result = await asyncio.to_thread(active_registry.call_tool, req.tool_name, req.arguments)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return CallToolResponse.from_entity(result)
