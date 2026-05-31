# TaskPilotAgent

TaskPilotAgent 是一个基于 FastAPI 的任务规划与工具调度服务：支持多模型（OpenAI/Claude/Gemini/OpenAI-compatible），并通过 MCP（Model Context Protocol）聚合/调用工具（本地 MCP 工具 + MCP Market 聚合层）。

## Agent 架构梳理

TaskPilotAgent 当前已经从“单次聊天请求”转向“可回看的任务系统 + 可配置 Agent + 统一工具网关”。一次用户请求会先变成任务，再进入 Agent 运行时，运行时按 Agent 配置过滤工具、调用模型、调用工具，并把过程事件持续写入任务时间线。

### 整体组成

```mermaid
flowchart LR
  User["用户入口<br/>Web / API / WebSocket"] --> API["FastAPI 路由<br/>brain/app.py"]
  API --> Task["任务系统<br/>TaskStore"]
  Task --> Event["任务事件<br/>task_created / tool_call / result"]
  Task --> Artifact["任务产物<br/>artifact / work_dir"]
  API --> Context["AgentContext<br/>请求上下文"]
  Context --> Registry["AgentRegistry<br/>config/agents/*"]
  Context --> Memory["记忆 / 知识库<br/>memory_manager / RAG"]
  Context --> Gateway["ToolGateway<br/>权限过滤 + 工具集合"]
  Registry --> Runtime["Agent 核心<br/>Supervisor / ReAct / PlanSolve"]
  Gateway --> Builtin["内置工具<br/>plan / handoff / request_input"]
  Gateway --> Market["MCP Market<br/>工具聚合层"]
  Market --> LocalMCP["本地 MCP 服务<br/>filesystem / search / code / report"]
  Market --> RemoteMCP["远程 MCP 服务<br/>mcp_world 等扩展"]
  Runtime --> LLM["LLMManager<br/>planner / executor / react / summary"]
  Runtime --> Summary["SummaryAgent<br/>最终回答"]
  Summary --> SSE["SSEPrinter<br/>实时输出"]
  SSE --> Event
  SSE --> Web["Web 任务页<br/>实时展示 + 历史回看"]
```

核心代码位置：

| 组件 | 主要文件 | 作用 |
| --- | --- | --- |
| 启动进程 | `task-pilot-agent/main.py`、`task-pilot-agent/app_main.py` | 启动 FastAPI worker 和本地 MCP 子进程 |
| 用户入口 | `task-pilot-agent/brain/app.py` | 提供 `/agent/autoagent`、任务 API、Web 页面 |
| 任务系统 | `task-pilot-agent/brain/core/tasks.py` | 保存任务、事件、产物、状态和工作目录 |
| Agent 注册表 | `task-pilot-agent/brain/core/agent_registry.py` | 加载 `config/agents/*/agent.yaml`、system prompt 和 evals |
| Agent 运行时 | `task-pilot-agent/brain/core/handlers/*.py` | 选择 Supervisor、ReAct 或兼容的 PlanSolve 执行链路 |
| ReAct Agent | `task-pilot-agent/brain/core/agents/ReActAgentImp.py` | 决定是否调用工具，执行“思考 -> 工具 -> 观察”循环 |
| 总结 Agent | `task-pilot-agent/brain/core/agents/summary_agent.py` | 把工具结果和过程证据汇总成最终答案 |
| 工具网关 | `task-pilot-agent/brain/core/tools/gateway.py` | 按 Agent 配置、权限和用户授权过滤工具 |
| 工具集合 | `task-pilot-agent/brain/core/tools/collection.py` | 暴露工具 schema、执行工具、记录工具事件 |
| MCP 工具适配 | `task-pilot-agent/brain/core/tools/mcp_tool.py` | 把 Agent 工具调用转成 MCP Market 调用 |
| 本地 MCP 工具 | `task-pilot-agent/tools/mcp_local/mcp_server.py` | 提供文件、搜索、代码、报告、天气等工具 |
| Web 页面 | `task-pilot-agent/brain/web/autoagent.html` | 创建任务、查看任务列表、展示时间线和最终结果 |

