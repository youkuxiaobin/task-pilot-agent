from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from brain.core.agent_registry import AgentConfig
from brain.core.context import AgentContext
from brain.core.printer import SSEPrinter
from brain.core.run_events import RunEventType
from brain.core.sessions import AgentMessageRole, AgentSessionStatus, SessionStore
from brain.core.tasks import FINAL_STATUSES, AgentTaskStatus, TaskStore, serialize_event, serialize_task
from brain.core.tools.collection import ToolCollection
from brain.models.requests import AgentMessage, GptQueryReq
from utils.logger import clear_log_context, configure_log_context, get_logger

logger = get_logger(__name__)
SUPPORTED_AGENT_MODES = {"react", "supervisor"}


@dataclass
class AutoAgentRequestPrep:
    request: GptQueryReq
    messages: List[AgentMessage]
    trace_id: str
    agent_config: Optional[AgentConfig]
    resolved_mode: str
    selected_tools: Optional[List[str]]
    approved_tools: Optional[List[str]]
    agent_snapshot: Optional[Dict[str, Any]]


@dataclass
class AutoAgentRunState:
    task_id: str
    trace_id: str
    task_store: Optional[TaskStore] = None
    session_store: Optional[SessionStore] = None
    session_id: Optional[str] = None
    user_message_id: Optional[str] = None
    assistant_message_id: Optional[str] = None
    latest_input: str = ""
    input_files: List[Dict[str, Any]] = field(default_factory=list)
    created_task_payload: Dict[str, Any] = field(default_factory=dict)
    session_history_message_count: int = 0
    last_result: Dict[str, Any] = field(default_factory=lambda: {"output": None, "chunks": []})
    printer: Optional[SSEPrinter] = None


@dataclass
class AutoAgentRuntimeDeps:
    default_agent_mode: str
    running_tasks: Dict[str, asyncio.Task]
    agent_factory: Any
    clone_request: Callable[[GptQueryReq], GptQueryReq]
    validate_user_message: Callable[[GptQueryReq], List[AgentMessage]]
    fill_request_defaults: Callable[[GptQueryReq], None]
    resolve_agent_config: Callable[[str], Optional[AgentConfig]]
    normalize_tool_selection: Callable[[Any], Optional[List[str]]]
    serialize_file_items: Callable[[Any], List[Dict[str, Any]]]
    next_session_event_seq: Callable[[SessionStore, TaskStore, str, Optional[str]], int]
    session_title_from_content: Callable[[str], str]
    assert_session_user: Callable[[Any, Optional[str]], None]
    build_session_model_messages: Callable[
        [SessionStore, Optional[str], List[AgentMessage], Optional[str]],
        List[AgentMessage],
    ]
    sync_session_run: Callable[..., None]
    message_event_payload: Callable[..., Dict[str, Any]]
    extract_result_text: Callable[[Dict[str, Any]], Optional[str]]
    is_result_text_chunk: Callable[[Dict[str, Any]], bool]
    usage_increments_from_event: Callable[[Dict[str, Any]], Dict[str, int]]
    extract_remote_artifacts: Callable[[Dict[str, Any]], List[Dict[str, Any]]]
    artifact_event_payload: Callable[[str, str, Any], Dict[str, Any]]
    update_session_status: Callable[..., None]
    convert_agent_messages: Callable[[AgentContext, Optional[List[AgentMessage]]], None]
    load_task_memory_context: Callable[[AgentContext, str], Any]
    memory_context_status_text: Callable[[Dict[str, Any]], str]
    build_tool_collection: Callable[[AgentContext], Any]
    blocked_tool_reasons: Callable[
        [List[str], Optional[AgentConfig], Optional[List[str]], Optional[List[str]]],
        Dict[str, str],
    ]
    approval_requests_from_blocked_tools: Callable[
        [List[str], Dict[str, str], Optional[AgentConfig], Optional[List[str]]],
        List[Dict[str, Any]],
    ]
    approval_waiting_message: Callable[[List[Dict[str, Any]], Optional[str]], str]
    register_workspace_artifacts: Callable[..., None]
    sync_plan_terminal_status: Callable[..., None]
    maybe_update_session_summary: Callable[..., Optional[Dict[str, Any]]]
    renew_background_run: Callable[[str], None]
    background_lease_heartbeat_seconds: float = 30.0


