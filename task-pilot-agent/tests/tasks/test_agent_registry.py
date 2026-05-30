from __future__ import annotations

import textwrap

import pytest

from brain.core.agent_registry import AgentRegistry, default_agents_dir


def test_agent_registry_loads_yaml_prompt_and_filters_tools(tmp_path):
    agent_dir = tmp_path / "agents" / "research_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "system_prompt.md").write_text("hello {available_tools}", encoding="utf-8")
    (agent_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: research_agent
            name: Research Agent
            description: Search-focused agent
            mode: react
            system_prompt_file: system_prompt.md
            tools:
              - name: mcp_local:deepsearch
                description: Search tool
              - name: mcp_world:*
            metadata:
              owner: test
            """
        ).strip(),
        encoding="utf-8",
    )

    registry = AgentRegistry(tmp_path / "agents")
    agent = registry.get("research_agent")

    assert agent is not None
    assert agent.system_prompt == "hello {available_tools}"
    assert agent.mode == "react"
    assert agent.metadata["owner"] == "test"
    assert agent.allows_tool("mcp_local:deepsearch")
    assert agent.allows_tool("mcp_world:anything")
    assert not agent.allows_tool("mcp_local:code_interpreter")
    assert registry.filter_tool_names(
        "research_agent",
        ["mcp_local:deepsearch", "mcp_local:code_interpreter", "mcp_world:browser"],
    ) == ["mcp_local:deepsearch", "mcp_world:browser"]


def test_agent_registry_falls_back_to_all_tools_for_unknown_agent(tmp_path):
    registry = AgentRegistry(tmp_path / "missing")

    assert registry.list_agents() == []
    assert registry.filter_tool_names("unknown", ["tool-a", "tool-b"]) == ["tool-a", "tool-b"]


def test_agent_registry_rejects_prompt_path_escape(tmp_path):
    agent_dir = tmp_path / "agents" / "bad_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: bad_agent
            name: Bad Agent
            system_prompt_file: ../outside.md
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="inside its directory"):
        AgentRegistry(tmp_path / "agents")


def test_default_agents_dir_uses_app_config_file(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text("env: test", encoding="utf-8")
    monkeypatch.setenv("APP_CONFIG_FILE", str(config_file))
    monkeypatch.delenv("APP_AGENT_CONFIG_DIR", raising=False)
    monkeypatch.delenv("AGENT_CONFIG_DIR", raising=False)

    assert default_agents_dir() == config_dir / "agents"