### 组件 1：用户入口

用户入口包括 Web 页面、SSE API、WebSocket API 和后台任务 API。入口层只负责接收请求、补齐默认值、创建或恢复任务，不直接写 Agent 执行逻辑。

```mermaid
flowchart TD
  A["用户输入任务"] --> B{"入口类型"}
  B --> C["Web 页面<br/>GET /agent/web/autoagent"]
  B --> D["SSE<br/>POST /agent/autoagent"]
  B --> E["后台任务<br/>POST /agent/tasks"]
  B --> F["WebSocket<br/>WS /agent/ws/autoagent"]
  C --> D
  D --> G["_run_autoagent"]
  E --> H["创建 queued 任务"]
  H --> G
  F --> G
  G --> I["TaskStore + AgentContext"]
```

```mermaid
sequenceDiagram
  participant User as 用户
  participant Web as Web 页面
  participant API as FastAPI /agent
  participant Runner as _run_autoagent
  participant Store as TaskStore

  User->>Web: 输入任务并点击发送
  Web->>API: POST /agent/autoagent
  API->>Runner: 建立 SSE 队列并执行请求
  Runner->>Store: create_task()
  Store-->>Runner: taskId / workDir
  Runner-->>Web: SSE task_created / task_running
  Web-->>User: 展示任务状态和时间线
```

### 组件 2：任务系统

任务系统是主干。每次 Agent 运行都对应一条任务记录，过程事件、工具调用、工具结果、最终输出和产物都挂到同一个 `taskId` 上。

```mermaid
flowchart TD
  A["create_task"] --> B["生成 taskId / traceId"]
  B --> C["创建任务工作目录"]
  C --> D["写入 meta_agent_task"]
  D --> E["add_event: task_created"]
  E --> F["update_status: running"]
  F --> G["运行中持续 add_event"]
  G --> H{"执行结果"}
  H --> I["completed + output_text"]
  H --> J["failed + error_message"]
  H --> K["cancelled"]
  I --> L["历史回看"]
  J --> L
  K --> L
```

```mermaid
sequenceDiagram
  participant Runner as _run_autoagent
  participant Store as TaskStore
  participant DB as 数据库
  participant FS as 任务工作目录

  Runner->>Store: create_task(input, agent_id, mode)
  Store->>FS: prepare_task_workspace(taskId)
  Store->>DB: insert meta_agent_task
  Runner->>Store: add_event(task_created)
  Runner->>Store: update_status(running)
  Runner->>Store: add_event(tool_call / tool_result / result)
  Runner->>Store: update_status(completed / failed / cancelled)
```

### 组件 3：Agent 注册表

每个 Agent 一个目录。`agent.yaml` 描述职责、类型、工具、权限、交接关系；`system_prompt.md` 保存完整提示词；`evals.yaml` 保存评测样例。

```mermaid
flowchart TD
  A["config/agents/"] --> B["遍历每个 Agent 目录"]
  B --> C["读取 agent.yaml"]
  C --> D["读取 system_prompt.md"]
  D --> E["读取 evals.yaml"]
  E --> F["校验 id、type、handoffs、权限"]
  F --> G["生成 AgentConfig"]
  G --> H["注册到 AgentRegistry"]
  H --> I["Web/API 可列出 Agent"]
  H --> J["运行时可按 agent_id 取配置"]
```

```mermaid
sequenceDiagram
  participant App as FastAPI Worker
  participant Registry as AgentRegistry
  participant YAML as config/agents
  participant Runtime as Agent Runtime

  App->>Registry: 初始化全局 AgentRegistry
  Registry->>YAML: 读取 */agent.yaml
  Registry->>YAML: 读取 system_prompt.md / evals.yaml
  Registry-->>App: 可用 Agent 列表
  Runtime->>Registry: get(agent_id)
  Registry-->>Runtime: AgentConfig + system prompt + 权限
```

