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
    nav_block = source.split("const navBase = [", 1)[1].split("]", 1)[0]

    for marker in [
        "新会话",
        "Agent",
        "工具",
        "所有会话",
        "我能为你做什么？",
        "运行详情",
        "activeView === 'home'",
        "activeView === 'taskDetail'",
        "activeView === 'tasks'",
        "activeView === 'agents'",
        "activeView === 'tools'",
    ]:
        assert marker in source

    assert "nav.newSession" in nav_block
    assert "nav.agents" in nav_block
    assert "nav.allSessions" in nav_block
    assert "nav.allTasks" not in nav_block
    assert "nav.tools" not in nav_block

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
        "/agent/sessions",
        "/messages",
        "/stream",
        "/agent/ws/sessions",
        "/agent/tasks",
        "/events",
        "/artifacts",
        "/cancel",
        "method: 'DELETE'",
        "/retry",
        "task.inputPlaceholder",
        "/agent/agents",
        "/agent/agents/diagnostics",
        "/agent/tools",
        "/file/v1/upload_file_form",
        "工具调用",
        "waiting_input",
        "waiting_approval",
        "task.needApproval",
        "用户补充",
        "task_retry_requested",
        "运行已重试",
        "task_handoff_requested",
        "Agent 已交接",
        "tool_policy_applied",
        "工具策略已应用",
        "memory_context_loaded",
        "上下文已检索",
        "runtime_boundary_applied",
        "运行环境",
        "todo_list_updated",
        "TODO 更新",
        "todoListSummary",
        "task_artifact_added",
        "产物已登记",
        "agent_selected",
        "Supervisor 已选择 Agent",
    ]:
        assert marker in source


def test_session_lists_are_compact_with_status_only():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    styles = STYLE_PATH.read_text(encoding="utf-8")
    task_row_block = source.split('<button v-for="session in sessions"', 1)[1].split("</button>", 1)[0]

    assert '<span class="recent-meta">' not in source
    assert "recent-meta" not in styles
    assert '<span class="task-title">{{ sessionTitle(session) }}</span>' in task_row_block
    assert "status-pill" in task_row_block
    assert "formatDate(session.createdAt)" not in task_row_block
    assert "formatDuration(session.durationMs)" not in task_row_block
    assert "display: flex;" in styles.split(".task-row", 1)[1].split("}", 1)[0]


