from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from brain.core.agent_registry import AgentRegistry, default_agents_dir


APP_ROOT = Path(__file__).resolve().parents[2]


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
    (agent_dir / "evals.yaml").write_text(
        textwrap.dedent(
            """
            cases:
              - id: search_regression
                name: Search Regression
                input: Find current information and summarize it.
                expected: Uses search before answering.
                tags: [search, regression]
                metadata:
                  priority: high
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
    assert len(agent.evals) == 1
    assert agent.evals[0].id == "search_regression"
    assert agent.evals[0].tags == ["search", "regression"]
    assert agent.evals[0].metadata["priority"] == "high"
    assert registry.list_evals("research_agent")[0].name == "Search Regression"
    assert agent.allows_tool("mcp_local:deepsearch")
    assert agent.allows_tool("mcp_world:anything")
    assert not agent.allows_tool("mcp_local:code_interpreter")
    assert registry.filter_tool_names(
        "research_agent",
        ["mcp_local:deepsearch", "mcp_local:code_interpreter", "mcp_world:browser"],
    ) == ["mcp_local:deepsearch", "mcp_world:browser"]


def test_agent_system_prompt_is_used_by_plan_solve_agents():
    context_source = (APP_ROOT / "brain" / "core" / "context.py").read_text(encoding="utf-8")
    planning_source = (APP_ROOT / "brain" / "core" / "agents" / "planning_agent.py").read_text(encoding="utf-8")
    executor_source = (APP_ROOT / "brain" / "core" / "agents" / "executor_agent.py").read_text(encoding="utf-8")
    summary_source = (APP_ROOT / "brain" / "core" / "agents" / "summary_agent.py").read_text(encoding="utf-8")

    assert "def compose_system_prompt" in context_source
    assert "compose_system_prompt(prompt)" in planning_source
    assert "compose_system_prompt(prompt)" in executor_source
    assert "agent_system_prompt" in summary_source
    assert "RoleType.SYSTEM" in summary_source


def test_agent_registry_blocks_high_risk_tools_until_enabled(tmp_path, monkeypatch):
    agent_dir = tmp_path / "agents" / "safe_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: safe_agent
            name: Safe Agent
            tools:
              - name: mcp_local:code_interpreter
                policy:
                  risk: high
                  requires_explicit_enable: true
              - name: mcp_local:*
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.delenv("APP_ALLOW_HIGH_RISK_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_HIGH_RISK_TOOLS", raising=False)

    agent = AgentRegistry(tmp_path / "agents").get("safe_agent")

    assert agent is not None
    assert not agent.allows_tool("mcp_local:code_interpreter")
    assert agent.tool_block_reason("mcp_local:code_interpreter") == "high_risk_requires_enable"
    assert agent.allows_tool("mcp_local:deepsearch")
    assert agent.to_dict()["tools"][0]["allowed"] is False
    assert agent.to_dict()["tools"][0]["blockReason"] == "high_risk_requires_enable"

    monkeypatch.setenv("ALLOW_HIGH_RISK_TOOLS", "true")

    assert agent.allows_tool("mcp_local:code_interpreter")
    assert agent.tool_block_reason("mcp_local:code_interpreter") == ""
    assert agent.to_dict()["tools"][0]["allowed"] is True
    assert agent.to_dict()["tools"][0]["blockReason"] == ""


def test_agent_registry_loads_structured_agent_yaml_and_denied_tools(tmp_path):
    agents_root = tmp_path / "agents"
    target_dir = agents_root / "report_agent"
    target_dir.mkdir(parents=True)
    (target_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: report_agent
            name: Report Agent
            type: react_worker
            system_prompt: Report safely.
            """
        ).strip(),
        encoding="utf-8",
    )

    agent_dir = agents_root / "search_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "system_prompt.md").write_text("search safely", encoding="utf-8")
    (agent_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: search_agent
            name: Search Agent
            type: react_worker
            mode: react
            system_prompt_file: system_prompt.md
            model:
              context: executor
              temperature: 0.2
              max_steps: 5
            capabilities: [search, research]
            tools:
              allowed:
                - id: mcp_local:deepsearch
                  alias: 深度搜索
                  purpose: 搜索公开网页和资料。
                  when_to_use: 需要最新信息时使用。
                  input_schema:
                    type: object
                    properties:
                      query:
                        type: string
                    required: [query]
                  output_schema:
                    type: object
                    properties:
                      summary:
                        type: string
                  risk_level: low
                  timeout_seconds: 120
              denied:
                - mcp_local:shell
            handoffs:
              allowed:
                - report_agent
            memory:
              read: [task_history]
              write: [research_findings]
            permissions:
              can_write_files: false
              can_run_shell: false
              can_access_network: true
            output:
              format: markdown
              required_sections: [结论, 来源]
            """
        ).strip(),
        encoding="utf-8",
    )

    registry = AgentRegistry(agents_root)
    agent = registry.get("search_agent")

    assert agent is not None
    assert agent.type == "react_worker"
    assert agent.model["context"] == "executor"
    assert agent.capabilities == ["search", "research"]
    assert agent.tools[0].name == "mcp_local:deepsearch"
    assert agent.tools[0].alias == "深度搜索"
    assert agent.tools[0].purpose == "搜索公开网页和资料。"
    assert agent.tools[0].when_to_use == "需要最新信息时使用。"
    assert agent.tools[0].timeout_seconds == 120
    assert agent.tools[0].input_schema["required"] == ["query"]
    assert agent.tools[0].output_schema["properties"]["summary"]["type"] == "string"
    assert agent.tools[0].policy["risk"] == "low"
    assert agent.denied_tools == ["mcp_local:shell"]
    assert agent.handoffs["allowed"] == ["report_agent"]
    assert agent.memory["read"] == ["task_history"]
    assert agent.permissions["can_access_network"] is True
    assert agent.output["required_sections"] == ["结论", "来源"]
    assert agent.allows_tool("mcp_local:deepsearch")
    assert not agent.allows_tool("mcp_local:shell")
    assert agent.tool_block_reason("mcp_local:shell") == "denied_tools"
    payload = agent.to_dict()
    assert payload["type"] == "react_worker"
    assert payload["tools"][0]["timeoutSeconds"] == 120
    assert payload["tools"][0]["inputSchema"]["properties"]["query"]["type"] == "string"
    assert payload["tools"][0]["outputSchema"]["properties"]["summary"]["type"] == "string"
    assert payload["tools"][0]["allowed"] is True
    assert payload["deniedTools"] == ["mcp_local:shell"]
    assert payload["permissions"]["can_run_shell"] is False


def test_agent_registry_permissions_filter_risky_tool_categories(tmp_path):
    agent_dir = tmp_path / "agents" / "offline_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: offline_agent
            name: Offline Agent
            tools:
              - name: mcp_local:*
              - name: mcp_world:*
            permissions:
              can_write_files: false
              can_run_shell: false
              can_access_network: false
            """
        ).strip(),
        encoding="utf-8",
    )

    agent = AgentRegistry(tmp_path / "agents").get("offline_agent")

    assert agent is not None
    assert agent.allows_tool("mcp_local:calculator")
    assert not agent.allows_tool("mcp_local:shell")
    assert not agent.allows_tool("mcp_local:deepsearch")
    assert not agent.allows_tool("mcp_world:browser")
    assert not agent.allows_tool("mcp_local:file_write")
    assert not agent.allows_tool("mcp_local:report")
    assert agent.tool_block_reason("mcp_local:shell") == "permission_can_run_shell"
    assert agent.tool_block_reason("mcp_local:deepsearch") == "permission_can_access_network"
    assert agent.tool_block_reason("mcp_local:file_write") == "permission_can_write_files"