### 组件 4：Agent 核心运行时

当前主线是 ReAct/Supervisor。`plans_executor` 仍保留为兼容链路；新能力优先进入 ReAct/Supervisor 和工具系统。

```mermaid
flowchart TD
  A["_run_autoagent"] --> B["解析 AgentConfig"]
  B --> C["构建 AgentContext"]
  C --> D["加载记忆上下文"]
  D --> E["ToolGateway 构建工具集合"]
  E --> F["AgentHandlerFactory 选择 Handler"]
  F --> G{"Handler"}
  G --> H["SupervisorHandler<br/>选择目标 Agent"]
  G --> I["ReactHandler<br/>ReAct 循环"]
  G --> J["PlanSolveHandler<br/>兼容旧流程"]
  H --> I
  I --> K["SummaryAgent"]
  J --> K
  K --> L["SSE result + task_completed"]
```

```mermaid
sequenceDiagram
  participant Runner as _run_autoagent
  participant Registry as AgentRegistry
  participant Gateway as ToolGateway
  participant Factory as AgentHandlerFactory
  participant React as ReactHandler
  participant Agent as ReActAgentImp
  participant Summary as SummaryAgent

  Runner->>Registry: get(agent_id)
  Runner->>Gateway: build_collection(ctx)
  Gateway-->>Runner: ToolCollection
  Runner->>Factory: get_handler(ctx, req)
  Factory-->>Runner: ReactHandler
  Runner->>React: handle(ctx, req)
  React->>Agent: run(query)
  Agent-->>React: evidence / final_answer
  React->>Summary: summarize(query, evidence)
  Summary-->>Runner: final_text
```

### 组件 5：工具系统

Agent 不直接调用工具。所有工具都经过 `ToolGateway -> ToolCollection -> MCPTool`，这样才能统一处理权限、schema、超时、审计信息、工具调用事件和结果事件。

```mermaid
flowchart TD
  A["AgentConfig.tools"] --> B["ToolGateway"]
  C["selected_tools / approved_tools"] --> B
  D["permissions / policy"] --> B
  B --> E["加入内置工具<br/>plan / handoff / request_input"]
  B --> F["MCPToolFetcher 拉取工具市场"]
  F --> G["ToolCollection.add_tool"]
  G --> H{"是否允许"}
  H --> I["可用工具 schema 暴露给模型"]
  H --> J["blockedTools 写入策略事件"]
  I --> K["模型选择工具"]
  K --> L["ToolCollection.execute"]
  L --> M["MCPTool.execute"]
  M --> N["POST /aggre_mcp_market/call_tool"]
  N --> O["本地或远程 MCP Server"]
```

```mermaid
sequenceDiagram
  participant Agent as ReActAgentImp
  participant Tools as ToolCollection
  participant Adapter as MCPTool
  participant Market as MCP Market
  participant MCP as 本地 MCP Server
  participant Printer as SSEPrinter
  participant Store as TaskStore

  Agent->>Tools: to_openai_tools()
  Tools-->>Agent: 工具 schema
  Agent->>Tools: execute("mcp_local:file_read", args)
  Tools->>Printer: tool_call
  Printer->>Store: add_event(tool_call)
  Tools->>Adapter: execute(args)
  Adapter->>Market: POST /call_tool
  Market->>MCP: file_read(args)
  MCP-->>Market: result
  Market-->>Adapter: result
  Adapter-->>Tools: text/json
  Tools->>Printer: tool_result
  Printer->>Store: add_event(tool_result)
  Tools-->>Agent: observation
```

### 组件 6：记忆 / 知识库

记忆只增强上下文，不替代任务记录。是否读取、写入哪些范围，由 Agent 配置里的 `memory.read` 和 `memory.write` 控制。