def test_task_detail_is_continuous_chat_window():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    styles = STYLE_PATH.read_text(encoding="utf-8")
    submit_block = source.split("async function submitConversationMessage", 1)[1].split("async function readSse", 1)[0]

    for marker in [
        "const chatMessages = ref([])",
        "const chatHasMore = ref(false)",
        "const chatHistoryLoading = ref(false)",
        "const EVENT_REPLAY_LIMIT = '10000'",
        "const visibleArtifacts = computed",
        "const visibleChildTasks = computed",
        "child-task-list",
        "task.childTasks",
        "function createSessionId",
        "function resetConversationState",
        "async function submitChatMessage",
        "function markComposerCompositionStart",
        "function markComposerCompositionEnd",
        "function composerEnterState",
        "function shouldIgnoreComposerEnter",
        "function handleComposerEnter",
        "function handleChatComposerEnter",
        "COMPOSER_COMPOSITION_SETTLE_MS",
        "async function loadOlderMessages",
        "function earliestBackendMessageId",
        "function backendMessageCount",
        "function appendAssistantContent",
        "function assistantAnswerText",
        "function ensureActiveAssistantMessage",
        "function seedChatFromTask",
        "function resultTextFromEvents",
        "function replayAssistantOutput",
        "function sessionEventDedupeKey",
        "function hasSessionEvent",
        "eventRunId(event)",
        "replayAssistantOutput(nextMessages, processRunId, replayedOutput",
        "if (!['user', 'assistant'].includes(role)) return null",
        ".filter(Boolean)",
        "chatMessages.value.push(userMessage, assistantMessage)",
        "v-for=\"(message, index) in chatMessages\"",
        "class=\"chat-composer\"",
        "@compositionstart=\"markComposerCompositionStart\"",
        "@compositionend=\"markComposerCompositionEnd\"",
        "@keydown.enter.exact=\"handleChatComposerEnter\"",
        "appendAssistantContent(text, runId)",
        "appendAssistantContent(text, payload.taskId || payload.runId || '')",
        "new URLSearchParams({ afterSeq: String(afterSeq), limit: '50' })",
        "new URLSearchParams({ limit: EVENT_REPLAY_LIMIT })",
        "const progressItems = computed",
        "const currentTimelineRunId = computed",
        "const activeTimelineItems = computed",
        "const currentPlanPanel = computed",
        "const planDrawerOpen = ref(false)",
        "function togglePlanDrawer",
        "function closePlanDrawer",
        "normalizePlanSnapshotForDisplay(plans[plans.length - 1])",
        "function planSnapshotFromEvent",
        "function normalizePlanSnapshotForDisplay",
        "function isCompletedRunStatus",
        "type === 'plan_completed' || plan.status === 'completed' || plan.planStatus === 'completed'",
        "step.note || lt('运行已完成', 'Run completed')",
        "function planEvidenceSummary",
        "function planStepStatusLabel",
        "function toolThoughtSummary",
        "function toolActionTitle",
        "function progressDetailRows",
        "function shouldShowArtifact",
        "function artifactDisplayName",
        "function artifactTypeText",
        "function shouldShowProgressItem",
        "function timelineItemMatchesCurrentRun",
        "function timelineItemMatchesRun",
        "function sessionHasVisibleProgressEvents",
        "function sessionProcessRunId",
        "function sessionLatestEventRunId",
        "function isNoisyProgressNotification",
        "function isProgressMessage",
        "processOnly: !processOutput",
        "conversation-layout",
        "conversation-layout-has-plan",
        "plan-drawer-open",
        "conversation-thread",
        "plan-drawer-toggle",
        "plan-drawer-scrim",
        "plan-drawer-close",
        "conversation-plan-sidebar",
        "conversation-plan-panel",
        "conversation-plan-list",
        "conversation-stream",
        "conversation-progress",
        "progress-action-list",
        "progress-detail-row",
        "progress-main",
        "progress-state",
    ]:
        assert marker in source

    assert "if (source === 'home') resetConversationState()" in submit_block
    assert "const streamAfterSeq = source === 'chat' ? maxSessionSeq() : 0" in submit_block
    assert "await ensureCurrentSession(text)" in submit_block
    assert "content: text" in submit_block
    assert "files: uploadedFiles" in submit_block
    assert "options: {" in submit_block
    assert "/messages" in submit_block
    assert "/messages?${params}" in source
    assert "const params = new URLSearchParams({ limit: '50', before })" in source
    assert "chatMessages.value = [...olderMessages, ...chatMessages.value]" in source
    assert "keepChat: true" in source
    assert "runId && currentTaskId.value && runId !== currentTaskId.value && running.value" in source
    assert "startSessionStream(currentSessionId.value, streamAfterSeq)" in source
    assert '@click="item.id === \'home\' ? newTask() : switchView(item.id)"' in source
    detail_block = source.split('<section v-else-if="activeView === \'taskDetail\'"', 1)[1].split(
        '<section v-else-if="activeView === \'tasks\'"', 1
    )[0]
    assert 'class="conversation-layout"' in detail_block
    assert "'conversation-layout-has-plan': currentPlanPanel" in detail_block
    assert "'plan-drawer-open': currentPlanPanel && planDrawerOpen" in detail_block
    assert 'class="conversation-thread"' in detail_block
    assert 'class="conversation-overview"' not in detail_block
    assert "{{ conversationAgentName }}" not in detail_block
    assert "conversationOverviewStats" not in detail_block
    assert "currentProgressItem.title" not in detail_block
    assert 'class="conversation-plan-sidebar"' in detail_block
    assert 'class="plan-drawer-toggle"' in detail_block
    assert 'class="plan-drawer-scrim"' in detail_block
    assert 'class="plan-drawer-close"' in detail_block
    assert '@click="togglePlanDrawer"' in detail_block
    assert '@click="closePlanDrawer"' in detail_block
    assert 'class="conversation-plan-panel"' in detail_block
    assert "currentPlanPanel.steps" in detail_block
    assert "planStepStatusLabel(step.status)" in detail_block
    assert "step.evidence" in detail_block
    thread_block = detail_block.split('class="conversation-thread"', 1)[1].split(
        'class="conversation-plan-sidebar"',
        1,
    )[0]
    assert 'class="conversation-plan-panel"' not in thread_block
    assert detail_block.index('class="conversation-thread"') < detail_block.index('class="conversation-plan-sidebar"')
    assert 'class="detail-header"' not in detail_block
    assert "currentTask?.input || t('task.current')" not in detail_block
    assert "{{ taskMeta }}" not in detail_block
    assert 'v-for="(message, index) in chatMessages"' in detail_block
    assert "isProgressMessage(message, index) && progressItems.length" in detail_block
    assert 'v-else-if="!message.processOnly"' in detail_block
    assert 'class="chat-history-control"' not in detail_block
    assert '@click="loadOlderMessages"' not in detail_block
    assert '@keydown.enter.exact.prevent="submitChatMessage"' not in detail_block
    assert "parsed.answer ?? parsed.final_answer ?? parsed.finalAnswer" in source
    assert 'class="timeline-card"' not in detail_block
    assert 'class="chat-card"' not in detail_block
    assert "t('task.final')" not in detail_block
    assert "eventFilters.eventType" not in detail_block
    assert "eventFilters.source" not in detail_block
    assert "retryTask" not in detail_block
    assert 'v-if="visibleArtifacts.length"' in detail_block
    assert 'v-for="artifact in visibleArtifacts"' in detail_block
    assert "artifactDisplayName(artifact)" in detail_block
    assert "artifactTypeText(artifact)" in detail_block
    assert ".conversation-layout" in styles
    assert ".conversation-layout-has-plan" in styles
    assert ".plan-drawer-toggle" in styles
    assert ".plan-drawer-toggle.open" in styles
    assert ".plan-drawer-scrim" in styles
    assert ".plan-drawer-close" in styles
    assert ".conversation-thread" in styles
    assert ".conversation-overview" not in styles
    assert ".conversation-overview-grid" not in styles
    assert ".conversation-overview-action" not in styles
    assert ".conversation-plan-sidebar" in styles
    assert ".conversation-plan-panel" in styles
    assert ".conversation-plan-list" in styles
    assert ".plan-step" in styles
    assert ".conversation-stream" in styles
    assert ".chat-history-control" not in styles
    assert ".conversation-progress" in styles
    assert ".chat-bubble" in styles
    assert ".progress-action-list" in styles
    assert ".progress-detail-row" in styles
    assert ".progress-main" in styles
    assert ".progress-state" in styles
    assert "dedupeTimelineItems([...replay, ...liveTimeline.value].filter(Boolean))" in source
    assert "activeTimelineItems.value.filter(shouldShowProgressItem)" in source
    assert "const plans = activeTimelineItems.value" in source
    assert "timelineItemMatchesRun(item, runId || currentTimelineRunId.value)" in source
    assert "function timelineToolResultDedupeKey" in source
    assert "function stableTimelineValue" in source
    timeline_item_block = styles.split(".timeline-item {", 1)[1].split("}", 1)[0]
    assert "width: 100%;" in timeline_item_block
    assert "overflow: visible;" in timeline_item_block
    assert ':open="item.open || index === progressItems.length - 1"' not in detail_block
    assert "<pre>{{ item.summary }}</pre>" not in detail_block


