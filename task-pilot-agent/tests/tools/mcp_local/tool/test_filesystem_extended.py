from __future__ import annotations

import pytest

from tools.mcp_local.tool.filesystem import edit_file, glob_paths, grep_files


@pytest.mark.asyncio
async def test_edit_file_replaces_exact_text_inside_workspace(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("alpha beta alpha", encoding="utf-8")

    result = await edit_file(
        "notes.txt",
        "alpha",
        "gamma",
        expected_replacements=2,
        work_dir=str(tmp_path),
    )

    assert result["replacements"] == 2
    assert target.read_text(encoding="utf-8") == "gamma beta gamma"


@pytest.mark.asyncio
async def test_edit_file_rejects_paths_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside-taskpilot-edit-test.txt"
    outside.write_text("alpha", encoding="utf-8")

    with pytest.raises(PermissionError):
        await edit_file(str(outside), "alpha", "beta", work_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_glob_and_grep_find_expected_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('TaskPilot')\n", encoding="utf-8")
    (tmp_path / "src" / "readme.md").write_text("TaskPilot docs\n", encoding="utf-8")

    glob_result = await glob_paths("*.py", root="src", work_dir=str(tmp_path))
    grep_result = await grep_files("taskpilot", root="src", file_pattern="*.md", work_dir=str(tmp_path))

    assert [item["name"] for item in glob_result["matches"]] == ["app.py"]
    assert len(grep_result["matches"]) == 1
    assert grep_result["matches"][0]["lineNumber"] == 1
