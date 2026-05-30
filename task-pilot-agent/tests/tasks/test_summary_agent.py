from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_summary_agent_records_final_question_and_answer(monkeypatch):
    from brain.core.agents import summary_agent as summary_module
    from brain.core.context import AgentContext

    class FakePromptStore:
        def get_prompt(self, _key):
            return "task={task}\nhistory={tool_call_history}\ntime={current_time}"

    class FakeLLM:
        async def stream_generate_async(self, messages, *, chunk_callback, **_kwargs):
            self.messages = messages
            chunk_callback("stored ")
            chunk_callback("answer")
            return SimpleNamespace(text="")

    class FakePrinter:
        def __init__(self):
            self.events = []

        def send(self, message_id, message_type, message, digital_employee, is_final):
            self.events.append(
                {
                    "message_id": message_id,
                    "message_type": message_type,
                    "message": message,
                    "digital_employee": digital_employee,
                    "is_final": is_final,
                }
            )

    class FakeMemory:
        def __init__(self):
            self.messages = []

        def add_message(self, **kwargs):
            self.messages.append(kwargs)
            return kwargs.get("trace_id") or "trace"

    monkeypatch.setattr(summary_module, "prompt_store", FakePromptStore())
    printer = FakePrinter()
    memory = FakeMemory()
    ctx = AgentContext(
        requestId="summary-trace",
        sessionId="summary-session",
        user_id="user-1",
        agent_id="agent-1",
        run_id="conversation-1",
        query="original question",
        task=None,
        printer=printer,
        toolCollection=None,
        dateInfo="2026-05-30",
        outputStyle="markdown",
    )

    agent = summary_module.SummaryAgent(ctx)
    agent.llm = FakeLLM()
    agent.memory = memory

    result = asyncio.run(agent.summarize("original question", ["step one"], ["evidence one"]))

    assert result == "stored answer"
    assert [item["message"] for item in printer.events] == ["stored ", "answer"]
    assert [(item["role"], item["content"]) for item in memory.messages] == [
        ("user", "original question"),
        ("assistant", "stored answer"),
    ]
    assert all(item["conversation_id"] == "conversation-1" for item in memory.messages)
    assert all(item["agent_id"] == "agent-1" for item in memory.messages)
    assert all(item["type_name"] == "summary" for item in memory.messages)
    assert all(item["trace_id"] == "summary-trace" for item in memory.messages)
