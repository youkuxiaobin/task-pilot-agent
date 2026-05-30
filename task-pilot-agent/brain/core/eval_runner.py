from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from brain.core.agent_registry import AgentConfig, AgentEvalCase


@dataclass(frozen=True)
class AgentEvalRun:
    task_id: str
    trace_id: str
    conversation_id: str
    user_id: str
    agent_id: str
    case_id: str
    input_text: str
    mode: str
    output_style: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "taskId": self.task_id,
            "traceId": self.trace_id,
            "conversationId": self.conversation_id,
            "userId": self.user_id,
            "agentId": self.agent_id,
            "caseId": self.case_id,
            "input": self.input_text,
            "mode": self.mode,
            "outputStyle": self.output_style,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class AgentEvalResult:
    task_id: str
    case_id: str
    status: str
    passed: Optional[bool]
    expected: str
    checks: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "taskId": self.task_id,
            "caseId": self.case_id,
            "status": self.status,
            "passed": self.passed,
            "expected": self.expected,
            "checks": self.checks,
        }


def build_eval_run(
    agent: AgentConfig,
    case: AgentEvalCase,
    *,
    user_id: str = "eval-runner",
    conversation_id: Optional[str] = None,
    output_style: Optional[str] = None,
) -> AgentEvalRun:
    task_id = str(uuid.uuid4())
    metadata = {
        "source": "eval",
        "evalCaseId": case.id,
        "evalCaseName": case.name,
        "agentSnapshot": agent.to_runtime_snapshot(),
        "expected": case.expected,
        "tags": case.tags,
        "evalMetadata": case.metadata,
    }
    return AgentEvalRun(
        task_id=task_id,
        trace_id=task_id,
        conversation_id=conversation_id or f"eval-{case.id}-{task_id}",
        user_id=user_id,
        agent_id=agent.id,
        case_id=case.id,
        input_text=case.input,
        mode=agent.mode or "react",
        output_style=output_style or "markdown",
        metadata=metadata,
    )


def evaluate_eval_task(
    task_payload: Dict[str, Any],
    event_payloads: List[Dict[str, Any]],
    artifact_payloads: List[Dict[str, Any]],
) -> AgentEvalResult:
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else {}
    eval_metadata = metadata.get("evalMetadata") if isinstance(metadata.get("evalMetadata"), dict) else {}
    checks_config = eval_metadata.get("checks") if isinstance(eval_metadata.get("checks"), dict) else {}
    case_id = str(metadata.get("evalCaseId") or "")
    expected = str(metadata.get("expected") or "")
    checks: List[Dict[str, Any]] = []

    required_status = _first_text(
        checks_config.get("final_status"),
        checks_config.get("status"),
        eval_metadata.get("final_status"),
    )
    if required_status:
        _append_check(
            checks,
            "final_status",
            task_payload.get("status") == required_status,
            required_status,
            task_payload.get("status"),
        )

    output = str(task_payload.get("output") or "")
    for needle in _as_text_list(checks_config.get("output_contains")):
        _append_check(checks, "output_contains", needle in output, needle, output)
    for needle in _as_text_list(checks_config.get("output_not_contains")):
        _append_check(checks, "output_not_contains", needle not in output, f"not {needle}", output)

    event_types = [str(item.get("eventType") or "") for item in event_payloads if isinstance(item, dict)]
    for event_type in _as_text_list(checks_config.get("required_event_types")):
        _append_check(checks, "required_event_type", event_type in event_types, event_type, event_types)

    min_artifacts = checks_config.get("min_artifacts")
    if min_artifacts is not None:
        try:
            minimum = int(min_artifacts)
        except (TypeError, ValueError):
            minimum = 0
        _append_check(checks, "min_artifacts", len(artifact_payloads) >= minimum, minimum, len(artifact_payloads))
    if checks_config.get("require_artifact") is True:
        _append_check(checks, "require_artifact", bool(artifact_payloads), True, len(artifact_payloads))

    if not checks:
        return AgentEvalResult(
            task_id=str(task_payload.get("taskId") or task_payload.get("task_id") or ""),
            case_id=case_id,
            status="needs_review",
            passed=None,
            expected=expected,
            checks=[],
        )

    passed = all(item["passed"] for item in checks)
    return AgentEvalResult(
        task_id=str(task_payload.get("taskId") or task_payload.get("task_id") or ""),
        case_id=case_id,
        status="passed" if passed else "failed",
        passed=passed,
        expected=expected,
        checks=checks,
    )


def _append_check(checks: List[Dict[str, Any]], name: str, passed: bool, expected: Any, actual: Any) -> None:
    checks.append(
        {
            "name": name,
            "passed": bool(passed),
            "expected": expected,
            "actual": actual,
        }
    )


def _as_text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    text = str(value)
    return [text] if text else []


def _first_text(*values: Any) -> str:
    for value in values:
        if value:
            return str(value)
    return ""
