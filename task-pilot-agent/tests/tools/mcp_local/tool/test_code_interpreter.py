import asyncio
import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def code_interpreter_module(monkeypatch):
    """Load code_interpreter with sample config to satisfy settings."""
    repo_root = Path(__file__).resolve().parents[5]
    config_path = repo_root / "config" / "config.yaml.example"
    monkeypatch.setenv("APP_CONFIG_FILE", str(config_path))

    module_name = "tools.mcp_local.tool.code_interpreter"
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


class FakeAgent:
    """Fake agent that simulates generated code calling sys.exit(1)."""

    def run(self, *args, **kwargs):
        raise SystemExit(1)


async def _noop_download_all_files_in_path(**_kwargs):
    return []


def test_download_task_system_exit_is_converted_to_error_output(monkeypatch, code_interpreter_module):
    """Ensure the download task that triggers sys.exit(1) returns an error output instead of killing the server."""
    ci = code_interpreter_module
    task = (
        "Download the enwiki-20230601-external_links.sql.gz file using wget with robust retry, "
        "resume, and mirror fallback capabilities. Use the primary URL and mirror URLs from "
        "previous steps. Create a comprehensive download script that handles network failures, "
        "resumes interrupted downloads, and tries multiple sources."
    )

    # Patch dependencies to keep the agent lightweight and side-effect free.
    monkeypatch.setattr(ci, "create_ci_agent", lambda **_kwargs: FakeAgent())
    monkeypatch.setattr(ci, "download_all_files_in_path", _noop_download_all_files_in_path)
    monkeypatch.setattr(ci, "get_prompt", lambda _name: {"task_template": "{{ task }}"})

    async def _run():
        collected = []
        async for chunk in ci.code_interpreter_agent(
            task=task,
            file_names=[],
            request_id="req-download",
            stream=True,
        ):
            collected.append(chunk)
        return collected

    results = asyncio.run(_run())

    assert len(results) == 1
    assert isinstance(results[0], ci.ActionOutput)
    assert "Code interpreter error" in results[0].content
    # Should surface the SystemExit code that caused the failure.
    assert "1" in results[0].content
