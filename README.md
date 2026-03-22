# TaskPilotAgent

TaskPilotAgent 是一个基于 FastAPI 的任务规划与工具调度服务：支持多模型（OpenAI/Claude/Gemini/OpenAI-compatible），并通过 MCP（Model Context Protocol）聚合/调用工具（本地 MCP 工具 + MCP Market 聚合层）。

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
- `mode`: 可选，`plans_executor`（默认）或 `react`
- `outputStyle`: 可选，默认取 `core.default_output_style`

curl 示例（SSE）：

```bash
curl -N http://127.0.0.1:9010/agent/autoagent \
  -H 'Content-Type: application/json' \
  -d '{
    "mode":"plans_executor",
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

## 核心逻辑（源码导览）

服务端的核心逻辑说明已迁移至 `task-pilot-agent/README.md`，根目录 README 仅保留快速上手与配置说明。

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
