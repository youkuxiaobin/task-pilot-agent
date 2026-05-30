from __future__ import annotations

from brain.core.agent_registry import AgentConfig, AgentEvalCase
from brain.core.eval_runner import build_eval_run


def test_build_eval_run_turns_case_into_task_spec():
    agent = AgentConfig(
        id="research_agent",
        name="Research Agent",
        mode="react",
    )
    case = AgentEvalCase(
        id="search_case",
        name="Search Case",
        input="Find and summarize current info.",
        expected="Uses search.",
        tags=["search", "regression"],
        metadata={"priority": "high"},
    )

    eval_run = build_eval_run(
        agent,
        case,
        user_id="tester",
        conversation_id="conversation-1",
        output_style="gaia",
    )

    assert eval_run.task_id
    assert eval_run.trace_id == eval_run.task_id
    assert eval_run.conversation_id == "conversation-1"
    assert eval_run.user_id == "tester"
    assert eval_run.agent_id == "research_agent"
    assert eval_run.case_id == "search_case"
    assert eval_run.input_text == "Find and summarize current info."
    assert eval_run.mode == "react"
    assert eval_run.output_style == "gaia"
    assert eval_run.metadata["source"] == "eval"
    assert eval_run.metadata["expected"] == "Uses search."
    assert eval_run.metadata["tags"] == ["search", "regression"]
    assert eval_run.metadata["evalMetadata"]["priority"] == "high"
    assert eval_run.to_dict()["taskId"] == eval_run.task_id
