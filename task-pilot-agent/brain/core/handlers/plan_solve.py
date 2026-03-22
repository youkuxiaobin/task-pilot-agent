from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple
from langfuse import observe


from brain.core.context import AgentContext
from brain.models.requests import AgentRequest
from brain.core.handlers.base import AgentHandlerService
from brain.core.agents.planning_agent import PlanningAgent
from brain.core.agents.executor_agent import ExecutorAgent
from brain.core.agents.summary_agent import SummaryAgent
from utils.logger import get_logger
from config.config import agentSettings

logger = get_logger(__name__)


class PlanSolveHandler(AgentHandlerService):
    def support(self, ctx: AgentContext, req: AgentRequest) -> bool:
        return ctx.mode == "plans_executor"

    @observe(name="plan_solve_handler")
    async def handle(self, ctx: AgentContext, req: AgentRequest) -> None:
        summary = SummaryAgent(ctx)
        planner = PlanningAgent(ctx)

        logger.info("Plan solve handler triggered for request %s in mode %s", ctx.requestId, ctx.mode)

        evidence_blocks: List[str] = []
        replan_count = 0

        plan_payload, plan_command = await self._call_planning_agent(ctx, ctx.query, planner, None)
        if not isinstance(plan_payload, dict):
            plan_payload = {}

        steps = plan_payload.get("steps", [])
        if not steps:
            result_msg = plan_payload.get("message", "未生成任何计划步骤。")
            logger.info("Planning agent returned empty plan for request %s", ctx.requestId)
            evidence_blocks.append(f"未生成计划， 模型给的结果如下\n{result_msg}")

        step_status = self._ensure_sequence(plan_payload.get("step_status"), len(steps), "not_started")
        finish_steps = self._ensure_step_results(plan_payload.get("finish_steps"), len(steps))
        plan_payload["step_status"] = step_status
        plan_payload["finish_steps"] = finish_steps
        plan_payload["command"] = plan_command

        finished = plan_command == "finish"
        current_index = self._next_actionable_step(step_status)

        while not finished and current_index < len(steps):
            total_steps = len(steps)
            step_text = self._normalize_step(steps[current_index])
            ctx.printer.send(
                None,
                "task",
                f"开始执行步骤 {current_index + 1}/{total_steps}：{step_text}",
                None,
                False,
            )

            executor = ExecutorAgent(ctx)
            try:
                executor_result = await executor.run(step_text)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Executor step failed for request %s", ctx.requestId)
                failure_message = f"步骤 {current_index + 1} 执行失败：{exc}"
                ctx.printer.send(None, "result", failure_message, None, False)
                step_status[current_index] = "failed"
                finish_steps[current_index] = {
                    "status": "failed",
                    "error": failure_message,
                    "tool_outputs": "",
                    "step_text": step_text,
                }
            else:
                tool_outputs = self._extract_tool_outputs(executor_result)
                formatted_result = self._normalize_executor_result(executor_result)
                if formatted_result:
                    evidence_blocks.append(f"step_{current_index + 1}: <step>{step_text}</step>\n <step_answer>{formatted_result}</step_answer>")

                step_status[current_index] = "completed"
                finish_steps[current_index] = {
                    "status": "completed",
                    "tool_outputs": formatted_result or tool_outputs,
                    "step_text": step_text,
                }

            plan_payload["step_status"] = step_status
            plan_payload["finish_steps"] = finish_steps
            next_index = self._next_actionable_step(step_status, current_index + 1)
            if next_index == len(steps):
                plan_payload["next_step"] = ""
            else:
                plan_payload["next_step"] = steps[next_index]
            ctx.printer.send(None, "plan", plan_payload, None, True)


            trigger_replan = self._should_trigger_replan(step_status[current_index])
            if trigger_replan:
                if replan_count >= agentSettings.core.planner_max_replans:
                    logger.warning(
                        "Max replan count %s reached for request %s, skip replanning.",
                        agentSettings.core.planner_max_replans,
                        ctx.requestId,
                    )
                else:
                    plan_payload, plan_command = await self._call_planning_agent(
                        ctx,
                        ctx.query,
                        planner,
                        plan_payload,
                    )
                    if not isinstance(plan_payload, dict):
                        plan_payload = {}
                    steps = plan_payload.get("steps", []) if isinstance(plan_payload, dict) else []
                    step_status = self._ensure_sequence(plan_payload.get("step_status"), len(steps), "not_started")
                   
                    finish_steps = self._ensure_step_results(plan_payload.get("finish_steps"), len(steps))
                    plan_payload["step_status"] = step_status
                    plan_payload["finish_steps"] = finish_steps
                    plan_payload["command"] = plan_command
                    if plan_command == "update":
                        replan_count += 1
                        evidence_blocks = []
                    finished = plan_command == "finish"
                    if finished or not steps:
                        break
                    current_index = self._next_actionable_step(step_status)
                    continue

            current_index = next_index
            logger.debug("Plan progress for request %s: current_index=%s", ctx.requestId, current_index)

        if not evidence_blocks:
            evidence_blocks.append("执行阶段未获取到有效工具结果，请结合任务上下文进行总结。")

        normalized_steps = [self._normalize_step(step) for step in plan_payload.get("steps", [])]
        try:
            await summary.summarize(ctx.query, normalized_steps, evidence_blocks)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Summary agent failed for request %s", ctx.requestId)
            ctx.printer.send(None, "result", f"总结阶段出现异常：{exc}", None, True)

    def _parse_plan(self, plan_output: Any) -> Dict[str, Any]:
        if isinstance(plan_output, dict):
            return plan_output
        if isinstance(plan_output, str):
            try:
                return json.loads(plan_output)
            except json.JSONDecodeError:
                logger.warning("Plan output is not valid JSON for current request")
                return {}
        return {}

    def _normalize_step(self, step: Any) -> str:
        if isinstance(step, str):
            return step.strip()
        try:
            return json.dumps(step, ensure_ascii=False)
        except TypeError:
            return str(step)

    def _normalize_executor_result(self, result: Any) -> str:
        if not result:
            return ""
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                return result.strip()
        else:
            parsed = result

        if isinstance(parsed, list):
            lines: List[str] = []
            for item in parsed:
                if isinstance(item, dict):
                    name = item.get("tool")
                    value = item.get("result")
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False)
                    lines.append(f"工具 {name or ''} 返回：{value}")
                else:
                    lines.append(str(item))
            return "\n".join(lines)
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        return str(parsed)

    def _ensure_sequence(self, value: Any, length: int, default_value: str) -> List[str]:
        if not isinstance(value, list):
            value = [default_value] * length
        if len(value) < length:
            value.extend([default_value] * (length - len(value)))
        elif len(value) > length:
            value = value[:length]
        return [str(item) for item in value]

    def _ensure_step_results(self, value: Any, length: int) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            results: List[Dict[str, Any]] = [{} for _ in range(length)]
        else:
            results = []
            for item in value[:length]:
                if isinstance(item, dict):
                    results.append(item)
                else:
                    results.append({"value": item})
            if len(results) < length:
                results.extend({} for _ in range(length - len(results)))
        if len(results) > length:
            results = results[:length]
        return results

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _should_trigger_replan(
        self,
        status: str,
    ) -> bool:
        if agentSettings.core.planner_replan_each_step:
            return True
        if agentSettings.core.planner_replan_on_failure and status != "completed":
            return True
        return False

    def _next_actionable_step(self, step_status: List[str], start: int = 0) -> int:
        for idx in range(start, len(step_status)):
            if step_status[idx] not in {"completed", "failed"}:
                return idx
        return len(step_status)

    def _build_planning_input(
        self,
        ctx: AgentContext,
        query: str,
        current_plan: Optional[Dict[str, Any]],
    ) -> str:
        payload: Dict[str, Any] = {
            "query": query,
        }
        history_payload = ctx.serialize_messages()
        if history_payload:
            payload["history"] = history_payload
        if current_plan:
            payload["current_plan"] = current_plan
        return json.dumps(payload, ensure_ascii=False)

    @observe(name="call_planning_agent")
    async def _call_planning_agent(
        self,
        ctx: AgentContext,
        query: str,
        planner: PlanningAgent,
        current_plan: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], str]:

        planner_input = self._build_planning_input(ctx, query, current_plan)
        plan_raw = await planner.run(planner_input)
        plan_payload = self._parse_plan(plan_raw)
        if not isinstance(plan_payload, dict):
            plan_payload = {}
        command = str(plan_payload.get("command") or "").strip()
        plan_payload["command"] = command
        if current_plan and command not in {"update", "finish"}:
            preserved_plan = self._clone_plan(current_plan)
            preserved_plan["command"] = command
            ctx.printer.send(None, "plan", preserved_plan, None, True)
            return preserved_plan, command
        ctx.printer.send(None, "plan", plan_payload, None, True)
        return plan_payload, command

    def _clone_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        if not plan:
            return {}
        try:
            return json.loads(json.dumps(plan, ensure_ascii=False))
        except (TypeError, ValueError):
            return dict(plan)

    def _extract_tool_outputs(self, result: Any) -> str:
        return str(result)
