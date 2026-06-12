from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from llm.manager import planner_mgr
from llm.types import LLMMessage, RoleType
from utils.logger import get_logger

logger = get_logger(__name__)

PLAN_TOOL_NAME = "builtin:plan_tool"
CACHE_ATTR = "_task_complexity_decision"
COMPLEXITY_JUDGE_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class TaskComplexityDecision:
    query: str
    needs_plan: bool
    reason: str
    confidence: float = 0.0
    source: str = "llm"


COMPLEXITY_JUDGE_SYSTEM_PROMPT = """You decide whether a user request needs an explicit visible plan before execution.

Return JSON only with this shape:
{"needs_plan": true|false, "reason": "short reason", "confidence": 0.0-1.0}

Use semantic judgment, not keyword matching.

needs_plan should be true when the request is likely to require several coordinated steps, progress tracking, replanning, long-running work, multi-source research, code or file changes with validation, data processing, report generation, or cross-tool workflow.

needs_plan should be false for simple chat, direct Q&A, one-shot fact lookup, weather/current value lookup, short explanation, or one simple tool call where an explicit plan would add noise.

When unsure, prefer false. The plan is only for tasks where showing progress helps the user."""

FALLBACK_LIST_MARKER = re.compile(r"(^|\n)\s*(?:[-*]|\d+[.)])\s+\S+")
FALLBACK_SEPARATORS = (
    "\n",
    "；",
    ";",
)

FINANCIAL_REPORT_SIGNALS = (
    "财报",
    "财年",
    "年报",
    "季报",
    "业绩",
    "营收",
    "收入",
    "利润",
    "净利润",
    "ebita",
    "ebitda",
    "financial report",
    "annual report",
    "earnings",
    "revenue",
    "profit",
)


async def should_use_plan(ctx: Any, query: Optional[str] = None, *, llm_manager: Any = None) -> bool:
    decision = await get_task_complexity_decision(ctx, query=query, llm_manager=llm_manager)
    return decision.needs_plan


async def get_task_complexity_decision(
    ctx: Any,
    query: Optional[str] = None,
    *,
    llm_manager: Any = None,
) -> TaskComplexityDecision:
    raw_query = str(query if query is not None else getattr(ctx, "query", "") or "").strip()
    cache_key = _normalize_query(raw_query)
    if not cache_key:
        return TaskComplexityDecision(query="", needs_plan=False, reason="empty request", source="empty")

    cached = getattr(ctx, CACHE_ATTR, None)
    if isinstance(cached, TaskComplexityDecision) and cached.query == cache_key:
        return cached

    manager = llm_manager or planner_mgr
    try:
        decision = await asyncio.wait_for(
            _judge_complexity_with_model(manager, raw_query, cache_key),
            timeout=COMPLEXITY_JUDGE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("task complexity judge failed, using conservative fallback: %s", exc)
        decision = _fallback_complexity_decision(raw_query, cache_key)

    try:
        setattr(ctx, CACHE_ATTR, decision)
    except Exception:
        pass
    return decision


async def _judge_complexity_with_model(manager: Any, raw_query: str, cache_key: str) -> TaskComplexityDecision:
    messages = [
        LLMMessage(role=RoleType.SYSTEM.value, content=COMPLEXITY_JUDGE_SYSTEM_PROMPT),
        LLMMessage(role=RoleType.USER.value, content=f"User request:\n{raw_query}"),
    ]
    response = await manager.generate_async(
        messages,
        stream=False,
        auto_compress=False,
        reserve_response_tokens=256,
    )
    text = response if isinstance(response, str) else getattr(response, "text", "") or ""
    payload = _parse_json_object(text)
    if not isinstance(payload.get("needs_plan"), bool):
        raise ValueError(f"complexity judge returned invalid payload: {text[:200]}")
    return TaskComplexityDecision(
        query=cache_key,
        needs_plan=bool(payload["needs_plan"]),
        reason=_trim_reason(payload.get("reason") or ""),
        confidence=_safe_float(payload.get("confidence")),
        source="llm",
    )


def _fallback_complexity_decision(raw_query: str, cache_key: str) -> TaskComplexityDecision:
    needs_plan = _looks_structurally_complex(raw_query)
    return TaskComplexityDecision(
        query=cache_key,
        needs_plan=needs_plan,
        reason="model judge unavailable; used structural fallback",
        confidence=0.35,
        source="fallback",
    )


def _looks_structurally_complex(query: str) -> bool:
    text = query.strip()
    if len(text) >= 220:
        return True
    if len(FALLBACK_LIST_MARKER.findall(text)) >= 2:
        return True
    separator_count = sum(text.count(separator) for separator in FALLBACK_SEPARATORS)
    return separator_count >= 3 and len(text) >= 120


def _normalize_query(query: str) -> str:
    return " ".join(str(query or "").strip().split())


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def _trim_reason(value: str, limit: int = 160) -> str:
    text = _normalize_query(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
