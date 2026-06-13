from __future__ import annotations
import json
import time
from typing import Any, Dict, Optional, Callable

from brain.core.run_events import EVENT_TYPE_ALIASES, PRINTER_PLAN_EVENT_TYPES
from brain.core.sanitization import sanitize_payload

PLAN_EVENT_TYPES = PRINTER_PLAN_EVENT_TYPES


class Printer:
    def send(self, message_id: Optional[str], message_type: str, message: Any, digital_employee: Optional[str], is_final: bool) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def update_agent_type(self, agent_type: int) -> None:
        raise NotImplementedError


class SSEPrinter(Printer):
    def __init__(
        self,
        enqueue: Callable[[str], None],
        request_id: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        seq_provider: Optional[Callable[[], Optional[int]]] = None,
    ):
        self.enqueue = enqueue
        self.request_id = request_id
        self.task_id = task_id
        self.session_id = session_id
        self.run_id = run_id or task_id
        self.event_sink = event_sink
        self.seq_provider = seq_provider

    def send(self, message_id: Optional[str], message_type: str, message: Any, digital_employee: Optional[str], is_final: bool) -> None:
        if not message_id:
            message_id = str(int(time.time() * 1000))
        data: Dict[str, Any] = {
            "requestId": self.request_id,
            "messageId": message_id,
            "messageType": message_type,
            "type": EVENT_TYPE_ALIASES.get(message_type, message_type),
            "messageTime": str(int(time.time() * 1000)),
            "resultMap": {},
            "finish": message_type == "result",
            "isFinal": is_final,
        }
        if self.task_id:
            data["taskId"] = self.task_id
        if self.run_id:
            data["runId"] = self.run_id
        if self.session_id:
            data["sessionId"] = self.session_id
        if self.seq_provider:
            seq = self.seq_provider()
            if seq is not None:
                data["seq"] = seq

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
        elif message_type in PLAN_EVENT_TYPES:
            payload = message if isinstance(message, dict) else {}
            if message_type == "todo_list_updated":
                data["todoList"] = payload
            else:
                data["plan"] = payload
            data["resultMap"] = payload
        elif message_type in {"tool_call", "tool_result", "stream", "browser", "code", "html", "markdown", "ppt", "file", "knowledge", "deep_search", "agent_phase"}:
            # unify as resultMap payload
            payload = message if isinstance(message, dict) else json.loads(json.dumps(message, default=str))
            data["resultMap"] = payload
            if message_type == "tool_call":
                data["toolCall"] = payload
            if message_type == "tool_result":
                if isinstance(payload.get("type"), str):
                    data["type"] = payload["type"]
                elif payload.get("failed") is True:
                    data["type"] = "tool_call_failed"
        elif message_type in {"agent_stream", "result"}:
            if isinstance(message, str):
                data["result"] = message
            else:
                payload = json.loads(json.dumps(message, default=str))
                data["resultMap"] = payload
                data["result"] = str(payload.get("taskSummary", ""))

        sanitized_data = sanitize_payload(data)
        if self.event_sink:
            self.event_sink(sanitized_data)
        self.enqueue("data: " + json.dumps(sanitized_data, ensure_ascii=False) + "\n\n")

    def close(self) -> None:
        self.enqueue("data: [DONE]\n\n")

    def update_agent_type(self, agent_type: int) -> None:
        self.agent_type = agent_type