def test_vue_sidebar_is_collapsible_resizable_and_detail_layout_is_responsive():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    styles = STYLE_PATH.read_text(encoding="utf-8")

    for marker in [
        "const sidebarDensityVersion = 'compact-1'",
        "taskpilot-sidebar-density-version",
        "const savedSidebarWidth = Number(savedSidebarWidthValue || 280)",
        "const sidebarWidth = ref(clampSidebarWidth(savedSidebarWidth))",
        "const sidebarCollapsed = ref(localStorage.getItem('taskpilot-sidebar-collapsed') === 'true')",
        "function toggleSidebarCollapsed",
        "function startSidebarResize",
        "window.addEventListener('pointermove', resizeSidebar)",
        "return Math.min(360, Math.max(240, Math.round(width)))",
        "@pointerdown=\"startSidebarResize\"",
        "sidebar-collapse-button",
        "sidebar-resize-handle",
        "topbar-sidebar-toggle",
        "panel-toggle-icon",
        ":style=\"shellStyle\"",
    ]:
        assert marker in source

    for marker in [
        "--sidebar-width: 280px",
        ".app-shell.sidebar-collapsed .sidebar",
        ".sidebar-resize-handle",
        ".panel-toggle-icon",
        "cursor: col-resize",
        "min-height: calc(36px * 4 + 2px * 3)",
        ".detail-view",
        "container-type: inline-size",
        "@container (max-width: 1180px)",
        "@container (max-width: 980px)",
        ".conversation-layout",
        "width: min(1160px, calc(100% - 48px))",
        "grid-template-columns: minmax(0, 720px)",
        ".conversation-plan-sidebar",
        "position: fixed",
        "transform: translateX(calc(100% + 30px))",
        ".conversation-plan-sidebar.open",
        ".conversation-thread",
        "width: 100%;",
        "width: fit-content",
        "overflow-wrap: anywhere",
    ]:
        assert marker in styles

    assert "min-height: 36px" in styles.split(".nav-item", 1)[1].split("}", 1)[0]
    assert "font-size: 14px" in styles.split(".nav-item", 1)[1].split("}", 1)[0]
    assert "max-width: 360px" in styles.split(".sidebar", 1)[1].split("}", 1)[0]
    assert "Agent 工作台" not in source
    assert "Agent Workspace" not in source
    assert '<div class="brand-mark">T</div>' not in source
    assert "brand-mark-plane" in source


