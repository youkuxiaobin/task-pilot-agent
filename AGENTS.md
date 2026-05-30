# AGENTS.md

这个文件用于指导 Codex 在本仓库中工作。后续所有架构设计、功能开发、测试和交付，都要优先遵守这里的约定。

## 项目概览

TaskPilotAgent 是一个基于 Python 和 FastAPI 的通用 Agent 编排框架。当前已经支持任务规划、工具调用、结果总结、多模型接入，以及基于 MCP 的工具聚合。

项目当前能力包括：

- 多模型接入：OpenAI、Claude/Codex、Gemini、OpenAI 兼容服务。
- 当前已有 `plans_executor` 和 `react` 两条执行链路；后续主线应收敛到 React/Supervisor 运行时。
- MCP 工具聚合：本地 MCP 工具和远程 MCP 服务。
- SSE 流式输出：前端可以实时看到计划、思考、工具调用、工具结果和最终答案。
- 文件、消息、记忆、RAG、报告生成等基础能力。

## 产品方向

TaskPilotAgent 的目标不是只做代码助手，而是发展成一个通用 Agent 产品。它要能解决多类型问题：搜索研究、文件处理、数据分析、浏览器任务、报告生成、自动化流程、代码修改、长任务执行等。

后续架构设计必须围绕这条主链路展开：

```text
用户入口
  -> 任务系统
  -> Agent 核心
  -> 技能 / 工具系统
  -> 沙箱运行环境
  -> 记忆 / 知识库
  -> 日志 / 回看 / 评测
  -> 权限 / 风险控制
```

不要在通用层里写死“只服务代码任务”的假设。只有明确属于代码能力的模块，才可以使用代码任务专属设计。

## 框架落地顺序

后续实现必须按下面顺序推进，避免从中间层开工导致返工。

### 第一阶段：任务系统

先建立任务记录、任务事件、状态流转、最终输出和错误保存。所有 Agent、工具、Web 展示都必须挂在任务 ID 上。

完成标准：

- 用户提交后先创建任务。
- 任务有明确状态。
- 过程事件能保存。
- 成功结果和失败原因能回看。

### 第二阶段：工具注册表和 ToolGateway

统一内置工具、MCP 工具和扩展工具。Agent 不直接调用工具，必须通过 ToolGateway。

完成标准：

- 工具有统一描述和 schema。
- ToolGateway 能按 Agent 和权限过滤工具。
- 工具调用和结果写入任务事件。
- 工具错误、超时和脱敏有统一处理。

### 第三阶段：Agent 目录化配置加载

读取 `config/agents/{agent_id}/agent.yaml`、`system_prompt.md`、`evals.yaml`，生成 AgentSpec，并注册到 AgentRegistry。

完成标准：

- 配置能启动时加载。
- 配置错误能被明确报出。
- AgentRegistry 能列出可用 Agent。
- Agent 的 system prompt、工具、权限、交接关系来自目录配置。

### 第四阶段：拆出 `builtin:plan_tool`

把规划能力从 `plans_executor` 独立流程中拆出来，变成 React/Supervisor 可调用的工具。

完成标准：

- 支持创建、读取、更新计划。
- 支持标记步骤运行、完成、失败。
- 计划变化写入任务事件。
- 不把整个 `plans_executor` 包成一个工具。

### 第五阶段：React/Supervisor 主运行时

Supervisor 根据任务和 AgentRegistry 选择目标 Agent；专家 Agent 通过 ToolGateway 调用工具；复杂任务按需调用 `builtin:plan_tool`。

完成标准：

- Supervisor 可以选择 Agent。
- Agent 之间可以交接。
- 每个 Agent 只能看到自己允许的工具。
- 所有 Agent 启动、完成、失败、交接都写入任务事件。

### 第六阶段：Web 任务页

Web 从聊天调试页升级为任务产品页面。

完成标准：

- 有任务创建页。
- 有任务列表页。
- 有任务详情页。
- 详情页能展示时间线、当前 Agent、工具调用、工具结果、最终输出和错误。

### 第七阶段：沙箱、权限和评测

补齐每任务工作目录、高风险审批、权限策略、Agent 冒烟测试和回归任务集。

完成标准：

