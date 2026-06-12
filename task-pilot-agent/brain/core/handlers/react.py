from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from brain.core.agents.ReActAgentImp import ReActAgentImp
from brain.core.agents.summary_agent import SummaryAgent
from brain.core.context import AgentContext
from brain.core.handlers.base import AgentHandlerService
from brain.core.planning_policy import FINANCIAL_REPORT_SIGNALS, PLAN_TOOL_NAME, should_use_plan
from brain.models.requests import AgentRequest
from config.config import agentSettings
from llm.manager import store as prompt_store
from utils.logger import get_logger

logger = get_logger(__name__)


class ReactHandler(AgentHandlerService):
    """
    A ReAct-style orchestrator that now delegates the think-act loop to a dedicated agent.
    """

    def __init__(self) -> None:
        self._prompt = prompt_store.get_prompt("react_system")
        self._max_steps = max(1, getattr(agentSettings.core, "react_max_steps", 6))

    def support(self, ctx: AgentContext, req: AgentRequest) -> bool:
        mode = getattr(req, "mode", None) or getattr(ctx, "mode", None) or ""
        return mode.lower() == "react"

    async def handle(self, ctx: AgentContext, req: AgentRequest) -> None:
        react_agent = ReActAgentImp(ctx, self._prompt, self._max_steps)

        self._emit_phase(ctx, "react", "started", agent="react")
        auto_plan_observation = await self._maybe_create_initial_plan(ctx)
        if auto_plan_observation:
            react_agent.history.append(auto_plan_observation)
            react_agent.evidence.append(str(auto_plan_observation.get("observation") or ""))
        try:
            result = await react_agent.run(ctx.query)
        except Exception as exc:
            self._emit_phase(ctx, "react", "failed", agent="react", error=str(exc))
            raise
        self._emit_phase(
            ctx,
            "react",
            "completed",
            agent="react",
            stepCount=react_agent.current_step,
            finalAnswer=bool(react_agent.final_answer),
        )

        evidence: List[str] = list(react_agent.evidence)
        if not react_agent.final_answer and react_agent.current_step >= react_agent.maxSteps:
            evidence.append("达到最大迭代次数，停止推理。")

        final_answer: Optional[str] = react_agent.final_answer or (result if isinstance(result, str) and result else None)
        if final_answer and not any("最终答案" in item for item in evidence):
            evidence.append(f"最终答案：{final_answer}")

        if await self._should_return_direct_answer(ctx, final_answer):
            self._emit_phase(ctx, "summary", "skipped", agent="summary", reason="simple_direct_answer")
            ctx.printer.send(None, "result", final_answer, None, True)
            return

        summary_step_index = await self._mark_summary_step_running(ctx)
        try:
            summary_agent = SummaryAgent(ctx)
            self._emit_phase(ctx, "summary", "started", agent="summary")
            final_text = await summary_agent.summarize(ctx.query, [], evidence)
        except Exception:
            logger.exception("React summary agent failed for request %s", ctx.requestId)
            await self._mark_summary_step_terminal(
                ctx,
                summary_step_index,
                "failed",
                "总结输出失败",
            )
            self._emit_phase(ctx, "summary", "failed", agent="summary")
            raise
        await self._mark_summary_step_terminal(
            ctx,
            summary_step_index,
            "completed",
            f"总结输出完成，长度 {len(final_text or '')} 字符",
        )
        self._emit_phase(
            ctx,
            "summary",
            "completed",
            agent="summary",
            outputLength=len(final_text or ""),
        )

    def _emit_phase(self, ctx: AgentContext, phase: str, status: str, **payload: Any) -> None:
        ctx.printer.send(
            None,
            "agent_phase",
            {
                "phase": phase,
                "status": status,
                "mode": ctx.mode,
                "agentId": ctx.agent_id,
                **payload,
            },
            None,
            True,
        )

    async def _mark_summary_step_running(self, ctx: AgentContext) -> Optional[int]:
        plan_tool = self._get_plan_tool(ctx)
        if plan_tool is None:
            return None
        plan = plan_tool.plan_dict()
        if not isinstance(plan, dict):
            return None
        statuses = plan.get("step_status")
        if not isinstance(statuses, list):
            return None
        running_index = next(
            (index for index, status in enumerate(statuses, start=1) if status == "running"),
            None,
        )
        next_index = next(
            (index for index, status in enumerate(statuses, start=1) if status == "not_started"),
            None,
        )
        if next_index is None:
            return running_index
        try:
            if running_index is not None and running_index < next_index:
                await plan_tool.execute(
                    {
                        "command": "mark_step",
                        "step_index": running_index,
                        "status": "completed",
                        "note": "已完成资料处理，进入总结输出",
                    }
                )
            await plan_tool.execute(
                {
                    "command": "mark_step",
                    "step_index": next_index,
                    "status": "running",
                    "note": "开始整理并输出最终结果",
                }
            )
            return next_index
        except Exception:
            logger.exception("failed to mark summary plan step running for request %s", ctx.requestId)
            return None

    async def _mark_summary_step_terminal(
        self,
        ctx: AgentContext,
        step_index: Optional[int],
        status: str,
        note: str,
    ) -> None:
        if not step_index:
            return
        plan_tool = self._get_plan_tool(ctx)
        if plan_tool is None:
            return
        try:
            await plan_tool.execute(
                {
                    "command": "mark_step",
                    "step_index": step_index,
                    "status": status,
                    "note": note,
                }
            )
        except Exception:
            logger.exception("failed to mark summary plan step %s for request %s", status, ctx.requestId)

    @staticmethod
    def _get_plan_tool(ctx: AgentContext) -> Any:
        tool_collection = getattr(ctx, "toolCollection", None)
        if not tool_collection:
            return None
        if hasattr(tool_collection, "get_tool"):
            return tool_collection.get_tool(PLAN_TOOL_NAME)
        return getattr(tool_collection, "tool_map", {}).get(PLAN_TOOL_NAME)

    async def _should_return_direct_answer(self, ctx: AgentContext, final_answer: Optional[str]) -> bool:
        if not final_answer:
            return False
        if await should_use_plan(ctx):
            return False
        return not self._has_existing_plan(ctx)

    def _has_existing_plan(self, ctx: AgentContext) -> bool:
        plan_tool = self._get_plan_tool(ctx)
        if plan_tool is None or not hasattr(plan_tool, "plan_dict"):
            return False
        try:
            plan = plan_tool.plan_dict()
        except Exception:
            logger.exception("failed to inspect plan state for request %s", ctx.requestId)
            return False
        return isinstance(plan, dict) and bool(plan.get("steps"))

    async def _maybe_create_initial_plan(self, ctx: AgentContext) -> Optional[Dict[str, Any]]:
        tool_collection = getattr(ctx, "toolCollection", None)
        if not tool_collection or PLAN_TOOL_NAME not in getattr(tool_collection, "tool_map", {}):
            return None
        if not await should_use_plan(ctx):
            return None

        title, steps = _initial_plan_title_and_steps(ctx)
        create_payload = {
            "command": "create",
            "title": title,
            "steps": steps,
            "summary": _truncate(getattr(ctx, "query", "") or "", 240),
            "rationale": "complex_task_auto_plan",
        }
        mark_payload = {
            "command": "mark_step",
            "step_index": 1,
            "status": "running",
            "note": "开始处理复杂请求",
        }

        self._emit_phase(ctx, "planning", "started", agent="react", planner="auto")
        try:
            create_result = await tool_collection.execute(PLAN_TOOL_NAME, create_payload)
            mark_result = await tool_collection.execute(PLAN_TOOL_NAME, mark_payload)
        except Exception as exc:
            logger.exception("auto plan creation failed for request %s", ctx.requestId)
            self._emit_phase(ctx, "planning", "failed", agent="react", planner="auto", error=str(exc))
            return None

        observation = _plan_observation(create_result, mark_result)
        self._emit_phase(
            ctx,
            "planning",
            "completed",
            agent="react",
            planner="auto",
            stepCount=len(steps),
        )
        return {
            "step": 0,
            "thought": "任务较复杂，先创建可回放计划。",
            "action": PLAN_TOOL_NAME,
            "input": create_payload,
            "observation": observation,
        }