```mermaid
flowchart TD
  A["AgentConfig.memory"] --> B{"是否允许读取"}
  B --> C["不读取：记录 disabled"]
  B --> D["读取 user_profile / task_history / RAG"]
  D --> E["memory_manager 检索"]
  E --> F["把结果写入 ctx.memory_context"]
  F --> G["add_event: memory_context_loaded"]
  G --> H["Agent prompt 可引用"]
```

```mermaid
sequenceDiagram
  participant Runner as _run_autoagent
  participant Ctx as AgentContext
  participant Memory as memory_manager
  participant Store as TaskStore

  Runner->>Ctx: 设置 agent_memory
  Runner->>Memory: 检索任务相关上下文
  Memory-->>Runner: 记忆命中 / 空结果 / 降级信息
  Runner->>Store: add_event(memory_context_loaded)
  Runner->>Ctx: 写入可用上下文
```

### 组件 7：日志 / 回看 / 评测

实时输出和历史回看走同一套事件结构。SSE 只是展示通道，任务完成后仍可通过任务 API 回放。

```mermaid
flowchart TD
  A["SSEPrinter.send"] --> B["浏览器实时看到消息"]
  A --> C["event_sink"]
  C --> D["TaskStore.add_event"]
  D --> E["meta_agent_task_event"]
  E --> F["GET /agent/tasks/{taskId}/events"]
  F --> G["Web loadTask"]
  G --> H["时间线 / 工具调用 / 最终答案"]
  I["evals.yaml"] --> J["EvalRunner"]
  J --> K["创建评测任务"]
  K --> D
```

```mermaid
sequenceDiagram
  participant Runtime as Agent Runtime
  participant Printer as SSEPrinter
  participant Store as TaskStore
  participant Web as Web 页面
  participant API as 任务 API

  Runtime->>Printer: send(tool_call / result)
  Printer-->>Web: SSE 实时输出
  Printer->>Store: event_sink 持久化事件
  Web->>API: GET /agent/tasks/{taskId}/events
  API->>Store: list_events(taskId)
  Store-->>API: 事件列表
  API-->>Web: 历史时间线
```

### 组件 8：权限 / 风险控制 / 沙箱边界

权限在 Agent 配置、用户授权、工具策略和运行环境中共同生效。高风险工具默认需要显式授权；沙箱模式下路径参数必须留在任务工作目录内。

```mermaid
flowchart TD
  A["agent.yaml permissions"] --> B["AgentConfig.allows_tool"]
  C["tools[].policy"] --> B
  D["approved_tools"] --> B
  B --> E{"允许工具？"}
  E --> F["加入 ToolCollection"]
  E --> G["加入 blockedTools"]
  F --> H["执行前检查 run_environment"]
  H --> I{"sandbox 路径是否越界"}
  I --> J["拒绝并返回错误"]
  I --> K["执行工具"]
  G --> L["tool_policy_applied 事件"]
```

```mermaid
sequenceDiagram
  participant Gateway as ToolGateway
  participant AgentCfg as AgentConfig
  participant Tools as ToolCollection
  participant Store as TaskStore

  Gateway->>AgentCfg: allows_tool(tool, approved_tools)
  AgentCfg-->>Gateway: allowed / blockReason
  Gateway->>Tools: add_tool 或记录 blocked
  Gateway-->>Store: tool_policy_applied
  Tools->>Tools: 执行前检查路径边界
  Tools-->>Tools: 允许执行或返回越界错误
```

### 真实请求 Demo：读取 README 并总结

下面用一个真实业务路径说明：用户让默认通用 Agent 读取仓库 README，并输出启动方式总结。这个例子会触发 ReAct、文件读取工具、工具事件持久化和最终总结。

请求示例：

```json
{
  "trace_id": "demo-readme-001",
  "user_id": "demo-user",
  "conversation_id": "demo-session-001",
  "agent_id": "task-pilot-agent",
  "mode": "react",
  "outputStyle": "markdown",
  "run_environment": "local",
  "messages": [
    {
      "role": "user",
      "content": "请读取 /path/to/task-pilot-agent/README.md，并用三句话总结如何启动项目"
    }
  ]
}
```

真实请求时序图：

