# TaskPilotAgent 对齐 Harness Agent 评审

日期：2026-05-30

对照对象：Harness 仓库 commit [`90831f95`](https://github.com/harness/harness/tree/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703)（2026-05-27）。

范围说明：这里对齐的是 Harness 里和 AI Task、Gitspace、Claude Code、任务页面、任务运行链路相关的能力，不是整个 Harness 平台。

## 一句话结论

TaskPilotAgent 现在更强的是“让模型规划、调用工具、总结结果”；Harness 更强的是“把一次 AI 任务做成一个可创建、可记录、可查看、可恢复、可管控的产品流程”。

如果目标是一步步拉齐 Harness，优先不要先改推理能力，而是先补任务记录、后台执行、状态流转、运行日志、权限边界、页面管理和成本指标。

## 当前已有能力

TaskPilotAgent 已经具备这些基础：

1. `plans_executor` 模式：先规划，再逐步执行，最后总结。
2. `react` 模式：模型可以循环思考、调用工具、观察结果。
3. MCP 工具聚合：可以从本地或远程 MCP Server 拉取工具并调用。
4. SSE 流式输出：页面可以实时看到计划、思考、工具调用、工具结果、最终答案。
5. 多模型配置：Planner、Executor、Summary、ReAct 可以按阶段使用不同模型。
6. 文件、消息、记忆、RAG 相关模块已经有雏形。
7. 本地 Web 控制台已经能展示过程信息，包括工具调用和工具结果。

这些能力说明：你的 Agent 内核不是空白，核心问题在于“产品化运行层”还不够完整。

## Harness 相关能力观察

Harness 当前 AI Task 这部分有几个重点：

1. 有独立的 AI 任务实体：包含任务 ID、用户、空间、Gitspace、初始提示词、显示名、AI Agent 类型、状态、输出、错误信息、输出元数据和用量指标。
2. 有明确状态：`uninitialized`、`running`、`completed`、`error`。
3. 有事件入口：通过 `start`、`stop` 事件驱动 AI Task。
4. 有后台事件处理：事件处理器支持并发、超时、重试。
5. 和 Gitspace 绑定：AI 任务运行在一个准备好的开发环境里，而不是只在一次 HTTP 请求里跑完。
6. 支持安装和配置 Claude Code：在 Gitspace 容器里安装 Claude Code，并写入权限和密钥配置。
7. 有任务页面：创建页、列表页、详情页、状态筛选、搜索、错误展示、输出展示、用量展示。
8. 有运行日志基础：Gitspace 和 Pipeline 里都有日志流、完整日志上传、取消状态更新等成熟模式。

也要注意：Harness 当前 AI Agent 类型主要是 `claude-code`；AI Task 的 stop 事件目前还是空实现；本地 Docker 编排里的 `StartAITask` 也是空实现。所以要对齐 Harness 的方向，而不是照搬每个细节。

## 主要差距

| 优先级 | 缺口 | 你现在的状态 | Harness 参考 | 建议目标 |
| --- | --- | --- | --- | --- |
| P0 | 任务记录 | 主要围绕一次请求和一次流式返回 | 有 AI Task 记录 | 增加独立的 Agent Task 记录，保存状态、输出、错误、用户、时间、模式 |
| P0 | 后台执行 | 请求连接断了，任务生命周期也很弱 | 事件驱动，后台处理 | 让任务可以后台跑，页面只是查看和订阅 |
| P0 | 状态流转 | 有步骤状态，但不是统一任务状态 | 有任务状态枚举 | 统一状态：等待、运行、完成、失败、取消 |
| P0 | 运行日志 | SSE 过程信息主要给当前页面看 | 有日志流和可回看能力 | 把计划、工具调用、工具结果、错误都存成任务时间线 |
| P0 | 运行隔离 | 主要靠本地工具和 MCP 调用 | Gitspace 容器环境 | 为每次任务准备独立工作目录，后续再升级到容器 |
| P0 | 权限边界 | 工具能被拉到就可能被模型调用 | Claude Code 有权限白名单 | 给工具加允许列表、拒绝列表、用户级权限和审计记录 |
| P1 | 取消和重试 | 页面 abort 不等于真正取消任务 | Pipeline 有取消状态更新 | 支持取消运行中的任务，并把未跑步骤标记清楚 |
| P1 | 成本和用量 | 没有统一保存到任务记录里 | 有 AI Usage Metric | 保存模型、输入输出 token、耗时、费用估算 |
| P1 | 页面管理 | 主要是单次调试控制台 | 有创建、列表、详情页面 | 补任务列表、详情、状态筛选、搜索、错误和输出展示 |
| P1 | 外部代码 Agent | 现在是自研 Agent 调工具 | Claude Code 可安装配置 | 增加 Agent Adapter：Codex、Claude Code、后续其他代码 Agent |
| P1 | 输出和产物 | 有文件服务和总结输出，但任务产物不统一 | 有 output metadata | 统一保存报告、文件、代码改动、链接和结构化结果 |
| P1 | 多用户边界 | 有 user_id、agent_id，但约束弱 | 绑定 user、space | 任务、工具、文件、日志都按用户和空间隔离 |
| P2 | Pipeline 化 | 当前是计划步骤，不是可调度流水线 | Harness Pipeline 很成熟 | 复杂任务可选 DAG、队列、阶段依赖、检查结果 |
| P2 | 评测回归 | 有测试，但缺运行轨迹回放 | 平台能力更完整 | 建立固定任务集，比较成功率、耗时、成本、工具调用质量 |

## 最应该先补的 6 件事

### 1. 增加 Agent Task 记录

这是对齐 Harness 的第一块地基。每次用户发起任务时，先创建一条任务记录，而不是只创建一个 SSE 请求。

建议字段：

- 任务 ID
- 用户 ID
- Agent ID
- 会话 ID
- 模式：`plans_executor` 或 `react`
- 用户输入
- 当前状态
- 最终输出
- 错误信息
- 创建时间、更新时间、开始时间、结束时间
- 用量指标
- 输出元数据

完成标准：

- 任务开始前能看到一条“等待中”的记录。
- 任务运行时状态变成“运行中”。
- 任务结束后能看到最终输出或错误原因。
- 页面刷新后，任务记录还在。

### 2. 增加任务时间线

现在 SSE 可以给前端看过程，但如果页面刷新、连接断开、用户之后回来查看，中间过程就不够可靠。

建议把这些都保存下来：

- 计划创建和更新
- 当前执行步骤
- 工具调用名称
- 工具入参
- 工具结果
- 工具流式输出片段
- 通知和进度
- 错误信息

完成标准：

- 任务详情页能回看完整过程。
- 工具失败时能看到哪个工具、什么输入、什么错误。
- 当前 SSE 仍然实时推送，但不再是唯一记录来源。

### 3. 把执行从 HTTP 请求里拆出来

当前主流程依赖用户这次请求持续存在。对长任务来说，这不稳定。

建议新增后台 Runner：

- 创建任务后立即返回任务 ID。
- 后台 Runner 领取任务并执行。
- 页面通过任务 ID 查看状态和订阅更新。
- 断线后可以重新进入详情页继续看。

完成标准：

- 浏览器断开后，任务可以继续跑完。
- 用户重新打开详情页，能看到最新状态。
- 同时运行多个任务时，互不影响。

### 4. 增加工具权限和审计

你的 MCP 工具体系很灵活，但越灵活越需要边界。Harness 给 Claude Code 写了允许的命令范围，这是一个很值得参考的点。

建议先做简单版本：

- 每个 Agent 配一份工具允许列表。
- 高风险工具单独标记。
- 每次工具调用都记录调用人、任务、工具、入参摘要、结果摘要。
- 密钥、token、cookie 这类内容必须脱敏。

完成标准：

- 未授权工具不会出现在模型可选工具里。
- 工具调用记录可以追查。
- 日志里不会直接出现密钥。

### 5. 补任务列表和详情页

你已经有调试控制台，但要对齐 Harness，需要从“聊天页面”升级到“任务管理页面”。

建议页面：

- 创建任务页：输入任务、选择模式、选择工具集或运行环境。
- 任务列表页：按状态、时间、用户、Agent 类型筛选。
- 任务详情页：展示输入、状态、时间线、工具调用、最终输出、错误、用量。

完成标准：

- 不依赖当前聊天窗口，也能找到历史任务。
- 失败任务能直接看失败原因。
- 运行中任务能看到实时进展。

### 6. 增加用量指标

Harness 已经把成本、耗时、输入 token、输出 token、模型列表放在任务上。你的项目也应该把这块补齐。

完成标准：

- 每个任务能看到用了哪些模型。
- 能看到总耗时。
- 能看到输入输出 token。
- 后续可以加费用估算。

## 分阶段路线图

### 第一阶段：任务可记录

目标：让每次 Agent 运行都变成一条可查询的任务。

要做：

1. 新增任务表和任务事件表。
2. 任务创建时先落库。
3. 执行过程中持续写入状态和事件。
4. 结束时保存最终输出或错误。
5. 提供任务列表和任务详情接口。

做到这一步后，就能解决“任务跑完后没有完整记录”的问题。

### 第二阶段：任务可回看

目标：让页面刷新后仍能看完整过程。

要做：

1. 把 SSE 里的计划、工具调用、工具结果都同步写入任务事件。
2. 任务详情页按时间线展示这些事件。
3. 当前页面实时展示，历史页面从数据库回放。

做到这一步后，就能接近 Harness 的任务详情体验。

### 第三阶段：任务可后台运行

目标：让长任务不依赖浏览器连接。

要做：

1. 新增后台 Runner。
2. 创建任务后由 Runner 执行。
3. 支持运行中、完成、失败、取消状态。
4. 支持断线后重新订阅。
5. 给 Runner 加并发上限和超时。

做到这一步后，Agent 才真正适合跑长任务。

### 第四阶段：任务可管控

目标：让工具调用和运行环境有边界。

要做：

1. 给工具加允许列表和拒绝列表。
2. 给高风险工具加审批或显式开关。
3. 对工具入参和结果做脱敏。
4. 保存工具调用审计记录。
5. 为每个任务创建独立工作目录。

做到这一步后，可以更放心地给 Agent 更多工具。

### 第五阶段：任务可度量

目标：知道每次任务花了多少时间、多少钱、效果如何。

要做：

1. 记录模型调用用量。
2. 记录总耗时和各阶段耗时。
3. 记录工具调用次数和失败次数。
4. 建立固定评测任务集。
5. 每次改动后跑回归对比。

做到这一步后，优化 Agent 就不会只靠感觉。

### 第六阶段：对齐代码 Agent 生态

目标：支持像 Harness 一样把 Claude Code、Codex 等代码 Agent 接进来。

要做：

1. 抽象 Agent Adapter。
2. 支持安装、配置、启动外部代码 Agent。
3. 支持给外部 Agent 设置权限。
4. 支持读取外部 Agent 的输出、日志和产物。
5. 后续再考虑 Gitspace 或容器级运行环境。

做到这一步后，TaskPilotAgent 就可以既保留自己的规划能力，又能接入成熟代码 Agent。

## 建议的近期版本

如果只做一个近期版本，建议做这个范围：

1. 任务记录。
2. 任务事件记录。
3. 任务列表接口。
4. 任务详情接口。
5. 当前 Web 控制台接入任务 ID。
6. 工具调用、工具结果、最终输出都能回看。

这个版本不需要先做容器，也不需要先接 Claude Code。先把“任务能被记录和回看”做扎实，后面的后台 Runner、权限、外部代码 Agent 都会更容易接。

## 需要注意的具体问题

1. RAG 初始化目前看起来没有完全接上：`MemoryManager` 里 `rag_retriever` 初始化被注释掉，但后面仍有方法会使用它。后续做评测和知识库前要先修。
2. 当前任务状态分散在计划步骤里，还没有统一的任务状态。建议不要继续扩大这种写法，先统一任务模型。
3. 工具调用已经能发到页面，但还缺持久化。建议把工具调用页面展示和任务事件存储放到同一套结构里。
4. MCP 工具市场很灵活，但需要权限层。否则一旦接入更多工具，风险会快速变大。
5. Harness 的 AI Task stop 目前也是空实现，你可以在取消能力上直接超过 Harness。

## 对齐时不要丢掉的优势

1. 你的 Plan-Solve-Summary 链路比 Harness 当前 AI Task 更偏通用任务推理，这个要保留。
2. MCP 工具体系比单一 Claude Code 更开放，这个是优势。
3. 多模型分阶段配置是优势，后续可以用来控制成本和效果。
4. 已经有工具调用过程上屏能力，这可以直接发展成任务时间线。

## 参考文件

本项目：

- `task-pilot-agent/brain/app.py`
- `task-pilot-agent/brain/core/handlers/plan_solve.py`
- `task-pilot-agent/brain/core/agents/executor_agent.py`
- `task-pilot-agent/brain/core/tools/collection.py`
- `task-pilot-agent/brain/core/tools/mcp_tool.py`
- `task-pilot-agent/brain/core/printer.py`
- `task-pilot-agent/brain/web/autoagent.html`
- `task-pilot-agent/memory/memory_mgr.py`

Harness：

- [`types/ai_task.go`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/types/ai_task.go)
- [`types/enum/ai_task_state.go`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/types/enum/ai_task_state.go)
- [`app/events/aitask/events.go`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/app/events/aitask/events.go)
- [`app/services/aitaskevent/handler.go`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/app/services/aitaskevent/handler.go)
- [`app/services/aitaskevent/service.go`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/app/services/aitaskevent/service.go)
- [`app/gitspace/orchestrator/utils/ai_agent.go`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/app/gitspace/orchestrator/utils/ai_agent.go)
- [`app/gitspace/orchestrator/container/container_orchestrator.go`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/app/gitspace/orchestrator/container/container_orchestrator.go)
- [`web/src/cde-gitness/pages/AITaskCreate/AITaskCreate.tsx`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/web/src/cde-gitness/pages/AITaskCreate/AITaskCreate.tsx)
- [`web/src/cde-gitness/pages/AITaskListing/AITaskListing.tsx`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/web/src/cde-gitness/pages/AITaskListing/AITaskListing.tsx)
- [`web/src/cde-gitness/pages/AITaskDetails/AITaskDetails.tsx`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/web/src/cde-gitness/pages/AITaskDetails/AITaskDetails.tsx)
- [`app/pipeline/canceler/canceler.go`](https://github.com/harness/harness/blob/90831f95eb54ed65f8a7f8a1cbdad6d5091a6703/app/pipeline/canceler/canceler.go)
