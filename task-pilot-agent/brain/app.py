from __future__ import annotations
import json
import asyncio
import os
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional
from pathlib import Path
import contextlib
from urllib.parse import urlparse

from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse, RedirectResponse

from brain.models.requests import (
    AgentMessage,
    AgentMCPToolDryRunReq,
    AgentMCPToolTestReq,
    AgentRunApprovalReq,
    AgentSessionCreateReq,
    AgentSessionMessageReq,
    AgentSessionUpdateReq,
    GptQueryReq,
    TaskUserInputReq,
)
from brain.core.agent_registry import AgentConfig, AgentRegistry
from brain.core.approval_service import (
    ApprovalServiceDeps,
    resolve_agent_session_run_approval as resolve_session_run_approval_service,
    start_retry_from_session_run_record as start_retry_from_session_run_record_service,
)
from brain.core.autoagent_runtime import AutoAgentRuntimeDeps, run_autoagent
from brain.core.context import AgentContext, FileItem
from brain.core.eval_runner import build_eval_run, evaluate_eval_task
from brain.core.sanitization import sanitize_payload
from brain.core.session_message_service import AgentSessionMessageDeps, add_session_message
from brain.core.session_context import (
    agent_message_from_session_message as _shared_agent_message_from_session_message,
    build_session_model_messages as _shared_build_session_model_messages,
    compose_session_summary as _shared_compose_session_summary,
    deserialize_context_file_items as _shared_deserialize_context_file_items,
    maybe_update_session_summary as _shared_maybe_update_session_summary,
    merge_session_metadata as _shared_merge_session_metadata,
    session_message_summary_line as _shared_session_message_summary_line,
    session_summary_message as _shared_session_summary_message,
    session_summary_text as _shared_session_summary_text,
)
from brain.core.task_memory_context import (
    agent_memory_read_limits as _shared_agent_memory_read_limits,
    coerce_score as _shared_coerce_score,
    load_task_memory_context as _shared_load_task_memory_context,
    memory_context_status_text as _shared_memory_context_status_text,
    summarize_context_metadata as _shared_summarize_context_metadata,
    summarize_context_result as _shared_summarize_context_result,
    summarize_context_results as _shared_summarize_context_results,
)
from brain.core.task_recovery import recover_background_tasks as _recover_background_tasks
from brain.core.task_runner import InProcessTaskRunner
from brain.core.sessions import (
    AgentMessageRole,
    AgentSessionStatus,
    SessionStore,
    serialize_message,
    serialize_run,
    serialize_run_event,
    serialize_session,
)
from brain.core.session_view_service import (
    PLAN_EVENT_TYPES,
    artifact_download_response as _artifact_download_response,
    artifact_event_payload as _artifact_event_payload,
    attach_session_run_record as _attach_session_run_record,
    collect_session_artifacts as _collect_session_artifacts,
    collect_session_events as _collect_session_events,
    collect_session_run_event_payloads as _collect_session_run_event_payloads,
    collect_session_run_payloads as _collect_session_run_payloads,
    message_event_payload as _message_event_payload,
    next_session_event_seq as _next_session_event_seq,
    run_record_metadata as _run_record_metadata,
    serialize_plan_event as _serialize_plan_event,
    serialize_plan_event_payload as _serialize_plan_event_payload,
    serialize_session_run_payload as _serialize_session_run_payload,
    session_pending_approval_payload as _session_pending_approval_payload,
    sync_plan_terminal_status as _sync_plan_terminal_status,
    load_session_run_records as load_session_run_records_for_view,
)
from brain.core.tasks import (
    BACKGROUND_DISPATCH_DEFAULT_MAX_RECOVERY_ATTEMPTS,
    AgentTaskStatus,
    TaskStore,
    serialize_artifact,
    serialize_event,
    serialize_task,
)
from brain.core.tool_policy import (
    normalize_tool_selection as _shared_normalize_tool_selection,
    tool_name_variants as _shared_tool_name_variants,
)
from brain.core.tools.collection import ToolCollection
from brain.core.tools.gateway import (
    ToolGateway,
    approval_requests_from_blocked_tools as _gateway_approval_requests_from_blocked_tools,
    approval_waiting_message as _gateway_approval_waiting_message,
    blocked_tool_reasons as _gateway_blocked_tool_reasons,
    find_agent_tool_spec as _gateway_find_agent_tool_spec,
)
from brain.core.tools.mcp_tool import MCPToolFetcher
from brain.core.handlers.factory import AgentHandlerFactory
from brain.core.handlers.react import ReactHandler
from brain.core.handlers.supervisor import SupervisorHandler
from auth.dependencies import require_current_user, require_current_websocket_user
from auth.models import TaskPilotUser
from config.config import agentSettings
from pydantic import ValidationError
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from utils.logger import get_logger
from llm.types import LLMMessage, RoleType
from memory.memory_mgr import memory_manager

logger = get_logger(__name__)

agent_router = APIRouter()
EVENT_REPLAY_QUERY_LIMIT = 10000

FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend"
FRONTEND_DIST = FRONTEND_ROOT / "dist"
SESSION_CONTEXT_HISTORY_LIMIT = 20
SESSION_CONTEXT_MAX_CHARS = 16_000
SESSION_SUMMARY_TRIGGER_MESSAGE_COUNT = 30
SESSION_SUMMARY_RECENT_MESSAGE_COUNT = 12
SESSION_SUMMARY_MAX_MESSAGES = 200
SESSION_SUMMARY_MAX_CHARS = 3000
DEFAULT_AGENT_MODE = "react"
SUPPORTED_AGENT_MODES = {"react", "supervisor"}
SESSION_STREAM_ACTIVE_SLEEP_SECONDS = 0.05
SESSION_STREAM_IDLE_SLEEP_SECONDS = 0.25
BACKGROUND_DISPATCH_LEASE_MS = 5 * 60 * 1000
BACKGROUND_LEASE_HEARTBEAT_SECONDS = 30.0
BACKGROUND_RECOVERY_MAX_ATTEMPTS = BACKGROUND_DISPATCH_DEFAULT_MAX_RECOVERY_ATTEMPTS

agentRegistry = AgentRegistry()
runningAgentTasks: Dict[str, asyncio.Task] = {}
BACKGROUND_RUNNER_OWNER = f"{os.getpid()}:{uuid.uuid4()}"


def _mark_background_run_started(run_id: str, _worker: Any = None) -> None:
    try:
        TaskStore().mark_background_dispatch_started(run_id, owner=BACKGROUND_RUNNER_OWNER)
    except Exception:
        logger.debug("failed to mark background run %s started", run_id, exc_info=True)


def _mark_background_run_finished(run_id: str, _worker: Any = None) -> None:
    try:
        task = TaskStore().get_task(run_id)
        TaskStore().mark_background_dispatch_finished(
            run_id,
            status=getattr(task, "status", None),
            error_message=getattr(task, "error_message", None),
        )
    except Exception:
        logger.debug("failed to mark background run %s finished", run_id, exc_info=True)


def _renew_background_run(run_id: str) -> None:
    try:
        TaskStore().renew_background_dispatch(
            run_id,
            owner=BACKGROUND_RUNNER_OWNER,
            lease_ms=BACKGROUND_DISPATCH_LEASE_MS,
        )
    except Exception:
        logger.debug("failed to renew background run %s", run_id, exc_info=True)


backgroundTaskRunner = InProcessTaskRunner(
    runningAgentTasks,
    create_task=lambda coro: asyncio.create_task(coro),
    on_start=_mark_background_run_started,
    on_done=_mark_background_run_finished,
)


def _start_background_run(run_id: str, coro: Any) -> Any:
    return backgroundTaskRunner.start(run_id, coro)


def _cancel_background_run(run_id: str, *, remove: bool = False) -> Optional[Any]:
    return backgroundTaskRunner.cancel(run_id, remove=remove)


def _normalize_tool_selection(selected_tools: Any) -> Optional[List[str]]:
    return _shared_normalize_tool_selection(selected_tools)


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _request_option_value(req: Any, option_name: str, *legacy_names: str) -> Any:
    options = getattr(req, "options", None)
    values: List[Any] = []
    if options is not None:
        values.append(getattr(options, option_name, None))
    values.extend(getattr(req, name, None) for name in legacy_names)
    return _first_not_none(*values)


def _request_agent_id(req: Any) -> Optional[str]:
    return _request_option_value(req, "agent_id", "agent_id", "agentId")


def _request_language(req: Any) -> Optional[str]:
    return _request_option_value(req, "language", "language")


def _request_output_style(req: Any) -> Optional[str]:
    return _request_option_value(req, "output_style", "outputStyle")


def _request_mode(req: Any) -> Optional[str]:
    return _normalize_agent_mode(_request_option_value(req, "mode", "mode"))


def _normalize_agent_mode(value: Any) -> Optional[str]:
    mode = str(value or "").strip().lower()
    if not mode:
        return None
    return mode if mode in SUPPORTED_AGENT_MODES else DEFAULT_AGENT_MODE


def _request_selected_tools(req: Any) -> Optional[List[str]]:
    return _normalize_tool_selection(
        _request_option_value(req, "selected_tools", "selected_tools", "selectedTools")
    )


def _request_approved_tools(req: Any) -> Optional[List[str]]:
    return _normalize_tool_selection(
        _request_option_value(req, "approved_tools", "approved_tools", "approvedTools")
    )


def _request_run_environment(req: Any) -> Optional[str]:
    return _request_option_value(req, "run_environment", "run_environment", "runEnvironment")


def _merge_tool_selection(*tool_groups: Any) -> List[str]:
    merged: List[str] = []
    seen = set()
    for tool_group in tool_groups:
        normalized = _normalize_tool_selection(tool_group)
        if not normalized:
            continue
        for tool_name in normalized:
            if tool_name in seen:
                continue
            seen.add(tool_name)
            merged.append(tool_name)
    return merged


def _blocked_tool_reasons(
    blocked_tools: List[str],
    agent_config: Optional[AgentConfig],
    selected_tools: Optional[List[str]],
    approved_tools: Optional[List[str]] = None,
) -> Dict[str, str]:
    return _gateway_blocked_tool_reasons(
        blocked_tools,
        agent_config,
        selected_tools,
        approved_tools=approved_tools,
    )


