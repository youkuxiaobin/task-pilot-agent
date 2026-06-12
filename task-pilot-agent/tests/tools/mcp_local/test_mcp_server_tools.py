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
        "file_edit",
        "file_glob",
        "file_grep",
        "process_command_start",
        "process_command_poll",
        "process_command_write",
        "process_command_stop",
        "process_command_list",
        "code_interpreter",
        "browser_agent",
        "audio_tool",
        "image_tool",
        "video_tool",
        "text_to_image",
        "config_read",
        "config_update",
        "mcp_manager_list_servers",
        "mcp_manager_write_manifest",
        "mcp_manager_add_server",
        "message_send",
        "skill_search",
        "skill_load",
        "skill_install",
        "create_subagent",
        "memory_search",
        "memory_add",
        "memory_delete",
        "get_current_weather",
        "get_weather_forecast",
    ]:
        assert tool_name in tools

    assert "ctx" not in tools["report"].parameters["properties"]
    assert "ctx" not in tools["message_send"].parameters["properties"]
