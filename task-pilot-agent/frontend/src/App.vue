<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ breaks: true, gfm: true })

const navBase = [
  { id: 'home', labelKey: 'nav.newSession', icon: 'compose' },
  { id: 'tasks', labelKey: 'nav.allSessions', icon: 'agent' },
  { id: 'agents', labelKey: 'nav.agents', icon: 'agent' },
]

const messages = {
  zh: {
    'nav.newTask': '新建任务',
    'nav.newSession': '新会话',
    'nav.agents': 'Agent',
    'nav.tools': '工具',
    'nav.allTasks': '所有任务',
    'nav.allSessions': '所有会话',
    'common.refresh': '刷新',
    'common.defaultAgent': '默认 Agent',
    'common.ready': '就绪',
    'common.running': 'Agent 正在处理',
    'common.advanced': '高级设置',
    'common.status': '状态',
    'common.agent': 'Agent',
    'common.createdAt': '创建时间',
    'common.duration': '耗时',
    'common.retry': '重试',
    'common.stop': '停止',
    'common.submit': '提交',
    'common.approve': '允许',
    'common.reject': '拒绝',
    'common.language': '语言',
    'common.upload': '上传文件',
    'common.hideSidebar': '隐藏侧边栏',
    'common.showSidebar': '显示侧边栏',
    'common.resizeSidebar': '拖拽调整侧边栏宽度',
    'common.moreOptions': '更多选项',
    'auth.noProviders': '暂无可用登录方式',
    'auth.logout': '退出',
    'auth.requiredTitle': '登录后使用 TaskPilot',
    'auth.requiredDesc': '任务、文件和历史记录会绑定到当前账号。',
    'auth.currentUser': '当前用户',
    'auth.account': '账号',
    'auth.profile': '账号信息',
    'auth.identities': '已绑定登录方式',
    'auth.linkProvider': '绑定登录方式',
    'auth.unlink': '解绑',
    'auth.noIdentities': '暂无绑定的第三方登录方式。',
    'task.current': '当前任务',
    'task.delete': '删除',
    'task.deleteConfirm': '确定删除这个会话吗？删除后无法在任务列表中查看。',
    'task.deleted': '会话已删除',
    'task.deleteFailed': '删除会话失败',
    'sidebar.recent': '最近会话',
    'sessions.eyebrow': '所有会话',
    'sessions.title': '会话历史',
    'sessions.desc': '打开会话、继续追问，并查看每次运行的过程和产物。',
    'sessions.empty': '暂无会话记录。',
    'sessions.search': '搜索会话',
    'home.title': '我能为你做什么？',
    'home.placeholder': '发起一个会话或提出任何问题',
    'advanced.output': '输出格式',
    'advanced.mode': '运行模式',
    'advanced.followAgent': '跟随 Agent 配置',
    'advanced.environment': '运行环境',
    'advanced.local': '本地环境',
    'advanced.sandbox': '沙箱环境',
    'advanced.tools': '本次工具',
    'advanced.noTools': '当前 Agent 暂无工具配置',
    'task.detail': '任务详情',
    'task.artifacts': '任务产物',
    'task.noArtifacts': '暂无产物。',
    'task.needInput': '需要补充信息',
    'task.inputPlaceholder': '补充任务需要的信息',
    'task.submitInput': '提交补充',
    'task.needApproval': '需要先处理工具审批',
    'chat.title': '对话',
    'chat.empty': '选择一个会话或发送第一条消息，Agent 会在这里连续回复。',
    'chat.placeholder': '继续追问或补充任务要求',
    'chat.user': '你',
    'chat.assistant': 'Agent',
    'chat.thinking': 'Agent 正在处理...',
    'chat.loadEarlier': '加载更早消息',
    'chat.loadingEarlier': '正在加载...',
    'chat.overview': '会话概览',
    'chat.messages': '消息',
    'chat.runs': '运行',
    'chat.artifacts': '产物',
    'chat.currentAction': '当前动作',
    'plan.title': '计划',
    'plan.evidence': '证据',
    'plan.notStarted': '未开始',
    'plan.skipped': '已跳过',
    'progress.current': '当前进度',
    'progress.latest': '当前动作',
    'progress.events': '过程',
    'progress.tools': '工具调用',
    'progress.none': '任务开始后会显示当前动作、工具调用和简短结果。',
    'progress.started': '开始',
    'progress.running': '进行中',
    'progress.completed': '完成',
    'progress.failed': '失败',
    'progress.waiting': '等待',
    'approval.requested': '需要审批',
    'approval.approved': '已允许',
    'approval.rejected': '已拒绝',
    'approval.processing': '处理中',
    'approval.none': '暂无待处理审批',
    'tasks.eyebrow': '所有任务',
    'tasks.title': '任务历史',
    'tasks.desc': '查看任务状态、结果、失败原因和历史过程。',
    'tasks.empty': '暂无任务记录。',
    'filter.search': '搜索任务',
    'filter.allStatus': '全部状态',
    'filter.allAgents': '全部 Agent',
    'filter.allTypes': '全部类型',
    'filter.allTime': '全部时间',
    'filter.hour': '最近 1 小时',
    'filter.today': '今天',
    'filter.week': '最近 7 天',
    'filter.month': '最近 30 天',
    'filter.allDuration': '全部耗时',
    'filter.short': '1 秒内',
    'filter.medium': '1-10 秒',
    'filter.long': '10-60 秒',
    'filter.veryLong': '超过 60 秒',
    'filter.allErrors': '全部错误状态',
    'filter.hasError': '有错误',
    'filter.noError': '无错误',
    'agents.eyebrow': 'Agent',
    'agents.title': '选择适合的 Agent',
    'agents.desc': '每个 Agent 都有自己的能力、工具和边界。',
    'agents.use': '用它新建任务',
    'agents.tools': '查看工具',
    'agents.configWarning': '配置检查异常',
    'tools.eyebrow': '工具',
    'tools.title': 'Agent 可用工具',
    'tools.desc': '查看当前 Agent 可用工具、风险等级和不可用原因。',
    'tools.empty': '当前没有可展示的工具。',
    'tools.noDesc': '暂无说明',
    'tools.reason': '原因',
    'tools.source': '来源',
    'tools.server': '服务',
    'tools.risk': '风险',
    'tools.mcpTitle': 'MCP 服务',
    'tools.mcpDesc': '查看工具来源服务是否可用，以及当前已加载的工具数量。',
    'tools.mcpEmpty': '暂无 MCP 服务配置。',
    'tools.refreshMcp': '刷新 MCP',
    'tools.toolCount': '工具数',
    'tools.checkedAt': '检查时间',
    'status.queued': '排队中',
    'status.idle': '就绪',
    'status.running': '运行中',
    'status.waiting_input': '等待输入',
    'status.waiting_approval': '等待审批',
    'status.completed': '已完成',
    'status.failed': '失败',
    'status.cancelled': '已取消',
    'status.unknown': '未知',
    'timeline.toolCall': '调用工具',
    'timeline.toolDone': '工具完成',
    'timeline.toolFailed': '工具失败',
    'timeline.plan': '任务计划',
    'timeline.agentSelected': 'Agent 选择',
    'timeline.handoff': '任务交接',
    'timeline.failed': '失败事件',
    'timeline.default': '任务事件',
  },
  en: {
    'nav.newTask': 'New Task',
    'nav.newSession': 'New Chat',
    'nav.agents': 'Agents',
    'nav.tools': 'Tools',
    'nav.allTasks': 'All Tasks',
    'nav.allSessions': 'All Chats',
    'common.refresh': 'Refresh',
    'common.defaultAgent': 'Default Agent',
    'common.ready': 'Ready',
    'common.running': 'Agent is working',
    'common.advanced': 'Advanced',
    'common.status': 'Status',
    'common.agent': 'Agent',
    'common.createdAt': 'Created',
    'common.duration': 'Duration',
    'common.retry': 'Retry',
    'common.stop': 'Stop',
    'common.submit': 'Submit',
    'common.approve': 'Allow',
    'common.reject': 'Deny',
    'common.language': 'Language',
    'common.upload': 'Upload files',
    'common.hideSidebar': 'Hide sidebar',
    'common.showSidebar': 'Show sidebar',
    'common.resizeSidebar': 'Drag to resize sidebar',
    'common.moreOptions': 'More options',
    'auth.noProviders': 'No sign-in providers available',
    'auth.logout': 'Log out',
    'auth.requiredTitle': 'Sign in to use TaskPilot',
    'auth.requiredDesc': 'Tasks, files, and history are tied to the current account.',
    'auth.currentUser': 'Current user',
    'auth.account': 'Account',
    'auth.profile': 'Profile',
    'auth.identities': 'Linked sign-in methods',
    'auth.linkProvider': 'Link sign-in method',
    'auth.unlink': 'Unlink',
    'auth.noIdentities': 'No third-party sign-in methods linked.',
    'task.current': 'Current task',
    'task.delete': 'Delete',
    'task.deleteConfirm': 'Delete this conversation? It will be removed from the task list.',
    'task.deleted': 'Conversation deleted',
    'task.deleteFailed': 'Delete failed',
    'sidebar.recent': 'Recent Chats',
    'sessions.eyebrow': 'All Chats',
    'sessions.title': 'Chat History',
    'sessions.desc': 'Open conversations, continue asking, and review each run with events and artifacts.',
    'sessions.empty': 'No conversations yet.',
    'sessions.search': 'Search conversations',
    'home.title': 'What can I do for you?',
    'home.placeholder': 'Start a chat or ask anything',
    'advanced.output': 'Output format',
    'advanced.mode': 'Run mode',
    'advanced.followAgent': 'Follow Agent config',
    'advanced.environment': 'Runtime',
    'advanced.local': 'Local',
    'advanced.sandbox': 'Sandbox',
    'advanced.tools': 'Tools for this task',
    'advanced.noTools': 'No tools configured for this Agent',
    'task.detail': 'Task Detail',
    'task.artifacts': 'Artifacts',
    'task.noArtifacts': 'No artifacts yet.',
    'task.needInput': 'More input needed',
    'task.inputPlaceholder': 'Add the information needed for this task',
    'task.submitInput': 'Submit input',
    'task.needApproval': 'Resolve the tool approval first',
    'chat.title': 'Conversation',
    'chat.empty': 'Select a chat or send the first message. The Agent will keep replying in this thread.',
    'chat.placeholder': 'Ask a follow-up or add more requirements',
    'chat.user': 'You',
    'chat.assistant': 'Agent',
    'chat.thinking': 'Agent is working...',
    'chat.loadEarlier': 'Load earlier messages',
    'chat.loadingEarlier': 'Loading...',
    'chat.overview': 'Chat overview',
    'chat.messages': 'Messages',
    'chat.runs': 'Runs',
    'chat.artifacts': 'Artifacts',
    'chat.currentAction': 'Current action',
    'plan.title': 'Plan',
    'plan.evidence': 'Evidence',
    'plan.notStarted': 'Not started',
    'plan.skipped': 'Skipped',
    'progress.current': 'Current progress',
    'progress.latest': 'Current action',
    'progress.events': 'Events',
    'progress.tools': 'Tool calls',
    'progress.none': 'Current actions, tool calls, and brief results will appear after the task starts.',
    'progress.started': 'Started',
    'progress.running': 'Running',
    'progress.completed': 'Done',
    'progress.failed': 'Failed',
    'progress.waiting': 'Waiting',
    'approval.requested': 'Approval needed',
    'approval.approved': 'Allowed',
    'approval.rejected': 'Denied',
    'approval.processing': 'Processing',
    'approval.none': 'No pending approval',
    'tasks.eyebrow': 'All Tasks',
    'tasks.title': 'Task History',
    'tasks.desc': 'Review task status, results, failures, and process history.',
    'tasks.empty': 'No task records.',
    'filter.search': 'Search tasks',
    'filter.allStatus': 'All statuses',
    'filter.allAgents': 'All Agents',
    'filter.allTypes': 'All types',
    'filter.allTime': 'All time',
    'filter.hour': 'Last hour',
    'filter.today': 'Today',
    'filter.week': 'Last 7 days',
    'filter.month': 'Last 30 days',
    'filter.allDuration': 'Any duration',
    'filter.short': 'Under 1s',
    'filter.medium': '1-10s',
    'filter.long': '10-60s',
    'filter.veryLong': 'Over 60s',
    'filter.allErrors': 'All error states',
    'filter.hasError': 'Has error',
    'filter.noError': 'No error',
    'agents.eyebrow': 'Agents',
    'agents.title': 'Choose the right Agent',
    'agents.desc': 'Each Agent has its own responsibilities, tools, and boundaries.',
    'agents.use': 'Use for new task',
    'agents.tools': 'View tools',
    'agents.configWarning': 'Config issues',
    'tools.eyebrow': 'Tools',
    'tools.title': 'Agent Tools',
    'tools.desc': 'Review available tools, risk levels, and blocked reasons for the current Agent.',
    'tools.empty': 'No tools to display.',
    'tools.noDesc': 'No description',
    'tools.reason': 'Reason',
    'tools.source': 'Source',
    'tools.server': 'Server',
    'tools.risk': 'Risk',
    'tools.mcpTitle': 'MCP Servers',
    'tools.mcpDesc': 'Check whether tool source servers are available and how many tools are loaded.',
    'tools.mcpEmpty': 'No MCP servers configured.',
    'tools.refreshMcp': 'Refresh MCP',
    'tools.toolCount': 'Tools',
    'tools.checkedAt': 'Checked',
    'status.queued': 'Queued',
    'status.idle': 'Ready',
    'status.running': 'Running',
    'status.waiting_input': 'Waiting for input',
    'status.waiting_approval': 'Waiting for approval',
    'status.completed': 'Completed',
    'status.failed': 'Failed',
    'status.cancelled': 'Cancelled',
    'status.unknown': 'Unknown',
    'timeline.toolCall': 'Tool call',
    'timeline.toolDone': 'Tool completed',
    'timeline.toolFailed': 'Tool failed',
    'timeline.plan': 'Task plan',
    'timeline.agentSelected': 'Agent selection',
    'timeline.handoff': 'Task handoff',
    'timeline.failed': 'Failure',
    'timeline.default': 'Task event',
  },
}

const activeView = ref('home')
const savedLanguage = localStorage.getItem('taskpilot-language')
const language = ref(savedLanguage === 'en' ? 'en' : 'zh')
const authLoading = ref(true)
const authRequired = ref(false)
const authenticated = ref(false)
const currentUser = ref(null)
const authProviders = ref([])
const accountLoading = ref(false)
const accountProfile = ref(null)
const accountIdentities = ref([])
const sidebarOpen = ref(false)
const sidebarDensityVersion = 'compact-1'
const savedSidebarDensityVersion = localStorage.getItem('taskpilot-sidebar-density-version')
const savedSidebarWidthValue = savedSidebarDensityVersion === sidebarDensityVersion
  ? localStorage.getItem('taskpilot-sidebar-width')
  : ''
