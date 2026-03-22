from __future__ import annotations
import json
import asyncio
import time
import uuid
from typing import AsyncIterator, Callable, List, Optional
from pathlib import Path
import contextlib

from fastapi.responses import StreamingResponse, PlainTextResponse, HTMLResponse

from brain.models.requests import AgentMessage, GptQueryReq
from brain.core.context import AgentContext, FileItem
from brain.core.printer import SSEPrinter
from brain.core.tools.collection import ToolCollection
    
from brain.core.tools.mcp_tool import MCPToolFetcher
from brain.core.handlers.factory import AgentHandlerFactory
from brain.core.handlers.react import ReactHandler
from brain.core.handlers.plan_solve import PlanSolveHandler
from config.config import agentSettings
from pydantic import ValidationError
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from utils.logger import get_logger, configure_log_context, clear_log_context
from llm.types import LLMMessage, RoleType

logger = get_logger(__name__)

agent_router = APIRouter()

WEB_ROOT = Path(__file__).resolve().parent / "web"

agentFactory = AgentHandlerFactory([PlanSolveHandler(), ReactHandler()])

async def build_tool_collection(ctx: AgentContext) -> ToolCollection:
    """build tool collection, including local tools and mcp market tools"""
    tc = ToolCollection()
    tc.agentContext = ctx

    try:
        mcp_market_url = getattr(agentSettings, 'mcp_market_url', 'http://127.0.0.1:9010/aggre_mcp_market')
        mcp_fetcher = MCPToolFetcher(ctx, mcp_market_url)
        mcp_tools = await mcp_fetcher.fetch_tools()
        
        for mcp_tool in mcp_tools:
            tc.add_tool(mcp_tool)
            logger.debug(f"add mcp tools: {mcp_tool.name} - {mcp_tool.description}")
        
        logger.debug(f"load {len(mcp_tools)} mcp tools")         
    except Exception as e:
        logger.error(f"load mcp tools error: {e}")
    if not tc.tool_map:
        logger.warning("No MCP tools loaded; executor will have no available tools for this request.")
    return tc


async def sse_stream(run_fn: Callable[[Callable[[str], None]], asyncio.Coroutine]) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[str] = asyncio.Queue()

    def enqueue(data: str) -> None:
        queue.put_nowait(data)

    task = asyncio.create_task(run_fn(enqueue))
    async def heartbeat() -> None:
        try:
            while not task.done():
                await asyncio.sleep(10)
                queue.put_nowait("data: heartbeat\n\n")

        except asyncio.CancelledError:
            pass

    hb = asyncio.create_task(heartbeat())

    try:
        while True:
            data = await queue.get()
            yield data.encode("utf-8")
            if data.strip() == "data: [DONE]":
                break
        await task
    finally:
        hb.cancel()
        with contextlib.suppress(Exception):
            await hb

def fill_output_styles(req: GptQueryReq):
    if req.outputStyle is None:
        req.outputStyle = agentSettings.core.default_output_style
    if agentSettings.core.output_styles.get(req.outputStyle) is None:
        req.outputStyle = agentSettings.core.default_output_style


def _clone_gpt_request(req: GptQueryReq) -> GptQueryReq:
    if hasattr(req, "model_copy"):
        return req.model_copy(deep=True)  # type: ignore[attr-defined]
    return req.copy(deep=True)  # type: ignore[attr-defined]


def _convert_agent_messages(ctx: AgentContext, messages: Optional[List[AgentMessage]]) -> None:
    if not messages:
        return

    history: List[LLMMessage] = []
    task_product_files: List[FileItem] = []
    product_files: List[FileItem] = []
    latest_user_content: Optional[str] = None

    valid_roles = {
        RoleType.SYSTEM.value,
        RoleType.USER.value,
        RoleType.ASSISTANT.value,
        RoleType.TOOL.value,
    }

    last_index = len(messages) - 1
    for idx, msg in enumerate(messages):
        content = (msg.content or "").strip()

        role = (msg.role or "").strip().lower()
        if role not in valid_roles:
            role = RoleType.USER.value

        upload_files = msg.uploadFile or []
        attached_files = msg.files or []
        if idx == last_index and role == RoleType.USER.value:
            latest_user_content = content
            product_files.extend(upload_files)
        else:
            task_product_files.extend(attached_files)
            history.append(LLMMessage(role=role, content=content))

    if latest_user_content:
        ctx.query = latest_user_content
    ctx.messages = history
    ctx.taskProductFiles = task_product_files
    ctx.productFiles = product_files


