from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_PATH = PROJECT_ROOT / "brain" / "app.py"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
APP_VUE_PATH = FRONTEND_ROOT / "src" / "App.vue"
STYLE_PATH = FRONTEND_ROOT / "src" / "styles.css"
PACKAGE_PATH = FRONTEND_ROOT / "package.json"
VITE_CONFIG_PATH = FRONTEND_ROOT / "vite.config.js"


def test_vue_frontend_has_product_navigation_and_core_views():
    source = APP_VUE_PATH.read_text(encoding="utf-8")

    for marker in [
        "新建任务",
        "Agent",
        "工具",
        "所有任务",
        "我能为你做什么？",
        "任务详情",
        "最终结果",
        "执行过程",
        "任务产物",
        "activeView === 'home'",
        "activeView === 'taskDetail'",
        "activeView === 'tasks'",
        "activeView === 'agents'",
        "activeView === 'tools'",
    ]:
        assert marker in source

    for marker in [
        "定时任务",
        "项目工作区",
        "资料和产物库",
        "自动运行任务",
        "免费计划",
        "开始免费试用",
        "制作幻灯片",
        "创建网站",
        "quickBase",
        "quickActions",
        "feature-banner",
        "credit-pill",
    ]:
        assert marker not in source


def test_vue_frontend_keeps_task_replay_and_control_flows():
    source = APP_VUE_PATH.read_text(encoding="utf-8")

    for marker in [
        "async function submitTask",
        "async function readSse",
        "function parseSse",
        "function handleStreamEvent",
        "async function stopTask",
        "async function retryTask",
        "async function sendTaskInput",
        "async function refreshTasks",
        "async function loadTask",
        "async function refreshAgents",
        "async function refreshToolCatalog",
        "async function uploadSelectedFiles",
        "/agent/autoagent",
        "/agent/tasks",
        "/events",
        "/artifacts",
        "/cancel",
        "/retry",
        "/input",
        "/agent/agents",
        "/agent/agents/diagnostics",
        "/agent/tools",
        "/file/v1/upload_file_form",
        "event_type",
        "全部事件",
        "工具调用",
        "全部来源",
        "waiting_input",
        "用户补充",
        "task_retry_requested",
        "任务已重试",
        "task_handoff_requested",
        "任务已交接",
        "tool_policy_applied",
        "工具策略已应用",
        "memory_context_loaded",
        "上下文已检索",
        "runtime_boundary_applied",
        "运行环境",
        "task_artifact_added",
        "任务产物已登记",
        "agent_selected",
        "Supervisor 已选择 Agent",
    ]:
        assert marker in source


def test_vue_submit_uses_defaults_and_only_sends_advanced_options_when_open():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    submit_block = source.split("async function submitTask", 1)[1].split("async function readSse", 1)[0]

    assert "agent_id: selectedAgentId.value || undefined" in submit_block
    assert "conversation_id: currentSessionId.value" in submit_block
    assert "language: language.value === 'en' ? 'en' : 'ch'" in submit_block
    assert "uploadFile: uploadedFiles" in submit_block
    assert "if (advancedOpen.value)" in submit_block
    assert "payload.outputStyle = outputStyle.value || undefined" in submit_block
    assert "payload.mode = runMode.value || undefined" in submit_block
    assert "payload.run_environment = runEnvironment.value || undefined" in submit_block
    assert "payload.selected_tools = selected" in submit_block
    assert "payload.approved_tools = approved" in submit_block


def test_vue_frontend_supports_chinese_and_english_switching():
    source = APP_VUE_PATH.read_text(encoding="utf-8")

    for marker in [
        "taskpilot-language",
        "v-model=\"language\"",
        "<option value=\"zh\">中文</option>",
        "<option value=\"en\">English</option>",
        "'home.title': '我能为你做什么？'",
        "'home.title': 'What can I do for you?'",
        "'task.final': '最终结果'",
        "'task.final': 'Final Result'",
        "body: JSON.stringify({ content, language: language.value === 'en' ? 'en' : 'ch' })",
        "const lt = (zh, en)",
        "const serviceError = (status)",
        "document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN'",
    ]:
        assert marker in source


def test_vue_frontend_exposes_file_upload_agent_picker_and_advanced_settings():
    source = APP_VUE_PATH.read_text(encoding="utf-8")

    for marker in [
        'ref="fileInputRef"',
        'type="file"',
        "multiple",
        "selectedFiles.length",
        "removeFile(index)",
        "v-model=\"selectedAgentId\"",
        "高级设置",
        "输出格式",
        "运行模式",
        "运行环境",
        "本次工具",
        "toggleTool(tool)",
        "toolRequiresApproval(tool)",
    ]:
        assert marker in source


def test_vue_styles_are_workspace_not_debug_console():
    styles = STYLE_PATH.read_text(encoding="utf-8")

    for marker in [
        ".app-shell",
        ".sidebar",
        ".main-nav",
        ".home-view",
        ".composer-card",
        ".detail-layout",
        ".timeline-panel",
        ".inspector-panel",
        ".agent-grid",
        ".tool-grid",
        "@media (max-width: 900px)",
        "@media (max-width: 680px)",
    ]:
        assert marker in styles

    for marker in [
        ".quick-grid",
        ".feature-banner",
        ".placeholder-view",
        ".credit-pill",
    ]:
        assert marker not in styles

    assert "gradient" not in styles.lower()


def test_vite_config_serves_under_agent_web_prefix():
    package = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))
    vite_config = VITE_CONFIG_PATH.read_text(encoding="utf-8")

    assert package["scripts"]["build"] == "vite build"
    assert "vue" in package["dependencies"]
    assert "base: '/agent/web/'" in vite_config
    assert "'/agent': 'http://127.0.0.1:9010'" in vite_config
    assert "'/file': 'http://127.0.0.1:9010'" in vite_config


def test_backend_serves_vue_dist_assets_and_keeps_legacy_fallback():
    source = APP_PATH.read_text(encoding="utf-8")

    for marker in [
        "FRONTEND_ROOT",
        "FRONTEND_DIST",
        '@agent_router.get("/web/assets/{asset_path:path}")',
        "autoagent_frontend_asset",
        "asset not found",
        '@agent_router.get("/web/autoagent")',
        "vue_index = FRONTEND_DIST / \"index.html\"",
        "FileResponse(str(vue_index), media_type=\"text/html\")",
        "WEB_ROOT / \"autoagent.html\"",
    ]:
        assert marker in source


def test_backend_threads_language_to_agent_context_and_task_events():
    app_source = APP_PATH.read_text(encoding="utf-8")
    context_source = (PROJECT_ROOT / "brain" / "core" / "context.py").read_text(encoding="utf-8")
    request_source = (PROJECT_ROOT / "brain" / "models" / "requests.py").read_text(encoding="utf-8")

    for marker in [
        "def _normalize_language",
        "request.language = _normalize_language",
        '"language": request.language',
        "language=request.language or \"ch\"",
        "language_override",
        "User supplemental input",
        '"language": language',
    ]:
        assert marker in app_source

    for marker in [
        "language: str = \"ch\"",
        "def language_instruction",
        "Output language: English",
        "输出语言：中文",
        "The following are relevant memory",
    ]:
        assert marker in context_source

    assert "language: Optional[str] = None" in request_source


def test_frontend_source_has_valid_javascript_syntax(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    source = APP_VUE_PATH.read_text(encoding="utf-8")
    match = re.search(r"<script setup>([\s\S]*?)</script>", source)
    assert match

    script_path = tmp_path / "app-script.js"
    script_path.write_text(match.group(1), encoding="utf-8")
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
