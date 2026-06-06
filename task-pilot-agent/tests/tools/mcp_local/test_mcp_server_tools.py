from __future__ import annotations


def test_mcp_local_registers_general_agent_tools():
    from tools.mcp_local import mcp_server

    tools = mcp_server.mcp._tool_manager._tools

    for tool_name in [
        "web_search",
        "fetch_url",
        "web_reader",
        "report",
        "file_read",
        "file_write",
        "code_interpreter",
        "audio_tool",
        "image_tool",
        "video_tool",
        "get_current_weather",
        "get_weather_forecast",
    ]:
        assert tool_name in tools

    assert "ctx" not in tools["report"].parameters["properties"]