```mermaid
sequenceDiagram
  participant Browser as Web 页面
  participant API as POST /agent/autoagent
  participant Runner as _run_autoagent
  participant Store as TaskStore
  participant Registry as AgentRegistry
  participant Gateway as ToolGateway
  participant React as ReActAgentImp
  participant LLM as LLMManager
  participant Tools as ToolCollection
  participant Market as MCP Market
  participant MCP as 本地文件读取工具
  participant Summary as SummaryAgent

  Browser->>API: 发送 demo 请求
  API->>Runner: 建立 SSE 流
  Runner->>Store: create_task(demo-readme-001)
  Runner->>Store: add_event(task_created)
  Runner->>Registry: get("task-pilot-agent")
  Runner->>Gateway: build_collection(ctx)
  Gateway-->>Runner: file_read 等允许工具
  Runner->>Store: add_event(tool_policy_applied)
  Runner->>React: handle(ctx)
  React->>LLM: ask_tool_async(query, tools)
  LLM-->>React: 选择 mcp_local:file_read
  React->>Tools: execute(file_read, path)
  Tools->>Store: add_event(tool_call)
  Tools->>Market: POST /call_tool
  Market->>MCP: file_read(path)
  MCP-->>Market: README 内容
  Market-->>Tools: 工具结果
  Tools->>Store: add_event(tool_result)
  Tools-->>React: observation
  React->>Summary: summarize(query, evidence)
  Summary-->>Browser: SSE result chunks
  Summary->>Store: add_event(result)
  Runner->>Store: update_status(completed)
  Runner->>Store: add_event(task_completed)
```

Demo 任务记录：

```json
{
  "taskId": "demo-readme-001",
  "traceId": "demo-readme-001",
  "conversationId": "demo-session-001",
  "userId": "demo-user",
  "agentId": "task-pilot-agent",
  "mode": "react",
  "outputStyle": "markdown",
  "status": "completed",
  "input": "请读取 /path/to/task-pilot-agent/README.md，并用三句话总结如何启动项目",
  "workDir": "uploads/tasks/demo-readme-001"
}
```

Demo 事件片段：

```json
[
  {
    "eventType": "task_created",
    "source": "autoagent",
    "payload": {
      "mode": "react",
      "agentConfigId": "task-pilot-agent",
      "runEnvironment": "local"
    }
  },
  {
    "eventType": "tool_policy_applied",
    "source": "policy",
    "payload": {
      "agentId": "task-pilot-agent",
      "availableTools": [
        "mcp_local:file_read",
        "mcp_local:file_list",
        "mcp_local:file_stat"
      ],
      "blockedTools": [
        "mcp_local:shell_exec"
      ],
      "blockedToolReasons": {
        "mcp_local:shell_exec": "high_risk_requires_approval"
      }
    }
  },
  {
    "eventType": "tool_call",
    "source": "sse",
    "payload": {
      "messageType": "tool_call",
      "resultMap": {
        "tool": "mcp_local:file_read",
        "argumentsSummary": "{\"path\":\"/path/to/task-pilot-agent/README.md\"}",
        "taskId": "demo-readme-001",
        "agentId": "task-pilot-agent",
        "runEnvironment": "local"
      }
    }
  },
  {
    "eventType": "tool_result",
    "source": "sse",
    "payload": {
      "messageType": "tool_result",
      "resultMap": {
        "tool": "mcp_local:file_read",
        "durationMs": 18,
        "failed": false,
        "resultSummary": "{\"path\":\"/path/to/task-pilot-agent/README.md\",\"bytes\":4096,\"content\":\"# TaskPilotAgent...\"}"
      }
    }
  },
  {
    "eventType": "result",
    "source": "sse",
    "payload": {
      "messageType": "result",
      "result": "项目使用 uv 安装依赖，复制 config.yaml.example 后补齐配置。进入 task-pilot-agent 目录执行 uv run main.py 启动。默认会同时启动 9010 的 Web/API 服务和 9009 的本地 MCP 工具服务。"
    }
  },
  {
    "eventType": "task_completed",
    "source": "autoagent",
    "payload": {
      "status": "completed"
    }
  }
]
```