def _prepare_autoagent_request(req: GptQueryReq, deps: AutoAgentRuntimeDeps) -> AutoAgentRequestPrep:
    request = deps.clone_request(req)
    has_explicit_conversation_id = bool(str(request.conversation_id or "").strip())
    trace_id = request.trace_id or str(uuid.uuid4())
    request.trace_id = trace_id
    configure_log_context(trace_id=trace_id)

    messages = deps.validate_user_message(request)
    deps.fill_request_defaults(request)
    if not has_explicit_conversation_id:
        request.conversation_id = trace_id

    agent_config = deps.resolve_agent_config(request.agent_id)
    resolved_mode = request.mode or (agent_config.mode if agent_config else None) or deps.default_agent_mode
    if str(resolved_mode or "").lower() not in SUPPORTED_AGENT_MODES:
        resolved_mode = deps.default_agent_mode
    selected_tools = deps.normalize_tool_selection(request.selected_tools)
    approved_tools = deps.normalize_tool_selection(request.approved_tools)
    agent_snapshot = agent_config.to_runtime_snapshot(approved_tools=approved_tools) if agent_config else None
    return AutoAgentRequestPrep(
        request=request,
        messages=messages,
        trace_id=trace_id,
        agent_config=agent_config,
        resolved_mode=resolved_mode,
        selected_tools=selected_tools,
        approved_tools=approved_tools,
        agent_snapshot=agent_snapshot,
    )


def _autoagent_run_metadata(
    prep: AutoAgentRequestPrep,
    state: AutoAgentRunState,
) -> Dict[str, Any]:
    request = prep.request
    agent_config = prep.agent_config
    return {
        "source": "autoagent",
        "agentConfigId": agent_config.id if agent_config else None,
        "agentSnapshot": prep.agent_snapshot,
        "selectedTools": prep.selected_tools,
        "approvedTools": prep.approved_tools,
        "inputFiles": state.input_files,
        "runEnvironment": request.run_environment,
        "language": request.language,
        "sessionMessageId": state.user_message_id,
        "sessionHistoryMessageCount": state.session_history_message_count,
    }


