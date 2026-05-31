<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ breaks: true, gfm: true })

const navBase = [
  { id: 'home', labelKey: 'nav.newTask', icon: '✎' },
  { id: 'tasks', labelKey: 'nav.allTasks', icon: '☷' },
  { id: 'agents', labelKey: 'nav.agents', icon: '◌' },
  { id: 'tools', labelKey: 'nav.tools', icon: '◇' },
]

const messages = {
  zh: {
    'app.subtitle': 'Agent 工作台',
    'nav.newTask': '新建任务',
    'nav.agents': 'Agent',
    'nav.tools': '工具',
    'nav.allTasks': '所有任务',
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
    'common.language': '语言',
    'common.upload': '上传文件',
    'auth.loginGoogle': '使用 Google 登录',
    'auth.logout': '退出',
    'auth.requiredTitle': '登录后使用 TaskPilot',
    'auth.requiredDesc': '任务、文件和历史记录会绑定到当前账号。',
    'auth.currentUser': '当前用户',
    'task.current': '当前任务',
    'sidebar.recent': '最近任务',
    'home.title': '我能为你做什么？',
    'home.placeholder': '分配一个任务或提出任何问题',
    'advanced.output': '输出格式',
    'advanced.mode': '运行模式',
    'advanced.followAgent': '跟随 Agent 配置',
    'advanced.environment': '运行环境',
    'advanced.local': '本地环境',
    'advanced.sandbox': '沙箱环境',
    'advanced.tools': '本次工具',
    'advanced.noTools': '当前 Agent 暂无工具配置',
    'task.detail': '任务详情',
    'task.final': '最终结果',
    'task.finalEmpty': '任务完成后会显示最终结果。',
    'task.timeline': '执行过程',
    'task.timelineEmpty': '任务开始后会显示计划、工具调用和状态变化。',
    'task.artifacts': '任务产物',
    'task.noArtifacts': '暂无产物。',
    'task.needInput': '需要补充信息',
    'task.inputPlaceholder': '补充任务需要的信息',
    'task.submitInput': '提交补充',
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
    'event.all': '全部事件',
    'event.tool': '工具调用',
    'event.agentPhase': 'Agent 阶段',
    'event.failed': '失败事件',
    'event.artifact': '任务产物',
    'event.eval': '评测结果',
    'event.memory': '上下文检索',
    'source.all': '全部来源',
    'source.taskSystem': '任务系统',
    'source.agent': 'Agent',
    'source.tool': '工具',
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
    'status.queued': '排队中',
    'status.running': '运行中',
    'status.waiting_input': '等待输入',
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
    'app.subtitle': 'Agent Workspace',
    'nav.newTask': 'New Task',
    'nav.agents': 'Agents',
    'nav.tools': 'Tools',
    'nav.allTasks': 'All Tasks',
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
    'common.language': 'Language',
    'common.upload': 'Upload files',
    'auth.loginGoogle': 'Continue with Google',
    'auth.logout': 'Log out',
    'auth.requiredTitle': 'Sign in to use TaskPilot',
    'auth.requiredDesc': 'Tasks, files, and history are tied to the current account.',
    'auth.currentUser': 'Current user',
    'task.current': 'Current task',
    'sidebar.recent': 'Recent Tasks',
    'home.title': 'What can I do for you?',
    'home.placeholder': 'Assign a task or ask anything',
    'advanced.output': 'Output format',
    'advanced.mode': 'Run mode',
    'advanced.followAgent': 'Follow Agent config',
    'advanced.environment': 'Runtime',
    'advanced.local': 'Local',
    'advanced.sandbox': 'Sandbox',
    'advanced.tools': 'Tools for this task',
    'advanced.noTools': 'No tools configured for this Agent',
    'task.detail': 'Task Detail',
    'task.final': 'Final Result',
    'task.finalEmpty': 'The final result will appear after the task is complete.',
    'task.timeline': 'Timeline',
    'task.timelineEmpty': 'Plans, tool calls, and status updates will appear after the task starts.',
    'task.artifacts': 'Artifacts',
    'task.noArtifacts': 'No artifacts yet.',
    'task.needInput': 'More input needed',
    'task.inputPlaceholder': 'Add the information needed for this task',
    'task.submitInput': 'Submit input',
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
    'event.all': 'All events',
    'event.tool': 'Tool calls',
    'event.agentPhase': 'Agent phase',
    'event.failed': 'Failures',
    'event.artifact': 'Artifacts',
    'event.eval': 'Eval results',
    'event.memory': 'Context retrieval',
    'source.all': 'All sources',
    'source.taskSystem': 'Task system',
    'source.agent': 'Agent',
    'source.tool': 'Tool',
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
    'status.queued': 'Queued',
    'status.running': 'Running',
    'status.waiting_input': 'Waiting for input',
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
const sidebarOpen = ref(false)
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
const currentSessionId = ref(`sess_${Date.now().toString(36)}`)
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
const selectedToolNames = ref(new Set())
const approvedToolNames = ref(new Set())
const toolSelectionTouched = ref(false)
const tasks = ref([])
const notifications = ref([])
const taskFilters = reactive({
  keyword: '',
  status: '',
  agentId: '',
  agentType: '',
  created: '',
  duration: '',
  hasError: '',
})
const eventFilters = reactive({
  eventType: '',
  source: '',
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

watch(language, (next) => {
  localStorage.setItem('taskpilot-language', next)
  document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN'
  if (!running.value) statusText.value = t('common.ready')
}, { immediate: true })

const currentAgent = computed(() => {
  const wanted = selectedAgentId.value || defaultAgentId.value
  return agents.value.find((agent) => agent.id === wanted) || null
})

const defaultAgentId = computed(() => {
  const general = agents.value.find((agent) => agent.id === 'task-pilot-agent')
  return general?.id || agents.value[0]?.id || ''
})

const recentTasks = computed(() => tasks.value.slice(0, 5))
const agentTypes = computed(() => [...new Set(agents.value.map((agent) => agent.type).filter(Boolean))].sort())
const taskCanRetry = computed(() => ['completed', 'failed', 'cancelled'].includes(currentTask.value?.status || ''))
const taskWaitingInput = computed(() => currentTask.value?.status === 'waiting_input')
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
const googleProvider = computed(() => authProviders.value.find((provider) => provider.provider === 'google') || null)
const displayUserName = computed(() => (
  currentUser.value?.displayName
  || currentUser.value?.primaryEmail
  || currentUser.value?.userId
  || ''
))

const mergedTimeline = computed(() => {
  const replay = currentEvents.value.map((event) => normalizeTimelineEvent(event))
  return [...replay, ...liveTimeline.value].filter(Boolean)
})

watch(selectedAgentId, async () => {
  await refreshToolCatalog()
})

function switchView(view) {
  const allowedViews = new Set(['home', 'tasks', 'agents', 'tools', 'taskDetail'])
  if (!allowedViews.has(view)) view = 'home'
  activeView.value = view
  sidebarOpen.value = false
  if (view === 'tasks') refreshTasks()
  if (view === 'agents') refreshAgents()
  if (view === 'tools') refreshToolCatalog()
}

function newTask() {
  currentSessionId.value = `sess_${Date.now().toString(36)}`
  currentTaskId.value = ''
  currentTask.value = null
  currentEvents.value = []
  currentArtifacts.value = []
  finalAnswer.value = ''
  liveTimeline.value = []
  taskInputText.value = ''
  statusText.value = t('common.ready')
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
  const text = query.value.trim()
  if (!text || running.value) return

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

  const traceId = `web-${Date.now().toString(36)}`
  const uploadedFiles = selectedFiles.value.length ? await uploadSelectedFiles(traceId) : []
  const payload = {
    messages: [{ role: 'user', content: text, uploadFile: uploadedFiles }],
    conversation_id: currentSessionId.value,
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

  query.value = ''
  selectedFiles.value = []
  if (fileInputRef.value) fileInputRef.value.value = ''

  streamController.value = new AbortController()
  try {
    const response = await fetch('/agent/autoagent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: streamController.value.signal,
    })
    if (!response.ok) throw new Error(serviceError(response.status))
    await readSse(response)
  } catch (error) {
    if (error.name !== 'AbortError') {
      addNotification(withDetail('任务执行失败', 'Task failed', error.message), 'failed')
      liveTimeline.value.push({ type: 'error', title: lt('任务执行失败', 'Task failed'), summary: error.message, time: Date.now(), open: true })
      if (currentTask.value) currentTask.value.status = 'failed'
    }
  } finally {
    running.value = false
    streamController.value = null
    statusText.value = t('common.ready')
    await refreshTasks()
    if (currentTaskId.value) await loadTask(currentTaskId.value, { stayOnDetail: true })
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
  }

  if (event.type === 'result' || event.type === 'agent_stream') {
    finalAnswer.value += payload.result || ''
  } else if (event.type === 'done') {
    if (currentTask.value) currentTask.value.status = 'completed'
  } else {
    const item = normalizeTimelineEvent({ eventType: event.type, payload, createdAt: event.time })
    if (item) liveTimeline.value.push(item)
  }
  nextTick(() => {
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  })
}

async function stopTask() {
  if (currentTaskId.value) {
    try {
      await fetch(`/agent/tasks/${encodeURIComponent(currentTaskId.value)}/cancel`, { method: 'POST' })
      addNotification(language.value === 'en' ? 'Cancel request sent' : '任务取消请求已发送', 'cancelled')
      if (currentTask.value) currentTask.value.status = 'cancelled'
      await refreshTasks()
    } catch (error) {
      addNotification(withDetail('取消失败', 'Cancel failed', error.message), 'failed')
    }
  }
  if (streamController.value) streamController.value.abort()
  running.value = false
  statusText.value = t('common.ready')
}

async function retryTask() {
  if (!currentTaskId.value || running.value) return
  statusText.value = lt('任务重试中', 'Retrying task')
  try {
    const response = await fetch(`/agent/tasks/${encodeURIComponent(currentTaskId.value)}/retry`, { method: 'POST' })
    if (!response.ok) throw new Error(serviceError(response.status))
    const task = await response.json()
    const retryTaskId = task.taskId || task.task_id
    addNotification(language.value === 'en' ? 'Task retried' : '任务已重试', 'running')
    await refreshTasks()
    if (retryTaskId) await loadTask(retryTaskId)
  } catch (error) {
    addNotification(withDetail('重试任务失败', 'Retry failed', error.message), 'failed')
  } finally {
    statusText.value = t('common.ready')
  }
}

async function sendTaskInput() {
  const content = taskInputText.value.trim()
  if (!currentTaskId.value || !content) return
  try {
    const response = await fetch(`/agent/tasks/${encodeURIComponent(currentTaskId.value)}/input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, language: language.value === 'en' ? 'en' : 'ch' }),
    })
    if (!response.ok) throw new Error(serviceError(response.status))
    taskInputText.value = ''
    addNotification(language.value === 'en' ? 'Input submitted' : '补充信息已提交', 'running')
    await loadTask(currentTaskId.value)
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

async function logout() {
  await fetch('/auth/logout', { method: 'POST' })
  authenticated.value = false
  currentUser.value = null
  newTask()
  await refreshAuth()
  await refreshTasks()
}

async function loadTask(taskId, options = {}) {
  if (!taskId) return
  try {
    const eventParams = new URLSearchParams({ limit: '2000' })
    if (eventFilters.eventType) eventParams.set('event_type', eventFilters.eventType)
    if (eventFilters.source) eventParams.set('source', eventFilters.source)
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
    liveTimeline.value = []
    currentTaskId.value = taskId
    activeView.value = 'taskDetail'
    sidebarOpen.value = false
    if (!options.stayOnDetail) await nextTick()
  } catch (error) {
    addNotification(error.message, 'failed')
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
    const response = await fetch(`/agent/tools?agent_id=${encodeURIComponent(agentId)}`)
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
  return tool.blockReason === 'high_risk_requires_enable' || tool.blockReason === 'high_risk_requires_approval'
}

function toolRiskText(tool) {
  const risk = String(tool.policy?.risk || '').toLowerCase()
  if (risk === 'critical') return lt('高风险', 'High risk')
  if (risk === 'high') return lt('高风险', 'High risk')
  if (toolRequiresApproval(tool)) return lt('需授权', 'Needs approval')
  if (tool.allowed === false) return lt('不可用', 'Unavailable')
  return lt('可用', 'Available')
}

function normalizeTimelineEvent(event) {
  const payload = event.payload || event.json || {}
  const resultMap = payload.resultMap || {}
  const eventType = event.eventType || payload.messageType || event.type || ''
  const summary = eventSummary(eventType, payload)
  if (!summary && !['tool_call', 'tool_result'].includes(eventType)) return null
  const failed = Boolean(payload.error || resultMap.error || resultMap.failed)
  return {
    type: eventType,
    title: timelineTitle(eventType, resultMap, failed),
    summary,
    raw: payload,
    time: event.createdAt || event.time || payload.messageTime || Date.now(),
    open: failed || eventType === 'task_failed' || eventType === 'agent_failed',
  }
}

function timelineTitle(type, resultMap = {}, failed = false) {
  const toolName = resultMap.tool || resultMap.name ? compactToolName(resultMap.tool || resultMap.name) : ''
  if (type === 'tool_call') return toolName ? `${t('timeline.toolCall')}${detailSep()}${toolName}` : t('timeline.toolCall')
  if (type === 'tool_result') {
    const title = failed ? t('timeline.toolFailed') : t('timeline.toolDone')
    return toolName ? `${title}${detailSep()}${toolName}` : title
  }
  if (type === 'plan') return t('timeline.plan')
  if (type === 'agent_selected') return t('timeline.agentSelected')
  if (type === 'task_handoff_requested') return t('timeline.handoff')
  if (type.includes('failed')) return t('timeline.failed')
  return eventTypeName(type)
}

function eventSummary(type, payload = {}) {
  const data = payload.resultMap && Object.keys(payload.resultMap).length ? payload.resultMap : payload
  if (type === 'tool_call') return stringify(data.argumentsSummary || data.arguments || data.input || data.args || data.tool || data.name)
  if (type === 'tool_result') return stringify(data.resultSummary || data.result || data.output || data.error || data.chunk)
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
  if (type === 'user_input') return withDetail('用户补充', 'User input', data.content || '')
  if (type === 'task_resume_requested') return lt('任务恢复请求已发送', 'Resume request sent')
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
  if (type === 'notifications') return data.task || data.error || ''
  if (type === 'task') return data.task || ''
  return stringify(data.task || data.message || data.error || '')
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
    agent_phase: 'Agent phase',
    memory_context_loaded: 'Context retrieval',
    runtime_boundary_applied: 'Runtime boundary',
    tool_policy_applied: 'Tool policy',
    task_artifact_added: 'Artifact',
  } : {
    task_created: '任务创建',
    task_queued: '任务排队',
    task_running: '任务运行',
    task_completed: '任务完成',
    task_failed: '任务失败',
    tool_call: '工具调用',
    tool_result: '工具结果',
    agent_phase: 'Agent 阶段',
    memory_context_loaded: '上下文检索',
    runtime_boundary_applied: '运行边界',
    tool_policy_applied: '工具策略',
    task_artifact_added: '任务产物',
  }
  return names[type] || type || t('timeline.default')
}

function compactToolName(name) {
  return String(name || '').replace(/^mcp_local[:-]/, '').replace(/^mcp_[^:-]+[:-]/, '')
}

function addNotification(text, status = 'info') {
  notifications.value.unshift({ id: `${Date.now()}-${Math.random()}`, text, status, time: Date.now() })
  notifications.value = notifications.value.slice(0, 8)
}

function statusLabel(status) {
  return t(`status.${status || 'unknown'}`) || status || t('status.unknown')
}

function statusClass(status) {
  return `status-${status || 'unknown'}`
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
  return `/agent/tasks/${encodeURIComponent(currentTaskId.value)}/artifacts/${encodeURIComponent(item.artifactId)}`
}

onMounted(async () => {
  await refreshAuth()
  if (needsLogin.value) return
  await refreshAgents()
  await refreshTasks()
  await refreshToolCatalog()
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="brand-row">
        <div class="brand-mark">T</div>
        <div>
          <div class="brand-title">TaskPilot</div>
          <div class="brand-subtitle">{{ t('app.subtitle') }}</div>
        </div>
      </div>

      <nav class="main-nav" :aria-label="language === 'en' ? 'Main navigation' : '主导航'">
        <button
          v-for="item in navItems"
          :key="item.id"
          type="button"
          class="nav-item"
          :class="{ active: activeView === item.id }"
          @click="switchView(item.id)"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <section class="side-section">
        <div class="side-title">
          <span>{{ t('sidebar.recent') }}</span>
          <button type="button" class="ghost-button small" @click="refreshTasks">{{ t('common.refresh') }}</button>
        </div>
        <div v-if="!recentTasks.length" class="empty-side">{{ t('tasks.empty') }}</div>
        <button
          v-for="task in recentTasks"
          :key="task.taskId"
          type="button"
          class="recent-task"
          :class="{ active: task.taskId === currentTaskId }"
          @click="loadTask(task.taskId)"
        >
          <span class="recent-title">{{ task.input || task.taskId }}</span>
        </button>
      </section>

    </aside>

    <main class="workspace">
      <header class="topbar">
        <div class="topbar-left">
          <button type="button" class="sidebar-toggle" @click="sidebarOpen = !sidebarOpen">☰</button>
          <button type="button" class="model-select" @click="switchView('agents')">
            {{ currentAgent?.name || t('common.defaultAgent') }}
            <span>⌄</span>
          </button>
        </div>
        <div class="topbar-right">
          <div v-if="!authLoading" class="auth-area">
            <div v-if="currentUser" class="user-chip" :title="`${t('auth.currentUser')}：${currentUser.userId}`">
              <img v-if="currentUser.avatarUrl" class="user-avatar" :src="currentUser.avatarUrl" alt="" />
              <span v-else class="user-avatar user-avatar-fallback">{{ displayUserName.slice(0, 1).toUpperCase() }}</span>
              <span>{{ displayUserName }}</span>
            </div>
            <button v-if="authenticated" type="button" class="ghost-button small" @click="logout">{{ t('auth.logout') }}</button>
            <button v-else-if="googleProvider" type="button" class="ghost-button small" @click="startProviderLogin('google')">{{ t('auth.loginGoogle') }}</button>
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
          @click="notifications.splice(index, 1)"
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
              v-if="googleProvider"
              type="button"
              class="primary-button"
              @click="startProviderLogin('google')"
            >
              {{ t('auth.loginGoogle') }}
            </button>
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
              rows="4"
              :placeholder="t('home.placeholder')"
              @keydown.enter.exact.prevent="submitTask"
            />
            <div class="composer-actions">
              <div class="left-actions">
                <button type="button" class="icon-button" :title="t('common.upload')" @click="fileInputRef?.click()">＋</button>
                <input ref="fileInputRef" class="sr-only" type="file" multiple @change="onFileChange" />
                <button type="button" class="tool-button" @click="switchView('tools')">◇ {{ t('nav.tools') }}</button>
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
                  <option value="gaia">Gaia</option>
                </select>
              </label>
              <label>
                {{ t('advanced.mode') }}
                <select v-model="runMode">
                  <option value="">{{ t('advanced.followAgent') }}</option>
                  <option value="plans_executor">Plans Executor</option>
                  <option value="react">ReAct</option>
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
        <div class="detail-header">
          <div>
            <div class="eyebrow">{{ t('task.detail') }}</div>
            <h2>{{ currentTask?.input || t('task.current') }}</h2>
            <p>{{ taskMeta }}</p>
          </div>
          <div class="detail-actions">
            <select v-model="eventFilters.eventType" class="compact-select" @change="loadTask(currentTaskId, { stayOnDetail: true })">
              <option value="">{{ t('event.all') }}</option>
              <option value="tool_call,tool_result">{{ t('event.tool') }}</option>
              <option value="agent_phase">{{ t('event.agentPhase') }}</option>
              <option value="task_failed,agent_failed">{{ t('event.failed') }}</option>
              <option value="task_artifact_added">{{ t('event.artifact') }}</option>
              <option value="eval_result">{{ t('event.eval') }}</option>
              <option value="memory_context_loaded">{{ t('event.memory') }}</option>
            </select>
            <select v-model="eventFilters.source" class="compact-select" @change="loadTask(currentTaskId, { stayOnDetail: true })">
              <option value="">{{ t('source.all') }}</option>
              <option value="sse">SSE</option>
              <option value="autoagent">{{ t('source.taskSystem') }}</option>
              <option value="agent">{{ t('source.agent') }}</option>
              <option value="tool">{{ t('source.tool') }}</option>
            </select>
            <button v-if="taskCanRetry" type="button" class="ghost-button" @click="retryTask">{{ t('common.retry') }}</button>
            <button v-if="running" type="button" class="danger-button" @click="stopTask">{{ t('common.stop') }}</button>
          </div>
        </div>

        <div class="detail-layout">
          <div class="timeline-panel" ref="scrollRef">
            <article class="answer-card">
              <div class="section-title">{{ t('task.final') }}</div>
              <div v-if="finalAnswer" class="markdown-body" v-html="renderMarkdown(finalAnswer)"></div>
              <div v-else class="muted-text">{{ t('task.finalEmpty') }}</div>
            </article>

            <article class="timeline-card">
              <div class="section-title">{{ t('task.timeline') }}</div>
              <div v-if="!mergedTimeline.length" class="muted-text">{{ t('task.timelineEmpty') }}</div>
              <details v-for="(item, index) in mergedTimeline" :key="`${item.type}-${item.time}-${index}`" class="timeline-item" :open="item.open">
                <summary>
                  <span class="timeline-dot"></span>
                  <span>{{ item.title }}</span>
                  <time>{{ formatTime(item.time) }}</time>
                </summary>
                <pre>{{ item.summary }}</pre>
              </details>
            </article>
          </div>

          <aside class="inspector-panel">
            <section class="panel-block">
              <div class="section-title">{{ t('task.detail') }}</div>
              <dl class="meta-list">
                <div><dt>{{ t('common.status') }}</dt><dd><span class="status-pill" :class="statusClass(currentTask?.status)">{{ statusLabel(currentTask?.status) }}</span></dd></div>
                <div><dt>{{ t('common.agent') }}</dt><dd>{{ agentName(currentTask?.agentId) || t('common.defaultAgent') }}</dd></div>
                <div><dt>{{ t('common.createdAt') }}</dt><dd>{{ formatDate(currentTask?.createdAt) }}</dd></div>
                <div><dt>{{ t('common.duration') }}</dt><dd>{{ formatDuration(currentTask?.durationMs) || '-' }}</dd></div>
              </dl>
            </section>

            <section class="panel-block">
              <div class="section-title">{{ t('task.artifacts') }}</div>
              <div v-if="!currentArtifacts.length" class="muted-text">{{ t('task.noArtifacts') }}</div>
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
            </section>

            <section v-if="taskWaitingInput" class="panel-block waiting-block">
              <div class="section-title">{{ t('task.needInput') }}</div>
              <textarea v-model="taskInputText" rows="4" :placeholder="t('task.inputPlaceholder')"></textarea>
              <button type="button" class="send-wide" @click="sendTaskInput">{{ t('task.submitInput') }}</button>
            </section>
          </aside>
        </div>
      </section>

      <section v-else-if="activeView === 'tasks'" class="view list-view">
        <div class="page-heading">
          <div>
            <div class="eyebrow">{{ t('tasks.eyebrow') }}</div>
            <h2>{{ t('tasks.title') }}</h2>
            <p>{{ t('tasks.desc') }}</p>
          </div>
          <button type="button" class="primary-button" @click="newTask">{{ t('nav.newTask') }}</button>
        </div>
        <div class="filter-bar">
          <input v-model="taskFilters.keyword" type="search" :placeholder="t('filter.search')" @input="refreshTasks" />
          <select v-model="taskFilters.status" @change="refreshTasks">
            <option value="">{{ t('filter.allStatus') }}</option>
            <option value="queued">{{ t('status.queued') }}</option>
            <option value="running">{{ t('status.running') }}</option>
            <option value="waiting_input">{{ t('status.waiting_input') }}</option>
            <option value="completed">{{ t('status.completed') }}</option>
            <option value="failed">{{ t('status.failed') }}</option>
            <option value="cancelled">{{ t('status.cancelled') }}</option>
          </select>
          <select v-model="taskFilters.agentId" @change="refreshTasks">
            <option value="">{{ t('filter.allAgents') }}</option>
            <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name || agent.id }}</option>
          </select>
          <select v-model="taskFilters.agentType" @change="refreshTasks">
            <option value="">{{ t('filter.allTypes') }}</option>
            <option v-for="type in agentTypes" :key="type" :value="type">{{ type }}</option>
          </select>
          <select v-model="taskFilters.created" @change="refreshTasks">
            <option value="">{{ t('filter.allTime') }}</option>
            <option value="hour">{{ t('filter.hour') }}</option>
            <option value="today">{{ t('filter.today') }}</option>
            <option value="week">{{ t('filter.week') }}</option>
            <option value="month">{{ t('filter.month') }}</option>
          </select>
          <select v-model="taskFilters.duration" @change="refreshTasks">
            <option value="">{{ t('filter.allDuration') }}</option>
            <option value="short">{{ t('filter.short') }}</option>
            <option value="medium">{{ t('filter.medium') }}</option>
            <option value="long">{{ t('filter.long') }}</option>
            <option value="very-long">{{ t('filter.veryLong') }}</option>
          </select>
          <select v-model="taskFilters.hasError" @change="refreshTasks">
            <option value="">{{ t('filter.allErrors') }}</option>
            <option value="true">{{ t('filter.hasError') }}</option>
            <option value="false">{{ t('filter.noError') }}</option>
          </select>
        </div>
        <div class="task-table">
          <button v-for="task in tasks" :key="task.taskId" type="button" class="task-row" @click="loadTask(task.taskId)">
            <span class="task-title">{{ task.input || task.taskId }}</span>
          </button>
          <div v-if="!tasks.length" class="empty-main">{{ t('tasks.empty') }}</div>
        </div>
      </section>

      <section v-else-if="activeView === 'agents'" class="view list-view">
        <div class="page-heading">
          <div>
            <div class="eyebrow">{{ t('agents.eyebrow') }}</div>
            <h2>{{ t('agents.title') }}</h2>
            <p>{{ t('agents.desc') }}</p>
          </div>
          <button type="button" class="primary-button" @click="newTask">{{ t('nav.newTask') }}</button>
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
          <select v-model="selectedAgentId" class="compact-select">
            <option value="">{{ t('common.defaultAgent') }}</option>
            <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name || agent.id }}</option>
          </select>
        </div>
        <div class="tool-grid">
          <article v-for="tool in toolCatalog" :key="tool.name" class="tool-card" :class="{ disabled: tool.allowed === false }">
            <div class="tool-card-head">
              <h3>{{ tool.name }}</h3>
              <span>{{ toolRiskText(tool) }}</span>
            </div>
            <p>{{ tool.description || tool.purpose || t('tools.noDesc') }}</p>
            <small v-if="tool.blockReason">{{ t('tools.reason') }}：{{ tool.blockReason }}</small>
          </article>
          <div v-if="!toolCatalog.length" class="empty-main">{{ t('tools.empty') }}</div>
        </div>
      </section>
    </main>
  </div>
</template>
