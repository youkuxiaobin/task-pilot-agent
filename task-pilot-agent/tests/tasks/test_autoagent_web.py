from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


HTML_PATH = Path(__file__).resolve().parents[2] / "brain" / "web" / "autoagent.html"
APP_PATH = Path(__file__).resolve().parents[2] / "brain" / "app.py"


def test_autoagent_page_keeps_task_replay_and_agent_selector():
    html = HTML_PATH.read_text(encoding="utf-8")

    for marker in [
        'id="task-list"',
        'id="refresh-tasks"',
        'id="task-keyword"',
        'id="task-user-filter"',
        'id="task-status-filter"',
        'id="task-agent-filter"',
        'id="task-agent-type-filter"',
        'id="sidebar-toggle"',
        'id="task-input-panel"',
        'id="task-input-text"',
        'id="send-task-input"',
        'id="task-actions"',
        'id="task-meta"',
        'id="task-event-type-filter"',
        'id="task-event-source-filter"',
        "reloadActiveTaskEvents",
        "event_type",
        "全部事件",
        "工具调用",
        "全部来源",
        "任务产物已登记",
        "task_retry_requested",
        "任务已重试",
        "eval_run_created",
        "评测任务已创建",
        "eval_result",
        "评测结果",
        'id="retry-task"',
        "retryTask",
        "/retry",
        "任务重试中",
        "重试任务失败",
        "taskAgentId",
        "taskMode",
        "环境 ${session.runEnvironment}",
        "formatArtifactMeta",
        "产物 ${artifacts.length}",
        'id="agent-id"',
        "refreshTaskList",
        "renderTaskAgentFilter",
        "renderTaskAgentTypeFilter",
        "scheduleTaskRefresh",
        "classList.toggle('open')",
        "sendTaskInput",
        "/input",
        "waiting_input",
        "用户补充",
        "keyword",
        "user_id",
        "agent_type",
        "refreshAgentList",
        "applySelectedAgentDefaults",
        "tool_policy_applied",
        "工具策略已应用",
        "task_queued",
        "任务排队中",
        "task_resume_requested",
        "任务恢复请求已发送",
        "task_resumed",
        "任务已恢复运行",
        "本次授权",
        "已授权",
        "memory_context_loaded",
        "上下文已检索",
        "上下文检索已按 Agent 配置关闭",
        "来源：",
        "memoryContextSourceText",
        "agent_phase",
        "阶段",
        "runtime_boundary_applied",
        "运行边界已应用",
        "runEnvironment",
        "durationMs",
        "formatUsageMeta",
        "工具耗时",
        "renderToolAuditHTML",
        "Arguments Summary",
        "Task ID",
        "Agent ID",
        "User ID",
        "Request ID",
        "Run ID",
        "Session ID",
        "Run Environment",
        "Work Dir",
        "Started At",
        "Completed At",
        "Result Summary",
        "defaultAgentId",
        "/agent/agents",
        "/agent/agents/diagnostics",
        "agentDiagnostics",
        "refreshAgentDiagnostics",
        "renderAgentDiagnosticsHTML",
        "配置检查通过",
        "配置检查异常",
        "Agent 列表加载失败",
        "loadTask",
        "renderTaskToSession",
        "lastSuccessfulEventText",
        "最后成功事件",
        "失败原因",
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
        "agentSnapshot",
        "formatAgentSnapshotMeta",
        "renderAgentSnapshotHTML",
        "Agent 说明",
        "agent_started",
        "agent_completed",
        "task_handoff_requested",
        "任务已交接",
    ]:
        assert marker in html


def test_autoagent_page_hides_runtime_configuration_controls():
    html = HTML_PATH.read_text(encoding="utf-8")

    for control_id in [
        "output-style",
        "mode",
        "run-environment",
        "tool-picker",
        "eval-case",
        "run-eval",
        "run-all-evals",
        "file-input",
        "file-summary",
        "task-event-type-filter",
        "task-event-source-filter",
    ]:
        assert re.search(rf'id="{control_id}"[^>]*hidden', html)

    for control_id in [
        "task-keyword",
        "task-user-filter",
        "task-status-filter",
        "task-agent-filter",
        "task-agent-type-filter",
        "task-created-filter",
        "task-duration-filter",
        "task-error-filter",
    ]:
        assert re.search(rf'<div class="task-filters" hidden>[\s\S]*id="{control_id}"', html)


def test_autoagent_submit_uses_config_defaults_except_agent():
    html = HTML_PATH.read_text(encoding="utf-8")
    submit_block = html.split("async function onSubmit", 1)[1].split("async function onStop", 1)[0]

    assert "agent_id: dom.agentId.value || undefined" in submit_block
    assert "conversation_id: session.id" in submit_block
    assert "outputStyle:" not in submit_block
    assert "mode:" not in submit_block
    assert "run_environment:" not in submit_block
    assert "selected_tools" not in submit_block
    assert "approved_tools" not in submit_block
    assert "uploadSelectedFiles" not in submit_block


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


def test_autoagent_page_keeps_task_list_filter_logic_hidden():
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