def _initialize_autoagent_run(
    state: AutoAgentRunState,
    prep: AutoAgentRequestPrep,
    deps: AutoAgentRuntimeDeps,
    *,
    task_store: TaskStore,
    session_store: SessionStore,
    printer: SSEPrinter,
) -> None:
    request = prep.request
    messages = prep.messages
    state.task_store = task_store
    state.session_store = session_store
    state.printer = printer
    state.user_message_id = request.session_message_id
    state.latest_input = (messages[-1].content or "").strip()
    state.input_files = deps.serialize_file_items(messages[-1].uploadFile if messages else None)
    state.session_id = request.conversation_id or state.trace_id
    request.conversation_id = state.session_id
    printer.session_id = state.session_id
    printer.run_id = state.task_id
    printer.seq_provider = lambda: deps.next_session_event_seq(
        session_store,
        task_store,
        state.session_id or state.task_id,
        request.user_id,
    )

    session_record = session_store.create_session(
        session_id=state.session_id,
        user_id=request.user_id,
        title=deps.session_title_from_content(state.latest_input),
        agent_id=request.agent_id,
        metadata={"source": "autoagent"},
    )
    deps.assert_session_user(session_record, request.user_id)
    if not state.user_message_id:
        user_message = session_store.add_message(
            state.session_id,
            run_id=state.task_id,
            user_id=request.user_id,
            role=AgentMessageRole.USER,
            content=state.latest_input,
            metadata={
                "source": "autoagent",
                "inputFiles": state.input_files,
            },
        )
        state.user_message_id = user_message.message_id
        request.session_message_id = state.user_message_id

    session_store.update_session(
        state.session_id,
        agent_id=request.agent_id,
        status=AgentSessionStatus.RUNNING,
        current_run_id=state.task_id,
        last_message_id=state.user_message_id,
        last_message_preview=state.latest_input,
    )

    original_message_count = len(messages)
    messages = deps.build_session_model_messages(
        session_store,
        state.session_id,
        messages,
        state.user_message_id,
    )
    prep.messages = messages
    request.messages = messages
    state.session_history_message_count = max(len(messages) - original_message_count, 0)

    existing_task = task_store.get_task(state.task_id)
    metadata = _autoagent_run_metadata(prep, state)
    created_task = task_store.create_task(
        task_id=state.task_id,
        trace_id=state.trace_id,
        conversation_id=request.conversation_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
        mode=prep.resolved_mode,
        output_style=request.outputStyle,
        input_text=state.latest_input,
        metadata=metadata,
    )
    state.created_task_payload = serialize_task(created_task)
    persisted_metadata = (
        state.created_task_payload.get("metadata")
        if isinstance(state.created_task_payload.get("metadata"), dict)
        else {}
    )
    deps.sync_session_run(
        session_store,
        run_id=state.task_id,
        session_id=state.session_id,
        user_id=request.user_id,
        user_message_id=state.user_message_id,
        trace_id=state.trace_id,
        agent_id=request.agent_id,
        mode=prep.resolved_mode,
        output_style=request.outputStyle,
        status=AgentTaskStatus.QUEUED,
        input_text=state.latest_input,
        work_dir=state.created_task_payload.get("workDir"),
        metadata=metadata,
    )

    event_input_files = (
        persisted_metadata.get("inputFiles")
        if existing_task and isinstance(persisted_metadata, dict)
        else state.input_files
    )
    lifecycle_event = RunEventType.TASK_RESUMED if existing_task else RunEventType.TASK_CREATED
    lifecycle_source = "resume" if existing_task else "autoagent"
    lifecycle_payload = {
        "mode": prep.resolved_mode,
        "outputStyle": request.outputStyle,
        "conversationId": request.conversation_id,
        "agentConfigId": prep.agent_config.id if prep.agent_config else None,
        "agentSnapshot": prep.agent_snapshot,
        "selectedTools": prep.selected_tools,
        "approvedTools": prep.approved_tools,
        "inputFiles": event_input_files,
        "runEnvironment": request.run_environment,
        "language": request.language,
        "workDir": state.created_task_payload.get("workDir"),
        "sessionHistoryMessageCount": state.session_history_message_count,
    }
    if existing_task:
        lifecycle_payload["status"] = AgentTaskStatus.RUNNING
    task_store.add_event(
        state.task_id,
        lifecycle_event,
        lifecycle_payload,
        trace_id=state.trace_id,
        source=lifecycle_source,
    )
    if state.user_message_id:
        task_store.add_event(
            state.task_id,
            RunEventType.USER_MESSAGE_CREATED,
            deps.message_event_payload(
                session_id=state.session_id,
                run_id=state.task_id,
                message_id=state.user_message_id,
                role=AgentMessageRole.USER,
                content=state.latest_input,
                input_files=event_input_files,
            ),
            trace_id=state.trace_id,
            source="session",
            message_id=state.user_message_id,
        )

    state.assistant_message_id = str(uuid.uuid4())


def _record_autoagent_stream_event(
    state: AutoAgentRunState,
    deps: AutoAgentRuntimeDeps,
    event_data: Dict[str, Any],
) -> None:
    result_text = deps.extract_result_text(event_data)
    if result_text is not None:
        event_data["assistantMessageId"] = state.assistant_message_id
        if deps.is_result_text_chunk(event_data):
            state.last_result["chunks"].append(result_text)
            state.last_result["output"] = "".join(state.last_result["chunks"])
        else:
            state.last_result["chunks"] = [result_text]
            state.last_result["output"] = result_text
    try:
        assert state.task_store is not None
        persisted_event = state.task_store.add_event(
            state.task_id,
            str(event_data.get("messageType") or RunEventType.STREAM_EVENT),
            event_data,
            trace_id=state.trace_id,
            source="sse",
            message_id=str(event_data.get("messageId") or ""),
        )
        persisted_event_payload = serialize_event(persisted_event)
        event_data["id"] = persisted_event_payload["id"]
        event_data["eventId"] = persisted_event_payload["eventId"]
        event_data["event_id"] = persisted_event_payload["event_id"]
        state.task_store.increment_usage_metrics(state.task_id, deps.usage_increments_from_event(event_data))
        for artifact in deps.extract_remote_artifacts(event_data):
            artifact_record = state.task_store.add_remote_artifact(
                state.task_id,
                artifact["url"],
                filename=artifact["filename"],
                description=artifact["description"],
                mime_type=artifact.get("mimeType"),
                file_size=artifact.get("fileSize") or 0,
                metadata=artifact.get("metadata"),
            )
            state.task_store.add_event(
                state.task_id,
                RunEventType.TASK_ARTIFACT_ADDED,
                deps.artifact_event_payload(state.session_id or "", state.task_id, artifact_record),
                trace_id=state.trace_id,
                source="artifact",
            )
    except Exception:
        logger.exception("failed to persist task event for task %s", state.task_id)