Demo 回看接口：

```bash
curl http://127.0.0.1:9010/agent/tasks/demo-readme-001
curl http://127.0.0.1:9010/agent/tasks/demo-readme-001/events
curl http://127.0.0.1:9010/agent/tasks/demo-readme-001/artifacts
```

## 目录结构

- `config/`：运行配置与 prompt
  - `config/config.yaml.example`：配置示例（复制后生效）
  - `config/prompt.yaml`、`config/prompt_en.yaml`：提示词模板（按 `lang` 覆盖）
- `task-pilot-agent/`：服务端代码（FastAPI + Agent + MCP）
  - `task-pilot-agent/main.py`：启动入口（会拉起本地 MCP 子进程 + FastAPI）
  - `task-pilot-agent/app_main.py`：FastAPI app（`/agent`、`/file/v1`、`/aggre_mcp_market`）
  - `task-pilot-agent/tools/mcp_local/`：本地 MCP 服务器与工具实现

## 快速开始（开发）

### 1) 安装依赖（uv）

```bash
cd task-pilot-agent
uv sync
```

### 2) 准备配置

```bash
cp ../config/config.yaml.example ../config/config.yaml
```

**配置必改项（建议先搜 `CHANGE_ME`）**

以下字段如果保持示例值，服务通常无法正常工作或存在安全风险：

- 数据库（文件/消息存储依赖，启动时会建表）
  - `db.password` 或 `db.url`
  - `db.host/db.port/db.user/db.name`（如果不用 `db.url`）
- 大模型（Planner/Executor/Summary/ReAct 都依赖）
  - `llm.config.api_key`、`llm.config.site_url`、`llm.config.model`
  - 若启用 `llm.contexts + llm.configs[]` 分阶段配置：每个 `llm.configs[].config.api_key/site_url/model` 也需要补齐
- 向量与嵌入（mem0 记忆依赖；不使用记忆可先关闭 `memory.search_memory`）
  - `embedder.config.api_key`、`embedder.config.openai_base_url`（或 provider 对应的 base_url）
  - `vector_store.config.url`（Qdrant 地址）、`vector_store.config.collection_name`（建议按环境区分）

按功能启用时需要配置的字段：

- 搜索（deepsearch/搜索组件用到）
  - `search[].api_key`（或通过环境变量 `JINA_SEARCH_API_KEY` / `BOCHA_SEARCH_API_KEY` / `SERPER_SEARCH_API_KEY`）
- browser-use 浏览器智能体（调用 browser agent 时用到）
  - `browser_use.sandbox_url`
  - `browser_use.config.api_key/site_url/model`
- 多模态工具（调用 audio/image/video tool 时用到）
  - `audio_llm.config.api_key/site_url/model`
  - `image_llm.config.api_key/site_url/model`
  - `video_llm.config.api_key/site_url/model`

**安全建议**

- 不要把真实 `api_key/password` 直接提交到仓库；推荐使用环境变量覆盖（见下文“环境变量与配置覆盖”）。

**数据库类型说明（`db.url`）**

`db.url` 是标准 SQLAlchemy DSN，支持切换数据库类型：

- MySQL / MariaDB（生产推荐）：`mysql://user:password@127.0.0.1:3306/meta_agent`
- SQLite（仅建议本地开发/单进程）：`sqlite:///./meta_agent.db`

SQLite 注意事项：

- 多 worker 并发写入容易出现 `database is locked`，建议设置 `UVICORN_WORKERS=1`
- `sqlite:///./xxx.db` 的相对路径以启动目录为准，生产环境建议使用绝对路径

必须配置/确认的关键项（与服务能否启动直接相关）：