localStorage.setItem('taskpilot-sidebar-density-version', sidebarDensityVersion)
const savedSidebarWidth = Number(savedSidebarWidthValue || 280)
const sidebarWidth = ref(clampSidebarWidth(savedSidebarWidth))
const sidebarCollapsed = ref(localStorage.getItem('taskpilot-sidebar-collapsed') === 'true')
const sidebarResizing = ref(false)
const openTaskMenuId = ref('')
const statusText = ref('Ready')
const running = ref(false)
const streamController = ref(null)
const query = ref('')
const selectedAgentId = ref('')
const outputStyle = ref('markdown')
const runMode = ref('')
const runEnvironment = ref('local')
const advancedOpen = ref(false)
const selectedFiles = ref([])
const fileInputRef = ref(null)
const scrollRef = ref(null)
const chatInput = ref('')
const chatMessages = ref([])
const chatMessageTotal = ref(0)
const chatHasMore = ref(false)
const chatHistoryLoading = ref(false)
const activeAssistantMessageId = ref('')
const currentSessionId = ref(createSessionId())
const currentSession = ref(null)
const currentTaskId = ref('')
const currentTask = ref(null)
const currentEvents = ref([])
const currentArtifacts = ref([])
const finalAnswer = ref('')
const liveTimeline = ref([])
const taskInputText = ref('')
const agents = ref([])
const agentDiagnostics = ref(null)
const toolCatalog = ref([])
const mcpServers = ref([])
const mcpToolCount = ref(0)
const mcpSource = ref('')
const mcpRefreshing = ref(false)
const selectedToolNames = ref(new Set())
const approvedToolNames = ref(new Set())
const toolSelectionTouched = ref(false)
const sessions = ref([])
const sessionWebSocket = ref(null)
const sessionEventSource = ref(null)
const sessionPollTimer = ref(null)
const approvalBusyKey = ref('')
let sessionPollInFlight = false
let sessionStreamExpectedClose = false
const tasks = ref([])
const notifications = ref([])
const NOTIFICATION_TTL_MS = 2800
const notificationTimers = new Map()
const taskFilters = reactive({
  keyword: '',
  status: '',
  agentId: '',
  agentType: '',
  created: '',
  duration: '',
  hasError: '',
})
const sessionFilters = reactive({
  keyword: '',
  status: '',
})

const t = (key) => messages[language.value]?.[key] || messages.zh[key] || key
const lt = (zh, en) => (language.value === 'en' ? en : zh)
const detailSep = () => (language.value === 'en' ? ': ' : '：')
const withDetail = (zhLabel, enLabel, detail = '') => `${lt(zhLabel, enLabel)}${detail ? `${detailSep()}${detail}` : ''}`
const serviceError = (status) => withDetail('服务返回', 'Server returned', status)

const navItems = computed(() => navBase.map((item) => ({
  ...item,
  label: t(item.labelKey),
})))

const shellStyle = computed(() => ({
  '--sidebar-width': `${sidebarWidth.value}px`,
}))

const topbarSidebarTitle = computed(() => {
  if (sidebarCollapsed.value || !sidebarOpen.value) return t('common.showSidebar')
  return t('common.hideSidebar')
})

watch(language, (next) => {
  localStorage.setItem('taskpilot-language', next)
  document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN'
  if (!running.value) statusText.value = t('common.ready')
}, { immediate: true })

watch(sidebarWidth, (next) => {
  localStorage.setItem('taskpilot-sidebar-width', String(next))
})

watch(sidebarCollapsed, (next) => {
  localStorage.setItem('taskpilot-sidebar-collapsed', String(next))
})

const currentAgent = computed(() => {
  const wanted = selectedAgentId.value || defaultAgentId.value
  return agents.value.find((agent) => agent.id === wanted) || null
})

const defaultAgentId = computed(() => {
  const general = agents.value.find((agent) => agent.id === 'task-pilot-agent')
  return general?.id || agents.value[0]?.id || ''
})

const recentSessions = computed(() => sessions.value.slice(0, 8))
const agentTypes = computed(() => [...new Set(agents.value.map((agent) => agent.type).filter(Boolean))].sort())
const taskCanRetry = computed(() => ['completed', 'failed', 'cancelled'].includes(currentTask.value?.status || ''))
const taskWaitingInput = computed(() => currentTask.value?.status === 'waiting_input')
const taskWaitingApproval = computed(() => currentTask.value?.status === 'waiting_approval')
const chatInputDisabled = computed(() => running.value || taskWaitingApproval.value)
const taskMeta = computed(() => {
  if (!currentTask.value) return ''
  const parts = [
    currentTask.value.taskId,
    statusLabel(currentTask.value.status),
    agentName(currentTask.value.agentId),
    currentTask.value.durationMs ? formatDuration(currentTask.value.durationMs) : '',
    currentArtifacts.value.length ? `${t('task.artifacts')} ${currentArtifacts.value.length}` : '',
  ]
  return parts.filter(Boolean).join(' · ')
})

const needsLogin = computed(() => authRequired.value && !authenticated.value)
const enabledAuthProviders = computed(() => authProviders.value.filter((provider) => provider?.provider))
const displayUserName = computed(() => (
  currentUser.value?.displayName
  || currentUser.value?.primaryEmail
  || currentUser.value?.userId
  || ''
))
const availableLinkProviders = computed(() => {
  const linked = new Set(accountIdentities.value
    .filter((identity) => identity.status === 'active')
    .map((identity) => identity.provider))
  return enabledAuthProviders.value.filter((provider) => !linked.has(provider.provider))
})

const mergedTimeline = computed(() => {
  const replay = currentEvents.value.map((event) => normalizeTimelineEvent(event))
  return [...replay, ...liveTimeline.value].filter(Boolean)
})

const progressItems = computed(() => mergedTimeline.value.filter(shouldShowProgressItem).map((item, index) => enrichProgressItem(item, index)))

const progressStats = computed(() => {
  const items = progressItems.value
  return {
    events: items.length,
    tools: items.filter((item) => item.type === 'tool_call').length,
  }
})

const currentProgressItem = computed(() => {
  const items = progressItems.value
  if (!running.value && currentTask.value?.status && currentTask.value.status !== 'running') {
    const status = currentTask.value.status
    const latest = items.length ? items[items.length - 1] : null
    return {
      title: statusLabel(status),
      brief: status === 'failed'
        ? compactSummary(currentTask.value.errorMessage || currentTask.value.error || latest?.title || '', 220)
        : compactSummary(latest?.title || statusLabel(status), 220),
      status: status === 'failed' ? 'failed' : status === 'completed' ? 'completed' : 'running',
    }
  }
  if (!items.length) return null
  const active = [...items].reverse().find((item) => item.status === 'running' || item.status === 'started')
  return running.value && active ? active : items[items.length - 1]
})

const conversationStatusValue = computed(() => (
  currentSession.value?.status
  || currentTask.value?.status
  || (running.value ? 'running' : 'idle')
))

const conversationStatusText = computed(() => statusLabel(conversationStatusValue.value))

const conversationAgentName = computed(() => (
  agentName(currentTask.value?.agentId || currentSession.value?.agentId || selectedAgentId.value || defaultAgentId.value)
  || t('common.defaultAgent')
))

const conversationMessageCount = computed(() => {
  const declared = Number(currentSession.value?.messageCount ?? chatMessageTotal.value)
  const localCount = chatMessages.value.length
  return Number.isFinite(declared) ? Math.max(declared, localCount) : localCount
})

const conversationRunCount = computed(() => {
  const declared = Number(currentSession.value?.runCount)
  if (Number.isFinite(declared)) return declared
  const runs = Array.isArray(currentSession.value?.runs) ? currentSession.value.runs.length : 0
  return runs || (currentTaskId.value ? 1 : 0)
})

const conversationOverviewStats = computed(() => [
  { label: t('chat.messages'), value: conversationMessageCount.value },
  { label: t('chat.runs'), value: conversationRunCount.value },
  { label: t('chat.artifacts'), value: currentArtifacts.value.length },
])

const currentPlanPanel = computed(() => {
  const plans = mergedTimeline.value
    .filter((item) => item && isPlanEvent(item.type))
    .map((item) => planSnapshotFromEvent(item.type, item.raw || {}))
    .filter(Boolean)
  return plans.length ? plans[plans.length - 1] : null
})

watch(selectedAgentId, async () => {
  await refreshToolCatalog()
})

function switchView(view) {
  const allowedViews = new Set(['home', 'tasks', 'agents', 'tools', 'taskDetail', 'account'])
  if (!allowedViews.has(view)) view = 'home'
  openTaskMenuId.value = ''
  activeView.value = view
  sidebarOpen.value = false
  if (view === 'tasks') refreshSessions()
  if (view === 'agents') refreshAgents()
  if (view === 'tools') {
    refreshToolCatalog()
    refreshMcpServers()
  }
  if (view === 'account') loadAccountProfile()
}

function closeTaskMenu() {
  openTaskMenuId.value = ''
}

function toggleTaskMenu(taskId) {
  openTaskMenuId.value = openTaskMenuId.value === taskId ? '' : taskId
}

function taskRecordId(task) {
  return task?.taskId || task?.task_id || ''
}

function sessionRecordId(session) {
  return session?.sessionId || session?.session_id || ''
}

function sessionTitle(session) {
  return session?.title || session?.lastMessagePreview || sessionRecordId(session)
}

function removeTaskFromLists(taskId) {
  tasks.value = tasks.value.filter((item) => taskRecordId(item) !== taskId)
}

function removeSessionFromLists(sessionId) {
  sessions.value = sessions.value.filter((item) => sessionRecordId(item) !== sessionId)
}

function clampSidebarWidth(value) {
  const width = Number.isFinite(value) ? value : 280
  return Math.min(360, Math.max(240, Math.round(width)))
}

function toggleSidebarCollapsed() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  if (sidebarCollapsed.value) sidebarOpen.value = false
  if (!sidebarCollapsed.value) sidebarOpen.value = true
}

function toggleSidebarFromTopbar() {
  if (window.matchMedia('(max-width: 900px)').matches) {
    sidebarOpen.value = !sidebarOpen.value
    if (sidebarOpen.value) sidebarCollapsed.value = false
    return
  }
  toggleSidebarCollapsed()
}

function startSidebarResize(event) {
  if (sidebarCollapsed.value || window.matchMedia('(max-width: 900px)').matches) return
  sidebarResizing.value = true
  document.body.classList.add('resizing-sidebar')
  window.addEventListener('pointermove', resizeSidebar)
  window.addEventListener('pointerup', stopSidebarResize, { once: true })
  event.preventDefault()
}

function resizeSidebar(event) {
  if (!sidebarResizing.value) return
  sidebarWidth.value = clampSidebarWidth(event.clientX)
}

function stopSidebarResize() {
  sidebarResizing.value = false
  document.body.classList.remove('resizing-sidebar')
  window.removeEventListener('pointermove', resizeSidebar)
}