def _attach_autoagent_event_sink(state: AutoAgentRunState, deps: AutoAgentRuntimeDeps) -> None:
    assert state.printer is not None
    state.printer.event_sink = lambda event_data: _record_autoagent_stream_event(state, deps, event_data)


def _mark_autoagent_running(state: AutoAgentRunState, prep: AutoAgentRequestPrep, deps: AutoAgentRuntimeDeps) -> bool:
    assert state.task_store is not None
    assert state.session_store is not None
    assert state.session_id is not None
    assert state.assistant_message_id is not None
    request = prep.request
    agent_config = prep.agent_config
    started_task = state.task_store.start_task(state.task_id)
    if not started_task or started_task.status != AgentTaskStatus.RUNNING:
        logger.info(
            "autoagent task %s did not start because current status is %s",
            state.task_id,
            getattr(started_task, "status", None),
        )
        return False
    deps.sync_session_run(
        state.session_store,
        run_id=state.task_id,
        status=AgentTaskStatus.RUNNING,
        work_dir=state.created_task_payload.get("workDir"),
    )
    state.task_store.add_event(
        state.task_id,
        RunEventType.TASK_RUNNING,
        {"status": AgentTaskStatus.RUNNING},
        trace_id=state.trace_id,
        source="autoagent",
    )
    state.task_store.add_event(
        state.task_id,
        RunEventType.AGENT_STARTED,
        {
            "agentId": request.agent_id,
            "agentConfigId": agent_config.id if agent_config else None,
            "agentType": agent_config.type if agent_config else None,
            "agentName": agent_config.name if agent_config else None,
            "agentSnapshot": prep.agent_snapshot,
            "mode": prep.resolved_mode,
            "runEnvironment": request.run_environment,
            "language": request.language,
        },
        trace_id=state.trace_id,
        source="agent",
    )
    state.task_store.add_event(
        state.task_id,
        RunEventType.ASSISTANT_MESSAGE_STARTED,
        deps.message_event_payload(
            session_id=state.session_id,
            run_id=state.task_id,
            message_id=state.assistant_message_id,
            role=AgentMessageRole.ASSISTANT,
            content="",
            status="started",
        ),
        trace_id=state.trace_id,
        source="session",
        message_id=state.assistant_message_id,
    )
    return True


def _build_autoagent_context(
    state: AutoAgentRunState,
    prep: AutoAgentRequestPrep,
    deps: AutoAgentRuntimeDeps,
) -> AgentContext:
    assert state.printer is not None
    request = prep.request
    ctx = AgentContext(
        requestId=state.trace_id,
        sessionId=state.session_id or request.conversation_id,
        query="",
        task=None,
        printer=state.printer,
        toolCollection=None,
        dateInfo=time.strftime("%Y-%m-%d"),
        isStream=True,
        streamMessageType=None,
        user_id=request.user_id,
        agent_id=request.agent_id,
        run_id=state.task_id,
        outputStyle=request.outputStyle,
        mode=prep.resolved_mode,
        task_id=state.task_id,
        work_dir=state.created_task_payload.get("workDir"),
        agent_system_prompt=prep.agent_config.system_prompt if prep.agent_config else None,
        agent_memory=prep.agent_config.memory if prep.agent_config else {},
        selected_tools=prep.selected_tools,
        approved_tools=prep.approved_tools,
        run_environment=request.run_environment or "local",
        language=request.language or "ch",
    )
    deps.convert_agent_messages(ctx, prep.messages)
    logger.debug("request context prepared: request_id=%s mode=%s", ctx.requestId, ctx.mode)
    return ctx


async def _load_autoagent_memory_and_runtime_events(
    state: AutoAgentRunState,
    deps: AutoAgentRuntimeDeps,
    ctx: AgentContext,
) -> None:
    assert state.task_store is not None
    assert state.printer is not None
    memory_context_payload = await deps.load_task_memory_context(ctx, state.latest_input)
    state.task_store.add_event(
        state.task_id,
        RunEventType.MEMORY_CONTEXT_LOADED,
        memory_context_payload,
        trace_id=state.trace_id,
        source="memory",
    )
    state.printer.send(None, "task", deps.memory_context_status_text(memory_context_payload), None, True)
    state.task_store.add_event(
        state.task_id,
        RunEventType.RUNTIME_BOUNDARY_APPLIED,
        {
            "runEnvironment": ctx.run_environment,
            "workDir": ctx.work_dir,
            "writableRoots": [ctx.work_dir] if ctx.work_dir else [],
            "artifactPolicy": "task_workspace_only",
        },
        trace_id=state.trace_id,
        source="runtime",
    )


