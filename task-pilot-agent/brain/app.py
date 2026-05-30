from __future__ import annotations
import fnmatch
import json
import asyncio
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional
from pathlib import Path
import contextlib

from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse, HTMLResponse

from brain.models.requests import AgentMessage, GptQueryReq, TaskUserInputReq
from brain.core.agent_registry import AgentConfig, AgentRegistry
from brain.core.context import AgentContext, FileItem
from brain.core.eval_runner import build_eval_run
from brain.core.printer import SSEPrinter
from brain.core.tasks import AgentTaskStatus, TaskStore, serialize_artifact, serialize_event, serialize_task
from brain.core.tools.builtin_plan_tool import BuiltinPlanTool
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
runningAgentTasks: Dict[str, asyncio.Task] = {}

def _normalize_tool_selection(selected_tools: Any) -> Optional[List[str]]:
    if selected_tools is None:
        return None
    if isinstance(selected_tools, str):
        selected_tools = [selected_tools]
    if not isinstance(selected_tools, list):
        return []
    return [str(tool).strip() for tool in selected_tools if str(tool).strip()]


def _matches_selected_tool(selected_patterns: Optional[List[str]], tool_name: str) -> bool:
    if selected_patterns is None:
        return True
    if not selected_patterns:
        return False
    for pattern in selected_patterns:
        if pattern in {"*", "all"} or fnmatch.fnmatch(tool_name, pattern):
            return True
    return False


async def build_tool_collection(ctx: AgentContext) -> ToolCollection:
    """build tool collection, including local tools and mcp market tools"""
    tc = ToolCollection()
    tc.agentContext = ctx
    selected_tools = _normalize_tool_selection(getattr(ctx, "selected_tools", None))
    agent_config = agentRegistry.get(ctx.agent_id)
    if agent_config:
        tc.set_allowed_tool_patterns(selected_tools if selected_tools is not None else agent_config.tool_patterns())
        tc.set_tool_timeout_patterns(
            {
                tool.name: tool.timeout_seconds
                for tool in agent_config.tools
                if tool.timeout_seconds
            }
        )
        tc.set_tool_allowed_checker(
            lambda tool_name: agent_config.allows_tool(tool_name)
            and _matches_selected_tool(selected_tools, tool_name)
        )
        if agent_config.allows_tool("builtin:plan_tool"):
            tc.add_tool(BuiltinPlanTool(ctx))
    elif selected_tools is not None:
        tc.set_allowed_tool_patterns(selected_tools)

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


def _validate_user_message(request: GptQueryReq) -> List[AgentMessage]:
    messages = list(getattr(request, "messages", []) or [])
    if not messages:
        raise ValueError("messages is required")
    last_role = (messages[-1].role or "").strip().lower()
    if last_role != RoleType.USER.value:
        raise ValueError("last message must be user role")
    return messages


def _fill_request_defaults(request: GptQueryReq) -> None:
    if not request.trace_id:
        request.trace_id = str(uuid.uuid4())
    if not request.user_id:
        request.user_id = str(uuid.uuid4())
    if not request.agent_id:
        request.agent_id = agentSettings.core.agent_id
    if not request.conversation_id:
        request.conversation_id = str(uuid.uuid4())
    fill_output_styles(request)


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