def _approval_requests_from_blocked_tools(
    blocked_tools: List[str],
    blocked_reasons: Dict[str, str],
    agent_config: Optional[AgentConfig],
    selected_tools: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    return _gateway_approval_requests_from_blocked_tools(
        blocked_tools,
        blocked_reasons,
        agent_config,
        selected_tools=selected_tools,
    )


def _approval_waiting_message(approval_requests: List[Dict[str, Any]], language: Optional[str] = None) -> str:
    return _gateway_approval_waiting_message(approval_requests, language)


def _find_agent_tool_spec(agent_config: Optional[AgentConfig], tool_name: str) -> Any:
    return _gateway_find_agent_tool_spec(agent_config, tool_name)


def _serialize_tool_spec_fields(tool_spec: Any) -> Dict[str, Any]:
    if not tool_spec:
        return {}
    return {
        "alias": getattr(tool_spec, "alias", ""),
        "purpose": getattr(tool_spec, "purpose", ""),
        "whenToUse": getattr(tool_spec, "when_to_use", ""),
        "required": bool(getattr(tool_spec, "required", False)),
        "timeoutSeconds": getattr(tool_spec, "timeout_seconds", None),
        "policy": getattr(tool_spec, "policy", None) or {},
    }


def _canonical_tool_id(name: str) -> str:
    text = str(name or "")
    if "-" in text and ":" not in text:
        candidate = text.replace("-", ":", 1)
        if candidate.startswith(("mcp_", "builtin:")):
            return candidate
    return text


def _tool_source(name: str, tool: Any = None) -> str:
    canonical = _canonical_tool_id(name)
    if canonical.startswith("builtin:"):
        return "builtin"
    if getattr(tool, "server_url", None) or getattr(tool, "tool_prefix", None):
        return "mcp"
    if canonical.startswith("mcp_"):
        return "mcp"
    if "browser" in canonical:
        return "browser"
    if canonical.startswith("plugin:"):
        return "plugin"
    return "builtin"


def _tool_display_name(name: str, tool_spec: Any, description: str = "") -> str:
    alias = str(getattr(tool_spec, "alias", "") or "").strip()
    if alias:
        return alias
    canonical = _canonical_tool_id(name)
    if ":" in canonical:
        return canonical.split(":", 1)[1]
    return description or canonical


def _infer_tool_risk_level(name: str, policy: Dict[str, Any]) -> str:
    risk = str((policy or {}).get("risk") or "").strip().lower()
    if risk:
        return risk
    canonical = _canonical_tool_id(name).lower()
    if any(token in canonical for token in ("shell", "command", "terminal", "process", "code_interpreter")):
        return "high"
    if any(
        token in canonical
        for token in ("file_write", "file_delete", "file_move", "file_copy", "directory_create", "config_update")
    ):
        return "high"
    if any(token in canonical for token in ("browser_click", "browser_fill", "browser_eval", "browser_js")):
        return "medium"
    return "low"


def _tool_requires_approval(
    name: str,
    policy: Dict[str, Any],
    agent_config: Optional[AgentConfig],
    block_reason: str = "",
) -> bool:
    if block_reason in {"high_risk_requires_enable", "high_risk_requires_approval"}:
        return True
    if bool((policy or {}).get("requires_explicit_enable")):
        return True
    risk = _infer_tool_risk_level(name, policy)
    permissions = agent_config.permissions if agent_config else {}
    approval_items = permissions.get("require_approval_for") if isinstance(permissions, dict) else None
    if isinstance(approval_items, str):
        required = {approval_items}
    elif isinstance(approval_items, list):
        required = {str(item) for item in approval_items}
    else:
        required = set()
    return risk in {"high", "critical"} and "high_risk_tools" in required


def _tool_mcp_metadata(name: str, tool: Any = None) -> Dict[str, Any]:
    canonical = _canonical_tool_id(name)
    tool_prefix = str(getattr(tool, "tool_prefix", "") or "")
    if not tool_prefix and canonical.startswith("mcp_") and ":" in canonical:
        tool_prefix = canonical.split(":", 1)[0]
    server_url = str(getattr(tool, "server_url", "") or "")
    protocol = str(getattr(tool, "protocol", "") or "")
    server_id = _mcp_server_id(server_url, tool_prefix) if (server_url or tool_prefix) else ""
    return {
        "serverId": server_id,
        "mcpServerId": server_id,
        "serverUrl": server_url,
        "protocol": protocol,
        "toolPrefix": tool_prefix,
        "metadata": dict(getattr(tool, "metadata", None) or {}),
    }


def _tool_metadata_fields(
    *,
    name: str,
    description: str,
    tool: Any = None,
    tool_spec: Any = None,
    agent_config: Optional[AgentConfig] = None,
    allowed: bool,
    block_reason: str = "",
) -> Dict[str, Any]:
    policy = dict(getattr(tool_spec, "policy", None) or {})
    tool_risk_level = str(getattr(tool, "risk_level", "") or "").strip()
    if tool_risk_level and "risk" not in policy:
        policy["risk"] = tool_risk_level
    source = _tool_source(name, tool)
    payload: Dict[str, Any] = {
        "id": _canonical_tool_id(name),
        "displayName": _tool_display_name(name, tool_spec, description),
        "source": source,
        "riskLevel": _infer_tool_risk_level(name, policy),
        "requiresApproval": bool(getattr(tool, "requires_approval", False))
        or _tool_requires_approval(name, policy, agent_config, block_reason),
        "available": allowed,
        "availability": "available" if allowed else "unavailable",
        "unavailableReason": "" if allowed else block_reason,
        "permissions": {},
    }
    if source == "mcp":
        payload.update(_tool_mcp_metadata(name, tool))
    return payload


def _serialize_available_tool(tool: Any, agent_config: Optional[AgentConfig] = None) -> Dict[str, Any]:
    name = str(getattr(tool, "name", "") or getattr(tool, "full_name", ""))
    tool_spec = _find_agent_tool_spec(agent_config, name)
    description = str(getattr(tool, "description", "") or getattr(tool_spec, "description", "") or "")
    payload = {
        "name": name,
        "description": description,
        "allowed": True,
        "blockReason": "",
        "inputSchema": getattr(tool, "input_schema", None) or getattr(tool_spec, "input_schema", None) or {},
        "outputSchema": getattr(tool, "output_schema", None) or getattr(tool_spec, "output_schema", None) or {},
    }
    payload.update(_serialize_tool_spec_fields(tool_spec))
    payload.update(
        _tool_metadata_fields(
            name=name,
            description=description,
            tool=tool,
            tool_spec=tool_spec,
            agent_config=agent_config,
            allowed=True,
        )
    )
    return payload


def _serialize_blocked_tool(tool_name: str, reason: str, agent_config: Optional[AgentConfig]) -> Dict[str, Any]:
    tool_spec = _find_agent_tool_spec(agent_config, tool_name)
    payload = {
        "name": tool_name,
        "description": str(getattr(tool_spec, "description", "") or ""),
        "allowed": False,
        "blockReason": reason,
        "inputSchema": getattr(tool_spec, "input_schema", None) or {},
        "outputSchema": getattr(tool_spec, "output_schema", None) or {},
    }
    payload.update(_serialize_tool_spec_fields(tool_spec))
    payload.update(
        _tool_metadata_fields(
            name=tool_name,
            description=payload["description"],
            tool=None,
            tool_spec=tool_spec,
            agent_config=agent_config,
            allowed=False,
            block_reason=reason,
        )
    )
    return payload


def _configured_mcp_server_items() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in getattr(agentSettings.mcp.mcp_market, "mcp_servers", []) or []:
        server_id = _mcp_server_id(item.url, item.tool_prefix)
        items.append(
            {
                "serverId": server_id,
                "name": item.tool_prefix or server_id,
                "url": item.url,
                "protocol": item.transport,
                "toolPrefix": item.tool_prefix,
                "authorizationConfigured": bool(item.authorization),
                "status": "configured",
                "toolCount": 0,
                "error": "",
                "lastCheckedAt": None,
                "durationMs": None,
            }
        )
    return items


def _get_mcp_market_registry() -> Any:
    try:
        from tools.aggre_mcp_market.service import runtime as mcp_registry_runtime
        active_registry = mcp_registry_runtime.get_registry()
        if active_registry is not None:
            return active_registry
        from tools.aggre_mcp_market import app as mcp_market_app
    except Exception:
        return None
    return getattr(mcp_market_app, "registry", None)


def _mcp_server_id(url: str, tool_prefix: str) -> str:
    if tool_prefix:
        return tool_prefix
    parsed = urlparse(url)
    candidate = f"{parsed.netloc}{parsed.path}".strip("/") or url
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in candidate)


def _serialize_mcp_server_status(item: Any) -> Dict[str, Any]:
    url = str(getattr(item, "url", "") or "")
    tool_prefix = str(getattr(item, "tool_prefix", "") or "")
    server_id = _mcp_server_id(url, tool_prefix)
    return {
        "serverId": server_id,
        "name": tool_prefix or server_id,
        "url": url,
        "protocol": str(getattr(getattr(item, "protocol", ""), "value", getattr(item, "protocol", "")) or ""),
        "toolPrefix": tool_prefix,
        "authorizationConfigured": bool(getattr(item, "authorization_configured", False)),
        "status": str(getattr(item, "status", "unknown") or "unknown"),
        "toolCount": int(getattr(item, "tool_count", 0) or 0),
        "error": str(getattr(item, "error", "") or ""),
        "lastCheckedAt": getattr(item, "last_checked_at", None),
        "durationMs": getattr(item, "duration_ms", None),
    }


def _mcp_status_payload() -> Dict[str, Any]:
    registry = _get_mcp_market_registry()
    if registry is None:
        return {
            "source": "config",
            "toolCount": 0,
            "items": _configured_mcp_server_items(),
        }
    servers = [_serialize_mcp_server_status(item) for item in registry.list_servers()]
    return {
        "source": "registry",
        "toolCount": len(registry.list_tools()),
        "items": servers,
    }


def _tool_name_variants(value: str) -> List[str]:
    return _shared_tool_name_variants(value)


def _resolve_tool_name(tool_map: Dict[str, Any], requested_name: str) -> str:
    for candidate in _tool_name_variants(requested_name):
        if candidate in tool_map:
            return candidate
    return requested_name


def _normalize_run_environment(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"local", "sandbox"}:
        return normalized
    return "local"