async def _apply_autoagent_tool_policy(
    state: AutoAgentRunState,
    prep: AutoAgentRequestPrep,
    deps: AutoAgentRuntimeDeps,
    ctx: AgentContext,
) -> tuple[ToolCollection, List[str], Dict[str, str]]:
    assert state.task_store is not None
    tc = await deps.build_tool_collection(ctx)
    ctx.toolCollection = tc
    blocked_tools = sorted(set(tc.blocked_tools))
    blocked_tool_reasons = deps.blocked_tool_reasons(
        blocked_tools,
        prep.agent_config,
        prep.selected_tools,
        prep.approved_tools,
    )
    state.task_store.add_event(
        state.task_id,
        RunEventType.TOOL_POLICY_APPLIED,
        {
            "agentId": ctx.agent_id,
            "selectedTools": prep.selected_tools,
            "approvedTools": prep.approved_tools,
            "availableTools": sorted(tc.tool_map.keys()),
            "blockedTools": blocked_tools,
            "blockedToolReasons": blocked_tool_reasons,
            "runEnvironment": prep.request.run_environment,
        },
        trace_id=state.trace_id,
        source="policy",
    )
    return tc, blocked_tools, blocked_tool_reasons


def _maybe_wait_for_autoagent_tool_approval(
    state: AutoAgentRunState,
    prep: AutoAgentRequestPrep,
    deps: AutoAgentRuntimeDeps,
    blocked_tools: List[str],
    blocked_tool_reasons: Dict[str, str],
) -> bool:
    assert state.task_store is not None
    assert state.printer is not None
    assert state.assistant_message_id is not None
    request = prep.request
    approval_requests = deps.approval_requests_from_blocked_tools(
        blocked_tools,
        blocked_tool_reasons,
        prep.agent_config,
        prep.selected_tools,
    )
    if not approval_requests:
        return False

    approval_event = state.task_store.add_event(
        state.task_id,
        RunEventType.APPROVAL_REQUESTED,
        {
            "approvalType": "high_risk_tools",
            "requests": approval_requests,
            "selectedTools": prep.selected_tools,
            "approvedTools": prep.approved_tools,
        },
        trace_id=state.trace_id,
        source="policy",
    )
    approval_event_payload = serialize_event(approval_event)
    waiting_message_text = deps.approval_waiting_message(approval_requests, request.language)
    state.task_store.update_status(state.task_id, AgentTaskStatus.WAITING_APPROVAL)
    deps.sync_session_run(
        state.session_store,
        run_id=state.task_id,
        status=AgentTaskStatus.WAITING_APPROVAL,
        assistant_message_id=state.assistant_message_id,
        output_text=waiting_message_text,
    )
    state.task_store.add_event(
        state.task_id,
        RunEventType.TASK_WAITING_APPROVAL,
        {
            "status": AgentTaskStatus.WAITING_APPROVAL,
            "approvalRequestEventId": approval_event.id,
            "approvalEventId": approval_event_payload.get("eventId"),
        },
        trace_id=state.trace_id,
        source="policy",
    )
    if state.session_store is not None and state.session_id:
        assistant_message = state.session_store.add_message(
            state.session_id,
            message_id=state.assistant_message_id,
            run_id=state.task_id,
            user_id=request.user_id,
            role=AgentMessageRole.ASSISTANT,
            content=waiting_message_text,
            status=AgentSessionStatus.WAITING_APPROVAL,
            metadata={
                "source": "approval",
                "taskStatus": AgentTaskStatus.WAITING_APPROVAL,
                "waitingApproval": True,
                "approvalRequestEventId": approval_event.id,
                "approvalEventId": approval_event_payload.get("eventId"),
                "approvalType": "high_risk_tools",
                "approvalRequests": approval_requests,
            },
        )
        state.assistant_message_id = assistant_message.message_id
        state.task_store.add_event(
            state.task_id,
            RunEventType.ASSISTANT_MESSAGE_COMPLETED,
            deps.message_event_payload(
                session_id=state.session_id,
                run_id=state.task_id,
                message_id=state.assistant_message_id,
                role=AgentMessageRole.ASSISTANT,
                content=waiting_message_text,
                status=AgentSessionStatus.WAITING_APPROVAL,
            ),
            trace_id=state.trace_id,
            source="session",
            message_id=state.assistant_message_id,
        )
    deps.update_session_status(
        state.session_store,
        state.session_id,
        status=AgentSessionStatus.WAITING_APPROVAL,
        current_run_id=state.task_id,
        last_message_id=state.assistant_message_id,
        last_message_preview=waiting_message_text,
    )
    return True