function createSessionId() {
  return `sess_${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function resetConversationState(options = {}) {
  stopSessionStream()
  stopSessionPolling()
  currentSessionId.value = createSessionId()
  currentSession.value = null
  currentTaskId.value = ''
  currentTask.value = null
  currentEvents.value = []
  currentArtifacts.value = []
  finalAnswer.value = ''
  liveTimeline.value = []
  taskInputText.value = ''
  chatInput.value = ''
  chatMessages.value = []
  chatMessageTotal.value = 0
  chatHasMore.value = false
  chatHistoryLoading.value = false
  activeAssistantMessageId.value = ''
  statusText.value = t('common.ready')
  if (options.resetQuery) query.value = ''
  if (options.resetFiles) {
    selectedFiles.value = []
    if (fileInputRef.value) fileInputRef.value.value = ''
  }
}

function newTask() {
  resetConversationState({ resetQuery: true, resetFiles: true })
  switchView('home')
  nextTick(() => document.querySelector('#query')?.focus())
}

function agentName(agentId) {
  const agent = agents.value.find((item) => item.id === agentId)
  return agent?.name || agentId || ''
}

function selectAgent(agentId) {
  selectedAgentId.value = agentId
  switchView('home')
}

function useAgent(agentId) {
  selectAgent(agentId)
  query.value = ''
  nextTick(() => document.querySelector('#query')?.focus())
}

function onFileChange(event) {
  selectedFiles.value = Array.from(event.target.files || [])
}

function removeFile(index) {
  const next = [...selectedFiles.value]
  next.splice(index, 1)
  selectedFiles.value = next
  if (fileInputRef.value) fileInputRef.value.value = ''
}

async function uploadSelectedFiles(requestId) {
  const uploaded = []
  for (const file of selectedFiles.value) {
    const form = new FormData()
    form.append('request_id', requestId)
    form.append('file', file)
    const response = await fetch('/file/v1/upload_file_form', { method: 'POST', body: form })
    if (!response.ok) throw new Error(withDetail('文件上传失败', 'File upload failed', file.name))
    const info = await response.json()
    uploaded.push({
      fileName: info.file_name || file.name,
      description: '',
      ossUrl: info.download_url || '',
      domainUrl: info.preview_url || '',
      fileSize: info.file_size || file.size,
      isInternalFile: false,
    })
  }
  return uploaded
}

async function submitTask() {
  await submitConversationMessage(query.value.trim(), 'home')
}

async function submitChatMessage() {
  await submitConversationMessage(chatInput.value.trim(), 'chat')
}

function chatMessageId(role) {
  return `${role}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function buildPriorConversationMessages() {
  return chatMessages.value
    .filter((message) => ['user', 'assistant'].includes(message.role) && String(message.content || '').trim())
    .map((message) => ({ role: message.role, content: message.content }))
}

function updateActiveAssistant(updates) {
  const index = chatMessages.value.findIndex((message) => message.id === activeAssistantMessageId.value)
  if (index < 0) return
  chatMessages.value[index] = {
    ...chatMessages.value[index],
    ...(typeof updates === 'function' ? updates(chatMessages.value[index]) : updates),
  }
}

function appendAssistantContent(text) {
  if (!text) return
  updateActiveAssistant((message) => ({
    content: `${message.content || ''}${text}`,
    status: 'running',
  }))
}

function seedChatFromTask(task, output) {
  const input = String(task?.input || task?.taskId || '').trim()
  const answer = String(output || '').trim()
  const nextMessages = []
  if (input) {
    nextMessages.push({
      id: chatMessageId('user'),
      role: 'user',
      content: input,
      taskId: task?.taskId || '',
      status: task?.status || '',
    })
  }
  if (answer || ['running', 'waiting_input', 'waiting_approval'].includes(task?.status || '')) {
    nextMessages.push({
      id: chatMessageId('assistant'),
      role: 'assistant',
      content: answer,
      taskId: task?.taskId || '',
      status: task?.status || '',
    })
  }
  chatMessages.value = nextMessages
  activeAssistantMessageId.value = ''
}

function mapBackendMessage(message, fallbackStatus = '') {
  const role = String(message?.role || '').trim().toLowerCase()
  if (!['user', 'assistant'].includes(role)) return null
  return {
    id: message.messageId || chatMessageId(message.role || 'message'),
    role,
    content: String(message.content || ''),
    taskId: message.runId || '',
    status: message.status || fallbackStatus || '',
    time: message.createdAt || Date.now(),
    processOnly: false,
    backend: true,
  }
}

function seedChatFromSession(session, options = {}) {
  const backendMessages = Array.isArray(session?.messages) ? session.messages : []
  const nextMessages = backendMessages
    .map((message) => mapBackendMessage(message, session?.status || ''))
    .filter(Boolean)
  chatMessageTotal.value = Number(session?.messageCount || backendMessages.length || 0)
  chatHasMore.value = chatMessageTotal.value > nextMessages.length
  const latestRun = sessionRunFromPayload(session)
  const processRunId = sessionProcessRunId(session, latestRun)
  const hasAssistantForProcessRun = nextMessages.some((message) => (
    message.role === 'assistant'
    && (!processRunId || message.taskId === processRunId)
  ))
  const processOutput = String(latestRun?.output || '').trim()
  if (!hasAssistantForProcessRun && (processOutput || sessionHasVisibleProgressEvents())) {
    nextMessages.push({
      id: `process-${processRunId || chatMessageId('assistant')}`,
      role: 'assistant',
      content: processOutput,
      taskId: processRunId,
      status: latestRun?.status || session?.status || '',
      time: sessionLatestEventTime() || latestRun?.updatedAt || Date.now(),
      processOnly: !processOutput,
    })
  }
  const activeRunId = session?.currentRunId || currentTaskId.value
  const hasAssistantForActiveRun = nextMessages.some((message) => message.role === 'assistant' && message.taskId === activeRunId)
  if (activeRunId && ['running', 'waiting_input', 'waiting_approval'].includes(session?.status || '') && !hasAssistantForActiveRun) {
    nextMessages.push({
      id: activeAssistantMessageId.value || chatMessageId('assistant'),
      role: 'assistant',
      content: '',
      taskId: activeRunId,
      status: session.status,
      time: Date.now(),
      processOnly: false,
    })
  }
  if (!options.keepLocal || nextMessages.length) {
    chatMessages.value = nextMessages
  }
  let latestAssistantId = ''
  for (let index = chatMessages.value.length - 1; index >= 0; index -= 1) {
    if (chatMessages.value[index]?.role === 'assistant') {
      latestAssistantId = chatMessages.value[index].id
      break
    }
  }
  activeAssistantMessageId.value = ['running', 'waiting_input', 'waiting_approval'].includes(session?.status || '')
    ? latestAssistantId
    : ''
}

function earliestBackendMessageId() {
  const firstMessage = chatMessages.value.find((message) => message.backend && message.id)
  return firstMessage?.id || ''
}

function backendMessageCount() {
  return chatMessages.value.filter((message) => message.backend).length
}

async function loadOlderMessages() {
  const sessionId = currentSessionId.value
  const before = earliestBackendMessageId()
  if (!sessionId || !before || chatHistoryLoading.value || !chatHasMore.value) return
  const scrollEl = scrollRef.value
  const previousHeight = scrollEl?.scrollHeight || 0
  chatHistoryLoading.value = true
  try {
    const params = new URLSearchParams({ limit: '50', before })
    const response = await fetch(`/agent/sessions/${encodeURIComponent(sessionId)}/messages?${params}`)
    if (!response.ok) throw new Error(withDetail('历史消息加载失败', 'Earlier messages failed to load', response.status))
    const data = await response.json()
    const existingIds = new Set(chatMessages.value.map((message) => message.id))
    const olderMessages = (Array.isArray(data.items) ? data.items : [])
      .map((message) => mapBackendMessage(message, currentSession.value?.status || ''))
      .filter(Boolean)
      .filter((message) => !existingIds.has(message.id))
    chatMessages.value = [...olderMessages, ...chatMessages.value]
    chatMessageTotal.value = Number(data.totalCount || data.count || chatMessageTotal.value || 0)
    chatHasMore.value = Boolean(data.hasMore)
    await nextTick()
    if (scrollEl) scrollEl.scrollTop += Math.max((scrollEl.scrollHeight || 0) - previousHeight, 0)
  } catch (error) {
    addNotification(error.message, 'failed')
  } finally {
    chatHistoryLoading.value = false
  }
}

function sessionLatestEventTime() {
  return currentEvents.value.reduce((latest, event) => Math.max(latest, Number(event.createdAt || 0)), 0)
}

function sessionLatestEventRunId() {
  for (let index = currentEvents.value.length - 1; index >= 0; index -= 1) {
    const event = currentEvents.value[index]
    const payload = event?.payload || {}
    const runId = event?.runId || payload.runId || payload.taskId
    if (runId) return runId
  }
  return ''
}

function sessionProcessRunId(session, latestRun) {
  return session?.currentRunId
    || latestRun?.runId
    || latestRun?.taskId
    || sessionLatestEventRunId()
    || currentTaskId.value
    || ''
}

function sessionHasVisibleProgressEvents() {
  return currentEvents.value.some((event) => {
    const item = normalizeTimelineEvent(event)
    return item && shouldShowProgressItem(item)
  })
}

function sessionRunFromPayload(session) {
  const runs = Array.isArray(session?.runs) ? session.runs : []
  const currentRunId = session?.currentRunId
  if (currentRunId) {
    const current = runs.find((run) => (run.runId || run.taskId) === currentRunId)
    if (current) return current
  }
  return runs[0] || null
}

function applySessionPayload(session, events = [], artifacts = [], options = {}) {
  currentSession.value = session
  currentSessionId.value = session?.sessionId || currentSessionId.value
  if (session?.agentId) selectedAgentId.value = session.agentId
  const run = sessionRunFromPayload(session)
  currentTask.value = run || {
    taskId: session?.currentRunId || '',
    status: session?.status || 'idle',
    agentId: session?.agentId || selectedAgentId.value || defaultAgentId.value,
    input: session?.lastMessagePreview || session?.title || '',
  }
  currentTaskId.value = currentTask.value?.taskId || currentTask.value?.runId || session?.currentRunId || ''
  currentEvents.value = Array.isArray(events) ? events : []
  currentArtifacts.value = Array.isArray(artifacts) ? artifacts : []
  if (Number.isFinite(Number(session?.messageCount))) {
    chatMessageTotal.value = Number(session.messageCount || 0)
    chatHasMore.value = chatMessageTotal.value > backendMessageCount()
  }
  finalAnswer.value = latestAssistantText(session)
  if (!options.keepChat) seedChatFromSession(session, options)
  liveTimeline.value = []
  running.value = session?.status === 'running'
  statusText.value = running.value ? t('common.running') : t('common.ready')
}

function latestAssistantText(session) {
  const backendMessages = Array.isArray(session?.messages) ? session.messages : []
  for (let index = backendMessages.length - 1; index >= 0; index -= 1) {
    const message = backendMessages[index]
    if (message?.role === 'assistant' && String(message.content || '').trim()) {
      return String(message.content || '')
    }
  }
  return ''
}

async function ensureCurrentSession(titleText = '') {
  const sessionId = currentSessionId.value || createSessionId()
  currentSessionId.value = sessionId
  const response = await fetch('/agent/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      title: titleText.trim().slice(0, 40) || undefined,
      agent_id: selectedAgentId.value || defaultAgentId.value || undefined,
    }),
  })
  if (!response.ok) throw new Error(withDetail('会话创建失败', 'Chat creation failed', response.status))
  currentSession.value = await response.json()
  await refreshSessions()
  return currentSession.value
}

async function refreshSessions() {
  if (needsLogin.value) {
    sessions.value = []
    return
  }
  const params = new URLSearchParams({ limit: '50' })
  if (sessionFilters.keyword) params.set('keyword', sessionFilters.keyword)
  if (sessionFilters.status) params.set('status', sessionFilters.status)
  try {
    const response = await fetch(`/agent/sessions?${params}`)
    if (!response.ok) throw new Error(serviceError(response.status))
    const data = await response.json()
    sessions.value = Array.isArray(data.items) ? data.items : []
  } catch (error) {
    addNotification(withDetail('会话列表加载失败', 'Chat list failed to load', error.message), 'failed')
  }
}

async function loadSession(sessionId, options = {}) {
  if (!sessionId) return
  openTaskMenuId.value = ''
  try {
    const eventParams = new URLSearchParams({ limit: '2000' })
    const [sessionResponse, eventsResponse, artifactsResponse] = await Promise.all([
      fetch(`/agent/sessions/${encodeURIComponent(sessionId)}`),
      fetch(`/agent/sessions/${encodeURIComponent(sessionId)}/events?${eventParams}`),
      fetch(`/agent/sessions/${encodeURIComponent(sessionId)}/artifacts`),
    ])
    if (!sessionResponse.ok) throw new Error(withDetail('会话加载失败', 'Chat failed to load', sessionResponse.status))
    if (!eventsResponse.ok) throw new Error(withDetail('会话过程加载失败', 'Chat events failed to load', eventsResponse.status))
    if (!artifactsResponse.ok) throw new Error(withDetail('会话产物加载失败', 'Chat artifacts failed to load', artifactsResponse.status))
    const session = await sessionResponse.json()
    const eventsData = await eventsResponse.json()
    const artifactsData = await artifactsResponse.json()
    applySessionPayload(
      session,
      Array.isArray(eventsData.items) ? eventsData.items : [],
      Array.isArray(artifactsData.items) ? artifactsData.items : [],
      options,
    )
    activeView.value = 'taskDetail'
    sidebarOpen.value = false
    if (session.status === 'running') {
      if (!options.noStream) startSessionStream(sessionId)
    } else {
      stopSessionStream()
      stopSessionPolling()
    }
    await nextTick()
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  } catch (error) {
    if (!options.silent) addNotification(error.message, 'failed')
  }
}

function maxSessionSeq() {
  return currentEvents.value.reduce((max, event) => Math.max(max, Number(event.seq || 0)), 0)
}

function mergeSessionEvent(event) {
  if (!event) return
  const seq = Number(event.seq || 0)
  if (seq && currentEvents.value.some((item) => Number(item.seq || 0) === seq)) return
  currentEvents.value = [...currentEvents.value, event].sort((left, right) => {
    const leftSeq = Number(left.seq || 0)
    const rightSeq = Number(right.seq || 0)
    if (leftSeq || rightSeq) return leftSeq - rightSeq
    return Number(left.createdAt || 0) - Number(right.createdAt || 0)
  })

  const eventType = event.eventType || event.type
  const payload = event.payload || {}
  if (payload.taskId || event.runId) {
    currentTaskId.value = payload.taskId || event.runId || currentTaskId.value
    currentTask.value = {
      ...(currentTask.value || {}),
      taskId: currentTaskId.value,
      status: currentTask.value?.status || 'running',
    }
    updateActiveAssistant({ taskId: currentTaskId.value, status: 'running' })
  }
  if (eventType === 'result' || eventType === 'agent_stream') {
    const text = payload.result || ''
    finalAnswer.value += text
    appendAssistantContent(text)
  }
  if (eventType === 'waiting_input') {
    if (currentTask.value) currentTask.value.status = 'waiting_input'
    running.value = false
    statusText.value = t('common.ready')
  }
  if (eventType === 'approval_requested' || eventType === 'task_waiting_approval') {
    if (currentTask.value) currentTask.value.status = 'waiting_approval'
    updateActiveAssistant({ status: 'waiting_approval' })
    running.value = false
    statusText.value = t('approval.requested')
  }
  if (eventType === 'task_completed') {
    if (currentTask.value) currentTask.value.status = 'completed'
    updateActiveAssistant({ status: 'completed' })
  }
  if (eventType === 'task_failed') {
    if (currentTask.value) currentTask.value.status = 'failed'
    updateActiveAssistant({ status: 'failed' })
  }
}

function sessionWebSocketUrl(sessionId, afterSeq) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const params = new URLSearchParams({ afterSeq: String(afterSeq) })
  return `${protocol}//${window.location.host}/agent/ws/sessions/${encodeURIComponent(sessionId)}?${params}`
}

async function handleSessionTransportPayload(payload, sessionId) {
  if (!payload || currentSessionId.value !== sessionId) return
  if (payload.type === 'session_event') {
    mergeSessionEvent(payload.event)
    return
  }
  if (payload.type === 'session_status') {
    if (currentSession.value) currentSession.value.status = payload.status
    if (currentTask.value) currentTask.value.status = payload.status === 'running' ? 'running' : currentTask.value.status
    running.value = payload.status === 'running'
    statusText.value = running.value ? t('common.running') : t('common.ready')
    if (payload.status && payload.status !== 'running') {
      sessionStreamExpectedClose = true
      stopSessionStream()
      await loadSession(sessionId, { silent: true, keepChat: true })
      await refreshSessions()
    }
    return
  }
  if (payload.type === 'done') {
    sessionStreamExpectedClose = true
    stopSessionStream()
    await loadSession(sessionId, { silent: true, keepChat: true })
    await refreshSessions()
  }
}

function startSessionStream(sessionId = currentSessionId.value) {
  stopSessionStream()
  stopSessionPolling()
  if (!sessionId) return
  const afterSeq = maxSessionSeq()
  sessionStreamExpectedClose = false
  if (typeof WebSocket !== 'undefined') {
    try {
      startSessionWebSocket(sessionId, afterSeq)
      return
    } catch {
      stopSessionStream()
    }
  }
  startSessionEventSource(sessionId, afterSeq)
}

function startSessionWebSocket(sessionId, afterSeq) {
  const socket = new WebSocket(sessionWebSocketUrl(sessionId, afterSeq))
  sessionWebSocket.value = socket
  socket.onmessage = async (message) => {
    let payload = null
    try {
      payload = JSON.parse(message.data || '{}')
    } catch {
      return
    }
    await handleSessionTransportPayload(payload, sessionId)
  }
  socket.onerror = () => {
    if (sessionWebSocket.value === socket) {
      stopSessionStream()
      if (!sessionStreamExpectedClose && currentSessionId.value === sessionId && running.value) {
        startSessionEventSource(sessionId, maxSessionSeq())
      }
    }
  }
  socket.onclose = () => {
    if (sessionWebSocket.value === socket) sessionWebSocket.value = null
    if (!sessionStreamExpectedClose && currentSessionId.value === sessionId && running.value) {
      startSessionEventSource(sessionId, maxSessionSeq())
    }
  }
}

function startSessionEventSource(sessionId, afterSeq = maxSessionSeq()) {
  stopSessionStream()
  stopSessionPolling()
  if (!sessionId) return
  if (typeof EventSource === 'undefined') {
    startSessionPolling(sessionId)
    return
  }
  const source = new EventSource(`/agent/sessions/${encodeURIComponent(sessionId)}/stream?afterSeq=${afterSeq}`)
  sessionEventSource.value = source
  source.onmessage = async (message) => {
    let payload = null
    try {
      payload = JSON.parse(message.data || '{}')
    } catch {
      return
    }
    await handleSessionTransportPayload(payload, sessionId)
  }
  source.onerror = () => {
    stopSessionStream()
    if (!sessionStreamExpectedClose && currentSessionId.value === sessionId && running.value) {
      startSessionPolling(sessionId)
    }
  }
}

