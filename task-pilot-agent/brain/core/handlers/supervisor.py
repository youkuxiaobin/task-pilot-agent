from __future__ import annotations

from typing import Awaitable, Callable, Iterable, Optional

from brain.core.agent_registry import AgentConfig, AgentRegistry
from brain.core.context import AgentContext
from brain.core.handlers.base import AgentHandlerService
from brain.core.handlers.plan_solve import PlanSolveHandler
from brain.core.handlers.react import ReactHandler
from brain.core.tasks import TaskStore
from brain.core.tools.collection import ToolCollection
from brain.models.requests import AgentRequest
from utils.logger import get_logger

logger = get_logger(__name__)

ToolCollectionBuilder = Callable[[AgentContext], Awaitable[ToolCollection]]


class SupervisorHandler(AgentHandlerService):
    """Route a supervisor agent run to one allowed worker agent."""

    def __init__(
        self,
        registry: AgentRegistry,
        tool_collection_builder: ToolCollectionBuilder,
        worker_handlers: Optional[Iterable[AgentHandlerService]] = None,
    ) -> None:
        self.registry = registry
        self.tool_collection_builder = tool_collection_builder
        self.worker_handlers = list(worker_handlers or [PlanSolveHandler(), ReactHandler()])

    def support(self, ctx: AgentContext, req: AgentRequest) -> bool:
        agent = self.registry.get(ctx.agent_id)
        mode = (getattr(req, "mode", None) or getattr(ctx, "mode", "") or "").lower()
        return bool(agent and agent.type == "supervisor") or mode == "supervisor"

    async def handle(self, ctx: AgentContext, req: AgentRequest) -> None:
        supervisor = self.registry.get(ctx.agent_id)
        if supervisor is None:
            raise ValueError(f"supervisor agent not found: {ctx.agent_id}")

        selection = self.registry.select_agent_for_task(supervisor.id, ctx.query)
        if selection is None:
            raise ValueError(f"supervisor `{supervisor.id}` has no selectable handoff agents")

        target = self.registry.get(selection.agent_id)
        if target is None:
            raise ValueError(f"selected agent not found: {selection.agent_id}")

        self._record_event(
            ctx,
            "agent_selected",
            {
                **selection.to_dict(),
                "agentName": target.name,
                "agentDescription": target.description,
                "agentSnapshot": target.to_runtime_snapshot(
                    approved_tools=getattr(ctx, "approved_tools", None),
                ),
            },
        )
        self._send_task(ctx, f"Supervisor 已选择 Agent：{target.name or target.id}")

        original_agent_id = ctx.agent_id
        original_mode = ctx.mode
        original_prompt = ctx.agent_system_prompt
        original_tools = ctx.toolCollection

        delegate_req = _copy_request(req)
        _set_request_attr(delegate_req, "agent_id", target.id)
        _set_request_attr(delegate_req, "mode", target.mode or "react")

        try:
            ctx.agent_id = target.id
            ctx.mode = target.mode or "react"
            ctx.agent_system_prompt = target.system_prompt
            ctx.toolCollection = await self.tool_collection_builder(ctx)
            worker = self._select_worker(ctx, delegate_req)
            if worker is None:
                raise ValueError(f"no worker handler for selected agent `{target.id}`")

            self._record_event(ctx, "agent_started", self._lifecycle_payload(target, "running"))
            self._send_task(ctx, f"Agent 已启动：{target.name or target.id}")
            await worker.handle(ctx, delegate_req)
            self._record_event(ctx, "agent_completed", self._lifecycle_payload(target, "completed"))
            self._send_task(ctx, f"Agent 已完成：{target.name or target.id}")
        except Exception as exc:
            self._record_event(ctx, "agent_failed", {**self._lifecycle_payload(target, "failed"), "error": str(exc)})
            self._send_task(ctx, f"Agent 失败：{target.name or target.id} · {exc}")
            raise
        finally:
            ctx.agent_id = original_agent_id
            ctx.mode = original_mode
            ctx.agent_system_prompt = original_prompt
            ctx.toolCollection = original_tools

    def _select_worker(self, ctx: AgentContext, req: AgentRequest) -> Optional[AgentHandlerService]:
        for handler in self.worker_handlers:
            if handler.support(ctx, req):
                return handler
        return None

    def _record_event(self, ctx: AgentContext, event_type: str, payload: dict) -> None:
        task_id = getattr(ctx, "task_id", None)
        if not task_id:
            return
        try:
            TaskStore().add_event(
                task_id,
                event_type,
                payload,
                trace_id=getattr(ctx, "requestId", None),
                source="supervisor",
            )
        except Exception:
            logger.exception("failed to persist supervisor event %s for task %s", event_type, task_id)

    @staticmethod
    def _send_task(ctx: AgentContext, text: str) -> None:
        printer = getattr(ctx, "printer", None)
        if printer is not None:
            printer.send(None, "task", text, None, False)

    @staticmethod
    def _lifecycle_payload(agent: AgentConfig, status: str) -> dict:
        return {
            "agentId": agent.id,
            "agentConfigId": agent.id,
            "agentName": agent.name,
            "agentDescription": agent.description,
            "agentType": agent.type,
            "mode": agent.mode,
            "capabilities": list(agent.capabilities),
            "agentSnapshot": agent.to_runtime_snapshot(),
            "status": status,
        }


def _copy_request(req: AgentRequest) -> AgentRequest:
    if hasattr(req, "model_copy"):
        return req.model_copy(deep=True)  # type: ignore[attr-defined,return-value]
    return req.copy(deep=True)  # type: ignore[attr-defined,return-value]


def _set_request_attr(req: AgentRequest, name: str, value: str) -> None:
    try:
        setattr(req, name, value)
    except (AttributeError, TypeError, ValueError):
        return