def _record_autoagent_failure(
    state: AutoAgentRunState,
    prep: AutoAgentRequestPrep,
    deps: AutoAgentRuntimeDeps,
    error_message: str,
    *,
    printer_result: Any,
    workspace_artifact_dir: Optional[str] = None,
) -> None:
    request = prep.request
    agent_config = prep.agent_config
    if state.printer is not None:
        state.printer.send(None, "result", printer_result, None, True)
    if state.task_store is not None and workspace_artifact_dir:
        deps.register_workspace_artifacts(
            state.task_store,
            state.task_id,
            state.trace_id,
            workspace_artifact_dir,
            session_id=state.session_id,
        )
    if state.task_store is not None:
        state.task_store.update_status(state.task_id, AgentTaskStatus.FAILED, error_message=error_message)
        deps.sync_session_run(
            state.session_store,
            run_id=state.task_id,
            status=AgentTaskStatus.FAILED,
            error_message=error_message,
            output_text=printer_result if isinstance(printer_result, str) else error_message,
        )
        state.task_store.add_event(
            state.task_id,
            RunEventType.AGENT_FAILED,
            {
                "agentId": request.agent_id,
                "agentConfigId": agent_config.id if agent_config else None,
                "error": error_message,
            },
            trace_id=state.trace_id,
            source="agent",
        )
        deps.sync_plan_terminal_status(
            state.task_store,
            state.task_id,
            trace_id=state.trace_id,
            terminal_status="failed",
            reason=error_message,
            source="autoagent",
        )
        state.task_store.add_event(
            state.task_id,
            RunEventType.TASK_FAILED,
            {"error": error_message},
            trace_id=state.trace_id,
            source="autoagent",
        )
    deps.update_session_status(
        state.session_store,
        state.session_id,
        status=AgentSessionStatus.IDLE,
        current_run_id="",
        last_message_preview=printer_result if isinstance(printer_result, str) else error_message,
    )


def _mark_autoagent_waiting_input(
    state: AutoAgentRunState,
    prep: AutoAgentRequestPrep,
    deps: AutoAgentRuntimeDeps,
    ctx: AgentContext,
) -> None:
    assert state.task_store is not None
    assert state.assistant_message_id is not None
    waiting_message_text = str(
        state.last_result["output"]
        or ctx.waiting_input_prompt
        or "waiting input"
    )
    current_task = state.task_store.get_task(state.task_id)
    if not current_task or current_task.status != AgentTaskStatus.WAITING_INPUT:
        state.task_store.request_user_input(
            state.task_id,
            waiting_message_text,
            trace_id=state.trace_id,
            source="autoagent",
        )
    deps.sync_session_run(
        state.session_store,
        run_id=state.task_id,
        status=AgentTaskStatus.WAITING_INPUT,
        assistant_message_id=state.assistant_message_id,
        output_text=waiting_message_text,
    )
    if state.session_store is not None and state.session_id:
        assistant_message = state.session_store.add_message(
            state.session_id,
            message_id=state.assistant_message_id,
            run_id=state.task_id,
            user_id=prep.request.user_id,
            role=AgentMessageRole.ASSISTANT,
            content=waiting_message_text,
            status=AgentSessionStatus.WAITING_INPUT,
            metadata={
                "source": "autoagent",
                "taskStatus": AgentTaskStatus.WAITING_INPUT,
                "waitingInput": True,
            },
        )
        state.assistant_message_id = assistant_message.message_id
        state.task_store.add_event(
            state.task_id,
            RunEventType.ASSISTANT_MESSAGE_COMPLETED,
            deps.message_event_payload(
                session_id=state.session_id,
                run_id=state.task_id,
                message_id=state.assistant_message_id,
                role=AgentMessageRole.ASSISTANT,
                content=waiting_message_text,
                status=AgentSessionStatus.WAITING_INPUT,
            ),
            trace_id=state.trace_id,
            source="session",
            message_id=state.assistant_message_id,
        )
    deps.update_session_status(
        state.session_store,
        state.session_id,
        status=AgentSessionStatus.WAITING_INPUT,
        current_run_id=state.task_id,
        last_message_id=state.assistant_message_id,
        last_message_preview=waiting_message_text,
    )