def test_agent_registry_rejects_missing_handoff_target(tmp_path):
    agent_dir = tmp_path / "agents" / "router_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: router_agent
            name: Router Agent
            handoffs:
              allowed:
                - missing_agent
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="handoff target not found"):
        AgentRegistry(tmp_path / "agents")


def test_agent_registry_supervisor_selects_allowed_agent_by_task_terms(tmp_path):
    agents_root = tmp_path / "agents"
    supervisor_dir = agents_root / "supervisor"
    research_dir = agents_root / "research_agent"
    report_dir = agents_root / "report_agent"
    supervisor_dir.mkdir(parents=True)
    research_dir.mkdir()
    report_dir.mkdir()
    (supervisor_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: supervisor
            name: Supervisor
            type: supervisor
            handoffs:
              allowed:
                - report_agent
                - research_agent
            """
        ).strip(),
        encoding="utf-8",
    )
    (research_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: research_agent
            name: Research Agent
            description: Search public web sources and summarize findings.
            capabilities: [search, research]
            """
        ).strip(),
        encoding="utf-8",
    )
    (report_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: report_agent
            name: Report Agent
            description: Draft formatted reports.
            capabilities: [report, writing]
            """
        ).strip(),
        encoding="utf-8",
    )

    selection = AgentRegistry(agents_root).select_agent_for_task(
        "supervisor",
        "Search web sources for the latest release notes.",
    )

    assert selection is not None
    assert selection.agent_id == "research_agent"
    assert selection.supervisor_id == "supervisor"
    assert selection.score > 0
    assert "search" in selection.matched_terms


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


def test_agent_registry_rejects_invalid_eval_case(tmp_path):
    agent_dir = tmp_path / "agents" / "bad_eval_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text("id: bad_eval_agent\nname: Bad Eval\n", encoding="utf-8")
    (agent_dir / "evals.yaml").write_text("cases:\n  - id: bad\n", encoding="utf-8")

    with pytest.raises(ValueError, match="input is required"):
        AgentRegistry(tmp_path / "agents")


def test_agent_registry_rejects_id_directory_mismatch(tmp_path):
    agent_dir = tmp_path / "agents" / "actual_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text("id: other_agent\nname: Other\n", encoding="utf-8")

    with pytest.raises(ValueError, match="id must match directory"):
        AgentRegistry(tmp_path / "agents")


def test_agent_registry_rejects_unsupported_agent_type(tmp_path):
    agent_dir = tmp_path / "agents" / "bad_type_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        "id: bad_type_agent\nname: Bad Type\ntype: python.module.Agent\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported agent type"):
        AgentRegistry(tmp_path / "agents")


def test_agent_registry_rejects_invalid_tool_schema(tmp_path):
    agent_dir = tmp_path / "agents" / "bad_schema_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        textwrap.dedent(
            """
            id: bad_schema_agent
            name: Bad Schema Agent
            tools:
              - name: mcp_local:deepsearch
                input_schema: not-a-mapping
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Expected mapping for input_schema"):
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


def test_default_eval_cases_cover_core_task_categories():
    agents_root = Path(__file__).resolve().parents[3] / "config" / "agents"
    registry = AgentRegistry(agents_root)

    tags = {tag for case in registry.list_evals() for tag in case.tags}
    supervisor = registry.get("supervisor_agent")

    assert {"search", "file", "data", "browser", "code", "report"}.issubset(tags)
    assert supervisor is not None
    assert supervisor.handoffs["allowed"] == ["task-pilot-agent"]
    assert supervisor.allows_tool("builtin:handoff")
    handoff = next(tool for tool in supervisor.tools if tool.name == "builtin:handoff")
    assert handoff.input_schema["required"] == ["target_agent_id", "task"]
