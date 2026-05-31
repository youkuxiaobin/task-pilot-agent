from __future__ import annotations

import asyncio


def test_streamable_mcp_ready_check_waits_for_tool_list(monkeypatch):
    import mcp.client.session as session_module
    import mcp.client.streamable_http as streamable_http_module
    import mcp_process

    calls: list[str] = []

    class FakeStreamableClient:
        async def __aenter__(self):
            calls.append("client_enter")
            return "read", "write", lambda: "session-1"

        async def __aexit__(self, exc_type, exc, tb):
            calls.append("client_exit")

    class FakeClientSession:
        def __init__(self, read, write):
            assert read == "read"
            assert write == "write"

        async def __aenter__(self):
            calls.append("session_enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            calls.append("session_exit")

        async def initialize(self):
            calls.append("initialize")

        async def list_tools(self):
            calls.append("list_tools")

    monkeypatch.setattr(
        streamable_http_module,
        "streamablehttp_client",
        lambda url: FakeStreamableClient(),
    )
    monkeypatch.setattr(session_module, "ClientSession", FakeClientSession)

    session_id = asyncio.run(
        mcp_process._initialize_mcp_session_once("http://127.0.0.1:9009/mcp", "streamable-http")
    )

    assert session_id == "session-1"
    assert calls == [
        "client_enter",
        "session_enter",
        "initialize",
        "list_tools",
        "session_exit",
        "client_exit",
    ]