def test_vue_home_composer_matches_manus_compact_scale():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    styles = STYLE_PATH.read_text(encoding="utf-8")
    composer_block = styles.split(".composer-card {", 1)[1].split("}", 1)[0]
    textarea_block = styles.split(".composer-card textarea {", 1)[1].split("}", 1)[0]
    hero_block = styles.split(".hero-block {", 1)[1].split("}", 1)[0]

    assert 'rows="2"' in source
    assert "function handleHomeComposerEnter" in source
    assert '@keydown.enter.exact="handleHomeComposerEnter"' in source
    assert '@keydown.enter.exact.prevent="submitTask"' not in source
    assert 'class="tool-button"' not in source
    assert "width: min(900px, 100%)" in hero_block
    assert "width: min(880px, 100%)" in composer_block
    assert "padding: 12px 14px 10px" in composer_block
    assert "min-height: 58px" in textarea_block
    assert "max-height: 130px" in textarea_block
    assert "resize: none" in textarea_block
    assert "font-size: 16px" in textarea_block


def test_vue_recent_tasks_have_more_menu_and_delete_action():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    styles = STYLE_PATH.read_text(encoding="utf-8")

    for marker in [
        "const openTaskMenuId = ref('')",
        "function toggleTaskMenu",
        "function taskRecordId",
        "function removeTaskFromLists",
        "async function deleteTask",
        "function sessionRecordId",
        "function removeSessionFromLists",
        "async function archiveSession",
        "class=\"task-menu-button\"",
        "class=\"task-menu-popover\"",
        "t('task.delete')",
        "response.status !== 404",
        "removeSessionFromLists(sessionId)",
        "method: 'DELETE'",
        "@click.stop.prevent=\"archiveSession(session)\"",
    ]:
        assert marker in source

    for marker in [
        ".recent-task-row",
        ".task-menu-button",
        ".task-menu-popover",
        ".task-menu-danger",
    ]:
        assert marker in styles

    popover_block = styles.split(".task-menu-popover {", 1)[1].split("}", 1)[0]
    button_block = styles.split(".task-menu-popover button {", 1)[1].split("}", 1)[0]
    assert "min-width: 76px" in popover_block
    assert "min-height: 26px" in button_block
    assert "font-size: 13px" in button_block


def test_vue_notifications_auto_dismiss_and_dedupe():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    styles = STYLE_PATH.read_text(encoding="utf-8")
    toast_stack_block = styles.split(".toast-stack {", 1)[1].split("}", 1)[0]
    toast_item_block = styles.split(".toast-item {", 1)[1].split("}", 1)[0]

    for marker in [
        "const NOTIFICATION_TTL_MS = 2800",
        "const notificationTimers = new Map()",
        "function dismissNotification",
        "function addNotification(text, status = 'info', ttl = NOTIFICATION_TTL_MS)",
        ".filter((item) => item.text === message && item.status === status)",
        "setTimeout(() => dismissNotification(id), ttl)",
        "notificationTimers.forEach((timer) => clearTimeout(timer))",
        "@click=\"dismissNotification(item.id)\"",
    ]:
        assert marker in source

    assert "width: min(320px, calc(100vw - 44px))" in toast_stack_block
    assert "font-size: 13px" in toast_item_block


