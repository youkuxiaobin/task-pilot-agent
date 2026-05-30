from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


HTML_PATH = Path(__file__).resolve().parents[2] / "brain" / "web" / "autoagent.html"
APP_PATH = Path(__file__).resolve().parents[2] / "brain" / "app.py"


def test_autoagent_page_contains_task_replay_controls():
    html = HTML_PATH.read_text(encoding="utf-8")

    for marker in [
        'id="task-list"',
        'id="refresh-tasks"',
        'id="task-keyword"',
        'id="task-status-filter"',
        'id="task-agent-filter"',
        'id="sidebar-toggle"',
        'id="task-input-panel"',
        'id="task-input-text"',
        'id="send-task-input"',
        'id="task-meta"',
        'id="agent-id"',
        'id="run-environment"',
        'id="tool-picker"',
        'id="tool-options"',
        'id="eval-case"',
        'id="run-eval"',
        'id="run-all-evals"',
        'id="file-input"',
        'id="file-summary"',
        "refreshTaskList",
        "renderTaskAgentFilter",
        "scheduleTaskRefresh",
        "classList.toggle('open')",
        "sendTaskInput",
        "/input",
        "waiting_input",
        "用户补充",
        "keyword",
        "refreshAgentList",
        "applySelectedAgentDefaults",
        "renderToolOptions",
        "getSelectedTools",
        "selected_tools",
        "tool.allowed",
        "不可用",
        "tool_policy_applied",
        "工具策略已应用",
        "run_environment",
        "runEnvironment",
        "renderEvalOptions",
        "runSelectedEval",
        "runAllEvals",
        "/evals/run",
        "/evals/",
        "uploadSelectedFiles",
        "upload_file_form",
        "uploadFile",
        "durationMs",
        "formatUsageMeta",
        "工具耗时",
        "renderToolAuditHTML",
        "Task ID",
        "Agent ID",
        "User ID",
        "Request ID",
        "Started At",
        "Completed At",
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
        "agent_selected",
        "Supervisor 已选择 Agent",
        "agent-summary",
        "renderAgentSummary",
        "能力",
        "交接",
        "agent_started",
        "agent_completed",
        "task_handoff_requested",
        "任务已交接",
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


def test_websocket_disconnect_keeps_background_worker_detached():
    source = APP_PATH.read_text(encoding="utf-8")
    ws_block = source.split("async def autoagent_ws", 1)[1].split("@agent_router.get(\"/web/health\")", 1)[0]

    assert "except WebSocketDisconnect:" in ws_block
    assert "detached = True" in ws_block
    assert "worker.cancel()" not in ws_block


def test_tasks_api_has_background_create_endpoint():
    source = APP_PATH.read_text(encoding="utf-8")

    assert '@agent_router.post("/tasks")' in source
    assert "async def create_agent_task" in source
    assert "task_queued" in source


def test_autoagent_page_exposes_task_list_filters():
    html = HTML_PATH.read_text(encoding="utf-8")

    for marker in [
        "task-created-filter",
        "task-duration-filter",
        "task-error-filter",
        "created_from",
        "min_duration_ms",
        "max_duration_ms",
        "has_error",
    ]:
        assert marker in html
