from __future__ import annotations
import json
import time
from typing import Any, Dict, Optional, Callable


class Printer:
    def send(self, message_id: Optional[str], message_type: str, message: Any, digital_employee: Optional[str], is_final: bool) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def update_agent_type(self, agent_type: int) -> None:
        raise NotImplementedError


class SSEPrinter(Printer):
    def __init__(self, enqueue: Callable[[str], None], request_id: str):
        self.enqueue = enqueue
        self.request_id = request_id

    def send(self, message_id: Optional[str], message_type: str, message: Any, digital_employee: Optional[str], is_final: bool) -> None:
        if not message_id:
            message_id = str(int(time.time() * 1000))
        data: Dict[str, Any] = {
            "requestId": self.request_id,
            "messageId": message_id,
            "messageType": message_type,
            "messageTime": str(int(time.time() * 1000)),
            "resultMap": {},
            "finish": message_type == "result",
            "isFinal": is_final,
        }

        if digital_employee:
            data["digitalEmployee"] = digital_employee

    
        if message_type in {"tool_thought", "plan_thought"}:
            key = "toolThought" if message_type == "tool_thought" else "planThought"
            data[key] = message
        elif message_type in {"task", "notifications"}:
            data["task"] = str(message)
        elif message_type == "task_summary":
            if isinstance(message, dict):
                data["resultMap"] = message
                data["taskSummary"] = str(message.get("taskSummary", ""))
        elif message_type == "plan":
            data["plan"] = message if isinstance(message, dict) else {}
        elif message_type in {"tool_call", "tool_result", "stream", "browser", "code", "html", "markdown", "ppt", "file", "knowledge", "deep_search"}:
            # unify as resultMap payload
            payload = message if isinstance(message, dict) else json.loads(json.dumps(message, default=str))
            data["resultMap"] = payload
            if message_type == "tool_call":
                data["toolCall"] = payload
        elif message_type in {"agent_stream", "result"}:
            if isinstance(message, str):
                data["result"] = message
            else:
                payload = json.loads(json.dumps(message, default=str))
                data["resultMap"] = payload
                data["result"] = str(payload.get("taskSummary", ""))

        self.enqueue("data: " + json.dumps(data, ensure_ascii=False) + "\n\n")

    def close(self) -> None:
        self.enqueue("data: [DONE]\n\n")

    def update_agent_type(self, agent_type: int) -> None:
        self.agent_type = agent_type