def _serialize_file_items(files: Optional[List[FileItem]]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for item in files or []:
        serialized.append(
            {
                "fileName": item.fileName,
                "description": item.description,
                "ossUrl": item.ossUrl,
                "domainUrl": item.domainUrl,
                "fileSize": item.fileSize,
                "isInternalFile": item.isInternalFile,
            }
        )
    return serialized


async def _run_autoagent(req: GptQueryReq, enqueue: Callable[[str], None]) -> None:
    request = _clone_gpt_request(req)
    trace_id = request.trace_id or str(uuid.uuid4())
    request.trace_id = trace_id
    configure_log_context(trace_id=trace_id)

    try:
        try:
            messages = _validate_user_message(request)
        except ValueError as exc:
            logger.warning(str(exc))
            return

        _fill_request_defaults(request)
        agent_config = _resolve_agent_config(request.agent_id)
        resolved_mode = request.mode or (agent_config.mode if agent_config else None) or "plans_executor"
        selected_tools = _normalize_tool_selection(request.selected_tools)

        task_id = trace_id
        last_result: Dict[str, Optional[str]] = {"output": None}
        printer = SSEPrinter(enqueue, trace_id, task_id=task_id)
        task_store: Optional[TaskStore] = None
        worker_task = asyncio.current_task()

        try:
            if worker_task:
                runningAgentTasks[task_id] = worker_task
            task_store = TaskStore()
            latest_input = (messages[-1].content or "").strip()
            input_files = _serialize_file_items(messages[-1].uploadFile if messages else None)
            created_task = task_store.create_task(
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
                    "selectedTools": selected_tools,
                    "inputFiles": input_files,
                },
            )
            created_task_payload = serialize_task(created_task)
            task_store.add_event(
                task_id,
                "task_created",
                {
                    "mode": resolved_mode,
                    "outputStyle": request.outputStyle,
                    "conversationId": request.conversation_id,
                    "agentConfigId": agent_config.id if agent_config else None,
                    "selectedTools": selected_tools,
                    "inputFiles": input_files,
                    "workDir": created_task_payload.get("workDir"),
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
                work_dir=created_task_payload.get("workDir"),
                agent_system_prompt=agent_config.system_prompt if agent_config else None,
                selected_tools=selected_tools,
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
        except asyncio.CancelledError:
            logger.info("autoagent task cancelled for request %s", trace_id)
            printer.send(None, "result", "task cancelled", None, True)
            if task_store is not None:
                task_store.update_status(task_id, AgentTaskStatus.CANCELLED, error_message="task cancelled")
                task_store.add_event(
                    task_id,
                    "task_cancelled",
                    {"status": AgentTaskStatus.CANCELLED},
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
            if runningAgentTasks.get(task_id) is worker_task:
                runningAgentTasks.pop(task_id, None)
            printer.close()
    finally:
        clear_log_context()


@agent_router.get("/agents")
async def list_agents() -> Dict[str, Any]:
    agentRegistry.reload()
    return {
        "items": [agent.to_dict() for agent in agentRegistry.list_agents()],
        "defaultAgentId": agentSettings.core.agent_id,
    }


@agent_router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> Dict[str, Any]:
    agentRegistry.reload()
    agent = agentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent.to_dict()


@agent_router.get("/agents/{agent_id}/evals")
async def list_agent_evals(agent_id: str) -> Dict[str, Any]:
    agentRegistry.reload()
    agent = agentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"items": [item.__dict__ for item in agent.evals]}


@agent_router.post("/agents/{agent_id}/evals/{case_id}/run")
async def run_agent_eval(
    agent_id: str,
    case_id: str,
    user_id: str = Query(default="eval-runner"),
    output_style: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    agentRegistry.reload()
    agent = agentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    case = next((item for item in agent.evals if item.id == case_id), None)
    if not case:
        raise HTTPException(status_code=404, detail="eval case not found")

    eval_run = build_eval_run(agent, case, user_id=user_id, output_style=output_style)
    store = TaskStore()
    task = store.create_task(
        task_id=eval_run.task_id,
        trace_id=eval_run.trace_id,
        conversation_id=eval_run.conversation_id,
        user_id=eval_run.user_id,
        agent_id=eval_run.agent_id,
        mode=eval_run.mode,
        output_style=eval_run.output_style,
        input_text=eval_run.input_text,
        metadata=eval_run.metadata,
    )
    store.add_event(
        eval_run.task_id,
        "eval_run_created",
        eval_run.to_dict(),
        trace_id=eval_run.trace_id,
        source="eval",
    )

    req = GptQueryReq(
        trace_id=eval_run.trace_id,
        user_id=eval_run.user_id,
        agent_id=eval_run.agent_id,
        conversation_id=eval_run.conversation_id,
        outputStyle=eval_run.output_style,
        mode=eval_run.mode,
        messages=[AgentMessage(role=RoleType.USER.value, content=eval_run.input_text)],
    )
    asyncio.create_task(_run_autoagent(req, lambda _data: None))
    return {"task": serialize_task(task), "eval": eval_run.to_dict()}


@agent_router.get("/tasks")
async def list_agent_tasks(
    user_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    store = TaskStore()
    tasks = store.list_tasks(
        user_id=user_id,
        status=status,
        agent_id=agent_id,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
    return {"items": [serialize_task(task) for task in tasks], "limit": limit, "offset": offset}


@agent_router.post("/tasks")
async def create_agent_task(req: GptQueryReq) -> Dict[str, Any]:
    request = _clone_gpt_request(req)
    try:
        messages = _validate_user_message(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _fill_request_defaults(request)
    trace_id = request.trace_id or str(uuid.uuid4())
    agent_config = _resolve_agent_config(request.agent_id or "")
    resolved_mode = request.mode or (agent_config.mode if agent_config else None) or "plans_executor"
    selected_tools = _normalize_tool_selection(request.selected_tools)
    latest_input = (messages[-1].content or "").strip()
    input_files = _serialize_file_items(messages[-1].uploadFile if messages else None)

    store = TaskStore()
    task = store.create_task(
        task_id=trace_id,
        trace_id=trace_id,
        conversation_id=request.conversation_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
        mode=resolved_mode,
        output_style=request.outputStyle,
        input_text=latest_input,
        metadata={
            "source": "api",
            "agentConfigId": agent_config.id if agent_config else None,
            "selectedTools": selected_tools,
            "inputFiles": input_files,
        },
    )
    store.add_event(
        trace_id,
        "task_queued",
        {
            "status": AgentTaskStatus.QUEUED,
            "mode": resolved_mode,
            "outputStyle": request.outputStyle,
            "agentConfigId": agent_config.id if agent_config else None,
            "selectedTools": selected_tools,
            "inputFiles": input_files,
        },
        trace_id=trace_id,
        source="api",
    )
    asyncio.create_task(_run_autoagent(request, lambda _data: None))
    return serialize_task(task)


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


@agent_router.post("/tasks/{task_id}/cancel")
async def cancel_agent_task(task_id: str) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status in {
        AgentTaskStatus.COMPLETED,
        AgentTaskStatus.FAILED,
        AgentTaskStatus.CANCELLED,
    }:
        return serialize_task(task)

    worker = runningAgentTasks.get(task_id)
    if worker and not worker.done():
        worker.cancel()

    store.update_status(task_id, AgentTaskStatus.CANCELLED, error_message="task cancelled")
    store.add_event(
        task_id,
        "task_cancel_requested",
        {"status": AgentTaskStatus.CANCELLED},
        trace_id=task.trace_id,
        source="api",
    )
    updated = store.get_task(task_id)
    return serialize_task(updated or task)


@agent_router.post("/tasks/{task_id}/retry")
async def retry_agent_task(task_id: str) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if not task.input_text:
        raise HTTPException(status_code=400, detail="task has no input to retry")

    retry_trace_id = str(uuid.uuid4())
    task_payload = serialize_task(task)
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    selected_tools = _normalize_tool_selection(metadata.get("selectedTools") if metadata else None)
    store.create_task(
        task_id=retry_trace_id,
        trace_id=retry_trace_id,
        conversation_id=task.conversation_id,
        user_id=task.user_id,
        agent_id=task.agent_id,
        mode=task.mode,
        output_style=task.output_style,
        input_text=task.input_text,
        metadata={"source": "retry", "parentTaskId": task.task_id, "selectedTools": selected_tools},
    )
    store.add_event(
        task.task_id,
        "task_retry_requested",
        {"retryTaskId": retry_trace_id},
        trace_id=task.trace_id,
        source="api",
    )

    retry_req = GptQueryReq(
        trace_id=retry_trace_id,
        user_id=task.user_id,
        agent_id=task.agent_id,
        conversation_id=task.conversation_id,
        outputStyle=task.output_style,
        mode=task.mode,
        selected_tools=selected_tools,
        messages=[AgentMessage(role=RoleType.USER.value, content=task.input_text)],
    )
    asyncio.create_task(_run_autoagent(retry_req, lambda _data: None))
    retry_task = store.get_task(retry_trace_id)
    return serialize_task(retry_task) if retry_task else {"taskId": retry_trace_id}


@agent_router.post("/tasks/{task_id}/input")
async def add_agent_task_input(task_id: str, req: TaskUserInputReq) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        event = store.add_user_input(task_id, req.content, user_id=req.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated_task = store.get_task(task_id) or task
    return {"task": serialize_task(updated_task), "event": serialize_event(event)}


@agent_router.get("/tasks/{task_id}/artifacts")
async def list_agent_task_artifacts(task_id: str) -> Dict[str, Any]:
    store = TaskStore()
    if not store.get_task(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    artifacts = store.list_artifacts(task_id)
    return {"items": [serialize_artifact(artifact) for artifact in artifacts]}


@agent_router.get("/tasks/{task_id}/artifacts/{artifact_id}")
async def download_agent_task_artifact(task_id: str, artifact_id: str) -> FileResponse:
    store = TaskStore()
    artifact = store.get_artifact(task_id, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    file_path = Path(artifact.file_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="artifact file not found")
    return FileResponse(
        str(file_path),
        media_type=artifact.mime_type or "application/octet-stream",
        filename=artifact.filename,
    )


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
    detached = False
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
        detached = True
    finally:
        if not detached:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        with contextlib.suppress(Exception):
            await websocket.close()


@agent_router.get("/web/health")
async def health() -> PlainTextResponse:
    return PlainTextResponse("ok")