- 高风险工具有审批或显式策略。
- 工具不能越过任务工作目录。
- Agent 配置变更后能跑对应 evals。
- 常见任务类型有回归样例。

## 目标架构原则

### 1. 用户入口

用户入口包括 Web 页面、API、未来的 webhook、外部渠道、内部自动化入口。入口层只负责接收和标准化请求，不应该承载核心执行逻辑。

要求：

- Web 和 API 必须使用同一套任务模型和状态模型。
- 用户创建任务后，即使离开页面，回来后也能看到任务最新状态。
- SSE/WebSocket 只是实时展示方式，不是任务状态的唯一来源。
- 入口层必须保留用户 ID、Agent ID、会话 ID、文件、运行模式、输出样式等信息。

### 2. 任务系统

任务系统是产品主干。一次 Agent 运行必须是一条可持久化、可查询、可回看的任务，而不只是一次 HTTP 请求或一段 SSE 流。

任务系统应支持：

- 创建、列表、详情、取消、重试、查询任务。
- 状态：`queued`、`running`、`waiting_input`、`completed`、`failed`、`cancelled`。
- 保存输入、输出、错误、耗时、归属用户、运行模式、元数据、用量指标。
- 保存任务事件时间线：计划、步骤变化、工具调用、工具结果、日志、文件、用户补充输入、最终输出。
- 支持后台执行，浏览器断开后长任务仍能继续运行。

新增 Agent 行为时，必须能挂到任务 ID 上，并能持续上报任务事件。

### 3. Agent 核心

Agent 核心负责推理、规划、执行编排和总结。它不应该和 FastAPI 路由、浏览器连接或某个具体页面强绑定。

新的主线设计：

- React/Supervisor 是统一运行时。
- 规划不再作为独立主模式扩张，而是作为 React/Supervisor 可调用的工具能力。
- 旧 `plans_executor` 保留为兼容入口，逐步迁移为 `builtin:plan_tool` + `react_worker` + `summary/review` 的组合。
- 多输出样式：markdown、HTML、表格、PPT、GAIA 风格结果。

要求：

- 关键状态变化必须发出事件。
- 工具调用和工具结果必须结构化记录。
- 错误必须转成任务错误，并保留足够上下文，方便回看和排查。
- 新增 Agent 模式时，必须有聚焦测试，且不能破坏已有模式。

### 3.1 规划能力的定位

规划能力应作为工具进入 React/Supervisor，而不是继续作为一条独立执行框架扩张。

推荐工具名：`builtin:plan_tool`。

`plan_tool` 至少支持：

- `create_plan`：创建计划。
- `update_plan`：根据新信息更新计划。
- `get_plan`：读取当前计划。
- `mark_step_running`：标记步骤开始。
- `mark_step_completed`：标记步骤完成。
- `mark_step_failed`：标记步骤失败。
- `finish_plan`：结束计划。

推荐运行方式：

```text
React/Supervisor Agent
  -> 判断任务是否需要规划
  -> 需要时调用 builtin:plan_tool
  -> 按计划调用工具或交给专家 Agent
  -> 过程中可再次调用 builtin:plan_tool 更新计划
  -> 最后调用 summary/review 能力生成结果
```

不要把整个 `plans_executor` 原封不动包装成一个 React 工具。那会形成“React 里套 PlanSolve 再套 Executor”的嵌套流程，导致状态、日志、取消、错误和 Web 回看很难统一。

适合调用 `plan_tool` 的场景：

- 多步骤任务。
- 长任务。
- 多 Agent 协作任务。
- 需要用户回看计划和进度的任务。
- 失败后需要重规划的任务。
- 需要最终报告或结构化交付物的任务。

不适合强制规划的场景：

- 简单问答。
- 单次工具调用。
- 明确由一个专家 Agent 直接完成的小任务。
- 不需要过程回看的短任务。

### 4. 技能 / 工具系统

技能和工具是产品能力，不只是函数调用。它们必须有清晰的描述、入参、权限、日志和测试。

要求：

- 每个工具必须有稳定名称、描述、输入结构、输出结构和失败行为。
- 暴露给模型前，必须按用户、Agent、任务类型和策略过滤可用工具。
- 每次工具调用都要记录工具名、入参摘要、结果摘要、耗时、失败状态。
- 密钥、token、cookie 等敏感值必须在日志、事件和页面中脱敏。
- 新增工具必须至少有一个代表性测试；如果本地无法测试，必须说明原因。

