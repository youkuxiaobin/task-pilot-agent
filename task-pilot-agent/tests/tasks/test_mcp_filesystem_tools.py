from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from tools.mcp_local.tool.filesystem import (
    copy_file,
    create_directory,
    delete_path,
    file_stat,
    list_directory,
    move_file,
    read_file,
    shell_exec,
    write_file,
)


def test_file_read_supports_absolute_paths_outside_workspace(tmp_path):
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("hello from host", encoding="utf-8")
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()

    result = asyncio.run(read_file(str(outside_file), work_dir=str(work_dir)))

    assert result["content"] == "hello from host"
    assert result["path"] == str(outside_file)
    assert result["truncated"] is False


def test_file_write_list_stat_and_append_stay_inside_workspace(tmp_path):
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()

    asyncio.run(write_file("notes/demo.txt", "hello", work_dir=str(work_dir)))
    asyncio.run(write_file("notes/demo.txt", " world", mode="append", work_dir=str(work_dir)))
    read_result = asyncio.run(read_file("notes/demo.txt", work_dir=str(work_dir)))
    stat_result = asyncio.run(file_stat("notes/demo.txt", work_dir=str(work_dir)))
    list_result = asyncio.run(list_directory("notes", work_dir=str(work_dir)))

    assert read_result["content"] == "hello world"
    assert stat_result["size"] == len("hello world")
    assert [entry["name"] for entry in list_result["entries"]] == ["demo.txt"]


def test_write_move_and_delete_reject_paths_outside_workspace(tmp_path):
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    outside_file = tmp_path / "outside.txt"

    with pytest.raises(PermissionError):
        asyncio.run(write_file(str(outside_file), "nope", work_dir=str(work_dir)))

    with pytest.raises(PermissionError):
        asyncio.run(move_file(str(outside_file), "inside.txt", work_dir=str(work_dir)))

    with pytest.raises(PermissionError):
        asyncio.run(delete_path(str(outside_file), work_dir=str(work_dir)))


def test_copy_move_create_and_delete_inside_workspace(tmp_path):
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    outside_file = tmp_path / "source.txt"
    outside_file.write_text("copy me", encoding="utf-8")

    asyncio.run(create_directory("nested", work_dir=str(work_dir)))
    copy_result = asyncio.run(copy_file(str(outside_file), "nested/copied.txt", work_dir=str(work_dir)))
    move_result = asyncio.run(move_file("nested/copied.txt", "nested/moved.txt", work_dir=str(work_dir)))
    delete_result = asyncio.run(delete_path("nested/moved.txt", work_dir=str(work_dir)))

    assert Path(copy_result["destination"]).name == "copied.txt"
    assert Path(move_result["destination"]).name == "moved.txt"
    assert delete_result["deleted"] is True
    assert not (work_dir / "nested" / "moved.txt").exists()


def test_shell_exec_runs_in_workspace_and_blocks_external_working_dir(tmp_path):
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    command = f'"{sys.executable}" -c "print(123)"'

    result = asyncio.run(shell_exec(command, work_dir=str(work_dir)))

    assert result["exitCode"] == 0
    assert result["stdout"].strip() == "123"

    with pytest.raises(PermissionError):
        asyncio.run(shell_exec(command, working_dir=str(tmp_path), work_dir=str(work_dir)))
