from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

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