async def _run_autoagent(req: GptQueryReq, enqueue: Callable[[str], None]) -> None:
    request = _clone_gpt_request(req)
    trace_id = request.trace_id or str(uuid.uuid4())
    request.trace_id = trace_id
    configure_log_context(trace_id=trace_id)

    try:
        messages = list(getattr(request, "messages", []) or [])
        if not messages:
            logger.warning("messages is empty")
            return

        last_role = (messages[-1].role or "").strip().lower()
        if last_role != RoleType.USER.value:
            logger.warning("last message must be user role")
            return
        
        if not request.user_id:
            request.user_id = str(uuid.uuid4())
        if not request.agent_id:
            request.agent_id = agentSettings.core.agent_id
        if not request.conversation_id:
            request.conversation_id = str(uuid.uuid4())

        fill_output_styles(request)
        
        printer = SSEPrinter(enqueue, trace_id)
        try:
            ctx = AgentContext(
                requestId=trace_id,
                sessionId=trace_id,
                query="",
                task=None,
                printer=printer,
                toolCollection=None,
                dateInfo=time.strftime("%Y-%m-%d"),
                isStream=True,
                streamMessageType=None,
                user_id=request.user_id,
                agent_id=request.agent_id,
                run_id=request.conversation_id,
                outputStyle=request.outputStyle,
                mode=request.mode or "plans_executor",
            )
            _convert_agent_messages(ctx, messages)
            logger.debug("request context prepared: request_id=%s mode=%s", ctx.requestId, ctx.mode)

            tc = await build_tool_collection(ctx)
            ctx.toolCollection = tc

            handler = agentFactory.get_handler(ctx, request)  # type: ignore[arg-type]
            if not handler:
                printer.send(None, "result", {"taskSummary": "unknown agentType"}, None, True)
                return

            try:
                await handler.handle(ctx, request)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("autoagent handler failed for request %s", ctx.requestId)
                printer.send(None, "result", f"autoagent error: {exc}", None, True)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("autoagent pipeline failed for request %s", trace_id)
            printer.send(None, "result", f"autoagent error: {exc}", None, True)
        finally:
            printer.close()
    finally:
        clear_log_context()


@agent_router.get("/web/autoagent", response_class=HTMLResponse)
async def autoagent_console() -> HTMLResponse:
    html_path = WEB_ROOT / "autoagent.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

@agent_router.post("/autoagent")
async def autoagent(req: GptQueryReq):
    async def run(enqueue: Callable[[str], None]) -> None:
        await _run_autoagent(req, enqueue)

    return StreamingResponse(sse_stream(run), media_type="text/event-stream")


@agent_router.websocket("/ws/autoagent")
async def autoagent_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        payload = await websocket.receive_json()
    except WebSocketDisconnect:
        return
    except Exception as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json({"error": f"invalid payload: {exc}"})
        with contextlib.suppress(Exception):
            await websocket.close(code=1003)
        return

    try:
        req = GptQueryReq.model_validate(payload)
    except ValidationError as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json({"error": "invalid_request", "details": exc.errors()})
        with contextlib.suppress(Exception):
            await websocket.close(code=1003)
        return

    queue: asyncio.Queue[str] = asyncio.Queue()

    def enqueue(data: str) -> None:
        queue.put_nowait(data)

    worker = asyncio.create_task(_run_autoagent(req, enqueue))
    try:
        while True:
            data = await queue.get()
            payload_text = data
            if payload_text.startswith("data: "):
                payload_text = payload_text[6:]
            payload_text = payload_text.rstrip()
            if not payload_text:
                continue

            sent = False
            if payload_text not in {"heartbeat", "[DONE]"}:
                try:
                    parsed = json.loads(payload_text)
                except json.JSONDecodeError:
                    pass
                else:
                    await websocket.send_json(parsed)
                    sent = True

            if not sent:
                await websocket.send_text(payload_text)

            if payload_text == "[DONE]":
                break
    except WebSocketDisconnect:
        worker.cancel()
    finally:
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        with contextlib.suppress(Exception):
            await websocket.close()


@agent_router.get("/web/health")
async def health() -> PlainTextResponse:
    return PlainTextResponse("ok")