def _initial_plan_title_and_steps(ctx: AgentContext) -> Tuple[str, List[str]]:
    query = getattr(ctx, "query", "") or ""
    language = str(getattr(ctx, "language", "") or "").lower()
    english = language.startswith("en")
    lower_query = query.lower()
    if any(token in lower_query for token in ("实现", "开发", "修复", "测试", "代码", "implement", "build", "fix", "test", "code")):
        steps = (
            [
                "Understand the request and affected area.",
                "Make focused changes.",
                "Run meaningful validation.",
                "Summarize the result and remaining risk.",
            ]
            if english
            else ["明确需求和影响范围", "完成必要改动", "运行有效验证", "总结结果和剩余风险"]
        )
    elif any(token in lower_query for token in FINANCIAL_REPORT_SIGNALS):
        steps = (
            [
                "Find and verify authoritative financial report sources.",
                "Read reports and extract key financial metrics.",
                "Analyze segment performance and major changes.",
                "Summarize conclusions, sources, and uncertainty.",
            ]
            if english
            else ["搜索并确认权威财报来源", "读取财报并提取关键财务指标", "分析业务板块表现和主要变化", "整理结论、来源和不确定性"]
        )
    elif any(token in lower_query for token in ("文件", "数据", "表格", "报告", "file", "data", "spreadsheet", "report")):
        steps = (
            [
                "Inspect the available input.",
                "Process and analyze the material.",
                "Create the requested output.",
                "Validate the output and summarize findings.",
            ]
            if english
            else ["检查输入材料", "处理并分析内容", "生成所需结果", "验证结果并总结发现"]
        )
    elif any(token in lower_query for token in ("搜索", "网页", "调研", "研究", "对比", "search", "web", "research", "compare")):
        steps = (
            [
                "Clarify the research target.",
                "Search and read relevant sources.",
                "Compare evidence and resolve conflicts.",
                "Answer with sources and uncertainty.",
            ]
            if english
            else ["明确调研目标", "搜索并阅读相关来源", "对比证据并处理冲突", "给出结论、来源和不确定性"]
        )
    else:
        steps = (
            [
                "Clarify the goal.",
                "Gather needed context.",
                "Work through the solution.",
                "Review and produce the final answer.",
            ]
            if english
            else ["明确目标", "收集必要上下文", "分步骤完成处理", "复核并给出最终答复"]
        )
    title = _truncate(query, 80) or ("Complex request plan" if english else "复杂请求处理计划")
    return title, steps


def _plan_observation(create_result: Any, mark_result: Any) -> str:
    payload = {
        "create": _compact_tool_result(create_result),
        "mark_step": _compact_tool_result(mark_result),
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _compact_tool_result(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return _truncate(value, 500)
    if not isinstance(parsed, dict):
        return parsed
    message = parsed.get("message")
    plan = parsed.get("plan") if isinstance(parsed.get("plan"), dict) else {}
    return {
        "message": message,
        "eventType": plan.get("eventType"),
        "title": plan.get("title"),
        "step_status": plan.get("step_status"),
    }


def _truncate(value: str, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