def _complete_autoagent_success(
    state: AutoAgentRunState,
    prep: AutoAgentRequestPrep,
    deps: AutoAgentRuntimeDeps,
) -> None:
    assert state.task_store is not None
    assert state.assistant_message_id is not None
    request = prep.request
    agent_config = prep.agent_config
    output_text = state.last_result["output"]
    state.task_store.update_status(
        state.task_id,
        AgentTaskStatus.COMPLETED,
        output_text=output_text,
    )
    deps.sync_session_run(
        state.session_store,
        run_id=state.task_id,
        status=AgentTaskStatus.COMPLETED,
        assistant_message_id=state.assistant_message_id,
        output_text=output_text,
    )
    completed_assistant_message_id: Optional[str] = None
    if state.session_store is not None and state.session_id and output_text is not None:
        assistant_message = state.session_store.add_message(
            state.session_id,
            message_id=state.assistant_message_id,
            run_id=state.task_id,
            user_id=request.user_id,
            role=AgentMessageRole.ASSISTANT,
            content=str(output_text),
            metadata={
                "source": "autoagent",
                "taskStatus": AgentTaskStatus.COMPLETED,
            },
        )
        state.assistant_message_id = assistant_message.message_id
        completed_assistant_message_id = state.assistant_message_id
        state.task_store.add_event(
            state.task_id,
            RunEventType.ASSISTANT_MESSAGE_COMPLETED,
            deps.message_event_payload(
                session_id=state.session_id,
                run_id=state.task_id,
                message_id=state.assistant_message_id,
                role=AgentMessageRole.ASSISTANT,
                content=str(output_text or ""),
                status="final",
            ),
            trace_id=state.trace_id,
            source="session",
            message_id=state.assistant_message_id,
        )
    deps.update_session_status(
        state.session_store,
        state.session_id,
        status=AgentSessionStatus.IDLE,
        current_run_id="",
        last_message_id=completed_assistant_message_id,
        last_message_preview=output_text or state.latest_input,
    )
    deps.maybe_update_session_summary(
        state.session_store,
        state.session_id,
        task_store=state.task_store,
        task_id=state.task_id,
        trace_id=state.trace_id,
    )
    deps.sync_plan_terminal_status(
        state.task_store,
        state.task_id,
        trace_id=state.trace_id,
        terminal_status="completed",
        reason="run completed",
        source="autoagent",
    )
    state.task_store.add_event(
        state.task_id,
        RunEventType.AGENT_COMPLETED,
        {
            "agentId": request.agent_id,
            "agentConfigId": agent_config.id if agent_config else None,
            "status": AgentTaskStatus.COMPLETED,
        },
        trace_id=state.trace_id,
        source="agent",
    )
    state.task_store.add_event(
        state.task_id,
        RunEventType.TASK_COMPLETED,
        {"status": AgentTaskStatus.COMPLETED},
        trace_id=state.trace_id,
        source="autoagent",
    )


async def _run_autoagent_handler(
    state: AutoAgentRunState,
    prep: AutoAgentRequestPrep,
    deps: AutoAgentRuntimeDeps,
    ctx: AgentContext,
    handler: Any,
) -> None:
    try:
        await handler.handle(ctx, prep.request)
        assert state.task_store is not None
        deps.register_workspace_artifacts(
            state.task_store,
            state.task_id,
            state.trace_id,
            ctx.work_dir,
            session_id=state.session_id,
        )
        if ctx.waiting_for_input:
            _mark_autoagent_waiting_input(state, prep, deps, ctx)
            return
        _complete_autoagent_success(state, prep, deps)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("autoagent handler failed for request %s", ctx.requestId)
        _record_autoagent_failure(
            state,
            prep,
            deps,
            str(exc),
            printer_result=f"autoagent error: {exc}",
            workspace_artifact_dir=ctx.work_dir,
        )


