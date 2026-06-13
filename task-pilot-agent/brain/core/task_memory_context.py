from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional

from brain.core.context import AgentContext
from brain.core.context_budget import truncate_context_text
from brain.core.sanitization import sanitize_payload


def coerce_score(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def summarize_context_metadata(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    summarized: Dict[str, str] = {}
    for key, item in list(value.items())[:8]:
        summarized[str(key)] = truncate_context_text(item, 120)
    return summarized


def summarize_context_result(item: Any, fallback_source: str) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "id": "",
            "source": fallback_source,
            "score": None,
            "metadata": {},
            "snippet": truncate_context_text(item),
        }

    text_value = (
        item.get("content")
        or item.get("text")
        or item.get("chunk")
        or item.get("page_content")
        or item.get("summary")
        or ""
    )
    source = str(item.get("source") or fallback_source)
    identifier = str(item.get("id") or item.get("document_id") or item.get("doc_id") or "")
    return {
        "id": truncate_context_text(identifier, 128),
        "source": truncate_context_text(source, 128),
        "score": coerce_score(item.get("score")),
        "metadata": summarize_context_metadata(item.get("metadata")),
        "snippet": truncate_context_text(text_value),
    }


def summarize_context_results(items: Any, fallback_source: str, limit: int = 5) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [
        summarize_context_result(item, fallback_source)
        for item in items[:limit]
        if item is not None
    ]


def agent_memory_read_limits(ctx: AgentContext) -> tuple[int, int]:
    memory_config = ctx.agent_memory if isinstance(ctx.agent_memory, dict) else {}
    if "read" not in memory_config:
        return 5, 5
    raw_scopes = memory_config.get("read")
    if raw_scopes in (None, ""):
        return 0, 0
    if isinstance(raw_scopes, str):
        scopes = {raw_scopes}
    elif isinstance(raw_scopes, list):
        scopes = {str(item) for item in raw_scopes}
    else:
        return 0, 0
    normalized = {scope.strip().lower() for scope in scopes if scope.strip()}
    if not normalized:
        return 0, 0
    if "*" in normalized or "all" in normalized:
        return 5, 5

    memory_scopes = {"memory", "task_history", "user_profile", "conversation_history"}
    knowledge_scopes = {"knowledge", "knowledge_base", "rag", "documents", "files"}
    memory_limit = 5 if normalized.intersection(memory_scopes) else 0
    rag_limit = 5 if normalized.intersection(knowledge_scopes) else 0
    return memory_limit, rag_limit


async def load_task_memory_context(
    ctx: AgentContext,
    query: str,
    *,
    memory_manager: Any,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    search_config: Dict[str, Any] = {}
    if hasattr(memory_manager, "get_search_config"):
        try:
            raw_config = memory_manager.get_search_config()
            if isinstance(raw_config, dict):
                search_config = raw_config
        except Exception as exc:  # pragma: no cover - defensive
            search_config = {"warning": exc.__class__.__name__}

    memory_limit, rag_limit = agent_memory_read_limits(ctx)
    payload: Dict[str, Any] = {
        "querySummary": truncate_context_text(query, 160),
        "scope": {
            "userId": ctx.user_id,
            "agentId": ctx.agent_id,
            "runId": ctx.run_id,
        },
        "memoryEnabled": bool(search_config.get("memory_enabled", True)) and memory_limit > 0,
        "ragEnabled": bool(search_config.get("rag_enabled", True)) and rag_limit > 0,
        "memoryCount": 0,
        "ragCount": 0,
        "warningCount": 0,
        "warnings": [],
        "memoryResults": [],
        "ragResults": [],
    }

    if not query.strip():
        payload["warnings"] = [{"component": "memory_context", "reason": "empty_query"}]
        payload["warningCount"] = 1
        ctx.memory_context = sanitize_payload(payload)
        return ctx.memory_context

    if not payload["memoryEnabled"] and not payload["ragEnabled"]:
        ctx.memory_context = sanitize_payload(payload)
        return ctx.memory_context

    try:
        search_fn = getattr(memory_manager, "unified_search_async", None) or memory_manager.unified_search
        raw_result = search_fn(
            query=query,
            user_id=ctx.user_id,
            agent_id=ctx.agent_id,
            run_id=ctx.run_id,
            memory_limit=memory_limit if payload["memoryEnabled"] else 0,
            rag_limit=rag_limit if payload["ragEnabled"] else 0,
        )
        if inspect.isawaitable(raw_result):
            raw_result = await raw_result
        if not isinstance(raw_result, dict):
            raw_result = {}
    except Exception as exc:  # pragma: no cover - should not block tasks
        if logger is not None:
            logger.warning("memory context lookup degraded for request %s: %s", ctx.requestId, exc.__class__.__name__)
        raw_result = {
            "memory_results": [],
            "rag_results": [],
            "warnings": [{"component": "memory_context", "reason": exc.__class__.__name__}],
        }

    memory_results = raw_result.get("memory_results") or []
    rag_results = raw_result.get("rag_results") or []
    warnings = raw_result.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = [{"component": "memory_context", "reason": str(warnings)}]

    payload["memoryCount"] = len(memory_results) if isinstance(memory_results, list) else 0
    payload["ragCount"] = len(rag_results) if isinstance(rag_results, list) else 0
    payload["warningCount"] = len(warnings)
    payload["warnings"] = warnings[:5]
    payload["memoryResults"] = summarize_context_results(memory_results, "memory")
    payload["ragResults"] = summarize_context_results(rag_results, "knowledge")
    sanitized_payload = sanitize_payload(payload)
    ctx.memory_context = sanitized_payload if isinstance(sanitized_payload, dict) else {}
    return ctx.memory_context


def memory_context_status_text(payload: Dict[str, Any]) -> str:
    if payload.get("memoryEnabled") is False and payload.get("ragEnabled") is False:
        return "上下文检索已按 Agent 配置关闭"
    memory_count = int(payload.get("memoryCount") or 0)
    rag_count = int(payload.get("ragCount") or 0)
    warning_count = int(payload.get("warningCount") or 0)
    status = f"上下文已检索：记忆 {memory_count} 条，知识库 {rag_count} 条"
    if warning_count:
        status += f"，降级 {warning_count} 项"
    return status