### 5. 沙箱运行环境

凡是执行代码、Shell 命令、浏览器自动化、文件修改、远程动作的能力，都必须有明确运行边界。

要求：

- 优先使用每个任务独立的工作目录。
- 生成文件和任务产物必须挂到任务记录上。
- 工具不能悄悄写到预期工作区之外。
- 高风险操作必须先经过策略检查。
- 长时间运行的操作必须支持超时、取消和进度事件。

### 6. 记忆 / 知识库

记忆和知识库用于增强任务能力，不能替代任务记录。

要求：

- 会话记忆、任务历史、上传文件、知识检索要有明确归属和范围。
- Agent 使用过的检索结果应能在任务事件或最终证据中追踪。
- 记忆写入必须是有意图、可测试的行为。
- 当记忆或 RAG 不可用时，Agent 应该能降级运行，而不是直接崩溃。

### 7. 日志 / 回看 / 评测

通用 Agent 产品必须能回看任务过程。任务完成后，仍应能查看它做了什么、调用了什么工具、哪里失败、产出了什么。

要求：

- 任务时间线要持久化，不要只依赖临时 SSE 消息。
- 工具调用、工具结果、模型阶段、错误、输出产物都要能在页面回看。
- 针对任务状态、工具事件、流式输出、最终答案建立回归测试。
- 维护一组代表性评测任务，覆盖搜索研究、文件处理、数据分析、浏览器使用、代码任务和报告生成。

### 8. 权限 / 风险控制

权限和安全是产品需求，不是上线前才补的清理工作。

要求：

- 暴露给模型的工具必须经过允许列表过滤。
- 高风险工具必须有拒绝列表、审批或显式开关。
- 密钥必须在事件、日志、异常和页面中脱敏。
- 审计信息要能回答：谁、在什么时候、通过哪个任务、调用了什么工具、造成了什么变化。
- 不要新增绕过工具注册、任务系统或策略检查的执行路径。

## Web 端要求

Web 端要支持完整任务生命周期，而不是只展示聊天记录。

目标页面：

- 任务创建页：输入任务、选择模式、上传文件、选择输出样式、选择技能/工具、选择运行环境。
- 任务列表页：按状态、关键词、用户、Agent 类型、创建时间、耗时、错误状态筛选。
- 任务详情页：展示输入、当前状态、计划、时间线、工具调用、工具输出、产物、最终答案、错误、用量指标。
- 实时更新：可使用 SSE/WebSocket，但必须能和持久化任务记录对齐。
- 历史回看：历史任务不依赖原始流连接，也能完整渲染。
- 失败视图：清楚展示失败原因和最后一个成功步骤。

Web 变更规则：

- 后端事件结构变化时，必须同步修改 Web 渲染，或明确兼容策略。
- 修改任务事件渲染时，必须用代表性事件数据检查展示效果。
- 不要在任务详情页隐藏工具调用、错误、风险提示。
- 长任务必须让用户看懂状态：排队中、运行中、等待输入、已完成、失败、已取消。

## 每次变更的安全规则

动手改文件前，先判断这次变更影响哪些层：

- 用户入口
- 任务系统
- Agent 核心
- 技能 / 工具
- 沙箱运行环境
- 记忆 / 知识库
- 日志 / 回看 / 评测
- 权限 / 风险控制
- Web UI

每次改文件后，必须测试被改动层和直接相连的层。小改动也要验证，因为很多回归来自很小的改动。

最低验证要求：

- Python 后端变更：运行被改模块对应的 pytest 文件或目录。
- Agent 流程变更：测试受影响的 React/Supervisor、`builtin:plan_tool` 或兼容 `plans_executor` 入口，并确认 SSE/事件结构。
- 工具变更：测试工具成功、失败、schema 暴露。
- 任务系统变更：测试创建、列表、详情、状态流转、错误、取消或重试。
- 记忆/RAG 变更：测试关闭、空结果、正常检索。
- Web 变更：能本地打开就打开页面，检查创建、实时更新、详情渲染、错误状态和移动端布局。
- 权限或沙箱变更：测试允许路径和拒绝路径。
- 配置变更：测试默认配置加载和环境变量覆盖。

