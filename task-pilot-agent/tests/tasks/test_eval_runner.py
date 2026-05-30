from __future__ import annotations

from brain.core.agent_registry import AgentConfig, AgentEvalCase
from brain.core.eval_runner import build_eval_run, evaluate_eval_task


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
    assert eval_run.metadata["agentSnapshot"]["id"] == "research_agent"
    assert eval_run.metadata["agentSnapshot"]["name"] == "Research Agent"
    assert eval_run.metadata["expected"] == "Uses search."
    assert eval_run.metadata["tags"] == ["search", "regression"]
    assert eval_run.metadata["evalMetadata"]["priority"] == "high"
    assert eval_run.to_dict()["taskId"] == eval_run.task_id


def test_evaluate_eval_task_checks_status_events_output_and_artifacts():
    task_payload = {
        "taskId": "eval-task",
        "status": "completed",
        "output": "final answer contains citation",
        "metadata": {
            "source": "eval",
            "evalCaseId": "search_case",
            "expected": "Uses search.",
            "evalMetadata": {
                "checks": {
                    "final_status": "completed",
                    "output_contains": ["citation"],
                    "required_event_types": ["tool_call", "task_completed"],
                    "min_artifacts": 1,
                }
            },
        },
    }
    events = [{"eventType": "tool_call"}, {"eventType": "task_completed"}]
    artifacts = [{"artifactId": "artifact-1"}]

    result = evaluate_eval_task(task_payload, events, artifacts).to_dict()

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert [item["name"] for item in result["checks"]] == [
        "final_status",
        "output_contains",
        "required_event_type",
        "required_event_type",
        "min_artifacts",
    ]


def test_evaluate_eval_task_reports_failure_and_manual_review():
    failed = evaluate_eval_task(
        {
            "taskId": "eval-task",
            "status": "failed",
            "output": "no citation",
            "metadata": {
                "source": "eval",
                "evalCaseId": "search_case",
                "expected": "Uses search.",
                "evalMetadata": {"checks": {"final_status": "completed"}},
            },
        },
        [],
        [],
    ).to_dict()
    needs_review = evaluate_eval_task(
        {
            "taskId": "eval-task",
            "status": "completed",
            "output": "answer",
            "metadata": {"source": "eval", "evalCaseId": "manual", "expected": "Human rubric."},
        },
        [],
        [],
    ).to_dict()

    assert failed["status"] == "failed"
    assert failed["passed"] is False
    assert needs_review["status"] == "needs_review"
    assert needs_review["passed"] is None
