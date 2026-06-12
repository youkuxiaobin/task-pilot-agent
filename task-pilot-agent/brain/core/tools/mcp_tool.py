from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from .base import BaseTool
from brain.core.context import AgentContext
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MCPTool(BaseTool):
    """Adapter that exposes MCP-market tools through the agent tool interface."""

    full_name: str
    name: str
    description: str
    input_schema: Optional[Dict[str, Any]]
    output_schema: Optional[Dict[str, Any]]
    server_url: str
    protocol: str
    tool_prefix: str
    context: AgentContext
    mcp_market_url: str
    request_timeout: Optional[float] = 900


    def __post_init__(self) -> None:
        self.name = self.full_name
        if not self.description:
            self.description = f"MCP tool: {self.name}"

    def to_params(self) -> Dict[str, Any]:
        if self.input_schema:
            return self.input_schema
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, input_obj: Dict[str, Any]) -> str | None:
        call_url = f"{self.mcp_market_url}/call_tool"
        arguments = self._with_runtime_arguments(input_obj)
        payload = {"tool_name": self.full_name, "arguments": arguments}
        headers = {"Accept": "text/event-stream, application/json"}
        logger.debug(
            "execute mcp tool %s with argument keys=%s",
            self.full_name,
            sorted(arguments.keys()),
        )
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(call_url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return f"call tools error (HTTP {response.status}): {error_text}"

                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" in content_type or "application/x-ndjson" in content_type:
                        return await self._handle_streaming_response(response)
                    return await self._handle_direct_response(response)
        except Exception as exc:  # pragma: no cover - network/runtime edge
            logger.exception("call tools error")
            return f"call tools error: {exc}"

    def _with_runtime_arguments(self, input_obj: Dict[str, Any]) -> Dict[str, Any]:
        arguments = dict(input_obj or {})
        schema = self.input_schema if isinstance(self.input_schema, dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        defaults = {
            "request_id": getattr(self.context, "requestId", None),
            "trace_id": getattr(self.context, "requestId", None),
            "task_id": getattr(self.context, "task_id", None),
            "work_dir": getattr(self.context, "work_dir", None),
            "user_id": getattr(self.context, "user_id", None),
            "agent_id": getattr(self.context, "agent_id", None),
            "run_id": getattr(self.context, "run_id", None),
            "session_id": getattr(self.context, "sessionId", None),
        }
        for key, value in defaults.items():
            if key in properties and key not in arguments and value:
                arguments[key] = value
        return arguments

    async def _handle_direct_response(self, response: aiohttp.ClientResponse) -> str | None:
        try:
            result = await response.json()
        except json.JSONDecodeError:
            return await response.text()

        logger.debug("mcp tool direct result received for %s", self.full_name)
        payload = result.get("result", result)
        if isinstance(payload, (dict, list)):
            return json.dumps(payload, ensure_ascii=False, indent=2)
        return str(payload)

    async def _handle_streaming_response(self, response: aiohttp.ClientResponse) -> str | None:
        final_result: Any = None
        final_error: Optional[str] = None

        async for chunk in response.content:
            if not chunk:
                continue
            line = chunk.decode("utf-8", errors="ignore").strip()
            if not line or not line.startswith("data:"):
                continue

            data_str = line[5:].lstrip()
            if data_str == "[DONE]":
                break

            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                logger.warning("Failed to parse streaming event: %s", data_str)
                continue

            method = event.get("method")
            if isinstance(method, str):
                params = event.get("params") or {}
                self._dispatch_notification(method, params)

            if "result" in event:
                final_result = event["result"]
                if "structuredContent" in final_result:
                    final_result = final_result["structuredContent"]
                    if "result" in final_result:
                        final_result = final_result["result"]
                
                self._dispatch_stream_chunk(final_result)

            if "error" in event and event["error"]:
                final_error = str(event["error"])
                self.context.printer.send(
                    None,
                    "notifications",
                    {"process_message": final_error},
                    None,
                    False,
                )

        if final_result is not None:
            if isinstance(final_result, (dict, list)):
                return json.dumps(final_result, ensure_ascii=False, indent=2)
            return str(final_result)

        if final_error is not None:
            return final_error

        return "streaming completed"

    def _dispatch_notification(self, method: str, params: Any) -> None:
        if method == "notifications/message":
            message = params.get("data") if isinstance(params, dict) else None
            if isinstance(message, str):
                #print(f"##########report_agent message: {message} {self.name}")
                if self.name == "report":
                    
                    json_message = json.loads(message)
                    if "type" in json_message and json_message["type"] == "report_chunk":
                        self.context.printer.send(
                            None,
                            "result",
                            json_message["chunk"],
                            None,
                            False,
                        )
                else:
                    self.context.printer.send(
                        None,
                        "notifications",
                        {"process_message": message},
                        None,
                        False,
                    )
        elif method == "notifications/progress" and isinstance(params, dict):
            progress = params.get("progress")
            total = params.get("total")
            message = params.get("message") or "progress"
            progress_text = message
            if isinstance(progress, (int, float)):
                percent = progress * 100
                progress_text = f"{message} ({percent:.1f}%)" if message else f"progress {percent:.1f}%"
            if total not in (None, 0) and isinstance(progress, (int, float)):
                progress_text += f" / total {total}"
            self.context.printer.send(
                None,
                "notifications",
                {"process_message": progress_text},
                None,
                False,
            )
        else:
            try:
                raw = json.dumps(params, ensure_ascii=False)
            except Exception:
                raw = str(params)
            self.context.printer.send(
                None,
                "notifications",
                {"process_message": f"{method}: {raw}"},
                None,
                False,
            )

    def _dispatch_stream_chunk(self, result: Any) -> None:
        if isinstance(result, (dict, list)):
            chunk = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            chunk = str(result)
        self.context.printer.send(None, "stream", {"chunk": chunk}, None, False)


class MCPToolFetcher:
    def __init__(self, context: AgentContext, mcp_market_url: str = "http://127.0.0.1:9010/aggre_mcp_market"):
        self.context = context
        self.mcp_market_url = mcp_market_url.rstrip("/")

    async def fetch_tools(self) -> List[MCPTool]:
        tools_url = f"{self.mcp_market_url}/tools"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(tools_url) as response:
                    if response.status != 200:
                        logger.error("fetch tools failed HTTP %s", response.status)
                        return []
                    tools_data = await response.json()
        except Exception as exc:  # pragma: no cover - network failure
            logger.exception("call mcp market error")
            return []

        mcp_tools: List[MCPTool] = []
        for tool_data in tools_data:
            mcp_tools.append(
                MCPTool(
                    full_name=tool_data["full_name"],
                    name=tool_data["name"],
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("input_schema"),
                    output_schema=tool_data.get("output_schema"),
                    server_url=tool_data["server_url"],
                    protocol=tool_data["protocol"],
                    tool_prefix=tool_data["tool_prefix"],
                    context=self.context,
                    mcp_market_url=self.mcp_market_url,
                )
            )
        return mcp_tools

    async def get_tool_by_name(self, name: str) -> Optional[MCPTool]:
        tools = await self.fetch_tools()
        for tool in tools:
            if tool.full_name == name:
                return tool
        return None