如果完整测试成本过高，运行最小但有意义的测试，并在汇报中说明没跑什么、为什么没跑。不能只写完代码就报告完成。

## 每次交付前检查清单

汇报完成前，按本次改动范围检查下面内容：

1. 用户仍然可以提交任务。
2. 任务从开始到结束的状态正确。
3. 计划和步骤进度仍然可见。
4. 工具调用信息仍然出现在页面上。
5. 工具结果信息仍然出现在页面上。
6. 最终答案仍然能正常渲染。
7. 错误信息清楚可见。
8. 涉及文件或产物时，文件和产物仍然可访问。
9. 涉及日志或任务事件时，任务完成后仍能查看。
10. 未授权或不可用工具不会暴露给模型。
11. 密钥不会出现在日志、事件或页面里。
12. 被改动区域的已有测试仍然通过。

## 设计约束

- 优先使用持久化任务状态，不要把产品能力建立在请求内临时状态上。
- 优先使用结构化事件，不要把任务时间线做成不可解析的纯文本。
- 优先增强已有 Agent 模式，不要轻易新增模式。
- 优先通过工具注册和工具集合调用，不要写临时直连工具。
- 优先显式策略，不要依赖隐藏约定。
- 尽量保持 Web 和 API 协议向后兼容。
- 不要新增大依赖，除非确认它符合目标架构。
- 不要提交 API key、密码、cookie、本地数据库、日志和用户数据。

## Agent 目录化配置

后续新增或调整单个 Agent 时，默认采用“一个 Agent 一个目录”的方式。Agent 的个性、职责、system prompt、可用工具、权限、交接关系和评测样例都放在自己的目录里；真正执行逻辑仍由统一 Agent Runtime 负责。

默认目录：

```text
config/agents/
  supervisor_agent/
    agent.yaml
    system_prompt.md
    evals.yaml
    README.md

  search_agent/
    agent.yaml
    system_prompt.md
    evals.yaml
    README.md

  report_agent/
    agent.yaml
    system_prompt.md
    evals.yaml
    README.md
```

约定：

- `agent.yaml`：必需。保存结构化配置。
- `system_prompt.md`：必需。保存完整 system prompt。
- `evals.yaml`：建议。保存该 Agent 的冒烟测试和评测样例。
- `README.md`：建议。说明这个 Agent 的职责、边界和常见用法。

### agent.yaml 结构

`agent.yaml` 用来描述 Agent 的产品配置，不直接写任意 Python 类路径。

Supervisor Agent 推荐结构：

```yaml
id: supervisor_agent
name: 调度 Agent
type: supervisor
enabled: true

description: 负责理解用户任务、选择合适 Agent、决定是否需要规划、管理任务交接。
system_prompt_file: system_prompt.md

model:
  context: planner
  temperature: 0.1
  max_steps: 8

capabilities:
  - route
  - plan
  - delegate
  - review_progress

tools:
  allowed:
    - id: builtin:plan_tool
      alias: 规划工具
      purpose: 创建、更新、推进和完成任务计划。
      when_to_use: 多步骤、长任务、多 Agent 协作或需要过程回看时使用。
      risk_level: low
      timeout_seconds: 30

  denied:
    - mcp_local:shell

handoffs:
  allowed:
    - search_agent
    - browser_agent
    - data_agent
    - code_agent
    - report_agent
    - review_agent

memory:
  read:
    - user_profile
    - task_history
  write:
    - research_findings

permissions:
  can_write_files: false
  can_run_shell: false
  can_access_network: true
  require_approval_for: []

output:
  format: markdown
  required_sections:
    - 当前判断
    - 选用 Agent
    - 下一步动作
```

普通专家 Agent 示例：

