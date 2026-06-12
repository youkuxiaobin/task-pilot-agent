from __future__ import annotations

import asyncio
from types import SimpleNamespace

from brain.core.planning_policy import get_task_complexity_decision, should_use_plan


class FakeComplexityManager:
    def __init__(self, text: str, *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    async def generate_async(self, *_args, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("judge unavailable")
        return SimpleNamespace(text=self.text)


def test_complexity_judge_uses_model_json_and_caches_result():
    ctx = SimpleNamespace(query="用户给出的复杂任务")
    manager = FakeComplexityManager(
        '```json\n{"needs_plan": true, "reason": "requires multiple coordinated steps", "confidence": 0.9}\n```'
    )

    first = asyncio.run(get_task_complexity_decision(ctx, llm_manager=manager))
    second = asyncio.run(get_task_complexity_decision(ctx, llm_manager=manager))

    assert first.needs_plan is True
    assert first.source == "llm"
    assert first.confidence == 0.9
    assert second is first
    assert manager.calls == 1


def test_complexity_judge_can_return_no_plan_for_simple_request():
    ctx = SimpleNamespace(query="北京天气")
    manager = FakeComplexityManager('{"needs_plan": false, "reason": "single lookup", "confidence": 0.8}')

    assert asyncio.run(should_use_plan(ctx, llm_manager=manager)) is False
    assert manager.calls == 1


def test_complexity_judge_fallback_uses_structure_when_model_fails():
    ctx = SimpleNamespace(
        query="""请处理下面事项：
- 收集资料
- 分析差异
- 输出报告"""
    )
    manager = FakeComplexityManager("", fail=True)

    decision = asyncio.run(get_task_complexity_decision(ctx, llm_manager=manager))

    assert decision.needs_plan is True
    assert decision.source == "fallback"
    assert manager.calls == 1