def test_vue_submit_uses_defaults_and_only_sends_advanced_options_when_open():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    submit_block = source.split("async function submitTask", 1)[1].split("async function readSse", 1)[0]

    assert "agent_id: selectedAgentId.value || undefined" in submit_block
    assert "await ensureCurrentSession(text)" in submit_block
    assert "language: language.value === 'en' ? 'en' : 'ch'" in submit_block
    assert "files: uploadedFiles" in submit_block
    assert "options: {" in submit_block
    assert "if (advancedOpen.value)" in submit_block
    assert "payload.options.mode = runMode.value || undefined" in submit_block
    assert "payload.options.run_environment = runEnvironment.value || undefined" in submit_block
    assert "payload.options.output_style" not in submit_block
    assert "payload.options.selected_tools" not in submit_block
    assert "payload.options.approved_tools" not in submit_block
    mode_select = source.split("<select v-model=\"runMode\">", 1)[1].split("</select>", 1)[0]
    assert "<option value=\"react\">ReAct</option>" in mode_select
    assert "Legacy Plan Executor" not in mode_select
    assert "plans_executor" not in mode_select


def test_vue_frontend_supports_chinese_and_english_switching():
    source = APP_VUE_PATH.read_text(encoding="utf-8")

    for marker in [
        "taskpilot-language",
        "v-model=\"language\"",
        "<option value=\"zh\">中文</option>",
        "<option value=\"en\">English</option>",
        "'home.title': '我能为你做什么？'",
        "'home.title': 'What can I do for you?'",
        "language: language.value === 'en' ? 'en' : 'ch'",
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
        "运行模式",
        "运行环境",
    ]:
        assert marker in source
    for marker in [
        "输出格式",
        "本次工具",
        "v-model=\"outputStyle\"",
        "toggleTool(tool)",
        "selectedToolNames",
        "approvedToolNames",
    ]:
        assert marker not in source


def test_vue_tools_panel_shows_tool_source_risk_and_server():
    source = APP_VUE_PATH.read_text(encoding="utf-8")
    styles = STYLE_PATH.read_text(encoding="utf-8")

    for marker in [
        "'tools.source': '来源'",
        "'tools.server': '服务'",
        "'tools.risk': '风险'",
        "'tools.source': 'Source'",
        "'tools.server': 'Server'",
        "'tools.risk': 'Risk'",
        "function toolSourceText(tool)",
        "function toolServerText(tool)",
        "function toolMetaRows(tool)",
        "function toolRequiresApproval(tool)",
        "tool.riskLevel || tool.policy?.risk",
        "tool.requiresApproval",
        "tool.blockReason === 'high_risk_requires_approval'",
        "class=\"tool-meta-list\"",
        "toolMetaRows(tool)",
    ]:
        assert marker in source

    for marker in [
        ".tool-meta-list",
        ".tool-meta-list span",
        ".tool-meta-list b",
    ]:
        assert marker in styles


def test_vue_approval_actions_are_available_for_pending_tool_approval():
    source = APP_VUE_PATH.read_text(encoding="utf-8")

    for marker in [
        "function approvalRequestKeys(item = {})",
        "item.raw?.eventId",
        "payload.approvalRequestEventEventId",
        "item.runId || currentTaskId.value",
        "function approvalPending(item)",
        "function resolveApproval(item, approved)",
        "/approval",
        "approvedTools: approved ? requestedTools : []",
        "@click=\"resolveApproval(item, true)\"",
        "@click=\"resolveApproval(item, false)\"",
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
        ".conversation-thread",
        ".conversation-stream",
        ".conversation-progress",
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


def test_backend_serves_vue_dist_assets_without_legacy_fallback():
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
    ]:
        assert marker in source
    assert "autoagent.html" not in source
    assert "HTMLResponse" not in source


def test_backend_threads_language_to_agent_context_and_task_events():
    app_source = APP_PATH.read_text(encoding="utf-8")
    runtime_source = (PROJECT_ROOT / "brain" / "core" / "autoagent_runtime.py").read_text(encoding="utf-8")
    context_source = (PROJECT_ROOT / "brain" / "core" / "context.py").read_text(encoding="utf-8")
    request_source = (PROJECT_ROOT / "brain" / "models" / "requests.py").read_text(encoding="utf-8")

    for marker in [
        "def _normalize_language",
        "request.language = _normalize_language",
        '"language": request.language',
        "language_override",
        "User supplemental input",
        '"language": language',
    ]:
        assert marker in app_source

    for marker in [
        "language=request.language or \"ch\"",
        "run_environment=request.run_environment or \"local\"",
    ]:
        assert marker in runtime_source

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