```yaml
id: search_agent
name: 搜索 Agent
type: react_worker
enabled: true

description: 负责搜索资料、读取网页、整理来源和结论。
system_prompt_file: system_prompt.md

model:
  context: executor
  temperature: 0.2
  max_steps: 5

capabilities:
  - search
  - research
  - web_read

tools:
  allowed:
    - id: mcp_local:deepsearch
      alias: 深度搜索
      purpose: 搜索公开网页和资料。
      when_to_use: 需要最新信息、外部来源或事实验证时使用。
      risk_level: low
      timeout_seconds: 120

    - id: mcp_local:web_reader
      alias: 网页读取
      purpose: 读取指定网页正文。
      when_to_use: 已有 URL，需要提取页面内容时使用。
      risk_level: low
      timeout_seconds: 60

  denied:
    - mcp_local:file_write
    - mcp_local:shell

handoffs:
  allowed:
    - report_agent
    - review_agent

memory:
  read:
    - user_profile
    - task_history
  write:
    - research_findings

permissions:
  can_write_files: false
  can_run_shell: false
  can_access_network: true
  require_approval_for: []

output:
  format: markdown
  required_sections:
    - 结论
    - 关键证据
    - 来源
    - 不确定点
```

字段规则：

- `id` 必须和目录名一致。
- `type` 只能使用代码里明确支持的安全类型，例如 `supervisor`、`react_worker`、`summary_worker`、`review_worker`。不要允许 YAML 填任意 Python 类路径。`plan_solve_worker` 只作为旧 `plans_executor` 兼容方向，不作为新增 Agent 的默认类型。
- `system_prompt_file` 必须指向当前 Agent 目录下的文件。
- `tools.allowed` 只声明这个 Agent 能使用哪些工具，以及这些工具对它的用途说明。工具真实 schema 仍以 Tool Registry 或 MCP 返回为准。
- `tools.denied` 优先级高于 `tools.allowed`。
- `handoffs.allowed` 中的 Agent 必须存在于 Agent Registry。
- `permissions` 决定工具过滤、审批和沙箱策略。
- `output` 用于约束该 Agent 的默认输出格式，不替代最终 Summary。
- 复杂任务 Agent 可以通过 `tools.allowed` 显式允许 `builtin:plan_tool`；简单任务 Agent 不要默认强制规划。

### system_prompt.md 结构

`system_prompt.md` 保存完整 system prompt，避免把长提示词塞进 YAML。

推荐写法：

```md
你是搜索 Agent。

你的职责是帮助用户查找信息、验证来源、整理事实。

规则：
- 需要最新信息时，必须使用搜索工具。
- 不要编造来源。
- 如果来源冲突，要指出冲突。
- 输出必须包含结论、证据、来源和不确定点。
```

### evals.yaml 结构

每个 Agent 至少应有冒烟测试样例。Agent 配置变更后，应优先运行这个 Agent 对应的评测。

示例：

```yaml
smoke_cases:
  - name: 搜索开源 Agent 项目趋势
    input: 帮我查一下最近通用 Agent 开源项目趋势
    expected_behavior:
      - 必须调用搜索工具
      - 必须返回来源
      - 不能只靠模型记忆回答

regression_cases:
  - name: 不允许写文件
    input: 把搜索结果写入本地文件
    expected_behavior:
      - 不得调用文件写入工具
      - 应说明当前 Agent 没有写文件权限
```

### 加载和校验

启动时由 Agent 配置加载器读取 `config/agents/*/agent.yaml`，生成 AgentSpec 并注册到 Agent Registry。

加载时必须校验：

- 目录名和 `id` 一致。
- `system_prompt_file` 存在。
- `type` 在允许列表中。
- `allowed` 和 `denied` 中的工具能被 Tool Registry 识别，或有明确的延迟解析策略。
- `handoffs.allowed` 中的目标 Agent 存在。
- 高风险权限必须有审批或明确策略。
- `evals.yaml` 如果存在，格式必须可解析。

运行时流程：

```text
AgentRegistry 读取 AgentSpec
  -> Supervisor 选择目标 Agent
  -> Agent Runtime 加载 system_prompt.md
  -> ToolGateway 按 agent.yaml 过滤工具
  -> Agent 执行
  -> 任务系统记录 Agent 启动、工具调用、交接、完成或失败事件
```

### Web 展示要求

使用目录化 Agent 配置后，Web 端要能展示：

- 当前运行的是哪个 Agent。
- Agent 名称、描述和能力标签。
- 这个 Agent 可用的工具列表。
- Agent 之间的交接记录。
- Agent 失败时的错误原因和最后一个事件。

## 开发命令

### 启动应用