def _normalize_language(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    if normalized in {"en", "en-us", "english"}:
        return "en"
    if normalized in {"ch", "zh", "zh-cn", "cn", "chinese"}:
        return "ch"
    return "ch"


def _truncate_for_event(value: Any, limit: int = 320) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _coerce_score(value: Any) -> Optional[float]:
    return _shared_coerce_score(value)


def _summarize_context_metadata(value: Any) -> Dict[str, str]:
    return _shared_summarize_context_metadata(value)


def _summarize_context_result(item: Any, fallback_source: str) -> Dict[str, Any]:
    return _shared_summarize_context_result(item, fallback_source)


def _summarize_context_results(items: Any, fallback_source: str, limit: int = 5) -> List[Dict[str, Any]]:
    return _shared_summarize_context_results(items, fallback_source, limit=limit)


def _agent_memory_read_limits(ctx: AgentContext) -> tuple[int, int]:
    return _shared_agent_memory_read_limits(ctx)


async def _load_task_memory_context(ctx: AgentContext, query: str) -> Dict[str, Any]:
    return await _shared_load_task_memory_context(
        ctx,
        query,
        memory_manager=memory_manager,
        logger=logger,
    )


def _memory_context_status_text(payload: Dict[str, Any]) -> str:
    return _shared_memory_context_status_text(payload)


async def build_tool_collection(ctx: AgentContext) -> ToolCollection:
    """Build task-scoped tools through the shared gateway."""
    mcp_market_url = getattr(agentSettings, "mcp_market_url", "http://127.0.0.1:9010/aggre_mcp_market")
    gateway = ToolGateway(
        agentRegistry,
        mcp_market_url=mcp_market_url,
        handoff_starter=_start_handoff_task,
        mcp_fetcher_cls=MCPToolFetcher,
    )
    return await gateway.build_collection(ctx)


agentFactory = AgentHandlerFactory(
    [
        SupervisorHandler(agentRegistry, build_tool_collection),
        ReactHandler(),
    ]
)


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


def _is_result_text_chunk(event_data: Dict[str, Any]) -> bool:
    return event_data.get("messageType") == "result" and isinstance(event_data.get("result"), str)


REMOTE_ARTIFACT_URL_KEYS = ("domainUrl", "domain_url", "downloadUrl", "download_url", "ossUrl", "url", "href")
REMOTE_ARTIFACT_STRONG_URL_KEYS = ("domainUrl", "domain_url", "downloadUrl", "download_url", "ossUrl")
REMOTE_ARTIFACT_NAME_KEYS = ("fileName", "filename", "name")
REMOTE_ARTIFACT_STRONG_NAME_KEYS = ("fileName", "filename")
LOCAL_ARTIFACT_SCAN_LIMIT = 100


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _first_present(mapping: Dict[str, Any], keys: tuple[str, ...]) -> Optional[Any]:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_present_item(mapping: Dict[str, Any], keys: tuple[str, ...]) -> tuple[Optional[str], Optional[Any]]:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return key, value
    return None, None


def _coerce_file_size(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _iter_remote_artifact_candidates(value: Any) -> List[Dict[str, Any]]:
    parsed = _json_value(value)
    if isinstance(parsed, list):
        items: List[Dict[str, Any]] = []
        for item in parsed:
            items.extend(_iter_remote_artifact_candidates(item))
        return items
    if not isinstance(parsed, dict):
        return []

    found: List[Dict[str, Any]] = []
    url_key, url = _first_present_item(parsed, REMOTE_ARTIFACT_URL_KEYS)
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        filename = _first_present(parsed, REMOTE_ARTIFACT_NAME_KEYS)
        has_artifact_signal = (
            url_key in REMOTE_ARTIFACT_STRONG_URL_KEYS
            or _first_present(parsed, REMOTE_ARTIFACT_STRONG_NAME_KEYS) is not None
            or _first_present(parsed, ("mimeType", "mime_type", "fileType")) is not None
            or _coerce_file_size(parsed.get("fileSize") or parsed.get("file_size") or parsed.get("size")) > 0
        )
        if has_artifact_signal and not filename:
            filename = Path(urlparse(url).path).name or "artifact"
        if has_artifact_signal and filename:
            found.append(
                {
                    "url": url,
                    "filename": str(filename),
                    "mimeType": parsed.get("mimeType") or parsed.get("mime_type") or parsed.get("fileType"),
                    "fileSize": _coerce_file_size(parsed.get("fileSize") or parsed.get("file_size") or parsed.get("size")),
                    "raw": parsed,
                }
            )

    for nested in parsed.values():
        found.extend(_iter_remote_artifact_candidates(nested))
    return found


def _extract_remote_artifacts(event_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if event_data.get("messageType") != "tool_result":
        return []
    result_map = event_data.get("resultMap") if isinstance(event_data.get("resultMap"), dict) else {}
    tool_name = str(event_data.get("tool") or result_map.get("tool") or event_data.get("name") or "")
    result_payload = event_data.get("result") if event_data.get("result") is not None else result_map
    artifacts = []
    seen = set()
    for item in _iter_remote_artifact_candidates(result_payload):
        key = (item["url"], item["filename"])
        if key in seen:
            continue
        seen.add(key)
        artifacts.append(
            {
                "url": item["url"],
                "filename": item["filename"],
                "mimeType": item.get("mimeType"),
                "fileSize": item.get("fileSize") or 0,
                "description": f"Generated by {tool_name}" if tool_name else "Generated artifact",
                "metadata": {
                    "source": "tool_result",
                    "tool": tool_name,
                    "raw": item.get("raw") or {},
                },
            }
        )
    return artifacts


def _register_workspace_artifacts(
    task_store: TaskStore,
    task_id: str,
    trace_id: str,
    work_dir: Optional[str],
    *,
    session_id: Optional[str] = None,
) -> None:
    if not work_dir:
        return
    root = Path(work_dir).expanduser().resolve()
    if not root.is_dir():
        return

    existing_paths = {
        str(Path(item.file_path).expanduser().resolve())
        for item in task_store.list_artifacts(task_id)
        if not str(item.file_path).startswith(("http://", "https://"))
    }
    registered = 0
    for path in sorted(root.rglob("*")):
        if registered >= LOCAL_ARTIFACT_SCAN_LIMIT:
            break
        if not path.is_file():
            continue
        resolved_path = path.expanduser().resolve()
        if str(resolved_path) in existing_paths:
            continue
        try:
            relative_path = str(resolved_path.relative_to(root))
        except ValueError:
            continue
        artifact_record = task_store.add_artifact(
            task_id,
            str(resolved_path),
            filename=resolved_path.name,
            description="Generated in task workspace",
            metadata={
                "source": "task_workspace",
                "relativePath": relative_path,
            },
        )
        task_store.add_event(
            task_id,
            "task_artifact_added",
            _artifact_event_payload(session_id or "", task_id, artifact_record),
            trace_id=trace_id,
            source="artifact",
        )
        existing_paths.add(str(resolved_path))
        registered += 1


def _usage_increments_from_event(event_data: Dict[str, Any]) -> Dict[str, int]:
    message_type = str(event_data.get("messageType") or "")
    increments: Dict[str, int] = {"events": 1}
    if message_type == "tool_call":
        increments["toolCalls"] = 1
    elif message_type == "tool_result":
        result_map = event_data.get("resultMap") if isinstance(event_data.get("resultMap"), dict) else {}
        increments["toolResults"] = 1
        if result_map.get("failed") is True:
            increments["toolFailures"] = 1
        duration = _coerce_file_size(result_map.get("durationMs"))
        if duration:
            increments["toolDurationMs"] = duration
    elif message_type == "notifications":
        increments["notifications"] = 1
    elif message_type == "stream":
        increments["streamEvents"] = 1
    return increments


def _resolve_agent_config(agent_id: str) -> Optional[AgentConfig]:
    try:
        agentRegistry.reload()
        return agentRegistry.get(agent_id)
    except Exception:
        logger.exception("failed to load agent config for %s", agent_id)
        return None


def _agent_config_error_detail(exc: Exception) -> Dict[str, Any]:
    return {
        "error": "agent_config_invalid",
        "message": str(exc),
        "diagnostics": agentRegistry.diagnostics(),
    }


def _reload_agent_registry_or_raise() -> None:
    try:
        agentRegistry.reload()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_agent_config_error_detail(exc)) from exc


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
    request.mode = _normalize_agent_mode(request.mode)
    request.run_environment = _normalize_run_environment(
        request.run_environment or agentSettings.core.default_run_environment
    )
    request.language = _normalize_language(request.language or getattr(agentSettings, "lang", "ch"))
    fill_output_styles(request)



def _current_user_id(current_user: Any, fallback_user_id: Optional[str] = None) -> Optional[str]:
    user_id = getattr(current_user, "user_id", None)
    if user_id:
        return str(user_id)
    return fallback_user_id


def _is_injected_user(current_user: Any) -> bool:
    return bool(getattr(current_user, "user_id", None))


def _ensure_task_owner(task: Any, current_user: Any) -> None:
    if not _is_injected_user(current_user):
        return
    owner = str(getattr(task, "user_id", "") or "")
    current_user_id = str(getattr(current_user, "user_id", "") or "")
    if owner == current_user_id:
        return
    if not owner and not agentSettings.auth.required:
        return
    raise HTTPException(status_code=404, detail="task not found")


def _ensure_session_owner(session_record: Any, current_user: Any) -> None:
    if not _is_injected_user(current_user):
        return
    owner = str(getattr(session_record, "user_id", "") or "")
    current_user_id = str(getattr(current_user, "user_id", "") or "")
    if owner == current_user_id:
        return
    if not owner and not agentSettings.auth.required:
        return
    raise HTTPException(status_code=404, detail="session not found")


def _session_title_from_content(content: str) -> str:
    text = " ".join((content or "").strip().split())
    if not text:
        return "新会话"
    return text[:40]


def _update_session_status(
    store: Optional[SessionStore],
    session_id: Optional[str],
    *,
    status: str,
    current_run_id: Optional[str] = None,
    last_message_id: Optional[str] = None,
    last_message_preview: Optional[str] = None,
) -> None:
    if store is None or not session_id:
        return
    try:
        store.update_session(
            session_id,
            status=status,
            current_run_id=current_run_id,
            last_message_id=last_message_id,
            last_message_preview=last_message_preview,
        )
    except Exception:
        logger.exception("failed to update agent session %s", session_id)


def _sync_session_run(
    store: Optional[SessionStore],
    *,
    run_id: Optional[str],
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_message_id: Optional[str] = None,
    assistant_message_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    mode: Optional[str] = None,
    output_style: Optional[str] = None,
    status: Optional[str] = None,
    input_text: Optional[str] = None,
    output_text: Optional[str] = None,
    error_message: Optional[str] = None,
    work_dir: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if store is None or not run_id:
        return
    try:
        existing = store.get_run(run_id)
        if existing:
            store.update_run(
                run_id,
                status=status,
                assistant_message_id=assistant_message_id,
                output_text=output_text,
                error_message=error_message,
                work_dir=work_dir,
                metadata=metadata,
            )
            return
        if not session_id:
            return
        store.create_run(
            run_id=run_id,
            session_id=session_id,
            user_id=user_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            trace_id=trace_id,
            agent_id=agent_id,
            mode=mode,
            output_style=output_style,
            status=status or AgentTaskStatus.QUEUED,
            input_text=input_text,
            output_text=output_text,
            error_message=error_message,
            work_dir=work_dir,
            metadata=metadata,
        )
    except Exception:
        logger.exception("failed to sync agent session run %s", run_id)


def _assert_session_user(session_record: Any, user_id: Optional[str]) -> None:
    owner = str(getattr(session_record, "user_id", "") or "")
    current_user_id = str(user_id or "")
    if owner and current_user_id and owner != current_user_id:
        raise ValueError("session not found")


def _load_owned_session(session_id: str, current_user: Any) -> Any:
    session_record = SessionStore().get_session(session_id)
    if not session_record:
        raise HTTPException(status_code=404, detail="session not found")
    _ensure_session_owner(session_record, current_user)
    return session_record


def _load_session_run_records(
    session_store: SessionStore,
    task_store: TaskStore,
    session_record: Any,
    run_id: str,
    current_user: Any,
) -> tuple[Optional[Any], Optional[Any]]:
    return load_session_run_records_for_view(
        session_store,
        task_store,
        session_record,
        run_id,
        current_user,
        _ensure_task_owner,
    )


def _run_record_retry_runtime(
    run_record: Any,
    *,
    approved_tools_override: Optional[List[str]] = None,
) -> Dict[str, Any]:
    metadata = _run_record_metadata(run_record)
    selected_tools = _normalize_tool_selection(metadata.get("selectedTools"))
    approved_tools = (
        _normalize_tool_selection(approved_tools_override)
        if approved_tools_override is not None
        else _normalize_tool_selection(metadata.get("approvedTools"))
    )
    run_environment = _normalize_run_environment(metadata.get("runEnvironment"))
    language = _normalize_language(metadata.get("language"))
    input_files = metadata.get("inputFiles") if isinstance(metadata.get("inputFiles"), list) else None
    agent_snapshot = metadata.get("agentSnapshot") if isinstance(metadata.get("agentSnapshot"), dict) else None
    if agent_snapshot is None:
        agent_config = _resolve_agent_config(getattr(run_record, "agent_id", "") or "")
        agent_snapshot = agent_config.to_runtime_snapshot(approved_tools=approved_tools) if agent_config else None
    return {
        "selectedTools": selected_tools,
        "approvedTools": approved_tools,
        "runEnvironment": run_environment,
        "language": language,
        "inputFiles": input_files,
        "agentSnapshot": agent_snapshot,
    }



def _approval_service_deps() -> ApprovalServiceDeps:
    return ApprovalServiceDeps(
        event_replay_query_limit=EVENT_REPLAY_QUERY_LIMIT,
        default_agent_id=agentSettings.core.agent_id,
        load_owned_session=_load_owned_session,
        load_session_run_records=_load_session_run_records,
        run_record_metadata=_run_record_metadata,
        run_record_retry_runtime=_run_record_retry_runtime,
        serialize_session_run_payload=_serialize_session_run_payload,
        normalize_tool_selection=_normalize_tool_selection,
        merge_tool_selection=_merge_tool_selection,
        normalize_run_environment=_normalize_run_environment,
        normalize_language=_normalize_language,
        resolve_agent_config=_resolve_agent_config,
        deserialize_file_items=_deserialize_file_items,
        sync_session_run=_sync_session_run,
        run_autoagent=_run_autoagent,
        start_background_run=_start_background_run,
    )


def _start_retry_from_session_run_record(
    session_store: SessionStore,
    session_record: Any,
    run_record: Any,
    *,
    source: str,
    approved_tools_override: Optional[List[str]] = None,
    approval_type: Optional[str] = None,
    parent_event_type: Optional[str] = None,
) -> Dict[str, Any]:
    return start_retry_from_session_run_record_service(
        session_store,
        session_record,
        run_record,
        _approval_service_deps(),
        source=source,
        approved_tools_override=approved_tools_override,
        approval_type=approval_type,
        parent_event_type=parent_event_type,
    )


async def _resolve_session_run_record_approval(
    session_store: SessionStore,
    session_record: Any,
    run_record: Any,
    req: AgentRunApprovalReq,
) -> Dict[str, Any]:
    from brain.core.approval_service import resolve_session_run_record_approval

    return await resolve_session_run_record_approval(
        session_store,
        session_record,
        run_record,
        req,
        _approval_service_deps(),
    )

async def _archive_owned_session(
    session_id: str,
    current_user: Any,
    *,
    source: str,
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    session_store = SessionStore()
    task_store = TaskStore()
    cancelled_run_id = ""
    run_id = str(session_record.current_run_id or "")
    if run_id:
        task = task_store.get_task(run_id)
        if task:
            _ensure_task_owner(task, current_user)
            if task.status not in {
                AgentTaskStatus.COMPLETED,
                AgentTaskStatus.FAILED,
                AgentTaskStatus.CANCELLED,
            }:
                _cancel_background_run(run_id, remove=True)
                task_store.update_status(run_id, AgentTaskStatus.CANCELLED, error_message="task cancelled")
                _sync_session_run(
                    SessionStore(),
                    run_id=run_id,
                    status=AgentTaskStatus.CANCELLED,
                    error_message="task cancelled",
                    output_text="task cancelled",
                )
                task_store.add_event(
                    run_id,
                    "task_cancel_requested",
                    {"status": AgentTaskStatus.CANCELLED, "reason": f"session {source}"},
                    trace_id=task.trace_id,
                    source="api",
                )
                cancelled_run_id = run_id
        else:
            run_record = session_store.get_run(run_id)
            if (
                run_record
                and run_record.session_id == session_id
                and (not session_record.user_id or run_record.user_id in {"", session_record.user_id})
                and run_record.status
                not in {
                    AgentTaskStatus.COMPLETED,
                    AgentTaskStatus.FAILED,
                    AgentTaskStatus.CANCELLED,
                }
            ):
                _cancel_background_run(run_id, remove=True)
                session_store.update_run(
                    run_id,
                    status=AgentTaskStatus.CANCELLED,
                    error_message="task cancelled",
                    output_text="task cancelled",
                )
                session_store.add_run_event(
                    session_id=session_id,
                    run_id=run_id,
                    user_id=session_record.user_id,
                    event_type="run_cancelled",
                    source="api",
                    payload={
                        "status": AgentTaskStatus.CANCELLED,
                        "reason": f"session {source}",
                    },
                )
                cancelled_run_id = run_id

    updated = session_store.archive_session(
        session_id,
        clear_current_run=True,
        last_message_preview="session archived",
    )
    if not updated:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        **serialize_session(updated),
        "sessionId": session_id,
        "archived": True,
        "deleted": source == "deleted",
        "cancelledRunId": cancelled_run_id,
    }


def _agent_message_from_session_message(record: Any) -> AgentMessage:
    return _shared_agent_message_from_session_message(record)


def _session_summary_text(session_record: Optional[Any]) -> str:
    return _shared_session_summary_text(session_record)


def _session_summary_message(summary_text: str) -> AgentMessage:
    return _shared_session_summary_message(summary_text)


def _session_message_summary_line(record: Any) -> str:
    return _shared_session_message_summary_line(record)


def _compose_session_summary(
    records: List[Any],
    *,
    existing_summary: str = "",
    max_chars: int = SESSION_SUMMARY_MAX_CHARS,
) -> str:
    return _shared_compose_session_summary(
        records,
        existing_summary=existing_summary,
        max_chars=max_chars,
    )


def _merge_session_metadata(session_record: Any, patch: Dict[str, Any]) -> Dict[str, Any]:
    return _shared_merge_session_metadata(session_record, patch)


def _maybe_update_session_summary(
    session_store: Optional[SessionStore],
    session_id: Optional[str],
    *,
    task_store: Optional[TaskStore] = None,
    task_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return _shared_maybe_update_session_summary(
        session_store,
        session_id,
        task_store=task_store,
        task_id=task_id,
        trace_id=trace_id,
        trigger_message_count=SESSION_SUMMARY_TRIGGER_MESSAGE_COUNT,
        recent_message_count=SESSION_SUMMARY_RECENT_MESSAGE_COUNT,
        max_messages=SESSION_SUMMARY_MAX_MESSAGES,
        max_chars=SESSION_SUMMARY_MAX_CHARS,
    )


def _build_session_model_messages(
    session_store: SessionStore,
    session_id: Optional[str],
    current_messages: List[AgentMessage],
    current_message_id: Optional[str],
    *,
    history_limit: Optional[int] = None,
) -> List[AgentMessage]:
    return _shared_build_session_model_messages(
        session_store,
        session_id,
        current_messages,
        current_message_id,
        history_limit=history_limit if history_limit is not None else SESSION_CONTEXT_HISTORY_LIMIT,
        max_context_chars=SESSION_CONTEXT_MAX_CHARS,
        logger=logger,
    )


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


def _deserialize_file_items(files: Any) -> List[FileItem]:
    return _shared_deserialize_context_file_items(files)



def _autoagent_runtime_deps() -> AutoAgentRuntimeDeps:
    return AutoAgentRuntimeDeps(
        default_agent_mode=DEFAULT_AGENT_MODE,
        running_tasks=runningAgentTasks,
        agent_factory=agentFactory,
        clone_request=_clone_gpt_request,
        validate_user_message=_validate_user_message,
        fill_request_defaults=_fill_request_defaults,
        resolve_agent_config=_resolve_agent_config,
        normalize_tool_selection=_normalize_tool_selection,
        serialize_file_items=_serialize_file_items,
        next_session_event_seq=_next_session_event_seq,
        session_title_from_content=_session_title_from_content,
        assert_session_user=_assert_session_user,
        build_session_model_messages=_build_session_model_messages,
        sync_session_run=_sync_session_run,
        message_event_payload=_message_event_payload,
        extract_result_text=_extract_result_text,
        is_result_text_chunk=_is_result_text_chunk,
        usage_increments_from_event=_usage_increments_from_event,
        extract_remote_artifacts=_extract_remote_artifacts,
        artifact_event_payload=_artifact_event_payload,
        update_session_status=_update_session_status,
        convert_agent_messages=_convert_agent_messages,
        load_task_memory_context=_load_task_memory_context,
        memory_context_status_text=_memory_context_status_text,
        build_tool_collection=build_tool_collection,
        blocked_tool_reasons=_blocked_tool_reasons,
        approval_requests_from_blocked_tools=_approval_requests_from_blocked_tools,
        approval_waiting_message=_approval_waiting_message,
        register_workspace_artifacts=_register_workspace_artifacts,
        sync_plan_terminal_status=_sync_plan_terminal_status,
        maybe_update_session_summary=_maybe_update_session_summary,
        renew_background_run=_renew_background_run,
        background_lease_heartbeat_seconds=BACKGROUND_LEASE_HEARTBEAT_SECONDS,
    )


async def _run_autoagent(req: GptQueryReq, enqueue: Callable[[str], None]) -> None:
    await run_autoagent(req, enqueue, _autoagent_runtime_deps())


def recover_incomplete_agent_tasks(
    *,
    limit: int = 50,
    lease_ms: int = BACKGROUND_DISPATCH_LEASE_MS,
    max_attempts: int = BACKGROUND_RECOVERY_MAX_ATTEMPTS,
) -> Dict[str, Any]:
    return _recover_background_tasks(
        store=TaskStore(),
        owner=BACKGROUND_RUNNER_OWNER,
        start_background_run=_start_background_run,
        run_autoagent=_run_autoagent,
        deserialize_file_items=_deserialize_file_items,
        limit=limit,
        lease_ms=lease_ms,
        max_attempts=max_attempts,
    )


@agent_router.get("/agents")
async def list_agents() -> Dict[str, Any]:
    _reload_agent_registry_or_raise()
    return {
        "items": [agent.to_dict() for agent in agentRegistry.list_agents()],
        "defaultAgentId": agentSettings.core.agent_id,
    }


@agent_router.get("/agents/diagnostics")
async def get_agent_diagnostics() -> Dict[str, Any]:
    return agentRegistry.diagnostics()


@agent_router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> Dict[str, Any]:
    _reload_agent_registry_or_raise()
    agent = agentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent.to_dict()


@agent_router.get("/tools")
async def list_agent_tools(agent_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    _reload_agent_registry_or_raise()
    resolved_agent_id = agent_id or agentSettings.core.agent_id
    agent_config = agentRegistry.get(resolved_agent_id)
    ctx = AgentContext(
        requestId=f"tool-list-{uuid.uuid4()}",
        sessionId="tool-list",
        user_id="",
        agent_id=resolved_agent_id,
        run_id="tool-list",
        query="",
        task=None,
        printer=None,
        toolCollection=None,
        dateInfo=time.strftime("%Y-%m-%d"),
        isStream=False,
        language=_normalize_language(getattr(agentSettings, "lang", "ch")),
    )
    tc = await build_tool_collection(ctx)
    blocked = sorted(set(tc.blocked_tools))
    blocked_items = [
        _serialize_blocked_tool(tool_name, reason, agent_config)
        for tool_name, reason in _blocked_tool_reasons(blocked, agent_config, None).items()
    ]
    return {
        "agentId": resolved_agent_id,
        "items": [_serialize_available_tool(tool, agent_config) for tool in tc.tool_map.values()],
        "blockedTools": blocked_items,
        "unavailable": blocked_items,
    }


@agent_router.get("/mcp/servers")
async def list_agent_mcp_servers(
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    _current_user_id(current_user, None)
    return _mcp_status_payload()


@agent_router.post("/mcp/tools/refresh")
async def refresh_agent_mcp_tools(
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    _current_user_id(current_user, None)
    registry = _get_mcp_market_registry()
    if registry is None:
        payload = _mcp_status_payload()
        payload["refreshed"] = False
        payload["error"] = "MCP registry not initialised"
        return payload
    await asyncio.to_thread(registry.refresh, True)
    payload = _mcp_status_payload()
    payload["refreshed"] = True
    return payload


@agent_router.post("/mcp/servers/{server_id}/refresh")
async def refresh_agent_mcp_server(
    server_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    _current_user_id(current_user, None)
    registry = _get_mcp_market_registry()
    if registry is None:
        payload = _mcp_status_payload()
        payload["refreshed"] = False
        payload["serverId"] = server_id
        payload["error"] = "MCP registry not initialised"
        return payload
    if not hasattr(registry, "refresh_server"):
        await asyncio.to_thread(registry.refresh, True)
        payload = _mcp_status_payload()
        payload["refreshed"] = True
        payload["serverId"] = server_id
        payload["refreshScope"] = "all"
        return payload
    matched = await asyncio.to_thread(registry.refresh_server, server_id, True)
    if not matched:
        raise HTTPException(status_code=404, detail="MCP server not found")
    payload = _mcp_status_payload()
    payload["refreshed"] = True
    payload["serverId"] = server_id
    payload["refreshScope"] = "server"
    return payload


@agent_router.post("/mcp/tools/{tool_id}/dry-run")
async def dry_run_agent_mcp_tool(
    tool_id: str,
    req: AgentMCPToolDryRunReq,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    requested_tool_name = str(tool_id or "").strip()
    if not requested_tool_name:
        raise HTTPException(status_code=400, detail="tool_id is required")

    _reload_agent_registry_or_raise()
    req_approved_tools = _request_approved_tools(req)
    req_run_environment = _request_run_environment(req)
    resolved_agent_id = _request_agent_id(req) or agentSettings.core.agent_id
    agent_config = agentRegistry.get(resolved_agent_id)
    effective_user_id = _current_user_id(current_user, None) or ""
    ctx = AgentContext(
        requestId=f"mcp-dry-run-{uuid.uuid4()}",
        sessionId="mcp-dry-run",
        user_id=effective_user_id,
        agent_id=resolved_agent_id,
        run_id=f"mcp-dry-run-{uuid.uuid4()}",
        query="",
        task=None,
        printer=None,
        toolCollection=None,
        dateInfo=time.strftime("%Y-%m-%d"),
        isStream=False,
        language=_normalize_language(getattr(agentSettings, "lang", "ch")),
        selected_tools=[requested_tool_name],
        approved_tools=req_approved_tools,
        run_environment=_normalize_run_environment(req_run_environment),
    )
    tc = await build_tool_collection(ctx)
    resolved_tool_name = _resolve_tool_name(tc.tool_map, requested_tool_name)
    if resolved_tool_name not in tc.tool_map:
        reason = _blocked_tool_reasons(
            [requested_tool_name],
            agent_config,
            [requested_tool_name],
            approved_tools=req_approved_tools,
        ).get(requested_tool_name)
        if reason and reason != "blocked_by_policy":
            raise HTTPException(status_code=403, detail=reason)
        raise HTTPException(status_code=404, detail="tool not found")

    try:
        result = await tc.execute(resolved_tool_name, req.arguments or {})
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"tool `{resolved_tool_name}` timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    execution = tc.last_execution or {}
    return {
        "toolName": resolved_tool_name,
        "requestedToolName": requested_tool_name,
        "agentId": resolved_agent_id,
        "dryRun": True,
        "ok": not bool(execution.get("failed")),
        "result": result,
        "execution": sanitize_payload(execution),
    }


@agent_router.post("/sessions")
async def create_agent_session(
    req: AgentSessionCreateReq,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    effective_user_id = _current_user_id(current_user, None) or ""
    store = SessionStore()
    try:
        session_record = store.create_session(
            session_id=req.session_id or req.sessionId,
            user_id=effective_user_id,
            title=req.title,
            agent_id=_request_agent_id(req) or agentSettings.core.agent_id,
            metadata=req.metadata,
        )
        _assert_session_user(session_record, effective_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return serialize_session(session_record)


@agent_router.get("/sessions")
async def list_agent_sessions(
    status: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    effective_user_id = _current_user_id(current_user, None)
    store = SessionStore()
    sessions = store.list_sessions(
        user_id=effective_user_id,
        status=status,
        keyword=keyword,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    count = store.count_sessions(
        user_id=effective_user_id,
        status=status,
        keyword=keyword,
        include_archived=include_archived,
    )
    return {"items": [serialize_session(item) for item in sessions], "count": count, "limit": limit, "offset": offset}


@agent_router.get("/sessions/{session_id}")
async def get_agent_session(
    session_id: str,
    messages_limit: int = Query(default=100, ge=1, le=500),
    runs_limit: int = Query(default=50, ge=1, le=200),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_store = SessionStore()
    session_record = session_store.get_session(session_id)
    if not session_record:
        raise HTTPException(status_code=404, detail="session not found")
    _ensure_session_owner(session_record, current_user)

    task_store = TaskStore()
    payload = serialize_session(session_record)
    payload["messages"] = [
        serialize_message(item)
        for item in session_store.list_messages(session_id, limit=messages_limit)
    ]
    payload["messageCount"] = session_store.count_messages(session_id)
    run_page = _collect_session_run_payloads(
        session_store,
        task_store,
        session_record,
        limit=runs_limit,
    )
    payload["runCount"] = run_page["count"]
    payload["runs"] = run_page["items"]
    current_run = None
    current_run_id = session_record.current_run_id or ""
    if current_run_id:
        try:
            run_record, task = _load_session_run_records(
                session_store,
                task_store,
                session_record,
                current_run_id,
                current_user,
            )
            current_run = _serialize_session_run_payload(
                session_id,
                run_record=run_record,
                task_record=task,
            )
            pending_approval = _session_pending_approval_payload(
                session_store,
                task_store,
                session_id,
                current_run_id,
            )
            if pending_approval:
                current_run["pendingApproval"] = pending_approval
                payload["pendingApproval"] = pending_approval
        except HTTPException:
            current_run = None
    artifacts = _collect_session_artifacts(
        session_store,
        task_store,
        session_id,
        runs=run_page["taskRecords"],
    )
    artifacts.sort(key=lambda item: item.get("createdAt") or 0, reverse=True)
    payload["currentRun"] = current_run
    payload["artifactSummary"] = {
        "count": len(artifacts),
        "items": artifacts[:20],
    }
    return payload


@agent_router.patch("/sessions/{session_id}")
async def update_agent_session(
    session_id: str,
    req: AgentSessionUpdateReq,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = SessionStore()
    session_record = store.get_session(session_id)
    if not session_record:
        raise HTTPException(status_code=404, detail="session not found")
    _ensure_session_owner(session_record, current_user)
    if req.archived is True:
        return await _archive_owned_session(session_id, current_user, source="archived")
    updated = store.update_session(
        session_id,
        title=req.title,
        agent_id=_request_agent_id(req),
        pinned=req.pinned,
        archived=req.archived,
        metadata=req.metadata,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="session not found")
    return serialize_session(updated)


@agent_router.post("/sessions/{session_id}/archive")
async def archive_agent_session(
    session_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    return await _archive_owned_session(session_id, current_user, source="archived")


@agent_router.delete("/sessions/{session_id}")
async def delete_agent_session(
    session_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    return await _archive_owned_session(session_id, current_user, source="deleted")


@agent_router.get("/sessions/{session_id}/messages")
async def list_agent_session_messages(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    before: Optional[str] = Query(default=None),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = SessionStore()
    session_record = store.get_session(session_id)
    if not session_record:
        raise HTTPException(status_code=404, detail="session not found")
    _ensure_session_owner(session_record, current_user)
    messages = store.list_messages(
        session_id,
        limit=limit,
        offset=offset,
        before_message_id=before,
    )
    count = store.count_messages(session_id, before_message_id=before)
    total_count = count if before is None else store.count_messages(session_id)
    return {
        "items": [serialize_message(item) for item in messages],
        "count": count,
        "totalCount": total_count,
        "hasMore": offset + len(messages) < count,
        "limit": limit,
        "offset": offset,
        "before": before,
    }



def _session_message_deps() -> AgentSessionMessageDeps:
    return AgentSessionMessageDeps(
        default_agent_id=agentSettings.core.agent_id,
        ensure_session_owner=_ensure_session_owner,
        current_user_id=_current_user_id,
        serialize_file_items=_serialize_file_items,
        request_language=_request_language,
        request_agent_id=_request_agent_id,
        request_selected_tools=_request_selected_tools,
        request_approved_tools=_request_approved_tools,
        request_run_environment=_request_run_environment,
        request_output_style=_request_output_style,
        request_mode=_request_mode,
        load_session_run_records=_load_session_run_records,
        run_record_metadata=_run_record_metadata,
        normalize_language=_normalize_language,
        resume_session_run_after_input=_resume_session_run_after_input,
        resume_task_after_input=_resume_task_after_input,
        run_autoagent=_run_autoagent,
        start_background_run=_start_background_run,
    )


@agent_router.post("/sessions/{session_id}/messages")
async def add_agent_session_message(
    session_id: str,
    req: AgentSessionMessageReq,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    return await add_session_message(session_id, req, current_user, _session_message_deps())


@agent_router.get("/sessions/{session_id}/events")
async def list_agent_session_events(
    session_id: str,
    event_type: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    after_seq: Optional[int] = Query(default=None, ge=0, alias="afterSeq"),
    limit: int = Query(default=500, ge=1, le=EVENT_REPLAY_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    effective_after_seq = after_seq if isinstance(after_seq, int) else None

    return _collect_session_events(
        session_record,
        event_type=event_type,
        source=source,
        after_seq=effective_after_seq,
        limit=limit,
        offset=offset,
    )


def _sse_data(payload: Dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"


def _websocket_query_int(websocket: WebSocket, name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw_value = websocket.query_params.get(name) if getattr(websocket, "query_params", None) is not None else None
    try:
        value = int(raw_value) if raw_value is not None else default
    except (TypeError, ValueError):
        value = default
    return max(min(value, maximum), minimum)


@agent_router.get("/sessions/{session_id}/stream")
async def stream_agent_session_events(
    session_id: str,
    after_seq: int = Query(default=0, ge=0, alias="afterSeq"),
    limit: int = Query(default=200, ge=1, le=EVENT_REPLAY_QUERY_LIMIT),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> StreamingResponse:
    initial_session = _load_owned_session(session_id, current_user)
    owner_user_id = initial_session.user_id

    async def generate() -> AsyncIterator[bytes]:
        next_seq = max(int(after_seq or 0), 0)
        idle_ticks = 0
        while True:
            session_record = SessionStore().get_session(session_id)
            if not session_record or session_record.user_id != owner_user_id:
                yield _sse_data(
                    {
                        "type": "done",
                        "sessionId": session_id,
                        "status": "missing",
                        "afterSeq": next_seq,
                    }
                ).encode("utf-8")
                break

            event_page = _collect_session_events(
                session_record,
                after_seq=next_seq,
                limit=limit,
            )
            items = event_page.get("items") if isinstance(event_page, dict) else []
            if items:
                idle_ticks = 0
                for event in items:
                    next_seq = max(next_seq, int(event.get("seq") or 0))
                    yield _sse_data(
                        {
                            "type": "session_event",
                            "sessionId": session_id,
                            "seq": next_seq,
                            "event": event,
                        }
                    ).encode("utf-8")

            status = session_record.status or AgentSessionStatus.IDLE
            if status != AgentSessionStatus.RUNNING:
                yield _sse_data(
                    {
                        "type": "done",
                        "sessionId": session_id,
                        "status": status,
                        "afterSeq": next_seq,
                    }
                ).encode("utf-8")
                break

            if not items:
                idle_ticks += 1

            if idle_ticks and idle_ticks % 10 == 0:
                yield _sse_data(
                    {
                        "type": "heartbeat",
                        "sessionId": session_id,
                        "status": status,
                        "afterSeq": next_seq,
                    }
                ).encode("utf-8")
            await asyncio.sleep(SESSION_STREAM_ACTIVE_SLEEP_SECONDS if items else SESSION_STREAM_IDLE_SLEEP_SECONDS)

    return StreamingResponse(generate(), media_type="text/event-stream")


@agent_router.get("/sessions/{session_id}/runs/current")
async def get_agent_session_current_run(
    session_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    current_run_id = session_record.current_run_id or ""
    if not current_run_id:
        return {"sessionId": session_id, "run": None}

    session_store = SessionStore()
    task_store = TaskStore()
    try:
        run_record, task = _load_session_run_records(
            session_store,
            task_store,
            session_record,
            current_run_id,
            current_user,
        )
    except HTTPException:
        return {"sessionId": session_id, "run": None, "currentRunId": current_run_id}
    payload = _serialize_session_run_payload(
        session_id,
        run_record=run_record,
        task_record=task,
    )
    pending_approval = _session_pending_approval_payload(
        session_store,
        task_store,
        session_id,
        current_run_id,
    )
    if pending_approval:
        payload["pendingApproval"] = pending_approval
    return {"sessionId": session_id, "run": payload}


@agent_router.get("/sessions/{session_id}/runs")
async def list_agent_session_runs(
    session_id: str,
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    session_store = SessionStore()
    task_store = TaskStore()
    page = _collect_session_run_payloads(
        session_store,
        task_store,
        session_record,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {
        "items": page["items"],
        "sessionId": session_id,
        "status": status,
        "count": page["count"],
        "hasMore": page["hasMore"],
        "limit": limit,
        "offset": offset,
    }


@agent_router.get("/sessions/{session_id}/runs/{run_id}")
async def get_agent_session_run(
    session_id: str,
    run_id: str,
    events_limit: int = Query(default=500, ge=1, le=EVENT_REPLAY_QUERY_LIMIT),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    session_store = SessionStore()
    task_store = TaskStore()
    run_record, task = _load_session_run_records(
        session_store,
        task_store,
        session_record,
        run_id,
        current_user,
    )
    payload = _serialize_session_run_payload(
        session_id,
        run_record=run_record,
        task_record=task,
    )
    pending_approval = _session_pending_approval_payload(
        session_store,
        task_store,
        session_id,
        run_id,
    )
    if pending_approval:
        payload["pendingApproval"] = pending_approval
    payload["events"] = _collect_session_run_event_payloads(
        session_store,
        task_store,
        session_id,
        run_id,
        events_limit=events_limit,
    )
    payload["artifacts"] = _collect_session_artifacts(
        session_store,
        task_store,
        session_id,
        run_id=run_id,
    )
    return payload


@agent_router.get("/sessions/{session_id}/runs/{run_id}/plan")
async def get_agent_session_run_plan(
    session_id: str,
    run_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    session_store = SessionStore()
    task_store = TaskStore()
    _load_session_run_records(session_store, task_store, session_record, run_id, current_user)

    plan_events: List[Dict[str, Any]] = []
    latest_plan: Optional[Dict[str, Any]] = None
    all_events = _collect_session_run_event_payloads(
        session_store,
        task_store,
        session_id,
        run_id,
        events_limit=EVENT_REPLAY_QUERY_LIMIT,
    )
    for seq, event in enumerate(all_events, start=1):
        event_name = str(event.get("eventType") or event.get("type") or "")
        normalized_name = str(event.get("type") or "")
        if event_name not in PLAN_EVENT_TYPES and normalized_name not in PLAN_EVENT_TYPES:
            continue
        plan_event = _serialize_plan_event_payload(session_id, run_id, event, seq)
        if plan_event is None:
            continue
        plan_events.append(plan_event)
        latest_plan = plan_event["plan"]

    return {
        "sessionId": session_id,
        "runId": run_id,
        "plan": latest_plan,
        "events": plan_events,
    }


@agent_router.post("/sessions/{session_id}/runs/{run_id}/cancel")
async def cancel_agent_session_run(
    session_id: str,
    run_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    session_store = SessionStore()
    task_store = TaskStore()
    run_record, task = _load_session_run_records(
        session_store,
        task_store,
        session_record,
        run_id,
        current_user,
    )

    if task is None:
        _cancel_background_run(run_id, remove=True)
        updated_run = session_store.update_run(
            run_id,
            status=AgentTaskStatus.CANCELLED,
            error_message="task cancelled",
            output_text="task cancelled",
        ) or run_record
        event = session_store.add_run_event(
            session_id=session_id,
            run_id=run_id,
            user_id=session_record.user_id,
            event_type="run_cancelled",
            source="api",
            payload={
                "status": AgentTaskStatus.CANCELLED,
                "reason": "session_cancel_requested",
            },
        )
        if session_record.current_run_id == run_id:
            session_store.update_session(
                session_id,
                status=AgentSessionStatus.IDLE,
                current_run_id="",
                last_message_preview="task cancelled",
            )
        payload = _serialize_session_run_payload(session_id, run_record=updated_run)
        payload["event"] = serialize_run_event(event)
        payload["sessionId"] = session_id
        payload["runId"] = run_id
        return payload

    payload = await cancel_agent_task(run_id, current_user=current_user)
    _sync_session_run(
        session_store,
        run_id=run_id,
        status=AgentTaskStatus.CANCELLED if payload.get("status") == AgentTaskStatus.CANCELLED else None,
        error_message="task cancelled" if payload.get("status") == AgentTaskStatus.CANCELLED else None,
    )
    if session_record.current_run_id == run_id:
        session_store.update_session(
            session_id,
            status=AgentSessionStatus.IDLE,
            current_run_id="",
            last_message_preview="task cancelled" if payload.get("status") == AgentTaskStatus.CANCELLED else task.input_text,
        )
    payload["sessionId"] = session_id
    payload["runId"] = payload.get("taskId")
    return payload


@agent_router.post("/sessions/{session_id}/runs/{run_id}/retry")
async def retry_agent_session_run(
    session_id: str,
    run_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    if session_record.status == AgentSessionStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="session is archived")
    session_store = SessionStore()
    task_store = TaskStore()
    run_record, task = _load_session_run_records(
        session_store,
        task_store,
        session_record,
        run_id,
        current_user,
    )

    if task is None:
        return _start_retry_from_session_run_record(
            session_store,
            session_record,
            run_record,
            source="retry",
            parent_event_type="run_retry_requested",
        )

    payload = await retry_agent_task(run_id, current_user=current_user)
    retry_run_id = str(payload.get("taskId") or "")
    if retry_run_id:
        session_store.update_session(
            session_id,
            status=AgentSessionStatus.RUNNING,
            current_run_id=retry_run_id,
            last_message_preview=task.input_text or "retry requested",
        )
    payload["sessionId"] = session_id
    payload["runId"] = retry_run_id
    return payload


@agent_router.post("/sessions/{session_id}/runs/{run_id}/approval")
async def resolve_agent_session_run_approval(
    session_id: str,
    run_id: str,
    req: AgentRunApprovalReq,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    return await resolve_session_run_approval_service(
        session_id,
        run_id,
        req,
        current_user,
        _approval_service_deps(),
    )


@agent_router.get("/sessions/{session_id}/artifacts")
async def list_agent_session_artifacts(
    session_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    session_store = SessionStore()
    task_store = TaskStore()
    runs = task_store.list_tasks(
        user_id=session_record.user_id or _current_user_id(current_user, None),
        conversation_id=session_id,
        limit=200,
    )
    artifacts = _collect_session_artifacts(
        session_store,
        task_store,
        session_id,
        runs=runs,
    )
    artifacts.sort(key=lambda item: item.get("createdAt") or 0)
    return {"items": artifacts, "sessionId": session_id}


@agent_router.get("/sessions/{session_id}/artifacts/{artifact_id}")
async def download_agent_session_artifact(
    session_id: str,
    artifact_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Any:
    session_record = _load_owned_session(session_id, current_user)
    session_store = SessionStore()
    artifact = session_store.get_artifact(session_id, artifact_id)
    if artifact:
        return _artifact_download_response(artifact)

    task_store = TaskStore()
    runs = task_store.list_tasks(
        user_id=session_record.user_id or _current_user_id(current_user, None),
        conversation_id=session_id,
        limit=200,
    )
    for run in runs:
        artifact = task_store.get_artifact(run.task_id, artifact_id)
        if artifact:
            return _artifact_download_response(artifact)
    raise HTTPException(status_code=404, detail="artifact not found")


@agent_router.get("/sessions/{session_id}/runs/{run_id}/artifacts")
async def list_agent_session_run_artifacts(
    session_id: str,
    run_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    session_store = SessionStore()
    task_store = TaskStore()
    _load_session_run_records(session_store, task_store, session_record, run_id, current_user)
    artifacts = _collect_session_artifacts(
        session_store,
        task_store,
        session_id,
        run_id=run_id,
    )
    return {"items": artifacts, "sessionId": session_id, "runId": run_id}


@agent_router.get("/sessions/{session_id}/tools")
async def list_agent_session_tools(
    session_id: str,
    agent_id: Optional[str] = Query(default=None),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    _reload_agent_registry_or_raise()
    resolved_agent_id = agent_id or session_record.agent_id or agentSettings.core.agent_id
    agent_config = agentRegistry.get(resolved_agent_id)
    effective_user_id = session_record.user_id or _current_user_id(current_user, None) or ""
    ctx = AgentContext(
        requestId=f"session-tool-list-{uuid.uuid4()}",
        sessionId=session_id,
        user_id=effective_user_id,
        agent_id=resolved_agent_id,
        run_id=session_record.current_run_id or "tool-list",
        query="",
        task=None,
        printer=None,
        toolCollection=None,
        dateInfo=time.strftime("%Y-%m-%d"),
        isStream=False,
        language=_normalize_language(getattr(agentSettings, "lang", "ch")),
    )
    tc = await build_tool_collection(ctx)
    blocked = sorted(set(tc.blocked_tools))
    blocked_items = [
        _serialize_blocked_tool(tool_name, reason, agent_config)
        for tool_name, reason in _blocked_tool_reasons(blocked, agent_config, None).items()
    ]
    return {
        "sessionId": session_id,
        "agentId": resolved_agent_id,
        "currentRunId": session_record.current_run_id,
        "items": [_serialize_available_tool(tool, agent_config) for tool in tc.tool_map.values()],
        "blockedTools": blocked_items,
        "unavailable": blocked_items,
    }


@agent_router.post("/sessions/{session_id}/tools/test")
async def test_agent_session_tool(
    session_id: str,
    req: AgentMCPToolTestReq,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    session_record = _load_owned_session(session_id, current_user)
    tool_name = str(_first_not_none(req.tool_name, req.toolName) or "").strip()
    if not tool_name:
        raise HTTPException(status_code=400, detail="tool_name is required")

    _reload_agent_registry_or_raise()
    req_approved_tools = _request_approved_tools(req)
    req_run_environment = _request_run_environment(req)
    resolved_agent_id = _request_agent_id(req) or session_record.agent_id or agentSettings.core.agent_id
    agent_config = agentRegistry.get(resolved_agent_id)
    effective_user_id = session_record.user_id or _current_user_id(current_user, None) or ""
    ctx = AgentContext(
        requestId=f"session-tool-test-{uuid.uuid4()}",
        sessionId=session_id,
        user_id=effective_user_id,
        agent_id=resolved_agent_id,
        run_id=session_record.current_run_id or f"tool-test-{uuid.uuid4()}",
        query="",
        task=None,
        printer=None,
        toolCollection=None,
        dateInfo=time.strftime("%Y-%m-%d"),
        isStream=False,
        language=_normalize_language(getattr(agentSettings, "lang", "ch")),
        selected_tools=[tool_name],
        approved_tools=req_approved_tools,
        run_environment=_normalize_run_environment(req_run_environment),
    )
    tc = await build_tool_collection(ctx)
    resolved_tool_name = _resolve_tool_name(tc.tool_map, tool_name)
    if resolved_tool_name not in tc.tool_map:
        reason = _blocked_tool_reasons(
            [tool_name],
            agent_config,
            [tool_name],
            approved_tools=req_approved_tools,
        ).get(tool_name)
        if reason and reason != "blocked_by_policy":
            raise HTTPException(status_code=403, detail=reason)
        raise HTTPException(status_code=404, detail="tool not found")

    try:
        result = await tc.execute(resolved_tool_name, req.arguments or {})
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"tool `{resolved_tool_name}` timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    execution = tc.last_execution or {}
    return {
        "sessionId": session_id,
        "agentId": resolved_agent_id,
        "toolName": resolved_tool_name,
        "requestedToolName": tool_name,
        "ok": not bool(execution.get("failed")),
        "result": result,
        "execution": execution,
    }


@agent_router.get("/agents/{agent_id}/evals")
async def list_agent_evals(agent_id: str) -> Dict[str, Any]:
    _reload_agent_registry_or_raise()
    agent = agentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"items": [item.__dict__ for item in agent.evals]}


def _create_eval_task(store: TaskStore, eval_run: Any) -> Any:
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
    return task


def _start_eval_task(eval_run: Any) -> None:
    req = GptQueryReq(
        trace_id=eval_run.trace_id,
        user_id=eval_run.user_id,
        agent_id=eval_run.agent_id,
        conversation_id=eval_run.conversation_id,
        outputStyle=eval_run.output_style,
        mode=eval_run.mode,
        messages=[AgentMessage(role=RoleType.USER.value, content=eval_run.input_text)],
    )
    _start_background_run(eval_run.task_id, _run_autoagent(req, lambda _data: None))


@agent_router.post("/agents/{agent_id}/evals/run")
async def run_agent_evals(
    agent_id: str,
    user_id: str = Query(default="eval-runner"),
    output_style: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    _reload_agent_registry_or_raise()
    agent = agentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")

    store = TaskStore()
    items: List[Dict[str, Any]] = []
    for case in agent.evals:
        eval_run = build_eval_run(agent, case, user_id=user_id, output_style=output_style)
        task = _create_eval_task(store, eval_run)
        _start_eval_task(eval_run)
        items.append({"task": serialize_task(task), "eval": eval_run.to_dict()})

    return {"items": items, "count": len(items)}


@agent_router.post("/agents/{agent_id}/evals/{case_id}/run")
async def run_agent_eval(
    agent_id: str,
    case_id: str,
    user_id: str = Query(default="eval-runner"),
    output_style: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    _reload_agent_registry_or_raise()
    agent = agentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    case = next((item for item in agent.evals if item.id == case_id), None)
    if not case:
        raise HTTPException(status_code=404, detail="eval case not found")

    store = TaskStore()
    eval_run = build_eval_run(agent, case, user_id=user_id, output_style=output_style)
    task = _create_eval_task(store, eval_run)
    _start_eval_task(eval_run)
    return {"task": serialize_task(task), "eval": eval_run.to_dict()}


@agent_router.post("/tasks/{task_id}/eval-result")
async def evaluate_agent_task(task_id: str) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    task_payload = serialize_task(task)
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    if metadata.get("source") != "eval":
        raise HTTPException(status_code=400, detail="task is not an eval task")

    event_payloads = [serialize_event(event) for event in store.list_events(task_id, limit=EVENT_REPLAY_QUERY_LIMIT)]
    artifact_payloads = [serialize_artifact(artifact) for artifact in store.list_artifacts(task_id)]
    result = evaluate_eval_task(task_payload, event_payloads, artifact_payloads).to_dict()
    store.add_event(
        task_id,
        "eval_result",
        result,
        trace_id=task.trace_id,
        source="eval",
    )
    return result


@agent_router.get("/tasks")
async def list_agent_tasks(
    user_id: Optional[str] = Query(default=None),
    conversation_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    agent_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    created_from: Optional[int] = Query(default=None),
    created_to: Optional[int] = Query(default=None),
    min_duration_ms: Optional[int] = Query(default=None, ge=0),
    max_duration_ms: Optional[int] = Query(default=None, ge=0),
    has_error: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    effective_user_id = _current_user_id(current_user, user_id)
    effective_conversation_id = conversation_id if isinstance(conversation_id, str) and conversation_id.strip() else None
    store = TaskStore()
    tasks = store.list_tasks(
        user_id=effective_user_id,
        conversation_id=effective_conversation_id,
        status=status,
        agent_id=agent_id,
        keyword=keyword,
        created_from_ms=created_from,
        created_to_ms=created_to,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
        has_error=has_error,
        limit=limit,
        offset=offset,
    )
    if agent_type:
        normalized_agent_type = agent_type.strip()
        _reload_agent_registry_or_raise()
        def task_matches_agent_type(task: Any) -> bool:
            agent = agentRegistry.get(task.agent_id)
            return bool(agent and agent.type == normalized_agent_type)

        tasks = [
            task
            for task in tasks
            if task_matches_agent_type(task)
        ]
    return {"items": [serialize_task(task) for task in tasks], "limit": limit, "offset": offset}


@agent_router.post("/tasks")
async def create_agent_task(
    req: GptQueryReq,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    request = _clone_gpt_request(req)
    try:
        messages = _validate_user_message(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    has_explicit_conversation_id = bool(str(request.conversation_id or "").strip())
    _fill_request_defaults(request)
    request.user_id = _current_user_id(current_user, request.user_id)
    trace_id = request.trace_id or str(uuid.uuid4())
    request.trace_id = trace_id
    if not has_explicit_conversation_id:
        request.conversation_id = trace_id
    agent_config = _resolve_agent_config(request.agent_id or "")
    resolved_mode = _normalize_agent_mode(
        request.mode or (agent_config.mode if agent_config else None) or DEFAULT_AGENT_MODE
    ) or DEFAULT_AGENT_MODE
    selected_tools = _normalize_tool_selection(request.selected_tools)
    approved_tools = _normalize_tool_selection(request.approved_tools)
    agent_snapshot = agent_config.to_runtime_snapshot(approved_tools=approved_tools) if agent_config else None
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
            "agentSnapshot": agent_snapshot,
            "selectedTools": selected_tools,
            "approvedTools": approved_tools,
            "inputFiles": input_files,
            "runEnvironment": request.run_environment,
            "language": request.language,
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
            "agentSnapshot": agent_snapshot,
            "selectedTools": selected_tools,
            "approvedTools": approved_tools,
            "inputFiles": input_files,
            "runEnvironment": request.run_environment,
            "language": request.language,
        },
        trace_id=trace_id,
        source="api",
    )
    _start_background_run(trace_id, _run_autoagent(request, lambda _data: None))
    return serialize_task(task)


async def _start_handoff_task(
    parent_ctx: AgentContext,
    target_agent_id: str,
    task_text: str,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    parent_config = _resolve_agent_config(parent_ctx.agent_id)
    allowed = parent_config.handoffs.get("allowed") if parent_config and isinstance(parent_config.handoffs, dict) else []
    if target_agent_id not in (allowed or []):
        raise ValueError(f"agent `{parent_ctx.agent_id}` cannot hand off to `{target_agent_id}`")

    target_config = _resolve_agent_config(target_agent_id)
    if not target_config:
        raise ValueError(f"target agent not found: {target_agent_id}")

    trace_id = str(uuid.uuid4())
    output_style = str(options.get("outputStyle") or parent_ctx.outputStyle or "markdown")
    mode = _normalize_agent_mode(options.get("mode") or target_config.mode or DEFAULT_AGENT_MODE) or DEFAULT_AGENT_MODE
    selected_tools = _normalize_tool_selection(options.get("selected_tools"))
    approved_tools = _normalize_tool_selection(parent_ctx.approved_tools)
    run_environment = _normalize_run_environment(parent_ctx.run_environment)
    language = _normalize_language(getattr(parent_ctx, "language", None))
    target_agent_snapshot = target_config.to_runtime_snapshot(approved_tools=approved_tools)

    request = GptQueryReq(
        trace_id=trace_id,
        user_id=parent_ctx.user_id,
        agent_id=target_agent_id,
        conversation_id=parent_ctx.sessionId or parent_ctx.run_id or str(uuid.uuid4()),
        outputStyle=output_style,
        mode=mode,
        selected_tools=selected_tools,
        approved_tools=approved_tools,
        run_environment=run_environment,
        language=language,
        messages=[AgentMessage(role=RoleType.USER.value, content=task_text)],
    )
    _fill_request_defaults(request)

    store = TaskStore()
    task = store.create_task(
        task_id=trace_id,
        trace_id=trace_id,
        conversation_id=request.conversation_id,
        user_id=request.user_id,
        agent_id=target_agent_id,
        mode=mode,
        output_style=request.outputStyle,
        input_text=task_text,
        metadata={
            "source": "handoff",
            "parentTaskId": parent_ctx.task_id,
            "parentAgentId": parent_ctx.agent_id,
            "agentSnapshot": target_agent_snapshot,
            "selectedTools": selected_tools,
            "approvedTools": approved_tools,
            "runEnvironment": run_environment,
            "language": language,
        },
    )
    store.add_event(
        trace_id,
        "task_queued",
        {
            "status": AgentTaskStatus.QUEUED,
            "mode": mode,
            "outputStyle": request.outputStyle,
            "agentConfigId": target_config.id,
            "agentSnapshot": target_agent_snapshot,
            "parentTaskId": parent_ctx.task_id,
            "parentAgentId": parent_ctx.agent_id,
            "selectedTools": selected_tools,
            "approvedTools": approved_tools,
            "runEnvironment": run_environment,
            "language": language,
        },
        trace_id=trace_id,
        source="handoff",
    )
    if parent_ctx.task_id:
        store.link_child_task(
            parent_ctx.task_id,
            trace_id,
            relationship="handoff",
            source="handoff",
        )
        store.add_event(
            parent_ctx.task_id,
            "task_handoff_requested",
            {
                "parentAgentId": parent_ctx.agent_id,
                "targetAgentId": target_agent_id,
                "childTaskId": trace_id,
                "targetAgentSnapshot": target_agent_snapshot,
                "task": task_text,
                "mode": mode,
                "outputStyle": request.outputStyle,
                "selectedTools": selected_tools,
                "approvedTools": approved_tools,
                "runEnvironment": run_environment,
                "language": language,
            },
            trace_id=parent_ctx.requestId,
            source="handoff",
        )
    _start_background_run(trace_id, _run_autoagent(request, lambda _data: None))
    return serialize_task(task)


async def _resume_task_after_input(
    task_id: str,
    supplemental_input: str,
    *,
    language_override: Optional[str] = None,
    session_message_id: Optional[str] = None,
) -> None:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise ValueError(f"task not found: {task_id}")
    task_payload = serialize_task(task)
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    selected_tools = _normalize_tool_selection(metadata.get("selectedTools") if metadata else None)
    approved_tools = _normalize_tool_selection(metadata.get("approvedTools") if metadata else None)
    run_environment = _normalize_run_environment(metadata.get("runEnvironment") if metadata else None)
    language = _normalize_language(language_override or (metadata.get("language") if metadata else None))
    input_files = metadata.get("inputFiles") if metadata else None
    supplemental_text = (
        f"User supplemental input: {supplemental_input.strip()}"
        if language == "en"
        else f"用户补充输入：{supplemental_input.strip()}"
    )
    messages = [
        AgentMessage(
            role=RoleType.USER.value,
            content=task.input_text or "",
            uploadFile=_deserialize_file_items(input_files),
        ),
        AgentMessage(
            role=RoleType.USER.value,
            content=supplemental_text,
        ),
    ]
    await _run_autoagent(
        GptQueryReq(
            trace_id=task.task_id,
            user_id=task.user_id,
            agent_id=task.agent_id,
            conversation_id=task.conversation_id,
            session_message_id=session_message_id,
            outputStyle=task.output_style,
            mode=task.mode,
            selected_tools=selected_tools,
            approved_tools=approved_tools,
            run_environment=run_environment,
            language=language,
            messages=messages,
        ),
        lambda _data: None,
    )


async def _resume_session_run_after_input(
    run_record: Any,
    supplemental_input: str,
    *,
    language_override: Optional[str] = None,
    session_message_id: Optional[str] = None,
) -> None:
    run_id = str(getattr(run_record, "run_id", "") or "")
    session_id = str(getattr(run_record, "session_id", "") or "")
    if not run_id or not session_id:
        raise ValueError("run record is missing run_id or session_id")
    runtime = _run_record_retry_runtime(run_record)
    language = _normalize_language(language_override or runtime["language"])
    supplemental_text = (
        f"User supplemental input: {supplemental_input.strip()}"
        if language == "en"
        else f"用户补充输入：{supplemental_input.strip()}"
    )
    messages = [
        AgentMessage(
            role=RoleType.USER.value,
            content=getattr(run_record, "input_text", None) or "",
            uploadFile=_deserialize_file_items(runtime["inputFiles"]),
        ),
        AgentMessage(
            role=RoleType.USER.value,
            content=supplemental_text,
        ),
    ]
    await _run_autoagent(
        GptQueryReq(
            trace_id=run_id,
            user_id=getattr(run_record, "user_id", None) or "",
            agent_id=getattr(run_record, "agent_id", None) or agentSettings.core.agent_id,
            conversation_id=session_id,
            session_message_id=session_message_id,
            outputStyle=getattr(run_record, "output_style", None) or None,
            mode=getattr(run_record, "mode", None) or None,
            selected_tools=runtime["selectedTools"],
            approved_tools=runtime["approvedTools"],
            run_environment=runtime["runEnvironment"],
            language=language,
            messages=messages,
        ),
        lambda _data: None,
    )


@agent_router.get("/tasks/{task_id}")
async def get_agent_task(
    task_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_owner(task, current_user)
    return serialize_task(task)


@agent_router.get("/tasks/{task_id}/events")
async def list_agent_task_events(
    task_id: str,
    event_type: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=EVENT_REPLAY_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0),
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_owner(task, current_user)
    events = store.list_events(task_id, event_type=event_type, source=source, limit=limit, offset=offset)
    return {
        "items": [serialize_event(event) for event in events],
        "eventType": event_type,
        "source": source,
        "limit": limit,
        "offset": offset,
    }


@agent_router.post("/tasks/{task_id}/cancel")
async def cancel_agent_task(
    task_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_owner(task, current_user)
    if task.status in {
        AgentTaskStatus.COMPLETED,
        AgentTaskStatus.FAILED,
        AgentTaskStatus.CANCELLED,
    }:
        return serialize_task(task)

    _cancel_background_run(task_id)

    store.update_status(task_id, AgentTaskStatus.CANCELLED, error_message="task cancelled")
    store.add_event(
        task_id,
        "task_cancel_requested",
        {"status": AgentTaskStatus.CANCELLED},
        trace_id=task.trace_id,
        source="api",
    )
    _sync_plan_terminal_status(
        store,
        task_id,
        trace_id=task.trace_id,
        terminal_status="cancelled",
        reason="task cancelled",
        source="api",
    )
    updated = store.get_task(task_id)
    return serialize_task(updated or task)


@agent_router.delete("/tasks/{task_id}")
async def delete_agent_task(
    task_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_owner(task, current_user)

    _cancel_background_run(task_id, remove=True)

    deleted = store.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="task not found")
    return {"taskId": task_id, "deleted": True}


@agent_router.post("/tasks/{task_id}/retry")
async def retry_agent_task(
    task_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_owner(task, current_user)
    if not task.input_text:
        raise HTTPException(status_code=400, detail="task has no input to retry")

    retry_trace_id = str(uuid.uuid4())
    task_payload = serialize_task(task)
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    selected_tools = _normalize_tool_selection(metadata.get("selectedTools") if metadata else None)
    approved_tools = _normalize_tool_selection(metadata.get("approvedTools") if metadata else None)
    run_environment = _normalize_run_environment(metadata.get("runEnvironment") if metadata else None)
    language = _normalize_language(metadata.get("language") if metadata else None)
    input_files = metadata.get("inputFiles") if metadata else None
    agent_snapshot = metadata.get("agentSnapshot") if isinstance(metadata.get("agentSnapshot"), dict) else None
    if agent_snapshot is None:
        agent_config = _resolve_agent_config(task.agent_id)
        agent_snapshot = agent_config.to_runtime_snapshot(approved_tools=approved_tools) if agent_config else None
    store.create_task(
        task_id=retry_trace_id,
        trace_id=retry_trace_id,
        conversation_id=task.conversation_id,
        user_id=task.user_id,
        agent_id=task.agent_id,
        mode=task.mode,
        output_style=task.output_style,
        input_text=task.input_text,
        metadata={
            "source": "retry",
            "parentTaskId": task.task_id,
            "agentSnapshot": agent_snapshot,
            "selectedTools": selected_tools,
            "approvedTools": approved_tools,
            "runEnvironment": run_environment,
            "language": language,
            "inputFiles": input_files,
        },
    )
    store.add_event(
        retry_trace_id,
        "task_queued",
        {
            "status": AgentTaskStatus.QUEUED,
            "mode": task.mode,
            "outputStyle": task.output_style,
            "parentTaskId": task.task_id,
            "agentConfigId": task.agent_id,
            "agentSnapshot": agent_snapshot,
            "selectedTools": selected_tools,
            "approvedTools": approved_tools,
            "runEnvironment": run_environment,
            "language": language,
            "inputFiles": input_files,
        },
        trace_id=retry_trace_id,
        source="retry",
    )
    store.add_event(
        task.task_id,
        "task_retry_requested",
        {"retryTaskId": retry_trace_id},
        trace_id=task.trace_id,
        source="api",
    )
    store.link_child_task(
        task.task_id,
        retry_trace_id,
        relationship="retry",
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
        approved_tools=approved_tools,
        run_environment=run_environment,
        language=language,
        messages=[
            AgentMessage(
                role=RoleType.USER.value,
                content=task.input_text,
                uploadFile=_deserialize_file_items(input_files),
            )
        ],
    )
    _start_background_run(retry_trace_id, _run_autoagent(retry_req, lambda _data: None))
    retry_task = store.get_task(retry_trace_id)
    return serialize_task(retry_task) if retry_task else {"taskId": retry_trace_id}


@agent_router.post("/tasks/{task_id}/input")
async def add_agent_task_input(
    task_id: str,
    req: TaskUserInputReq,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_owner(task, current_user)
    was_waiting = task.status == AgentTaskStatus.WAITING_INPUT
    task_payload = serialize_task(task)
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    language = _normalize_language(req.language or (metadata.get("language") if metadata else None))
    try:
        event = store.add_user_input(task_id, req.content, user_id=_current_user_id(current_user, req.user_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if was_waiting:
        store.add_event(
            task_id,
            "task_queued",
            {
                "status": AgentTaskStatus.QUEUED,
                "reason": "user_input_received",
                "language": language,
            },
            trace_id=task.trace_id,
            source="api",
        )
        store.add_event(
            task_id,
            "task_resume_requested",
            {"userInputEventId": event.id, "language": language},
            trace_id=task.trace_id,
            source="api",
        )
        _start_background_run(task_id, _resume_task_after_input(task_id, req.content, language_override=language))
    updated_task = store.get_task(task_id) or task
    return {"task": serialize_task(updated_task), "event": serialize_event(event)}


@agent_router.get("/tasks/{task_id}/artifacts")
async def list_agent_task_artifacts(
    task_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_owner(task, current_user)
    artifacts = store.list_artifacts(task_id)
    return {"items": [serialize_artifact(artifact) for artifact in artifacts]}


@agent_router.get("/tasks/{task_id}/artifacts/{artifact_id}")
async def download_agent_task_artifact(
    task_id: str,
    artifact_id: str,
    current_user: TaskPilotUser = Depends(require_current_user),
) -> Any:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_owner(task, current_user)
    artifact = store.get_artifact(task_id, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    if str(artifact.file_path).startswith(("http://", "https://")):
        return RedirectResponse(artifact.file_path)
    file_path = Path(artifact.file_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="artifact file not found")
    return FileResponse(
        str(file_path),
        media_type=artifact.mime_type or "application/octet-stream",
        filename=artifact.filename,
    )


@agent_router.get("/web/assets/{asset_path:path}")
async def autoagent_frontend_asset(asset_path: str) -> FileResponse:
    asset_file = (FRONTEND_DIST / "assets" / asset_path).resolve()
    assets_root = (FRONTEND_DIST / "assets").resolve()
    if not asset_file.is_file() or not asset_file.is_relative_to(assets_root):
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(str(asset_file))


@agent_router.get("/web/autoagent")
async def autoagent_console() -> Any:
    vue_index = FRONTEND_DIST / "index.html"
    if vue_index.is_file():
        return FileResponse(str(vue_index), media_type="text/html")
    raise HTTPException(status_code=404, detail="frontend bundle not found")

@agent_router.post("/autoagent")
async def autoagent(
    req: GptQueryReq,
    current_user: TaskPilotUser = Depends(require_current_user),
):
    req.user_id = _current_user_id(current_user, req.user_id)

    async def run(enqueue: Callable[[str], None]) -> None:
        await _run_autoagent(req, enqueue)

    return StreamingResponse(sse_stream(run), media_type="text/event-stream")


@agent_router.websocket("/ws/sessions/{session_id}")
async def session_events_ws(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    try:
        current_user = await require_current_websocket_user(websocket)
        initial_session = _load_owned_session(session_id, current_user)
    except HTTPException as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "error": exc.detail})
        with contextlib.suppress(Exception):
            await websocket.close(code=1008)
        return

    owner_user_id = str(initial_session.user_id or "")
    next_seq = _websocket_query_int(websocket, "afterSeq", 0, minimum=0, maximum=2_000_000_000)
    limit = _websocket_query_int(websocket, "limit", 200, minimum=1, maximum=EVENT_REPLAY_QUERY_LIMIT)
    last_status = ""
    idle_ticks = 0

    try:
        while True:
            session_record = SessionStore().get_session(session_id)
            if not session_record or str(session_record.user_id or "") != owner_user_id:
                await websocket.send_json(
                    {
                        "type": "done",
                        "sessionId": session_id,
                        "status": "missing",
                        "afterSeq": next_seq,
                    }
                )
                break

            event_page = _collect_session_events(
                session_record,
                after_seq=next_seq,
                limit=limit,
            )
            items = event_page.get("items") if isinstance(event_page, dict) else []
            if items:
                idle_ticks = 0
                for event in items:
                    next_seq = max(next_seq, int(event.get("seq") or 0))
                    await websocket.send_json(
                        {
                            "type": "session_event",
                            "sessionId": session_id,
                            "seq": next_seq,
                            "event": event,
                        }
                    )
            else:
                idle_ticks += 1

            status = session_record.status or AgentSessionStatus.IDLE
            if status != last_status:
                last_status = status
                await websocket.send_json(
                    {
                        "type": "session_status",
                        "sessionId": session_id,
                        "status": status,
                        "afterSeq": next_seq,
                    }
                )
            elif idle_ticks and idle_ticks % 10 == 0:
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "sessionId": session_id,
                        "status": status,
                        "afterSeq": next_seq,
                    }
                )
            await asyncio.sleep(SESSION_STREAM_ACTIVE_SLEEP_SECONDS if items else SESSION_STREAM_IDLE_SLEEP_SECONDS)
    except WebSocketDisconnect:
        return
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


@agent_router.websocket("/ws/autoagent")
async def autoagent_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        current_user = await require_current_websocket_user(websocket)
    except HTTPException as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json({"error": exc.detail})
        with contextlib.suppress(Exception):
            await websocket.close(code=1008)
        return

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

    req.user_id = _current_user_id(current_user, req.user_id)
    if not req.trace_id:
        req.trace_id = str(uuid.uuid4())
    queue: asyncio.Queue[str] = asyncio.Queue()

    def enqueue(data: str) -> None:
        queue.put_nowait(data)

    worker = _start_background_run(req.trace_id, _run_autoagent(req, enqueue))
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
