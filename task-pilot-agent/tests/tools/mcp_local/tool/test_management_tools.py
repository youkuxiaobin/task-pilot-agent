from __future__ import annotations

import pytest

from tools.mcp_local.tool.management_tools import (
    config_read,
    create_subagent,
    install_skill,
    load_skill,
    mcp_manager_list_servers,
    mcp_manager_write_manifest,
    search_skills,
    set_skill_enabled,
)


@pytest.mark.asyncio
async def test_config_read_redacts_sensitive_values():
    result = await config_read("llm", include_sensitive=True)

    assert result["section"] == "llm"
    assert "config" in result
    assert result["config"]["config"]["api_key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_mcp_manager_lists_servers_and_writes_manifest(tmp_path):
    listed = await mcp_manager_list_servers()
    written = await mcp_manager_write_manifest(work_dir=str(tmp_path))

    assert listed["count"] >= 1
    assert (tmp_path / "mcp.json").exists()
    assert written["serverCount"] == listed["count"]


@pytest.mark.asyncio
async def test_task_local_skill_install_search_and_load(tmp_path):
    installed = await install_skill("Research Helper", "# Research Helper\nUse sources.", work_dir=str(tmp_path))
    searched = await search_skills("research", work_dir=str(tmp_path))
    loaded = await load_skill("research-helper", work_dir=str(tmp_path))

    assert installed["installed"] is True
    assert installed["enabled"] is True
    assert any(item["id"] == "research-helper" for item in searched["items"])
    assert "Use sources" in loaded["content"]
    assert loaded["loadCount"] == 1
    assert loaded["contentTruncated"] is False


@pytest.mark.asyncio
async def test_task_local_skill_can_be_disabled_and_is_size_limited(tmp_path):
    await install_skill(
        "Long Draft Helper",
        "# Long Draft Helper\nKeep it focused.",
        work_dir=str(tmp_path),
        agent_ids=["writer-agent"],
    )

    disabled = await set_skill_enabled("long-draft-helper", False, work_dir=str(tmp_path))
    visible = await search_skills("draft", work_dir=str(tmp_path))
    all_items = await search_skills("draft", work_dir=str(tmp_path), include_disabled=True, agent_id="writer-agent")

    assert disabled["enabled"] is False
    assert all(item["id"] != "long-draft-helper" for item in visible["items"])
    assert any(item["id"] == "long-draft-helper" and item["enabled"] is False for item in all_items["items"])
    with pytest.raises(PermissionError):
        await load_skill("long-draft-helper", work_dir=str(tmp_path))

    loaded = await load_skill("long-draft-helper", work_dir=str(tmp_path), include_disabled=True)
    assert loaded["enabled"] is False
    assert loaded["agentIds"] == ["writer-agent"]

    with pytest.raises(ValueError):
        await install_skill("too-large", "x" * 20_001, work_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_create_subagent_defaults_to_task_workspace(tmp_path):
    result = await create_subagent(
        "draft-agent",
        "Draft Agent",
        "Drafts text",
        "You draft text.",
        tools=["file_read"],
        work_dir=str(tmp_path),
    )

    assert result["created"] is True
    assert result["runtimeAvailableAfterReload"] is False
    assert (tmp_path / "subagents" / "draft-agent" / "agent.yaml").exists()