```bash
# 推荐启动方式
cd task-pilot-agent && uv run main.py

# 直接用 uvicorn 启动
cd task-pilot-agent && uv run uvicorn app_main:app --host 0.0.0.0 --port 9010
```

应用启动后会有两个服务：

- MCP 服务：9009 端口，提供工具市场和本地工具。
- Web/API 服务：9010 端口，提供 FastAPI 应用。

### 运行测试

```bash
# 运行全部测试
cd task-pilot-agent && uv run pytest -v --tb=short tests/

# 使用测试脚本
cd task-pilot-agent && uv run python tests/run_tests.py

# 运行指定目录
cd task-pilot-agent && uv run pytest tests/memory/
cd task-pilot-agent && uv run pytest tests/llm_test/
cd task-pilot-agent && uv run pytest tests/gaia/

# 运行单个测试文件
cd task-pilot-agent && uv run pytest tests/memory/test_memory_mgr.py -v
```

### 依赖管理

项目使用 UV 管理依赖：

```bash
# 安装依赖
uv sync

# 新增依赖
uv add <package-name>

# 更新依赖锁
uv lock --upgrade
```

## 当前架构概览

### 当前兼容流程：Plan-Solve-Summarize

当前系统仍保留 `plans_executor` 兼容入口，它处理用户请求时主要分为三个阶段：

1. 规划阶段：`PlanningAgent`
   - 接收用户问题和上下文。
   - 生成结构化任务计划。
   - 入口：`task-pilot-agent/brain/core/agents/planning_agent.py`。

2. 执行阶段：`ExecutorAgent`
   - 逐步执行计划。
   - 使用 ReAct 风格的思考和工具调用循环。
   - 支持根据执行结果重新规划。
   - 入口：`task-pilot-agent/brain/core/agents/executor_agent.py`。

3. 总结阶段：`SummaryAgent`
   - 汇总执行结果。
   - 输出用户可读的最终答案。
   - 支持 markdown、HTML、PPT、表格等输出样式。
   - 入口：`task-pilot-agent/brain/core/agents/summary_agent.py`。

迁移边界：

- 新功能不再接入 `PlanSolveHandler`。
- 旧 `plans_executor` 只保留为兼容入口。
- 与规划相关的新能力统一进入 `builtin:plan_tool`。
- 与执行相关的新能力统一进入 React/Supervisor、专家 Agent 或 ToolGateway。
- 与最终输出相关的新能力统一进入 summary/review 能力。

后续不要继续把新能力绑定到这条独立主流程上。新能力优先进入 React/Supervisor 运行时，通过 `builtin:plan_tool` 获得规划能力。

目标主流程：

```text
HTTP 请求
  -> FastAPI: brain/app.py:autoagent
  -> TaskService 创建或读取任务记录
  -> TaskEventStore 写入入口事件
  -> AgentContext 初始化
  -> AgentRegistry 选择 Supervisor 或目标 Agent
  -> ToolGateway 暴露当前 Agent 允许的工具
  -> React/Supervisor 按需调用 builtin:plan_tool
  -> React/Supervisor 调用工具或交接专家 Agent
  -> Summary/Review 生成最终结果
  -> 任务事件和 SSE 同步输出给前端
```

### 当前请求流

```text
HTTP 请求
  -> FastAPI: brain/app.py:autoagent
  -> AgentContext 初始化
  -> ToolCollection 构建
  -> AgentHandlerFactory 选择处理器
  -> PlanSolveHandler 或 ReactHandler 执行
  -> SSE 实时返回给前端
```

### 工具系统

MCP 集成：

- `tools/mcp_local/`：本地 MCP 服务和内置工具。
- `tools/aggre_mcp_market/`：聚合多个 MCP 服务。
- 工具会被动态拉取并注册进 `ToolCollection`。
- 每个 MCP 工具会被包装成统一的 `MCPTool`。

内置工具包括：

- `code_interpreter`：执行 Python 代码。
- `deepsearch`：多源搜索。
- `report`：生成 markdown、HTML、PPT 报告。
- `weather`：天气查询。
- `planing`：规划和任务管理工具。

工具调用链路：

```text
ToolCollection.execute()
  -> MCPTool
  -> HTTP 调用 MCP Market
  -> MCP 服务
  -> 实际工具实现
```

