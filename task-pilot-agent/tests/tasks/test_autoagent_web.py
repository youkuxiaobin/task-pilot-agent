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
        "renderToolOptions",
        "refreshToolCatalog",
        "toolCatalog",
        "/agent/tools",
        "getSelectedTools",
        "getApprovedTools",
        "approved_tools",
        'data-approval="high_risk"',
        "schemaFieldNames",
        "renderToolSchemaLine",
        "inputSchema",
        "outputSchema",
        "selected_tools",
        "approvedTools",
        "tool.allowed",
        "blockReason",
        "toolBlockReasonText",
        "high_risk_requires_approval",
        "高风险工具需要本次审批",
        "原因：",
        "不可用",
        "需审批",
        "tool_policy_applied",
        "工具策略已应用",
        "task_queued",
        "任务排队中",
        "本次授权",
        "已授权",
        "memory_context_loaded",
        "上下文已检索",
        "来源：",
        "memoryContextSourceText",
        "agent_phase",
        "阶段",
        "runtime_boundary_applied",
        "运行边界已应用",
        "run_environment",
        "runEnvironment",
        "renderEvalOptions",
        "runSelectedEval",
        "runAllEvals",
        "已启动评测任务",
        "已启动 ${items.length} 个评测任务",
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
        "Run ID",
        "Session ID",
        "Run Environment",
        "Work Dir",
        "Started At",
        "Completed At",
        "Result Summary",
        "defaultAgentId",
        "/agent/agents",
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
