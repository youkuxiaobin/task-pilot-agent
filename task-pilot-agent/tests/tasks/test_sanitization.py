from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from brain.core.printer import SSEPrinter
from brain.core.sanitization import sanitize_payload


def test_sanitize_payload_redacts_sensitive_keys_and_secret_like_values():
    payload = {
        "api_key": "sk-test-abcdefghijklmnopqrstuvwxyz",
        "nested": {
            "authorization": "Bearer very-secret-token-value",
            "visible": "contains sk-test-anothersecretvalue inside text",
        },
        "items": [
            {"cookie": "session=secret"},
            "Bearer another-secret-token-value",
        ],
        "downloadUrl": "https://files.example.test/report.csv?token=raw-secret&file=report&api_key=raw-key#view",
    }

    sanitized = sanitize_payload(payload)

    assert sanitized["api_key"] == "***"
    assert sanitized["nested"]["authorization"] == "***"
    assert sanitized["nested"]["visible"] == "contains *** inside text"
    assert sanitized["items"][0]["cookie"] == "***"
    assert sanitized["items"][1] == "Bearer ***"
    assert sanitized["downloadUrl"] == "https://files.example.test/report.csv?token=***&file=report&api_key=***#view"


def test_sse_printer_redacts_sensitive_values_before_page_and_event_sink():
    output: List[str] = []
    events: List[Dict[str, Any]] = []
    printer = SSEPrinter(output.append, "request-1", task_id="task-1", event_sink=events.append)

    printer.send(
        "message-1",
        "tool_call",
        {
            "tool": "secret_tool",
            "arguments": {
                "api_key": "sk-test-secretvalue123456",
                "query": "Bearer page-secret-token-value",
            },
        },
        None,
        False,
    )

    event_payload = events[0]["resultMap"]["arguments"]
    assert event_payload["api_key"] == "***"
    assert event_payload["query"] == "Bearer ***"

    streamed_payload = json.loads(output[0].removeprefix("data: ").strip())
    assert streamed_payload["resultMap"]["arguments"]["api_key"] == "***"
    assert streamed_payload["toolCall"]["arguments"]["query"] == "Bearer ***"