### LLM 提供方系统

统一入口：`llm/manager.py:LLMManager`。

支持的提供方：

- OpenAI 和 OpenAI 兼容 API。
- Codex / Claude。
- Gemini。

每个提供方继承 `llm/providers/base.py:LLMProvider`，并实现：

- `ask()`：基础问答。
- `ask_tool()`：工具调用。
- `generate()`：流式生成。

相关能力：

- 上下文接近限制时自动压缩：`llm/compressor.py`。
- token 统计：`llm/tokenizer.py`。
- Prompt 模板：`llm/prompt_template.py`。

### 记忆系统

组件：

- `MemoryManager`：基于 mem0ai 的记忆管理。
- `MessageManager`：使用 MySQL 保存对话历史。
- `RAGRetriever`：检索历史上下文或知识库。
- `PlanManager`：保存和读取计划状态。

流程：

1. 用户消息通过 `MessageManager` 写入 MySQL。
2. 重要上下文通过 mem0ai 提取并向量化。
3. 向量存入 Qdrant，配置上也支持 Milvus。
4. Agent 执行前可以检索相关上下文。
5. 检索结果进入 Agent 工作上下文。

配置位置：`config/config.yaml` 中的 `memory` 和 `vector_store`。

### 文件管理

文件相关逻辑位于 `file/file_op.py`：

- 上传文件：`/file/v1/upload`。
- 下载文件：`/file/v1/download/{file_id}`。
- 数据库记录：`file/file_table_op.py`。
- 文件类型定义：`file/file_type.py`。

Agent 请求里的 `files` 会被自动加载到上下文中。

## 配置系统

主配置文件：`config/config.yaml`。

关键配置示例：

```yaml
core:
  planer_max_steps: 20
  executor_max_steps: 10
  planner_replan_each_step: true
  planner_replan_on_failure: true

llm:
  provider: "openai"
  config:
    api_key: "sk-xxx"
    site_url: "https://api.siliconflow.cn/v1"
    model: "Pro/deepseek-ai/DeepSeek-V3.2-Exp"
    context_length: 160000

mcp:
  mcp_local:
    port: 9009
  mcp_market:
    mcp_servers:
      - url: "http://127.0.0.1:9009/mcp"
        tool_prefix: "mcp_local"
```

### 配置优先级

从高到低：

1. 环境变量。
2. `.env` 文件。
3. `config/config.yaml`。
4. 代码默认值。

### 环境变量

- `APP_CONFIG_FILE`：配置文件路径。
- 数据库密码、API key 等敏感值应放在环境变量或 `.env` 中。

### Prompt 模板

位置：

- `config/prompt.yaml`：中文模板。
- `config/prompt_en.yaml`：英文模板。

语言由 `config.yaml` 中的 `lang: ch` 或 `lang: en` 控制。

## 关键实现细节

### Agent 状态管理

所有 Agent 继承 `BaseAgent`：

- 位置：`brain/core/agents/base_agent.py`。
- 状态：`IDLE`、`RUNNING`、`FINISHED`、`ERROR`。
- 每个 Agent 维护自己的消息历史。
- `step()` 是核心执行单元。
- `run()` 负责主循环和最大步数限制。

### ReAct 实现

`ExecutorAgent` 和 `ReActAgent` 使用 ReAct 风格：

```python
async def step(self):
    thought = await self.think()
    action = await self.act()
    observation = await self.execute_tool(action)
    return observation
```

流程是：思考 -> 行动 -> 观察 -> 继续思考。

### SSE 流式输出

SSE 逻辑位于 `brain/app.py:sse_stream()`。

常见事件：

- `task`
- `plan`
- `plan_thought`
- `tool_thought`
- `tool_call`
- `tool_result`
- `notifications`
- `stream`
- `result`

`SSEPrinter` 位于 `brain/core/printer.py`，负责统一封装输出。

### Handler 选择

`AgentHandlerFactory` 位于 `brain/core/handlers/factory.py`。

当前主要处理器：

- `ReactHandler`：后续主运行时基础，适合承载 Supervisor、专家 Agent、工具调用和规划工具。
- `PlanSolveHandler`：兼容旧 `plans_executor` 入口，后续应逐步拆分为 `builtin:plan_tool`、普通 worker、summary/review 能力。

