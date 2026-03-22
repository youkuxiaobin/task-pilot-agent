from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, AsyncIterator, Dict, List, Optional

from tools.aggre_mcp_market.mcp_clients.base import MCPClientBase
from tools.aggre_mcp_market.models import Protocol, ToolCallResult, ToolInfo


def _import_sse_components():
    try:
        from mcp.client.sse import sse_client
        from mcp.client.session import ClientSession
    except Exception as exc:  # pragma: no cover - import error bubble
        raise RuntimeError(
            "The MCP Python SDK is required. Install with: pip install mcp"
        ) from exc
    return sse_client, ClientSession


def _normalise_schema(schema: Any) -> Optional[Dict[str, Any]]:
    if schema is None:
        return None
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_dump"):
        return schema.model_dump(mode="json", exclude_none=True)  # type: ignore[attr-defined]
    return None


def _normalise_result(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        try:
            return result.model_dump(mode="json", exclude_none=True)  # type: ignore[attr-defined]
        except TypeError:
            return result.model_dump()  # type: ignore[attr-defined]
    return result


class SSEMCPClient(MCPClientBase):
    protocol = Protocol.SSE

    def __init__(self, url: str, authorization: Optional[str], tool_prefix: str):
        super().__init__(url, authorization, tool_prefix)

    # ------------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        if not self.authorization:
            return {}
        return {"Authorization": self.authorization}

    def _run_sync(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    # ------------------------------------------------------------------
    def list_tools(self) -> List[ToolInfo]:
        async def _run() -> List[ToolInfo]:
            sse_client, ClientSession = _import_sse_components()
            headers = self._headers()
            async with sse_client(self.url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    if hasattr(session, "initialize"):
                        await session.initialize()
                    tools_result = await session.list_tools()

            raw_tools = getattr(tools_result, "tools", tools_result)
            tools: List[ToolInfo] = []
            for tool in raw_tools:
                name = getattr(tool, "name", None)
                desc = getattr(tool, "description", None)
                input_schema = _normalise_schema(getattr(tool, "inputSchema", None))
                output_schema = _normalise_schema(getattr(tool, "outputSchema", None))
                full_name = f"{self.tool_prefix}-{name}" if self.tool_prefix else str(name)
                tools.append(
                    ToolInfo(
                        full_name=full_name,
                        name=str(name),
                        description=str(desc) if desc is not None else None,
                        input_schema=input_schema,
                        output_schema=output_schema,
                        server_url=self.url,
                        protocol=self.protocol,
                        tool_prefix=self.tool_prefix,
                    )
                )
            return tools

        return self._run_sync(_run())

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> ToolCallResult:
        async def _run() -> ToolCallResult:
            sse_client, ClientSession = _import_sse_components()
            headers = self._headers()
            async with sse_client(self.url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    if hasattr(session, "initialize"):
                        await session.initialize()
                    result = await session.call_tool(name, arguments)

            payload = _normalise_result(result)
            return ToolCallResult(
                name=name,
                arguments=arguments,
                result=payload,
                server_url=self.url,
                protocol=self.protocol,
                tool_prefix=self.tool_prefix,
            )

        return self._run_sync(_run())

    # ------------------------------------------------------------------
    def supports_streaming(self) -> bool:
        return True

    async def call_tool_stream(
        self,
        name: str,
        arguments: Dict[str, Any],
    ) -> AsyncIterator[Dict[str, Any]]:
        sse_client, ClientSession = _import_sse_components()
        headers = self._headers()
        queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()

        async def emit(event: Dict[str, Any]) -> None:
            await queue.put(event)

        async def progress_callback(progress: float, total: Optional[float], message: Optional[str]) -> None:
            await emit(
                {
                    "method": "notifications/progress",
                    "params": {
                        "progress": progress,
                        "total": total,
                        "message": message,
                    },
                }
            )

        async def logging_callback(params: Any) -> None:
            payload: Dict[str, Any]
            try:
                payload = params.model_dump(mode="json", exclude_none=True)  # type: ignore[attr-defined]
            except Exception:
                payload = {
                    "level": getattr(params, "level", None),
                    "logger": getattr(params, "logger", None),
                    "data": getattr(params, "data", None),
                }
            else:
                payload = dict(payload)
            if not isinstance(payload.get("data"), str):
                payload["data"] = json.dumps(payload.get("data"), ensure_ascii=False)
            await emit({"method": "notifications/message", "params": payload})

        async def run_call() -> None:
            try:
                async with sse_client(self.url, headers=headers) as (read, write):
                    async with ClientSession(read, write, logging_callback=logging_callback) as session:
                        if hasattr(session, "initialize"):
                            await session.initialize()
                        result = await session.call_tool(
                            name,
                            arguments,
                            progress_callback=progress_callback,
                        )
                        await emit({"result": _normalise_result(result)})
            except Exception as exc:
                err = str(exc)
                await emit(
                    {
                        "method": "notifications/message",
                        "params": {"data": err},
                        "error": err,
                    }
                )
            finally:
                await queue.put(None)

        runner = asyncio.create_task(run_call())

        async def iterator() -> AsyncIterator[Dict[str, Any]]:
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item
            finally:
                if not runner.done():
                    runner.cancel()
                    with contextlib.suppress(Exception):
                        await runner

        return iterator()
