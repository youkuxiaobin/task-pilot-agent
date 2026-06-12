from __future__ import annotations

import asyncio

import pytest

from tools.mcp_local.tool.process_manager import (
    list_process_commands,
    poll_process_command,
    start_process_command,
    stop_process_command,
)


@pytest.mark.asyncio
async def test_process_command_lifecycle(tmp_path):
    started = await start_process_command(
        "python -c \"import time; print('ready', flush=True); time.sleep(5)\"",
        work_dir=str(tmp_path),
    )
    process_id = started["processId"]

    try:
        for _ in range(20):
            polled = await poll_process_command(process_id)
            if "ready" in polled["stdout"]:
                break
            await asyncio.sleep(0.1)

        listed = await list_process_commands()
        stopped = await stop_process_command(process_id)

        assert any(item["processId"] == process_id for item in listed["processes"])
        assert "ready" in polled["stdout"]
        assert stopped["stopped"] is True
    finally:
        try:
            await stop_process_command(process_id, kill=True)
        except KeyError:
            pass