### 重规划机制

配置项：

- `planner_replan_each_step`：每步执行后是否重新规划。
- `planner_replan_on_failure`：失败时是否重新规划。
- `planner_max_replans`：最大重规划次数。

实现位置：`brain/core/handlers/plan_solve.py`。

## 新增组件规范

### 新增 Agent 类型

1. 在 `brain/core/agents/` 创建 Agent 类。
2. 继承 `BaseAgent` 或 `ReActAgent`。
3. 实现 `step()`，必要时实现 `think()` 和 `act()`。
4. 在对应 Handler 或 `AgentHandlerFactory` 中注册。
5. 增加聚焦测试，覆盖成功、失败、最大步数、工具调用事件。

示例：

```python
from brain.core.agents.base_agent import BaseAgent

class MyCustomAgent(BaseAgent):
    async def step(self):
        pass
```

### 新增工具

1. 在 `tools/mcp_local/tool/` 添加工具实现。
2. 在 `tools/mcp_local/mcp_server.py` 注册工具。
3. 确保 MCP Market 能发现工具。
4. 增加工具成功、失败、输入校验测试。
5. 确保工具权限、日志、脱敏和任务事件记录符合要求。

示例：

```python
from tool.my_tool import my_tool_function

@mcp.tool()
async def my_tool(param: str) -> str:
    """给模型看的工具说明"""
    return await my_tool_function(param)
```

### 新增 LLM 提供方

1. 在 `llm/providers/` 创建提供方类。
2. 继承 `LLMProvider`。
3. 实现 `ask()`、`ask_tool()`、`generate()`。
4. 在 `llm/manager.py` 注册。
5. 增加普通回复、工具调用、流式输出、错误处理测试。

### 新增 Handler

1. 在 `brain/core/handlers/` 创建 Handler。
2. 继承 `AgentHandlerService` 协议。
3. 实现 `handle()`。
4. 在 `AgentHandlerFactory` 中添加选择逻辑。
5. 确保它能接入任务系统、事件系统、权限系统和 Web 回看。

## 重要注意事项

### API key 管理

不要提交真实 API key。优先使用：

- 环境变量。
- `.env` 文件。
- 运行时配置覆盖。

### 数据库

系统需要数据库保存文件和对话历史。

- 数据库连接在 `config/config.yaml` 的 `db` 中配置。
- 表通过 SQLModel 初始化。
- 文件记录在 `file/file_table_op.py`。

### 向量数据库

记忆系统默认使用 Qdrant，也可配置 Milvus。

- 默认地址：`localhost:6333`。
- collection 名称和 embedding 维度在配置中设置。
- 本地启动示例：

```bash
docker run -p 6333:6333 qdrant/qdrant
```

### MCP 端口

默认会同时启动：

- 9009：MCP 工具服务。
- 9010：FastAPI Web/API 服务。

启动前要确认端口可用。

## 代码导航

- 主入口：`task-pilot-agent/main.py`
- FastAPI 应用：`task-pilot-agent/app_main.py`
- 请求处理：`task-pilot-agent/brain/app.py`
- Plan-Solve 主链路：`task-pilot-agent/brain/core/handlers/plan_solve.py`
- React 主链路：`task-pilot-agent/brain/core/handlers/react.py`
- Agent 基类：`task-pilot-agent/brain/core/agents/base_agent.py`
- 执行 Agent：`task-pilot-agent/brain/core/agents/executor_agent.py`
- 总结 Agent：`task-pilot-agent/brain/core/agents/summary_agent.py`
- 工具集合：`task-pilot-agent/brain/core/tools/collection.py`
- MCP 工具适配：`task-pilot-agent/brain/core/tools/mcp_tool.py`
- SSE 输出：`task-pilot-agent/brain/core/printer.py`
- LLM 管理：`task-pilot-agent/llm/manager.py`
- 记忆管理：`task-pilot-agent/memory/memory_mgr.py`
- 计划状态：`task-pilot-agent/brain/core/tools/plan_state.py`
- MCP 服务：`task-pilot-agent/tools/mcp_local/mcp_server.py`
- Web 调试页：`task-pilot-agent/brain/web/autoagent.html`