- `db`：文件服务会在启动时初始化表（`meta_agent_file`），数据库不可用会导致启动失败
- `llm`：主对话模型（可通过 `contexts` 为 planner/executor/summary/react 指定不同模型）
- `embedder` + `vector_store`：mem0 记忆（向量存储）相关
- `browser_use`：浏览器智能体依赖 browser-use sandbox（如不需要可先不调用相关工具）
- `audio_llm`/`image_llm`/`video_llm`：多模态工具需要

配置字段的详细说明见：`config/config.yaml.example`。

### 3) 启动服务

从 `task-pilot-agent/` 目录启动（代码会固定读取 `../config/config.yaml`）：

```bash
cd task-pilot-agent
uv run main.py
```

默认会启动：

- FastAPI：`http://0.0.0.0:9010`
- 本地 MCP Server：`http://0.0.0.0:9009/mcp`（由主进程 spawn 子进程拉起）

健康检查：`GET /health`

## 常用接口

### Agent（SSE / WebSocket）

- `POST /agent/autoagent`：SSE 流式输出（推荐）
- `GET /agent/web/autoagent`：简易 Web 调试页
- `WS /agent/ws/autoagent`：WebSocket 方式调用

请求体核心字段（`brain.models.requests.GptQueryReq`）：

- `messages`: 必填，最后一条必须是 `role=user`
- `agent_id`: 可选，不传时使用 `config/config.yaml.example` 中的 `core.agent_id`
- `mode`: 可选，当前默认 Agent 使用 `react`；旧链路仍支持 `plans_executor`
- `outputStyle`: 可选，默认取 `core.default_output_style`

curl 示例（SSE）：

```bash
curl -N http://127.0.0.1:9010/agent/autoagent \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id":"task-pilot-agent",
    "mode":"react",
    "outputStyle":"markdown",
    "messages":[{"role":"user","content":"帮我总结一下这个项目的启动流程"}]
  }'
```

### 文件服务

- `POST /file/v1/upload_file_form`：表单上传
- `POST /file/v1/upload_file_data`：multipart 上传（字段 `requestId`）
- `GET /file/v1/preview_file/{request_id}/{file_name}`：预览
- `GET /file/v1/download_file/{request_id}/{file_name}`：下载

### MCP Market（工具聚合层）

- `GET /aggre_mcp_market/tools`：列出聚合到的 MCP 工具
- `GET /aggre_mcp_market/prompt`：生成工具提示词片段
- `POST /aggre_mcp_market/call_tool`：调用指定工具（支持 `Accept: text/event-stream` 或 `?stream=true`）

## 源码导览

上面的架构图描述产品主链路；更细的服务端源码导览见 `task-pilot-agent/README.md`。

## 环境变量与配置覆盖

项目使用 Pydantic Settings，支持用环境变量覆盖 YAML（前缀 `APP_`，嵌套字段用 `__`）：

- 示例：`APP_SERVER__PORT=9010`、`APP_LLM__CONFIG__API_KEY=...`
- workers：`UVICORN_WORKERS=5`
- Langfuse（可选）：`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL`
- 搜索（可选）：`JINA_SEARCH_API_KEY`、`BOCHA_SEARCH_API_KEY`、`SERPER_SEARCH_API_KEY`（以及 `SERPER_SEARCH_PROXY`/`HTTP(S)_PROXY`）
- 文件 DB（可选覆盖）：`FILE_DB_URL`（优先于 `db.*` 生成的 DSN）

## 代码统计（Python）

当前仓库（`git ls-files`）统计：

- Python 文件：`112`
- Python 总行数（含测试）：`13281`
  - 业务代码（不含 `task-pilot-agent/tests/`）：`10166`
  - 测试代码（`task-pilot-agent/tests/`）：`3115`

可用以下命令自行刷新统计：

```bash
git ls-files '*.py' | wc -l
git ls-files '*.py' -z | xargs -0 wc -l | tail -n 1
```

## 运行测试（示例）

```bash
cd task-pilot-agent
uv run pytest -s tests/tools/mcp_local/tool/test_browser_agent.py -k test_browser_agent_1
```