function stopSessionStream() {
  if (sessionWebSocket.value) {
    sessionWebSocket.value.close()
    sessionWebSocket.value = null
  }
  if (sessionEventSource.value) {
    sessionEventSource.value.close()
    sessionEventSource.value = null
  }
}

function startSessionPolling(sessionId = currentSessionId.value) {
  stopSessionStream()
  stopSessionPolling()
  sessionPollTimer.value = window.setInterval(async () => {
    if (sessionPollInFlight) return
    sessionPollInFlight = true
    try {
      await loadSession(sessionId, { silent: true, noStream: true })
      await refreshSessions()
    } finally {
      sessionPollInFlight = false
    }
  }, 1500)
}

function stopSessionPolling() {
  if (sessionPollTimer.value) {
    window.clearInterval(sessionPollTimer.value)
    sessionPollTimer.value = null
  }
}

async function submitConversationMessage(text, source) {
  if (!text || running.value) return
  if (taskWaitingApproval.value && source === 'chat') {
    addNotification(t('task.needApproval'), 'waiting')
    return
  }

  if (source === 'home') resetConversationState()
  const userMessage = {
    id: chatMessageId('user'),
    role: 'user',
    content: text,
    status: 'submitted',
    time: Date.now(),
  }
  const assistantMessage = {
    id: chatMessageId('assistant'),
    role: 'assistant',
    content: '',
    status: 'running',
    time: Date.now(),
  }
  chatMessages.value.push(userMessage, assistantMessage)
  activeAssistantMessageId.value = assistantMessage.id

  running.value = true
  statusText.value = t('common.running')
  finalAnswer.value = ''
  liveTimeline.value = []
  currentEvents.value = []
  currentArtifacts.value = []
  currentTask.value = {
    taskId: currentTaskId.value || '',
    input: text,
    status: 'running',
    agentId: selectedAgentId.value || defaultAgentId.value,
  }
  switchView('taskDetail')
  await nextTick()

  streamController.value = new AbortController()
  try {
    await ensureCurrentSession(text)
    const uploadRequestId = currentSessionId.value || `web-${Date.now().toString(36)}`
    const uploadedFiles = selectedFiles.value.length ? await uploadSelectedFiles(uploadRequestId) : []
    const payload = {
      content: text,
      uploadFile: uploadedFiles,
      agent_id: selectedAgentId.value || undefined,
      language: language.value === 'en' ? 'en' : 'ch',
    }
    if (advancedOpen.value) {
      payload.outputStyle = outputStyle.value || undefined
      payload.mode = runMode.value || undefined
      payload.run_environment = runEnvironment.value || undefined
      const selected = [...selectedToolNames.value]
      const approved = [...approvedToolNames.value]
      if (toolSelectionTouched.value && selected.length) payload.selected_tools = selected
      if (approved.length) payload.approved_tools = approved
    }

    if (source === 'chat') chatInput.value = ''
    else query.value = ''
    selectedFiles.value = []
    if (fileInputRef.value) fileInputRef.value.value = ''

    const response = await fetch(`/agent/sessions/${encodeURIComponent(currentSessionId.value)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: streamController.value.signal,
    })
    if (!response.ok) throw new Error(serviceError(response.status))
    const data = await response.json()
    currentTaskId.value = data.runId || ''
    currentTask.value = {
      ...(currentTask.value || {}),
      taskId: currentTaskId.value,
      status: data.status || 'running',
      agentId: payload.agent_id || defaultAgentId.value,
    }
    updateActiveAssistant({ taskId: currentTaskId.value, status: 'running' })
    startSessionStream(currentSessionId.value)
  } catch (error) {
    if (error.name !== 'AbortError') {
      addNotification(withDetail('任务执行失败', 'Task failed', error.message), 'failed')
      liveTimeline.value.push({ type: 'error', title: lt('任务执行失败', 'Task failed'), summary: error.message, time: Date.now(), open: true })
      updateActiveAssistant({ content: error.message, status: 'failed' })
      if (currentTask.value) currentTask.value.status = 'failed'
      running.value = false
      statusText.value = t('common.ready')
    }
  } finally {
    streamController.value = null
    await refreshSessions()
    if (currentSessionId.value) await loadSession(currentSessionId.value, { keepChat: true, silent: true })
  }
}

async function readSse(response) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index
    while ((index = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      const event = parseSse(chunk)
      if (event) handleStreamEvent(event)
    }
  }
}

function parseSse(chunk) {
  const lines = chunk.split('\n')
  let type = 'message'
  const dataLines = []
  for (const line of lines) {
    if (line.startsWith('event:')) type = line.slice(6).trim()
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return null
  try {
    const json = JSON.parse(dataLines.join('\n'))
    return { type: json.messageType || type, json, time: Date.now() }
  } catch {
    return { type, json: { result: dataLines.join('\n') }, time: Date.now() }
  }
}

function handleStreamEvent(event) {
  const payload = event.json || {}
  if (payload.taskId) {
    currentTaskId.value = payload.taskId
    currentTask.value = {
      ...(currentTask.value || {}),
      taskId: payload.taskId,
      status: payload.finish ? 'completed' : currentTask.value?.status || 'running',
      agentId: payload.agentId || currentTask.value?.agentId,
      mode: payload.mode || currentTask.value?.mode,
    }
    updateActiveAssistant({
      taskId: payload.taskId,
      status: payload.finish ? 'completed' : 'running',
    })
  }

  if (event.type === 'result' || event.type === 'agent_stream') {
    finalAnswer.value += payload.result || ''
    appendAssistantContent(payload.result || '')
  } else if (event.type === 'done') {
    if (currentTask.value) currentTask.value.status = 'completed'
    updateActiveAssistant({ status: 'completed' })
  } else {
    const item = normalizeTimelineEvent({ eventType: event.type, payload, createdAt: event.time })
    if (item) liveTimeline.value.push(item)
  }
  nextTick(() => {
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  })
}

async function stopTask() {
  if (currentSessionId.value && currentTaskId.value) {
    try {
      await fetch(
        `/agent/sessions/${encodeURIComponent(currentSessionId.value)}/runs/${encodeURIComponent(currentTaskId.value)}/cancel`,
        { method: 'POST' },
      )
      addNotification(language.value === 'en' ? 'Cancel request sent' : '任务取消请求已发送', 'cancelled')
      if (currentTask.value) currentTask.value.status = 'cancelled'
      await refreshSessions()
    } catch (error) {
      addNotification(withDetail('取消失败', 'Cancel failed', error.message), 'failed')
    }
  }
  if (streamController.value) streamController.value.abort()
  updateActiveAssistant({ status: 'cancelled' })
  running.value = false
  statusText.value = t('common.ready')
}

async function retryTask() {
  if (!currentSessionId.value || !currentTaskId.value || running.value) return
  statusText.value = lt('任务重试中', 'Retrying task')
  try {
    const response = await fetch(
      `/agent/sessions/${encodeURIComponent(currentSessionId.value)}/runs/${encodeURIComponent(currentTaskId.value)}/retry`,
      { method: 'POST' },
    )
    if (!response.ok) throw new Error(serviceError(response.status))
    const task = await response.json()
    const retryTaskId = task.runId || task.taskId || task.task_id
    addNotification(language.value === 'en' ? 'Task retried' : '任务已重试', 'running')
    await refreshSessions()
    if (retryTaskId) {
      currentTaskId.value = retryTaskId
      startSessionPolling(currentSessionId.value)
      await loadSession(currentSessionId.value)
    }
  } catch (error) {
    addNotification(withDetail('重试任务失败', 'Retry failed', error.message), 'failed')
  } finally {
    statusText.value = t('common.ready')
  }
}

function approvalToolNames(data = {}) {
  if (Array.isArray(data.requests)) {
    return data.requests
      .map((item) => compactToolName(item?.tool || item?.name || ''))
      .filter(Boolean)
  }
  if (Array.isArray(data.requestedTools) && data.requestedTools.length) {
    return data.requestedTools.map((item) => compactToolName(item)).filter(Boolean)
  }
  if (Array.isArray(data.approvedTools)) {
    return data.approvedTools.map((item) => compactToolName(item)).filter(Boolean)
  }
  return []
}

function rawApprovalToolNames(data = {}) {
  if (Array.isArray(data.requests)) {
    return data.requests
      .map((item) => String(item?.tool || item?.name || '').trim())
      .filter(Boolean)
  }
  if (Array.isArray(data.requestedTools)) {
    return data.requestedTools.map((item) => String(item || '').trim()).filter(Boolean)
  }
  return []
}

function approvalResolvedFor(item) {
  const requestEventId = Number(item?.eventId || 0)
  if (!requestEventId) return false
  return currentEvents.value.some((event) => {
    const payload = event.payload || {}
    return (event.eventType || event.type) === 'approval_resolved'
      && Number(payload.approvalRequestEventId || 0) === requestEventId
  })
}

function approvalStatusText(item) {
  const requestEventId = Number(item?.eventId || 0)
  const resolved = currentEvents.value.find((event) => {
    const payload = event.payload || {}
    return (event.eventType || event.type) === 'approval_resolved'
      && Number(payload.approvalRequestEventId || 0) === requestEventId
  })
  if (!resolved) return t('approval.none')
  return resolved.payload?.approved ? t('approval.approved') : t('approval.rejected')
}

function approvalBusyFor(item, approved) {
  return approvalBusyKey.value === `${item.runId}:${item.eventId}:${approved ? 'approve' : 'reject'}`
}

function approvalPending(item) {
  return item?.type === 'approval_requested'
    && item.runId
    && !running.value
    && !approvalResolvedFor(item)
}

function canResolveApproval(item) {
  return approvalPending(item) && !approvalBusyKey.value
}

async function resolveApproval(item, approved) {
  if (!canResolveApproval(item)) return
  const sessionId = currentSessionId.value
  const runId = item?.runId
  if (!sessionId || !runId) return
  const actionKey = `${runId}:${item.eventId}:${approved ? 'approve' : 'reject'}`
  approvalBusyKey.value = actionKey
  statusText.value = t('approval.processing')
  try {
    const requestedTools = rawApprovalToolNames(item.raw || {})
    const response = await fetch(
      `/agent/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/approval`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approved,
          approvedTools: approved ? requestedTools : [],
          approvalType: item.raw?.approvalType || 'high_risk_tools',
          rerun: approved,
        }),
      },
    )
    if (!response.ok) throw new Error(serviceError(response.status))
    const payload = await response.json()
    addNotification(approved ? t('approval.approved') : t('approval.rejected'), approved ? 'running' : 'cancelled')
    await refreshSessions()
    const nextRunId = payload.runId || payload.taskId || ''
    if (approved && nextRunId) {
      currentTaskId.value = nextRunId
      startSessionPolling(sessionId)
    }
    await loadSession(sessionId, { silent: true, keepChat: true })
  } catch (error) {
    addNotification(withDetail('审批处理失败', 'Approval failed', error.message), 'failed')
  } finally {
    approvalBusyKey.value = ''
    statusText.value = running.value ? t('common.running') : t('common.ready')
  }
}

async function sendTaskInput() {
  const content = taskInputText.value.trim()
  if (!currentSessionId.value || !content) return
  try {
    taskInputText.value = ''
    await submitConversationMessage(content, 'chat')
    addNotification(language.value === 'en' ? 'Input submitted' : '补充信息已提交', 'running')
  } catch (error) {
    addNotification(withDetail('提交补充失败', 'Submit input failed', error.message), 'failed')
  }
}

async function refreshTasks() {
  if (needsLogin.value) {
    tasks.value = []
    return
  }
  const params = new URLSearchParams({ limit: '50' })
  if (taskFilters.keyword) params.set('keyword', taskFilters.keyword)
  if (taskFilters.status) params.set('status', taskFilters.status)
  if (taskFilters.agentId) params.set('agent_id', taskFilters.agentId)
  if (taskFilters.agentType) params.set('agent_type', taskFilters.agentType)
  const createdFrom = createdRange(taskFilters.created)
  if (createdFrom !== null) params.set('created_from', String(createdFrom))
  applyDurationFilter(params, taskFilters.duration)
  if (taskFilters.hasError) params.set('has_error', taskFilters.hasError)
  try {
    const response = await fetch(`/agent/tasks?${params}`)
    if (!response.ok) throw new Error(serviceError(response.status))
    const data = await response.json()
    tasks.value = Array.isArray(data.items) ? data.items : []
  } catch (error) {
      addNotification(withDetail('任务列表加载失败', 'Task list failed to load', error.message), 'failed')
  }
}

function applyAuthPayload(payload = {}) {
  authRequired.value = Boolean(payload.authRequired)
  authenticated.value = Boolean(payload.authenticated)
  currentUser.value = payload.user || null
  authProviders.value = Array.isArray(payload.providers) ? payload.providers : authProviders.value
  accountProfile.value = payload.user || accountProfile.value
}

async function loadAuthProviders() {
  try {
    const response = await fetch('/auth/providers')
    if (!response.ok) return
    const data = await response.json()
    authProviders.value = Array.isArray(data.items) ? data.items : []
  } catch {
    authProviders.value = []
  }
}

async function refreshAuth() {
  authLoading.value = true
  try {
    const response = await fetch('/auth/me')
    if (response.ok) {
      applyAuthPayload(await response.json())
      return
    }
    authRequired.value = response.status === 401
    authenticated.value = false
    currentUser.value = null
    await loadAuthProviders()
  } catch {
    authRequired.value = false
    authenticated.value = false
    currentUser.value = null
    await loadAuthProviders()
  } finally {
    authLoading.value = false
  }
}

function startProviderLogin(provider) {
  const nextPath = `${window.location.pathname}${window.location.search || ''}` || '/agent/web/autoagent'
  window.location.href = `/auth/${encodeURIComponent(provider)}/login?redirect_after=${encodeURIComponent(nextPath)}`
}

function startProviderLink(provider) {
  const nextPath = `${window.location.pathname}${window.location.search || ''}` || '/agent/web/autoagent'
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = `/auth/${encodeURIComponent(provider)}/link?redirect_after=${encodeURIComponent(nextPath)}`
  document.body.appendChild(form)
  form.submit()
}

function providerLabel(provider) {
  return provider?.label || provider?.provider || ''
}

function providerLoginLabel(provider) {
  const name = providerLabel(provider)
  return language.value === 'en' ? `Continue with ${name}` : `使用 ${name} 登录`
}

function providerLinkLabel(provider) {
  const name = providerLabel(provider)
  return language.value === 'en' ? `Link ${name}` : `绑定 ${name}`
}

async function loadAccountProfile() {
  if (!currentUser.value) return
  accountLoading.value = true
  try {
    const response = await fetch('/auth/users/me')
    if (!response.ok) throw new Error(serviceError(response.status))
    const data = await response.json()
    accountProfile.value = data.user || currentUser.value
    currentUser.value = accountProfile.value
    accountIdentities.value = Array.isArray(data.identities) ? data.identities : []
  } catch (error) {
    addNotification(withDetail('账号信息加载失败', 'Account failed to load', error.message), 'failed')
  } finally {
    accountLoading.value = false
  }
}

async function unlinkIdentity(identity) {
  if (!identity?.identityId || !identity?.provider) return
  try {
    const response = await fetch(
      `/auth/${encodeURIComponent(identity.provider)}/link/${encodeURIComponent(identity.identityId)}`,
      { method: 'DELETE' },
    )
    if (!response.ok) throw new Error(serviceError(response.status))
    await loadAccountProfile()
    await refreshAuth()
  } catch (error) {
    addNotification(withDetail('解绑失败', 'Unlink failed', error.message), 'failed')
  }
}

async function logout() {
  await fetch('/auth/logout', { method: 'POST' })
  authenticated.value = false
  currentUser.value = null
  accountProfile.value = null
  accountIdentities.value = []
  newTask()
  await refreshAuth()
  await refreshSessions()
  await refreshTasks()
}

async function loadTask(taskId, options = {}) {
  if (!taskId) return
  openTaskMenuId.value = ''
  try {
    const eventParams = new URLSearchParams({ limit: '2000' })
    const [taskResponse, eventsResponse, artifactsResponse] = await Promise.all([
      fetch(`/agent/tasks/${encodeURIComponent(taskId)}`),
      fetch(`/agent/tasks/${encodeURIComponent(taskId)}/events?${eventParams}`),
      fetch(`/agent/tasks/${encodeURIComponent(taskId)}/artifacts`),
    ])
    if (!taskResponse.ok) throw new Error(withDetail('任务详情加载失败', 'Task detail failed to load', taskResponse.status))
    if (!eventsResponse.ok) throw new Error(withDetail('任务事件加载失败', 'Task events failed to load', eventsResponse.status))
    if (!artifactsResponse.ok) throw new Error(withDetail('任务产物加载失败', 'Task artifacts failed to load', artifactsResponse.status))
    currentTask.value = await taskResponse.json()
    const eventsData = await eventsResponse.json()
    const artifactsData = await artifactsResponse.json()
    currentEvents.value = Array.isArray(eventsData.items) ? eventsData.items : []
    currentArtifacts.value = Array.isArray(artifactsData.items) ? artifactsData.items : []
    finalAnswer.value = currentTask.value.output || ''
    if (!options.keepChat) seedChatFromTask(currentTask.value, finalAnswer.value)
    else if (finalAnswer.value) {
      updateActiveAssistant((message) => ({
        content: message.content || finalAnswer.value,
        status: currentTask.value.status || message.status,
      }))
    }
    liveTimeline.value = []
    currentTaskId.value = taskId
    activeView.value = 'taskDetail'
    sidebarOpen.value = false
    await nextTick()
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  } catch (error) {
    addNotification(error.message, 'failed')
  }
}

async function deleteTask(task) {
  const taskId = taskRecordId(task)
  if (!taskId) return
  openTaskMenuId.value = ''
  try {
    const response = await fetch(`/agent/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
    if (!response.ok && response.status !== 404) throw new Error(serviceError(response.status))
    removeTaskFromLists(taskId)
    const deletedCurrentTask = taskId === currentTaskId.value
    if (deletedCurrentTask) newTask()
    addNotification(t('task.deleted'), 'running')
    await refreshTasks()
  } catch (error) {
    addNotification(`${t('task.deleteFailed')}${detailSep()}${error.message}`, 'failed')
  }
}

async function archiveSession(session) {
  const sessionId = sessionRecordId(session)
  if (!sessionId) return
  openTaskMenuId.value = ''
  try {
    const response = await fetch(`/agent/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
    if (!response.ok && response.status !== 404) throw new Error(serviceError(response.status))
    removeSessionFromLists(sessionId)
    if (sessionId === currentSessionId.value) newTask()
    addNotification(t('task.deleted'), 'running')
    await refreshSessions()
  } catch (error) {
    addNotification(`${t('task.deleteFailed')}${detailSep()}${error.message}`, 'failed')
  }
}

async function refreshAgents() {
  try {
    const [agentResponse, diagnosticsResponse] = await Promise.all([
      fetch('/agent/agents'),
      fetch('/agent/agents/diagnostics'),
    ])
    if (!agentResponse.ok) throw new Error(withDetail('Agent 列表加载失败', 'Agent list failed to load', agentResponse.status))
    const agentData = await agentResponse.json()
    agents.value = Array.isArray(agentData.items) ? agentData.items : []
    if (!selectedAgentId.value) selectedAgentId.value = agentData.defaultAgentId || defaultAgentId.value
    if (diagnosticsResponse.ok) agentDiagnostics.value = await diagnosticsResponse.json()
  } catch (error) {
    addNotification(error.message, 'failed')
  }
}

async function refreshToolCatalog() {
  const agentId = selectedAgentId.value || defaultAgentId.value
  if (!agentId) return
  try {
    const url = currentSession.value
      ? `/agent/sessions/${encodeURIComponent(currentSessionId.value)}/tools?agent_id=${encodeURIComponent(agentId)}`
      : `/agent/tools?agent_id=${encodeURIComponent(agentId)}`
    const response = await fetch(url)
    if (!response.ok) throw new Error(withDetail('工具列表加载失败', 'Tool list failed to load', response.status))
    const data = await response.json()
    toolCatalog.value = [
      ...(Array.isArray(data.items) ? data.items : []),
      ...(Array.isArray(data.blockedTools) ? data.blockedTools : []),
    ]
    selectedToolNames.value = new Set(toolCatalog.value.filter((tool) => tool.allowed !== false).map((tool) => tool.name))
    approvedToolNames.value = new Set()
    toolSelectionTouched.value = false
  } catch (error) {
    toolCatalog.value = []
    addNotification(error.message, 'failed')
  }
}

async function refreshMcpServers(options = {}) {
  if (needsLogin.value) {
    mcpServers.value = []
    mcpToolCount.value = 0
    mcpSource.value = ''
    return
  }
  const force = Boolean(options.force)
  mcpRefreshing.value = force
  try {
    const response = await fetch(force ? '/agent/mcp/tools/refresh' : '/agent/mcp/servers', {
      method: force ? 'POST' : 'GET',
    })
    if (!response.ok) throw new Error(withDetail('MCP 状态加载失败', 'MCP status failed to load', response.status))
    const data = await response.json()
    mcpServers.value = Array.isArray(data.items) ? data.items : []
    mcpToolCount.value = Number(data.toolCount || 0)
    mcpSource.value = data.source || ''
    if (force) {
      await refreshToolCatalog()
      addNotification(lt('MCP 工具已刷新', 'MCP tools refreshed'), 'running')
    }
  } catch (error) {
    addNotification(error.message, 'failed')
  } finally {
    mcpRefreshing.value = false
  }
}

function toggleTool(tool) {
  const next = new Set(selectedToolNames.value)
  if (next.has(tool.name)) next.delete(tool.name)
  else next.add(tool.name)
  selectedToolNames.value = next
  toolSelectionTouched.value = true

  const approval = new Set(approvedToolNames.value)
  if (toolRequiresApproval(tool) && next.has(tool.name)) approval.add(tool.name)
  else approval.delete(tool.name)
  approvedToolNames.value = approval
}

function toolRequiresApproval(tool) {
  return Boolean(tool.requiresApproval) || tool.blockReason === 'high_risk_requires_enable' || tool.blockReason === 'high_risk_requires_approval'
}

function toolRiskText(tool) {
  const risk = String(tool.riskLevel || tool.policy?.risk || '').toLowerCase()
  if (risk === 'critical') return lt('高风险', 'High risk')
  if (risk === 'high') return lt('高风险', 'High risk')
  if (risk === 'medium') return lt('中风险', 'Medium risk')
  if (risk === 'low') return lt('低风险', 'Low risk')
  if (toolRequiresApproval(tool)) return lt('需授权', 'Needs approval')
  if (tool.allowed === false) return lt('不可用', 'Unavailable')
  return lt('可用', 'Available')
}

function toolSourceText(tool) {
  const source = String(tool.source || '').toLowerCase()
  if (source === 'mcp') return 'MCP'
  if (source === 'builtin') return lt('内置', 'Built-in')
  if (source === 'browser') return lt('浏览器', 'Browser')
  if (source === 'plugin') return lt('插件', 'Plugin')
  return source || lt('未知', 'Unknown')
}

function toolServerText(tool) {
  return tool.mcpServerId || tool.serverId || tool.toolPrefix || ''
}

function toolMetaRows(tool) {
  const rows = [
    { label: t('tools.source'), value: toolSourceText(tool) },
    { label: t('tools.risk'), value: toolRiskText(tool) },
  ]
  const server = toolServerText(tool)
  if (server) rows.push({ label: t('tools.server'), value: server })
  return rows
}

function normalizeTimelineEvent(event) {
  const payload = event.payload || event.json || {}
  const resultMap = payload.resultMap || {}
  const eventType = event.eventType || payload.messageType || event.type || ''
  const summary = eventSummary(eventType, payload)
  if (!summary && !['tool_call', 'tool_result'].includes(eventType)) return null
  const failed = Boolean(payload.error || resultMap.error || resultMap.failed)
  return {
    eventId: event.id || payload.id || '',
    seq: event.seq || '',
    runId: event.runId || payload.runId || payload.taskId || '',
    type: eventType,
    title: timelineTitle(eventType, resultMap, failed, payload),
    summary,
    raw: payload,
    time: event.createdAt || event.time || payload.messageTime || Date.now(),
    open: failed || eventType === 'task_failed' || eventType === 'agent_failed',
  }
}

function timelineTitle(type, resultMap = {}, failed = false, payload = {}) {
  const toolName = resultMap.tool || resultMap.name ? compactToolName(resultMap.tool || resultMap.name) : ''
  if (type === 'tool_call') return toolActionTitle(toolName, toolInput(resultMap))
  if (type === 'tool_result') {
    return toolResultTitle(toolName, toolInput(resultMap), failed)
  }
  if (isPlanEvent(type)) return planEventTitle(type, payload.plan || resultMap || payload)
  if (type === 'tool_thought') return toolThoughtTitle(payload)
  if (type === 'plan_thought') return lt('规划任务', 'Planning task')
  if (type === 'agent_selected') return t('timeline.agentSelected')
  if (type === 'task_handoff_requested') return t('timeline.handoff')
  if (type === 'notifications') return compactSummary(notificationMessage(payload.task || payload.resultMap?.task || ''), 140) || eventTypeName(type)
  if (type.includes('failed')) return t('timeline.failed')
  return eventTypeName(type)
}

function eventSummary(type, payload = {}) {
  const data = payload.resultMap && Object.keys(payload.resultMap).length ? payload.resultMap : payload
  if (type === 'tool_call') return stringify(data.argumentsSummary || data.arguments || data.input || data.args || data.tool || data.name)
  if (type === 'tool_result') return stringify(data.resultSummary || data.result || data.output || data.error || data.chunk)
  if (type === 'tool_thought') return toolThoughtSummary(payload)
  if (type === 'plan_thought') return planThoughtSummary(payload)
  if (isPlanEvent(type)) return planEventSummary(type, data)
  if (type === 'result' || type === 'agent_stream') return ''
  if (type === 'task_created') return withDetail('任务已创建', 'Task created', data.mode)
  if (type === 'task_queued') return withDetail('任务排队中', 'Task queued', data.mode)
  if (type === 'task_running') return lt('任务运行中', 'Task running')
  if (type === 'task_completed') return lt('任务已完成', 'Task completed')
  if (type === 'task_failed') return withDetail('任务失败', 'Task failed', data.error || t('status.unknown'))
  if (type === 'task_cancel_requested') return lt('任务取消请求已发送', 'Cancel request sent')
  if (type === 'task_cancelled') return lt('任务已取消', 'Task cancelled')
  if (type === 'task_retry_requested') return withDetail('任务已重试', 'Task retried', data.retryTaskId || '')
  if (type === 'waiting_input') return withDetail('等待补充输入', 'Waiting for input', data.prompt || '')
  if (type === 'task_waiting_approval') return lt('等待工具审批', 'Waiting for tool approval')
  if (type === 'user_input') return withDetail('用户补充', 'User input', data.content || '')
  if (type === 'task_resume_requested') return lt('任务恢复请求已发送', 'Resume request sent')
  if (type === 'approval_requested') return withDetail('需要审批工具', 'Approval needed', approvalToolNames(data).join(', '))
  if (type === 'approval_resolved') return data.approved
    ? withDetail('已允许工具', 'Tools allowed', approvalToolNames(data).join(', '))
    : withDetail('已拒绝工具', 'Tools denied', approvalToolNames(data).join(', '))
  if (type === 'todo_list_updated') return todoListSummary(data)
  if (type === 'memory_context_loaded') return lt('上下文已检索', 'Context loaded')
  if (type === 'runtime_boundary_applied') return withDetail('运行环境', 'Runtime', data.runEnvironment || 'local')
  if (type === 'tool_policy_applied') return lt(`工具策略已应用：可用 ${(data.availableTools || []).length} 个，拦截 ${(data.blockedTools || []).length} 个`, `Tool policy applied: ${(data.availableTools || []).length} available, ${(data.blockedTools || []).length} blocked`)
  if (type === 'task_artifact_added') return withDetail('任务产物已登记', 'Artifact registered', data.filename || data.artifactId || '')
  if (type === 'eval_run_created') return withDetail('评测任务已创建', 'Eval task created', data.caseId || '')
  if (type === 'eval_result') return withDetail('评测结果', 'Eval result', data.status || '')
  if (type === 'agent_selected') return `${withDetail('Supervisor 已选择 Agent', 'Supervisor selected Agent', data.agentName || data.agentId || '')}${data.reason ? ` · ${data.reason}` : ''}`
  if (type === 'agent_started') return withDetail('Agent 已启动', 'Agent started', data.agentName || data.agentId || '')
  if (type === 'agent_completed') return withDetail('Agent 已完成', 'Agent completed', data.agentName || data.agentId || '')
  if (type === 'agent_failed') return `${withDetail('Agent 失败', 'Agent failed', data.agentName || data.agentId || '')}${data.error ? ` · ${data.error}` : ''}`
  if (type === 'agent_cancelled') return withDetail('Agent 已取消', 'Agent cancelled', data.agentName || data.agentId || '')
  if (type === 'task_handoff_requested') return withDetail('任务已交接', 'Task handed off', `${data.parentAgentId || ''} -> ${data.targetAgentId || ''}`)
  if (type === 'notifications') return notificationMessage(data.task || data.error || '')
  if (type === 'task') return data.task || ''
  return stringify(data.task || data.message || data.error || '')
}

function notificationMessage(value) {
  const parsed = parseJsonValue(value)
  if (parsed && typeof parsed === 'object') return parsed.process_message || parsed.message || parsed.task || stringify(parsed)
  const text = String(value || '')
  const match = text.match(/['"]process_message['"]\s*:\s*['"]([^'"]+)['"]/)
  return match ? match[1] : text
}

function toolThoughtSummary(payload = {}) {
  const thought = payload.toolThought || payload.resultMap || payload
  const step = thought.current_step || thought.currentStep || thought.step || ''
  const calls = Array.isArray(thought.tool_calls) ? thought.tool_calls : []
  const names = calls
    .map((call) => {
      if (Array.isArray(call)) return compactToolName(call[0])
      return compactToolName(call?.name || call?.tool)
    })
    .filter(Boolean)
  return [
    step ? withDetail('当前步骤', 'Current step', step) : '',
    names.length ? withDetail('准备调用工具', 'Preparing tools', names.join(', ')) : '',
  ].filter(Boolean).join(' · ')
}

function toolThoughtTitle(payload = {}) {
  const thought = payload.toolThought || payload.resultMap || payload
  const step = compactSummary(thought.current_step || thought.currentStep || thought.step || '', 140)
  if (step) return step
  const calls = Array.isArray(thought.tool_calls) ? thought.tool_calls : []
  const first = calls[0]
  if (Array.isArray(first)) return toolActionTitle(compactToolName(first[0]), first[1])
  if (first) return toolActionTitle(compactToolName(first.name || first.tool), first.arguments || first.args || first.input)
  return lt('准备调用工具', 'Preparing tool')
}

function toolInput(data = {}) {
  return data.arguments || data.args || data.input || parseJsonValue(data.argumentsSummary || '') || ''
}

function toolOutput(data = {}) {
  return data.resultSummary || data.result || data.output || data.error || data.chunk || ''
}

function toolActionTitle(toolName, input) {
  const name = compactToolName(toolName)
  const target = toolTarget(input)
  if (/search/i.test(name)) return target ? lt(`搜索：${target}`, `Search: ${target}`) : lt('搜索信息', 'Search information')
  if (/read/i.test(name)) return target ? lt(`读取：${target}`, `Read: ${target}`) : lt('读取文件', 'Read file')
  if (/write/i.test(name)) return target ? lt(`写入：${target}`, `Write: ${target}`) : lt('写入文件', 'Write file')
  if (/list/i.test(name)) return target ? lt(`列出：${target}`, `List: ${target}`) : lt('列出文件', 'List files')
  if (/delete/i.test(name)) return target ? lt(`删除：${target}`, `Delete: ${target}`) : lt('删除文件', 'Delete file')
  if (/shell|command|exec/i.test(name)) return target ? lt(`执行命令：${target}`, `Run command: ${target}`) : lt('执行命令', 'Run command')
  return target ? `${name || t('timeline.toolCall')}${detailSep()}${target}` : (name ? lt(`调用工具：${name}`, `Use tool: ${name}`) : t('timeline.toolCall'))
}

function toolResultTitle(toolName, input, failed = false) {
  const name = compactToolName(toolName)
  const target = toolTarget(input)
  const prefix = failed ? t('timeline.toolFailed') : t('timeline.toolDone')
  if (/search/i.test(name)) return target ? lt(`搜索完成：${target}`, `Search complete: ${target}`) : lt('搜索完成', 'Search complete')
  return target ? `${prefix}${detailSep()}${target}` : (name ? `${prefix}${detailSep()}${name}` : prefix)
}

function toolTarget(input) {
  const parsed = parseJsonValue(input)
  if (parsed && typeof parsed === 'object') {
    return compactSummary(parsed.query || parsed.url || parsed.path || parsed.command || parsed.content || parsed.filename || parsed.fileName || parsed.request || parsed.prompt || parsed, 120)
  }
  return compactSummary(parsed, 120)
}

function planThoughtSummary(payload = {}) {
  const thought = payload.planThought || payload.resultMap || payload
  return stringify(thought.current_step || thought.step || thought.plan || thought.message || thought)
}

function isPlanEvent(type) {
  return [
    'plan',
    'plan_created',
    'plan_updated',
    'plan_step_started',
    'plan_step_completed',
    'plan_step_failed',
    'plan_step_updated',
    'plan_completed',
  ].includes(type)
}

function planEventTitle(type, data = {}) {
  if (type === 'plan_created') return withDetail('创建计划', 'Plan created', data.title || '')
  if (type === 'plan_updated') return withDetail('更新计划', 'Plan updated', data.title || '')
  if (type === 'plan_step_started') return withDetail('开始步骤', 'Step started', data.step || data.currentStep || '')
  if (type === 'plan_step_completed') return withDetail('完成步骤', 'Step completed', data.step || data.currentStep || '')
  if (type === 'plan_step_failed') return withDetail('步骤失败', 'Step failed', data.step || data.currentStep || '')
  if (type === 'plan_completed') return withDetail('计划完成', 'Plan completed', data.title || '')
  return t('timeline.plan')
}

function planEventSummary(_type, data = {}) {
  const status = Array.isArray(data.step_status) ? data.step_status : []
  const steps = Array.isArray(data.steps) ? data.steps : []
  const completed = status.filter((item) => item === 'completed').length
  const note = data.note || data.finishReason || data.tool_result || ''
  return [
    steps.length ? withDetail('步骤', 'Steps', `${completed}/${steps.length}`) : '',
    note ? compactSummary(note, 160) : '',
  ].filter(Boolean).join(' · ')
}

function planSnapshotFromEvent(type, payload = {}) {
  const resultMap = payload.resultMap && typeof payload.resultMap === 'object' ? payload.resultMap : {}
  const plan = payload.plan || resultMap.plan || (Array.isArray(payload.steps) ? payload : null) || (Array.isArray(resultMap.steps) ? resultMap : null)
  if (!plan || !Array.isArray(plan.steps) || !plan.steps.length) return null
  const statuses = Array.isArray(plan.step_status) ? plan.step_status : []
  const notes = Array.isArray(plan.notes) ? plan.notes : []
  const evidence = Array.isArray(plan.evidence) ? plan.evidence : []
  const steps = plan.steps.map((step, index) => {
    const status = String(statuses[index] || 'not_started')
    return {
      index: index + 1,
      title: typeof step === 'string' ? step : stringify(step),
      status,
      note: compactSummary(notes[index] || '', 160),
      evidence: planEvidenceSummary(evidence[index]),
    }
  })
  return {
    title: plan.title || t('timeline.plan'),
    status: type === 'plan_completed' ? 'completed' : currentTask.value?.status || '',
    steps,
    completed: steps.filter((step) => ['completed', 'skipped'].includes(step.status)).length,
  }
}

function planEvidenceSummary(items) {
  const list = Array.isArray(items) ? items : (items ? [items] : [])
  return list
    .slice(0, 3)
    .map((item) => {
      if (!item || typeof item !== 'object') return compactSummary(item, 120)
      return compactSummary(item.summary || item.url || item.content || item.tool || item.name || item, 120)
    })
    .filter(Boolean)
    .join(' · ')
}

function planStepStatusClass(status) {
  const value = String(status || '')
  if (value === 'completed' || value === 'skipped') return 'completed'
  if (value === 'failed') return 'failed'
  if (['running', 'in_progress', 'started'].includes(value)) return 'running'
  if (['waiting', 'waiting_input', 'blocked'].includes(value)) return 'waiting'
  return 'waiting'
}

function planStepStatusLabel(status) {
  const value = String(status || '')
  if (value === 'not_started' || value === 'pending') return t('plan.notStarted')
  if (value === 'skipped') return t('plan.skipped')
  return progressStatusLabel(planStepStatusClass(value))
}

function todoListSummary(data = {}) {
  const items = Array.isArray(data.items) ? data.items : Array.isArray(data.todos) ? data.todos : []
  const completed = items.filter((item) => item?.status === 'completed').length
  const running = items.find((item) => ['running', 'waiting', 'blocked'].includes(item?.status))
  return [
    withDetail('TODO', 'Todo', `${completed}/${items.length}`),
    running?.title || data.summary || '',
  ].filter(Boolean).join(' · ')
}

function enrichProgressItem(item, index) {
  return {
    ...item,
    index,
    brief: compactSummary(item.summary),
    toolName: progressToolName(item),
    status: progressStatus(item),
  }
}

function shouldShowProgressItem(item) {
  if (item.type === 'tool_thought') return currentTask.value?.status === 'running'
  if (item.type === 'notifications') return !isNoisyProgressNotification(item.summary || item.title)
  const visibleTypes = new Set([
    'plan',
    'plan_created',
    'plan_updated',
    'plan_step_started',
    'plan_step_completed',
    'plan_step_failed',
    'plan_step_updated',
    'plan_completed',
    'todo_list_updated',
    'plan_thought',
    'tool_call',
    'tool_result',
    'waiting_input',
    'task_waiting_approval',
    'user_input',
    'task_artifact_added',
    'task_completed',
    'task_failed',
    'task_cancelled',
    'agent_failed',
    'task_handoff_requested',
    'approval_requested',
    'approval_resolved',
  ])
  if (visibleTypes.has(item.type)) return true
  if (item.status === 'failed') return true
  return false
}

function isNoisyProgressNotification(value) {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return true
  return [
    'query processquery',
    'query process queries',
    'query extend',
    'start search query',
    'end search query',
  ].some((prefix) => text.startsWith(prefix))
}

function progressToolName(item) {
  const raw = item.raw || {}
  const data = raw.resultMap && Object.keys(raw.resultMap).length ? raw.resultMap : raw
  if (item.type === 'tool_thought') {
    const calls = data.toolThought?.tool_calls || data.tool_calls || []
    const first = Array.isArray(calls) ? calls[0] : null
    if (Array.isArray(first)) return compactToolName(first[0])
    if (first) return compactToolName(first.name || first.tool)
  }
  return compactToolName(data.tool || data.name || raw.tool || raw.name)
}

function progressStatus(item) {
  const raw = item.raw || {}
  const resultMap = raw.resultMap || {}
  if (raw.error || resultMap.error || resultMap.failed || item.type.includes('failed')) return 'failed'
  if (item.type === 'approval_requested') return approvalResolvedFor(item) ? 'completed' : 'waiting'
  if (item.type === 'approval_resolved') return 'completed'
  if (item.type === 'tool_call') return 'started'
  if (item.type === 'tool_result' || item.type === 'task_completed' || item.type === 'agent_completed') return 'completed'
  if (item.type === 'plan_step_completed' || item.type === 'plan_completed') return 'completed'
  if (item.type === 'plan_step_failed') return 'failed'
  if (item.type === 'plan_step_started') return 'running'
  if (item.type === 'todo_list_updated') {
    const items = Array.isArray(resultMap.items) ? resultMap.items : Array.isArray(resultMap.todos) ? resultMap.todos : []
    if (items.some((todo) => todo?.status === 'failed')) return 'failed'
    if (items.some((todo) => ['running', 'waiting', 'blocked'].includes(todo?.status))) return 'running'
    return 'completed'
  }
  if (item.type === 'tool_thought' || item.type === 'plan_thought') return currentTask.value?.status === 'running' ? 'running' : 'completed'
  if (item.type === 'task_running' || item.type === 'agent_started') return 'running'
  return currentTask.value?.status === 'running' ? 'running' : 'completed'
}

function progressStatusLabel(status) {
  return t(`progress.${status || 'running'}`)
}

function isProgressMessage(message, index) {
  if (message.role !== 'assistant') return false
  if (activeAssistantMessageId.value) return message.id === activeAssistantMessageId.value
  for (let i = chatMessages.value.length - 1; i >= 0; i -= 1) {
    if (chatMessages.value[i]?.role === 'assistant') return i === index
  }
  return false
}

function progressDetailRows(item) {
  const raw = item.raw || {}
  const data = raw.resultMap && Object.keys(raw.resultMap).length ? raw.resultMap : raw
  const rows = []
  const add = (label, value, limit = 900) => {
    const text = compactSummary(value, limit)
    if (text) rows.push({ label, value: text })
  }
  add(lt('动作', 'Action'), item.title, 220)
  add(lt('工具', 'Tool'), item.toolName, 120)
  add(lt('状态', 'Status'), progressStatusLabel(item.status), 80)
  if (item.type === 'tool_call' || item.type === 'tool_result') {
    add(lt('输入', 'Input'), toolInput(data), 700)
  }
  if (item.type === 'tool_result') {
    add(lt('输出', 'Output'), toolOutput(data), 1000)
    add(lt('耗时', 'Duration'), data.durationMs ? formatDuration(data.durationMs) : '', 80)
  }
  if (!['tool_call', 'tool_result'].includes(item.type)) {
    add(lt('说明', 'Details'), item.summary, 700)
  }
  add(lt('时间', 'Time'), formatTime(item.time), 80)
  return rows
}

function compactSummary(value, limit = 180) {
  const parsed = parseJsonValue(value)
  const text = typeof parsed === 'object' && parsed !== null ? summarizeObject(parsed) : String(parsed || '')
  const cleaned = text.replace(/\s+/g, ' ').trim()
  if (!cleaned) return ''
  return cleaned.length > limit ? `${cleaned.slice(0, limit)}...` : cleaned
}

function parseJsonValue(value) {
  if (typeof value !== 'string') return value
  const trimmed = value.trim()
  if (!trimmed || !['{', '['].includes(trimmed[0])) return value
  try {
    return JSON.parse(trimmed)
  } catch {
    return value
  }
}

function summarizeObject(value) {
  if (Array.isArray(value)) {
    if (!value.length) return ''
    if (value.length === 1) return summarizeObject(value[0])
    return lt(`${value.length} 条结果`, `${value.length} results`)
  }
  const keys = ['query', 'content', 'prompt', 'path', 'fileName', 'filename', 'url', 'title', 'result', 'output', 'error', 'message']
  const parts = keys
    .filter((key) => value[key] !== undefined && value[key] !== null && value[key] !== '')
    .slice(0, 3)
    .map((key) => `${key}: ${compactSummary(value[key], 80)}`)
  if (parts.length) return parts.join(' · ')
  return JSON.stringify(value)
}

function eventTypeName(type) {
  const names = language.value === 'en' ? {
    task_created: 'Task created',
    task_queued: 'Task queued',
    task_running: 'Task running',
    task_completed: 'Task completed',
    task_failed: 'Task failed',
    tool_call: 'Tool call',
    tool_result: 'Tool result',
    tool_thought: 'Preparing tool',
    plan_thought: 'Planning task',
    todo_list_updated: 'Todo list',
    agent_phase: 'Agent phase',
    memory_context_loaded: 'Context retrieval',
    runtime_boundary_applied: 'Runtime boundary',
    tool_policy_applied: 'Tool policy',
    task_artifact_added: 'Artifact',
    task_waiting_approval: 'Waiting for approval',
    approval_requested: 'Approval needed',
    approval_resolved: 'Approval resolved',
  } : {
    task_created: '任务创建',
    task_queued: '任务排队',
    task_running: '任务运行',
    task_completed: '任务完成',
    task_failed: '任务失败',
    tool_call: '工具调用',
    tool_result: '工具结果',
    tool_thought: '准备调用工具',
    plan_thought: '规划任务',
    todo_list_updated: 'TODO 更新',
    agent_phase: 'Agent 阶段',
    memory_context_loaded: '上下文检索',
    runtime_boundary_applied: '运行边界',
    tool_policy_applied: '工具策略',
    task_artifact_added: '任务产物',
    task_waiting_approval: '等待审批',
    approval_requested: '需要审批',
    approval_resolved: '审批结果',
  }
  return names[type] || type || t('timeline.default')
}

function compactToolName(name) {
  return String(name || '').replace(/^mcp_local[:-]/, '').replace(/^mcp_[^:-]+[:-]/, '')
}

function dismissNotification(id) {
  if (!id) return
  const timer = notificationTimers.get(id)
  if (timer) clearTimeout(timer)
  notificationTimers.delete(id)
  notifications.value = notifications.value.filter((item) => item.id !== id)
}

function addNotification(text, status = 'info', ttl = NOTIFICATION_TTL_MS) {
  const message = String(text || '').trim()
  if (!message) return
  notifications.value
    .filter((item) => item.text === message && item.status === status)
    .forEach((item) => dismissNotification(item.id))

  const id = `${Date.now()}-${Math.random()}`
  notifications.value.unshift({ id, text: message, status, time: Date.now() })
  const overflow = notifications.value.slice(4)
  notifications.value = notifications.value.slice(0, 4)
  overflow.forEach((item) => dismissNotification(item.id))

  if (ttl > 0) {
    notificationTimers.set(id, setTimeout(() => dismissNotification(id), ttl))
  }
}

function statusLabel(status) {
  return t(`status.${status || 'unknown'}`) || status || t('status.unknown')
}

function statusClass(status) {
  return `status-${status || 'unknown'}`
}

function mcpStatusLabel(status) {
  const normalized = String(status || 'unknown')
  if (normalized === 'ok') return lt('可用', 'Available')
  if (normalized === 'error') return lt('异常', 'Error')
  if (normalized === 'configured') return lt('已配置', 'Configured')
  return t('status.unknown')
}

function formatMcpCheckedAt(value) {
  if (!value) return '-'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '-'
  return formatDate(numeric < 1000000000000 ? numeric * 1000 : numeric)
}

function formatDate(timestamp) {
  if (!timestamp) return ''
  return new Date(Number(timestamp)).toLocaleString([], {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatTime(timestamp) {
  if (!timestamp) return ''
  return new Date(Number(timestamp)).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatDuration(ms) {
  if (ms === null || ms === undefined || ms === '') return ''
  const value = Math.max(0, Number(ms) || 0)
  if (value < 1000) return `${value}ms`
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}s`
  return `${Math.round(value / 60000)}m`
}

function createdRange(value) {
  const now = Date.now()
  if (value === 'hour') return now - 60 * 60 * 1000
  if (value === 'today') {
    const start = new Date()
    start.setHours(0, 0, 0, 0)
    return start.getTime()
  }
  if (value === 'week') return now - 7 * 24 * 60 * 60 * 1000
  if (value === 'month') return now - 30 * 24 * 60 * 60 * 1000
  return null
}

function applyDurationFilter(params, value) {
  if (value === 'short') params.set('max_duration_ms', '1000')
  else if (value === 'medium') {
    params.set('min_duration_ms', '1000')
    params.set('max_duration_ms', '10000')
  } else if (value === 'long') {
    params.set('min_duration_ms', '10000')
    params.set('max_duration_ms', '60000')
  } else if (value === 'very-long') {
    params.set('min_duration_ms', '60000')
  }
}

function stringify(value) {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function renderMarkdown(text) {
  const value = String(text || '')
  if (!value.trim()) return ''
  try {
    return DOMPurify.sanitize(marked.parse(value))
  } catch {
    return value.replace(/[&<>"']/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[char]))
  }
}

function artifactHref(item) {
  return `/agent/sessions/${encodeURIComponent(currentSessionId.value)}/artifacts/${encodeURIComponent(item.artifactId)}`
}

function initialSessionIdFromUrl() {
  try {
    return new URLSearchParams(window.location.search || '').get('sessionId') || ''
  } catch {
    return ''
  }
}

onMounted(async () => {
  await refreshAuth()
  if (needsLogin.value) return
  await refreshAgents()
  await refreshSessions()
  await refreshTasks()
  await refreshToolCatalog()
  await refreshMcpServers()
  const initialSessionId = initialSessionIdFromUrl()
  if (initialSessionId) await loadSession(initialSessionId, { silent: true })
})

onBeforeUnmount(() => {
  stopSessionStream()
  stopSessionPolling()
  stopSidebarResize()
  notificationTimers.forEach((timer) => clearTimeout(timer))
  notificationTimers.clear()
})
</script>

<template>
  <div class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed, 'sidebar-resizing': sidebarResizing }" :style="shellStyle" @click="closeTaskMenu">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand-row">
        <div class="brand-mark" aria-label="TaskPilot">
          <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">
            <path class="brand-mark-route" d="M3.8 17.2c3.2-6 7.5-9.6 16-12" />
            <path class="brand-mark-plane" d="M4 19 21 4l-4.4 16.6-4.5-6.4L4 19Z" />
            <circle class="brand-mark-dot" cx="6.8" cy="17.3" r="1.7" />
          </svg>
        </div>
        <div>
          <div class="brand-title">TaskPilot</div>
        </div>
        <button
          type="button"
          class="sidebar-collapse-button"
          :title="t('common.hideSidebar')"
          @click="toggleSidebarCollapsed"
        >
          <span class="panel-toggle-icon" aria-hidden="true"></span>
        </button>
      </div>

      <nav class="main-nav" :aria-label="language === 'en' ? 'Main navigation' : '主导航'">
        <button
          v-for="item in navItems"
          :key="item.id"
          type="button"
          class="nav-item"
          :class="{ active: activeView === item.id }"
          @click="item.id === 'home' ? newTask() : switchView(item.id)"
        >
          <span class="nav-icon" aria-hidden="true">
            <svg v-if="item.icon === 'compose'" viewBox="0 0 24 24" focusable="false">
              <path d="M4 20h4.5L20 8.5a2.1 2.1 0 0 0-3-3L5.5 17 4 20Z" />
              <path d="M14.5 7.5 17 10" />
            </svg>
            <svg v-else viewBox="0 0 24 24" focusable="false">
              <path d="M12 4v2.2M12 17.8V20M4 12h2.2M17.8 12H20" />
              <circle cx="12" cy="12" r="3.2" />
              <path d="M7.3 7.3 5.7 5.7M16.7 16.7l1.6 1.6M16.7 7.3l1.6-1.6M7.3 16.7l-1.6 1.6" />
            </svg>
          </span>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <section class="side-section">
        <div class="side-title">
          <span>{{ t('sidebar.recent') }}</span>
          <button type="button" class="ghost-button small" @click="refreshSessions">{{ t('common.refresh') }}</button>
        </div>
        <div v-if="!recentSessions.length" class="empty-side">{{ t('sessions.empty') }}</div>
        <div
          v-for="session in recentSessions"
          :key="session.sessionId"
          class="recent-task-row"
          :class="{ active: session.sessionId === currentSessionId }"
        >
          <button type="button" class="recent-task" @click="loadSession(session.sessionId)">
            <span class="recent-title">{{ sessionTitle(session) }}</span>
          </button>
          <button
            type="button"
            class="task-menu-button"
            :title="t('common.moreOptions')"
            @click.stop.prevent="toggleTaskMenu(session.sessionId)"
          >
            ⋯
          </button>
          <div v-if="openTaskMenuId === session.sessionId" class="task-menu-popover" @click.stop>
            <button type="button" class="task-menu-danger" @click.stop.prevent="archiveSession(session)">{{ t('task.delete') }}</button>
          </div>
        </div>
      </section>

      <button
        type="button"
        class="sidebar-resize-handle"
        :title="t('common.resizeSidebar')"
        @pointerdown="startSidebarResize"
      ></button>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <div class="topbar-left">
          <button
            type="button"
            class="sidebar-toggle topbar-sidebar-toggle"
            :title="topbarSidebarTitle"
            @click="toggleSidebarFromTopbar"
          >
            <span class="panel-toggle-icon" aria-hidden="true"></span>
          </button>
          <button type="button" class="model-select" @click="switchView('agents')">
            {{ currentAgent?.name || t('common.defaultAgent') }}
            <span>⌄</span>
          </button>
        </div>
        <div class="topbar-right">
          <div v-if="!authLoading" class="auth-area">
            <button v-if="currentUser" type="button" class="user-chip" :title="`${t('auth.currentUser')}：${currentUser.userId}`" @click="switchView('account')">
              <img v-if="currentUser.avatarUrl" class="user-avatar" :src="currentUser.avatarUrl" alt="" />
              <span v-else class="user-avatar user-avatar-fallback">{{ displayUserName.slice(0, 1).toUpperCase() }}</span>
              <span>{{ displayUserName }}</span>
            </button>
            <button v-if="authenticated" type="button" class="ghost-button small" @click="logout">{{ t('auth.logout') }}</button>
            <template v-else>
              <button
                v-for="provider in enabledAuthProviders"
                :key="provider.provider"
                type="button"
                class="ghost-button small"
                @click="startProviderLogin(provider.provider)"
              >
                {{ providerLabel(provider) }}
              </button>
            </template>
          </div>
          <select v-model="language" class="language-select" :title="t('common.language')">
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
          <div class="run-status" :class="{ active: running }">
            <span class="status-dot"></span>
            <span>{{ statusText }}</span>
          </div>
        </div>
      </header>
      <div v-if="notifications.length" class="toast-stack">
        <button
          v-for="(item, index) in notifications.slice(0, 3)"
          :key="item.id"
          type="button"
          class="toast-item"
          :class="`toast-${item.status}`"
          @click="dismissNotification(item.id)"
        >
          {{ item.text }}
        </button>
      </div>

      <section v-if="needsLogin" class="view auth-view">
        <div class="auth-panel">
          <h1>{{ t('auth.requiredTitle') }}</h1>
          <p>{{ t('auth.requiredDesc') }}</p>
          <div class="auth-provider-list">
            <button
              v-for="provider in enabledAuthProviders"
              :key="provider.provider"
              type="button"
              class="primary-button"
              @click="startProviderLogin(provider.provider)"
            >
              {{ providerLoginLabel(provider) }}
            </button>
            <div v-if="!enabledAuthProviders.length" class="empty-card">{{ t('auth.noProviders') }}</div>
          </div>
        </div>
      </section>

      <section v-else-if="activeView === 'account'" class="view account-view">
        <div class="account-shell">
          <div class="account-header">
            <div>
              <div class="section-eyebrow">{{ t('auth.account') }}</div>
              <h1>{{ displayUserName }}</h1>
            </div>
            <button type="button" class="ghost-button" @click="loadAccountProfile">{{ t('common.refresh') }}</button>
          </div>
          <div class="account-grid">
            <section class="account-section">
              <h2>{{ t('auth.profile') }}</h2>
              <div class="profile-row">
                <span>{{ t('auth.currentUser') }}</span>
                <strong>{{ accountProfile?.userId || currentUser?.userId }}</strong>
              </div>
              <div class="profile-row">
                <span>Email</span>
                <strong>{{ accountProfile?.primaryEmail || currentUser?.primaryEmail || '-' }}</strong>
              </div>
              <div class="profile-row">
                <span>{{ t('common.status') }}</span>
                <strong>{{ accountProfile?.status || currentUser?.status || '-' }}</strong>
              </div>
            </section>
            <section class="account-section">
              <h2>{{ t('auth.identities') }}</h2>
              <div v-if="accountLoading" class="empty-card">{{ t('common.running') }}</div>
              <div v-else-if="!accountIdentities.length" class="empty-card">{{ t('auth.noIdentities') }}</div>
              <div v-else class="identity-list">
                <div v-for="identity in accountIdentities" :key="identity.identityId" class="identity-row">
                  <div>
                    <strong>{{ identity.provider }}</strong>
                    <span>{{ identity.email || identity.displayName || identity.providerSubjectType }}</span>
                  </div>
                  <button type="button" class="ghost-button small" @click="unlinkIdentity(identity)">{{ t('auth.unlink') }}</button>
                </div>
              </div>
            </section>
            <section class="account-section">
              <h2>{{ t('auth.linkProvider') }}</h2>
              <div class="auth-provider-list">
                <button
                  v-for="provider in availableLinkProviders"
                  :key="provider.provider"
                  type="button"
                  class="primary-button"
                  @click="startProviderLink(provider.provider)"
                >
                  {{ providerLinkLabel(provider) }}
                </button>
                <div v-if="!availableLinkProviders.length" class="empty-card">{{ t('auth.noProviders') }}</div>
              </div>
            </section>
          </div>
        </div>
      </section>

      <section v-else-if="activeView === 'home'" class="view home-view">
        <div class="hero-block">
          <h1>{{ t('home.title') }}</h1>
          <form class="composer-card" @submit.prevent="submitTask">
            <textarea
              id="query"
              v-model="query"
              rows="3"
              :placeholder="t('home.placeholder')"
              @keydown.enter.exact.prevent="submitTask"
            />
            <div class="composer-actions">
              <div class="left-actions">
                <button type="button" class="icon-button" :title="t('common.upload')" @click="fileInputRef?.click()">＋</button>
                <input ref="fileInputRef" class="sr-only" type="file" multiple @change="onFileChange" />
                <button type="button" class="tool-button" @click="switchView('tools')">
                  <span class="button-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" focusable="false">
                      <path d="M12 3 21 12 12 21 3 12Z" />
                    </svg>
                  </span>
                  <span>{{ t('nav.tools') }}</span>
                </button>
                <select v-model="selectedAgentId" class="agent-picker" :title="t('common.agent')">
                  <option value="">{{ t('common.defaultAgent') }}</option>
                  <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name || agent.id }}</option>
                </select>
              </div>
              <div class="right-actions">
                <button type="button" class="ghost-button" @click="advancedOpen = !advancedOpen">{{ t('common.advanced') }}</button>
                <button type="submit" class="send-button" :disabled="running || !query.trim()">↑</button>
              </div>
            </div>

            <div v-if="selectedFiles.length" class="file-strip">
              <span v-for="(file, index) in selectedFiles" :key="`${file.name}-${index}`" class="file-chip">
                {{ file.name }}
                <button type="button" @click="removeFile(index)">×</button>
              </span>
            </div>

            <div v-if="advancedOpen" class="advanced-panel">
              <label>
                {{ t('advanced.output') }}
                <select v-model="outputStyle">
                  <option value="markdown">Markdown</option>
                  <option value="html">HTML</option>
                  <option value="table">Table</option>
                  <option value="ppt">PPT</option>
                </select>
              </label>
              <label>
                {{ t('advanced.mode') }}
                <select v-model="runMode">
                  <option value="">{{ t('advanced.followAgent') }}</option>
                  <option value="react">ReAct</option>
                  <option value="plans_executor">Legacy Plan Executor</option>
                </select>
              </label>
              <label>
                {{ t('advanced.environment') }}
                <select v-model="runEnvironment">
                  <option value="local">{{ t('advanced.local') }}</option>
                  <option value="sandbox">{{ t('advanced.sandbox') }}</option>
                </select>
              </label>
              <div class="advanced-tools">
                <div class="advanced-title">{{ t('advanced.tools') }}</div>
                <div v-if="!toolCatalog.length" class="muted-text">{{ t('advanced.noTools') }}</div>
                <label v-for="tool in toolCatalog.slice(0, 12)" :key="tool.name" class="tool-check">
                  <input
                    type="checkbox"
                    :checked="selectedToolNames.has(tool.name)"
                    :disabled="tool.allowed === false && !toolRequiresApproval(tool)"
                    @change="toggleTool(tool)"
                  />
                  <span>{{ tool.name }}</span>
                  <em>{{ toolRiskText(tool) }}</em>
                </label>
              </div>
            </div>
          </form>
        </div>
      </section>

      <section v-else-if="activeView === 'taskDetail'" class="view detail-view">
        <div class="conversation-thread">
          <div v-if="running" class="conversation-controls">
            <button type="button" class="ghost-button small" @click="stopTask">{{ t('common.stop') }}</button>
          </div>
          <div class="conversation-overview">
            <div class="conversation-overview-main">
              <div>
                <span class="overview-eyebrow">{{ t('chat.overview') }}</span>
                <strong>{{ conversationAgentName }}</strong>
              </div>
              <span class="status-pill" :class="statusClass(conversationStatusValue)">{{ conversationStatusText }}</span>
            </div>
            <div class="conversation-overview-grid">
              <div v-for="item in conversationOverviewStats" :key="item.label" class="overview-stat">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </div>
            </div>
            <div v-if="currentProgressItem" class="conversation-overview-action">
              <span>{{ t('chat.currentAction') }}</span>
              <strong>{{ currentProgressItem.title }}</strong>
              <small v-if="currentProgressItem.brief">{{ currentProgressItem.brief }}</small>
            </div>
          </div>
          <div v-if="currentPlanPanel" class="conversation-plan-panel">
            <div class="conversation-plan-head">
              <div>
                <span class="overview-eyebrow">{{ t('plan.title') }}</span>
                <strong>{{ currentPlanPanel.title }}</strong>
              </div>
              <span>{{ currentPlanPanel.completed }}/{{ currentPlanPanel.steps.length }}</span>
            </div>
            <ol class="conversation-plan-list">
              <li
                v-for="step in currentPlanPanel.steps"
                :key="`${step.index}-${step.title}`"
                class="plan-step"
                :class="`plan-step-${planStepStatusClass(step.status)}`"
              >
                <span class="timeline-dot" :class="`dot-${planStepStatusClass(step.status)}`"></span>
                <div>
                  <strong>{{ step.index }}. {{ step.title }}</strong>
                  <small>{{ planStepStatusLabel(step.status) }}</small>
                  <p v-if="step.note">{{ step.note }}</p>
                  <p v-if="step.evidence">{{ t('plan.evidence') }}{{ detailSep() }}{{ step.evidence }}</p>
                </div>
              </li>
            </ol>
          </div>
          <div ref="scrollRef" class="conversation-stream">
            <div v-if="chatHasMore" class="chat-history-control">
              <button
                type="button"
                class="ghost-button small"
                :disabled="chatHistoryLoading"
                @click="loadOlderMessages"
              >
                {{ chatHistoryLoading ? t('chat.loadingEarlier') : t('chat.loadEarlier') }}
              </button>
            </div>
            <div v-if="!chatMessages.length" class="muted-text">{{ t('chat.empty') }}</div>
            <div
              v-for="(message, index) in chatMessages"
              :key="message.id"
              class="chat-message"
              :class="`chat-${message.role}`"
            >
              <div class="chat-speaker">
                <template v-if="message.role === 'assistant'">
                  <span class="assistant-mark" aria-hidden="true">
                    <svg viewBox="0 0 24 24" focusable="false">
                      <path class="assistant-mark-route" d="M3.8 17.2c3.2-6 7.5-9.6 16-12" />
                      <path class="assistant-mark-plane" d="M4 19 21 4l-4.4 16.6-4.5-6.4L4 19Z" />
                      <circle class="assistant-mark-dot" cx="6.8" cy="17.3" r="1.7" />
                    </svg>
                  </span>
                  <span>TaskPilot</span>
                  <small>{{ t('chat.assistant') }}</small>
                </template>
                <template v-else>{{ t('chat.user') }}</template>
              </div>
              <div class="chat-bubble">
                <template v-if="message.role === 'assistant'">
                  <div
                    v-if="isProgressMessage(message, index) && progressItems.length"
                    class="conversation-progress"
                  >
                    <div class="progress-action-list">
                      <details v-for="(item, itemIndex) in progressItems" :key="`${item.type}-${item.time}-${itemIndex}`" class="timeline-item progress-item" :open="item.open">
                        <summary>
                          <span class="timeline-dot" :class="`dot-${item.status}`"></span>
                          <span class="progress-main">
                            <strong>{{ item.title }}</strong>
                            <small v-if="item.toolName">{{ item.toolName }}</small>
                          </span>
                          <span class="progress-state" :class="`state-${item.status}`">{{ progressStatusLabel(item.status) }}</span>
                          <time>{{ formatTime(item.time) }}</time>
                        </summary>
                        <div class="progress-detail">
                          <div v-for="row in progressDetailRows(item)" :key="row.label" class="progress-detail-row">
                            <dt>{{ row.label }}</dt>
                            <dd>{{ row.value }}</dd>
                          </div>
                          <div v-if="item.type === 'approval_requested'" class="approval-box">
                            <div class="approval-tool-list">
                              <span v-for="tool in approvalToolNames(item.raw)" :key="tool">{{ tool }}</span>
                            </div>
                            <div v-if="approvalPending(item)" class="approval-actions">
                              <button
                                type="button"
                                class="primary-button small"
                                :disabled="!!approvalBusyKey"
                                @click="resolveApproval(item, true)"
                              >
                                {{ approvalBusyFor(item, true) ? t('approval.processing') : t('common.approve') }}
                              </button>
                              <button
                                type="button"
                                class="ghost-button small"
                                :disabled="!!approvalBusyKey"
                                @click="resolveApproval(item, false)"
                              >
                                {{ approvalBusyFor(item, false) ? t('approval.processing') : t('common.reject') }}
                              </button>
                            </div>
                            <div v-else class="approval-status">{{ approvalStatusText(item) }}</div>
                          </div>
                        </div>
                      </details>
                    </div>
                  </div>
                  <div
                    v-if="message.content"
                    class="markdown-body"
                    v-html="renderMarkdown(message.content)"
                  ></div>
                  <div v-else-if="!message.processOnly" class="muted-text">{{ t('chat.thinking') }}</div>
                </template>
                <div
                  v-else-if="message.content"
                  class="markdown-body"
                  v-html="renderMarkdown(message.content)"
                ></div>
              </div>
            </div>
          </div>

          <div v-if="currentArtifacts.length" class="conversation-artifacts">
            <a
              v-for="artifact in currentArtifacts"
              :key="artifact.artifactId"
              class="artifact-link"
              :href="artifactHref(artifact)"
              target="_blank"
              rel="noreferrer"
            >
              <span>{{ artifact.filename || artifact.artifactId }}</span>
              <small>{{ artifact.mimeType || 'file' }}</small>
            </a>
          </div>

          <section v-if="taskWaitingInput" class="conversation-waiting">
            <textarea v-model="taskInputText" rows="4" :placeholder="t('task.inputPlaceholder')"></textarea>
            <button type="button" class="send-wide" @click="sendTaskInput">{{ t('task.submitInput') }}</button>
          </section>
          <section v-else-if="taskWaitingApproval" class="conversation-waiting">
            <p>{{ t('task.needApproval') }}</p>
          </section>

          <form class="chat-composer" @submit.prevent="submitChatMessage">
            <input ref="fileInputRef" class="sr-only" type="file" multiple @change="onFileChange" />
            <textarea
              v-model="chatInput"
              rows="2"
              :placeholder="t('chat.placeholder')"
              :disabled="chatInputDisabled"
              @keydown.enter.exact.prevent="submitChatMessage"
            />
            <div class="chat-actions">
              <button type="button" class="icon-button" :title="t('common.upload')" :disabled="chatInputDisabled" @click="fileInputRef?.click()">＋</button>
              <button type="submit" class="send-button small-send" :disabled="chatInputDisabled || !chatInput.trim()">↑</button>
            </div>
            <div v-if="selectedFiles.length" class="file-strip chat-files">
              <span v-for="(file, index) in selectedFiles" :key="`${file.name}-${index}`" class="file-chip">
                {{ file.name }}
                <button type="button" @click="removeFile(index)">×</button>
              </span>
            </div>
          </form>
        </div>
      </section>

      <section v-else-if="activeView === 'tasks'" class="view list-view">
        <div class="page-heading">
          <div>
            <div class="eyebrow">{{ t('sessions.eyebrow') }}</div>
            <h2>{{ t('sessions.title') }}</h2>
            <p>{{ t('sessions.desc') }}</p>
          </div>
          <button type="button" class="primary-button" @click="newTask">{{ t('nav.newSession') }}</button>
        </div>
        <div class="filter-bar">
          <input v-model="sessionFilters.keyword" type="search" :placeholder="t('sessions.search')" @input="refreshSessions" />
          <select v-model="sessionFilters.status" @change="refreshSessions">
            <option value="">{{ t('filter.allStatus') }}</option>
            <option value="idle">{{ t('common.ready') }}</option>
            <option value="running">{{ t('status.running') }}</option>
            <option value="waiting_input">{{ t('status.waiting_input') }}</option>
            <option value="waiting_approval">{{ t('status.waiting_approval') }}</option>
          </select>
        </div>
        <div class="task-table">
          <button v-for="session in sessions" :key="session.sessionId" type="button" class="task-row" @click="loadSession(session.sessionId)">
            <span class="task-title">{{ sessionTitle(session) }}</span>
            <span class="status-pill" :class="statusClass(session.status)">{{ statusLabel(session.status) }}</span>
          </button>
          <div v-if="!sessions.length" class="empty-main">{{ t('sessions.empty') }}</div>
        </div>
      </section>

      <section v-else-if="activeView === 'agents'" class="view list-view">
        <div class="page-heading">
          <div>
            <div class="eyebrow">{{ t('agents.eyebrow') }}</div>
            <h2>{{ t('agents.title') }}</h2>
            <p>{{ t('agents.desc') }}</p>
          </div>
          <button type="button" class="primary-button" @click="newTask">{{ t('nav.newSession') }}</button>
        </div>
        <div v-if="agentDiagnostics?.status && agentDiagnostics.status !== 'ok'" class="warning-banner">
          {{ t('agents.configWarning') }}：{{ agentDiagnostics.invalidCount || 0 }}
        </div>
        <div class="agent-grid">
          <article v-for="agent in agents" :key="agent.id" class="agent-card">
            <div class="agent-card-head">
              <div>
                <h3>{{ agent.name || agent.id }}</h3>
                <p>{{ agent.description }}</p>
              </div>
              <span>{{ agent.type }}</span>
            </div>
            <div class="tag-row">
              <span v-for="capability in (agent.capabilities || []).slice(0, 5)" :key="capability">{{ capability }}</span>
            </div>
            <div class="agent-card-actions">
              <button type="button" class="ghost-button" @click="useAgent(agent.id)">{{ t('agents.use') }}</button>
              <button type="button" class="ghost-button" @click="selectedAgentId = agent.id; switchView('tools')">{{ t('agents.tools') }}</button>
            </div>
          </article>
        </div>
      </section>

      <section v-else-if="activeView === 'tools'" class="view list-view">
        <div class="page-heading">
          <div>
            <div class="eyebrow">{{ t('tools.eyebrow') }}</div>
            <h2>{{ t('tools.title') }}</h2>
            <p>{{ t('tools.desc') }}</p>
          </div>
          <div class="heading-actions">
            <select v-model="selectedAgentId" class="compact-select">
              <option value="">{{ t('common.defaultAgent') }}</option>
              <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name || agent.id }}</option>
            </select>
            <button type="button" class="ghost-button small" :disabled="mcpRefreshing" @click="refreshMcpServers({ force: true })">
              {{ mcpRefreshing ? t('common.running') : t('tools.refreshMcp') }}
            </button>
          </div>
        </div>
        <section class="mcp-panel">
          <div class="mcp-panel-head">
            <div>
              <h3>{{ t('tools.mcpTitle') }}</h3>
              <p>{{ t('tools.mcpDesc') }}</p>
            </div>
            <span>{{ t('tools.toolCount') }}：{{ mcpToolCount }}</span>
          </div>
          <div v-if="mcpServers.length" class="mcp-server-list">
            <article v-for="server in mcpServers" :key="`${server.toolPrefix || server.url}-${server.protocol}`" class="mcp-server-card">
              <div class="mcp-server-main">
                <strong>{{ server.toolPrefix || server.url }}</strong>
                <small>{{ server.protocol }} · {{ server.url }}</small>
              </div>
              <span class="status-pill" :class="statusClass(server.status)">{{ mcpStatusLabel(server.status) }}</span>
              <dl>
                <div>
                  <dt>{{ t('tools.toolCount') }}</dt>
                  <dd>{{ server.toolCount || 0 }}</dd>
                </div>
                <div>
                  <dt>{{ t('tools.checkedAt') }}</dt>
                  <dd>{{ formatMcpCheckedAt(server.lastCheckedAt) }}</dd>
                </div>
              </dl>
              <p v-if="server.error" class="mcp-error">{{ server.error }}</p>
            </article>
          </div>
          <div v-else class="empty-main">{{ t('tools.mcpEmpty') }}</div>
        </section>
        <div class="tool-grid">
          <article v-for="tool in toolCatalog" :key="tool.name" class="tool-card" :class="{ disabled: tool.allowed === false }">
            <div class="tool-card-head">
              <h3>{{ tool.name }}</h3>
              <span>{{ toolRiskText(tool) }}</span>
            </div>
            <p>{{ tool.description || tool.purpose || t('tools.noDesc') }}</p>
            <div class="tool-meta-list">
              <span v-for="row in toolMetaRows(tool)" :key="row.label">
                <b>{{ row.label }}</b>
                {{ row.value }}
              </span>
            </div>
            <small v-if="tool.blockReason">{{ t('tools.reason') }}：{{ tool.blockReason }}</small>
          </article>
          <div v-if="!toolCatalog.length" class="empty-main">{{ t('tools.empty') }}</div>
        </div>
      </section>
    </main>
  </div>
</template>
