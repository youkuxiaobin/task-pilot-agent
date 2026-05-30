from __future__ import annotations
import json
import asyncio
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional
from pathlib import Path
import contextlib

from fastapi.responses import StreamingResponse, PlainTextResponse, HTMLResponse

from brain.models.requests import AgentMessage, GptQueryReq
from brain.core.agent_registry import AgentConfig, AgentRegistry
from brain.core.context import AgentContext, FileItem
from brain.core.printer import SSEPrinter
from brain.core.tasks import AgentTaskStatus, TaskStore, serialize_event, serialize_task
from brain.core.tools.collection import ToolCollection
    
from brain.core.tools.mcp_tool import MCPToolFetcher
from brain.core.handlers.factory import AgentHandlerFactory
from brain.core.handlers.react import ReactHandler
from brain.core.handlers.plan_solve import PlanSolveHandler
from config.config import agentSettings
from pydantic import ValidationError
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from utils.logger import get_logger, configure_log_context, clear_log_context
from llm.types import LLMMessage, RoleType

logger = get_logger(__name__)

agent_router = APIRouter()

WEB_ROOT = Path(__file__).resolve().parent / "web"

agentFactory = AgentHandlerFactory([PlanSolveHandler(), ReactHandler()])
agentRegistry = AgentRegistry()

async def build_tool_collection(ctx: AgentContext) -> ToolCollection:
    """build tool collection, including local tools and mcp market tools"""
    tc = ToolCollection()
    tc.agentContext = ctx
    agent_config = agentRegistry.get(ctx.agent_id)
    if agent_config:
        tc.set_allowed_tool_patterns(agent_config.tool_patterns())

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


def _extract_result_text(event_data: Dict[str, Any]) -> Optional[str]:
    if event_data.get("messageType") != "result":
        return None
    result = event_data.get("result")
    if result:
        return str(result)
    result_map = event_data.get("resultMap")
    if isinstance(result_map, dict):
        task_summary = result_map.get("taskSummary")
        if task_summary:
            return str(task_summary)
        if result_map:
            return json.dumps(result_map, ensure_ascii=False, default=str)
    return None


def _resolve_agent_config(agent_id: str) -> Optional[AgentConfig]:
    try:
        agentRegistry.reload()
        return agentRegistry.get(agent_id)
    except Exception:
        logger.exception("failed to load agent config for %s", agent_id)
        return None


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
        agent_config = _resolve_agent_config(request.agent_id)
        resolved_mode = request.mode or (agent_config.mode if agent_config else None) or "plans_executor"

        task_id = trace_id
        last_result: Dict[str, Optional[str]] = {"output": None}
        printer = SSEPrinter(enqueue, trace_id, task_id=task_id)
        task_store: Optional[TaskStore] = None

        try:
            task_store = TaskStore()
            latest_input = (messages[-1].content or "").strip()
            task_store.create_task(
                task_id=task_id,
                trace_id=trace_id,
                conversation_id=request.conversation_id,
                user_id=request.user_id,
                agent_id=request.agent_id,
                mode=resolved_mode,
                output_style=request.outputStyle,
                input_text=latest_input,
                metadata={
                    "source": "autoagent",
                    "agentConfigId": agent_config.id if agent_config else None,
                },
            )
            task_store.add_event(
                task_id,
                "task_created",
                {
                    "mode": resolved_mode,
                    "outputStyle": request.outputStyle,
                    "conversationId": request.conversation_id,
                    "agentConfigId": agent_config.id if agent_config else None,
                },
                trace_id=trace_id,
                source="autoagent",
            )

            def record_stream_event(event_data: Dict[str, Any]) -> None:
                result_text = _extract_result_text(event_data)
                if result_text is not None:
                    last_result["output"] = result_text
                try:
                    assert task_store is not None
                    task_store.add_event(
                        task_id,
                        str(event_data.get("messageType") or "stream_event"),
                        event_data,
                        trace_id=trace_id,
                        source="sse",
                        message_id=str(event_data.get("messageId") or ""),
                    )
                except Exception:
                    logger.exception("failed to persist task event for task %s", task_id)

            printer.event_sink = record_stream_event
            task_store.update_status(task_id, AgentTaskStatus.RUNNING)
            task_store.add_event(
                task_id,
                "task_running",
                {"status": AgentTaskStatus.RUNNING},
                trace_id=trace_id,
                source="autoagent",
            )
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
                mode=resolved_mode,
                task_id=task_id,
                agent_system_prompt=agent_config.system_prompt if agent_config else None,
            )
            _convert_agent_messages(ctx, messages)
            logger.debug("request context prepared: request_id=%s mode=%s", ctx.requestId, ctx.mode)

            tc = await build_tool_collection(ctx)
            ctx.toolCollection = tc

            handler = agentFactory.get_handler(ctx, request)  # type: ignore[arg-type]
            if not handler:
                error_message = "unknown agentType"
                printer.send(None, "result", {"taskSummary": error_message}, None, True)
                task_store.update_status(task_id, AgentTaskStatus.FAILED, error_message=error_message)
                task_store.add_event(
                    task_id,
                    "task_failed",
                    {"error": error_message},
                    trace_id=trace_id,
                    source="autoagent",
                )
                return

            try:
                await handler.handle(ctx, request)
                task_store.update_status(
                    task_id,
                    AgentTaskStatus.COMPLETED,
                    output_text=last_result["output"],
                )
                task_store.add_event(
                    task_id,
                    "task_completed",
                    {"status": AgentTaskStatus.COMPLETED},
                    trace_id=trace_id,
                    source="autoagent",
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("autoagent handler failed for request %s", ctx.requestId)
                printer.send(None, "result", f"autoagent error: {exc}", None, True)
                task_store.update_status(task_id, AgentTaskStatus.FAILED, error_message=str(exc))
                task_store.add_event(
                    task_id,
                    "task_failed",
                    {"error": str(exc)},
                    trace_id=trace_id,
                    source="autoagent",
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("autoagent pipeline failed for request %s", trace_id)
            printer.send(None, "result", f"autoagent error: {exc}", None, True)
            if task_store is not None:
                task_store.update_status(task_id, AgentTaskStatus.FAILED, error_message=str(exc))
                task_store.add_event(
                    task_id,
                    "task_failed",
                    {"error": str(exc)},
                    trace_id=trace_id,
                    source="autoagent",
                )
        finally:
            printer.close()
    finally:
        clear_log_context()


@agent_router.get("/agents")
async def list_agents() -> Dict[str, Any]:
    agentRegistry.reload()
    return {"items": [agent.to_dict() for agent in agentRegistry.list_agents()]}


@agent_router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> Dict[str, Any]:
    agentRegistry.reload()
    agent = agentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent.to_dict()


@agent_router.get("/tasks")
async def list_agent_tasks(
    user_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    store = TaskStore()
    tasks = store.list_tasks(
        user_id=user_id,
        status=status,
        agent_id=agent_id,
        limit=limit,
        offset=offset,
    )
    return {"items": [serialize_task(task) for task in tasks], "limit": limit, "offset": offset}


@agent_router.get("/tasks/{task_id}")
async def get_agent_task(task_id: str) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return serialize_task(task)


@agent_router.get("/tasks/{task_id}/events")
async def list_agent_task_events(
    task_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    store = TaskStore()
    if not store.get_task(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    events = store.list_events(task_id, limit=limit, offset=offset)
    return {"items": [serialize_event(event) for event in events], "limit": limit, "offset": offset}


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