def _cancel_autoagent_run(state: AutoAgentRunState, prep: AutoAgentRequestPrep, deps: AutoAgentRuntimeDeps) -> None:
    request = prep.request
    agent_config = prep.agent_config
    if state.printer is not None:
        state.printer.send(None, "result", "task cancelled", None, True)
    if state.task_store is not None:
        state.task_store.update_status(state.task_id, AgentTaskStatus.CANCELLED, error_message="task cancelled")
        deps.sync_session_run(
            state.session_store,
            run_id=state.task_id,
            status=AgentTaskStatus.CANCELLED,
            error_message="task cancelled",
            output_text="task cancelled",
        )
        state.task_store.add_event(
            state.task_id,
            RunEventType.AGENT_CANCELLED,
            {
                "agentId": request.agent_id,
                "agentConfigId": agent_config.id if agent_config else None,
                "status": AgentTaskStatus.CANCELLED,
            },
            trace_id=state.trace_id,
            source="agent",
        )
        deps.sync_plan_terminal_status(
            state.task_store,
            state.task_id,
            trace_id=state.trace_id,
            terminal_status="cancelled",
            reason="task cancelled",
            source="autoagent",
        )
        state.task_store.add_event(
            state.task_id,
            RunEventType.TASK_CANCELLED,
            {"status": AgentTaskStatus.CANCELLED},
            trace_id=state.trace_id,
            source="autoagent",
        )
    deps.update_session_status(
        state.session_store,
        state.session_id,
        status=AgentSessionStatus.IDLE,
        current_run_id="",
        last_message_preview="task cancelled",
        )


async def _renew_background_lease_until_done(task_id: str, deps: AutoAgentRuntimeDeps) -> None:
    interval = max(float(deps.background_lease_heartbeat_seconds or 0), 0.01)
    try:
        while True:
            await asyncio.sleep(interval)
            deps.renew_background_run(task_id)
    except asyncio.CancelledError:
        raise


async def run_autoagent(req: GptQueryReq, enqueue: Callable[[str], None], deps: AutoAgentRuntimeDeps) -> None:
    try:
        try:
            prep = _prepare_autoagent_request(req, deps)
        except ValueError as exc:
            logger.warning(str(exc))
            return

        request = prep.request
        trace_id = prep.trace_id
        task_id = trace_id
        state = AutoAgentRunState(task_id=task_id, trace_id=trace_id)
        printer = SSEPrinter(enqueue, trace_id, task_id=task_id, run_id=task_id)
        worker_task = asyncio.current_task()
        lease_heartbeat_task: Optional[asyncio.Task] = None

        try:
            if deps.background_lease_heartbeat_seconds > 0:
                lease_heartbeat_task = asyncio.get_running_loop().create_task(
                    _renew_background_lease_until_done(task_id, deps)
                )
            if worker_task:
                deps.running_tasks[task_id] = worker_task
            task_store = TaskStore()
            existing_task = task_store.get_task(task_id)
            if existing_task and existing_task.status in FINAL_STATUSES:
                logger.info(
                    "autoagent task %s skipped because current status is %s",
                    task_id,
                    existing_task.status,
                )
                return
            session_store = SessionStore()
            state.task_store = task_store
            state.session_store = session_store
            _initialize_autoagent_run(
                state,
                prep,
                deps,
                task_store=task_store,
                session_store=session_store,
                printer=printer,
            )
            _attach_autoagent_event_sink(state, deps)
            if not _mark_autoagent_running(state, prep, deps):
                return
            ctx = _build_autoagent_context(state, prep, deps)
            await _load_autoagent_memory_and_runtime_events(state, deps, ctx)
            _, blocked_tools, blocked_tool_reasons = await _apply_autoagent_tool_policy(state, prep, deps, ctx)
            if _maybe_wait_for_autoagent_tool_approval(state, prep, deps, blocked_tools, blocked_tool_reasons):
                return

            handler = deps.agent_factory.get_handler(ctx, request)  # type: ignore[arg-type]
            if not handler:
                error_message = "unknown agentType"
                _record_autoagent_failure(
                    state,
                    prep,
                    deps,
                    error_message,
                    printer_result={"taskSummary": error_message},
                )
                return
            await _run_autoagent_handler(state, prep, deps, ctx, handler)
        except asyncio.CancelledError:
            logger.info("autoagent task cancelled for request %s", trace_id)
            _cancel_autoagent_run(state, prep, deps)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("autoagent pipeline failed for request %s", trace_id)
            _record_autoagent_failure(
                state,
                prep,
                deps,
                str(exc),
                printer_result=f"autoagent error: {exc}",
            )
        finally:
            if lease_heartbeat_task is not None:
                lease_heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await lease_heartbeat_task
            if deps.running_tasks.get(task_id) is worker_task:
                deps.running_tasks.pop(task_id, None)
            printer.close()
    finally:
        clear_log_context()
