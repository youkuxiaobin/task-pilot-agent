from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


HTML_PATH = Path(__file__).resolve().parents[2] / "brain" / "web" / "autoagent.html"


def test_autoagent_page_contains_task_replay_controls():
    html = HTML_PATH.read_text(encoding="utf-8")

    for marker in [
        'id="task-list"',
        'id="refresh-tasks"',
        'id="task-keyword"',
        'id="task-status-filter"',
        'id="task-agent-filter"',
        'id="sidebar-toggle"',
        'id="task-meta"',
        'id="agent-id"',
        'id="tool-picker"',
        'id="tool-options"',
        "refreshTaskList",
        "renderTaskAgentFilter",
        "scheduleTaskRefresh",
        "classList.toggle('open')",
        "keyword",
        "refreshAgentList",
        "applySelectedAgentDefaults",
        "renderToolOptions",
        "getSelectedTools",
        "selected_tools",
        "durationMs",
        "Result Summary",
        "defaultAgentId",
        "/agent/agents",
        "loadTask",
        "renderTaskToSession",
        "/agent/tasks",
        "/artifacts",
        "renderArtifactsHTML",
        "任务产物",
        "/cancel",
        "任务取消请求已发送",
    ]:
        assert marker in html


def test_autoagent_inline_javascript_has_valid_syntax(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    html = HTML_PATH.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert scripts

    script_path = tmp_path / "autoagent-inline.js"
    script_path.write_text("\n".join(scripts), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(script_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
