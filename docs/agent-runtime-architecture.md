# TaskPilotAgent Runtime Architecture

Chinese version: [`agent-runtime-architecture.zh-CN.md`](agent-runtime-architecture.zh-CN.md)

This document describes the current TaskPilotAgent implementation. It is based
on the code paths listed in the "Source Evidence" sections, not on a target-only
design.

## What The System Is

TaskPilotAgent is now a session-based Agent product with a durable task/run
ledger underneath it.

- A **session** is the user-visible conversation container.
- A **message** is a user or assistant turn inside a session.
- A **run** is one Agent execution triggered by a user message.
- An **event** records what happened during a run: status changes, tool calls,
  plan updates, approvals, artifacts, errors, and final output.
- A **task** is still the primary durable execution ledger used by the runtime.
  Session runs mirror that ledger for the web UI and replay APIs.

Source evidence:

| Area | Code |
| --- | --- |
| FastAPI routers and startup | [`task-pilot-agent/app_main.py`](../task-pilot-agent/app_main.py), [`task-pilot-agent/main.py`](../task-pilot-agent/main.py) |
| Session/message/run/event tables and store | [`task-pilot-agent/brain/core/sessions.py`](../task-pilot-agent/brain/core/sessions.py) |
| Task/event/artifact execution ledger | [`task-pilot-agent/brain/core/tasks.py`](../task-pilot-agent/brain/core/tasks.py) |
| Main runtime lifecycle | [`task-pilot-agent/brain/core/autoagent_runtime.py`](../task-pilot-agent/brain/core/autoagent_runtime.py) |
| Session message entry and resume logic | [`task-pilot-agent/brain/core/session_message_service.py`](../task-pilot-agent/brain/core/session_message_service.py) |
| Session replay payloads | [`task-pilot-agent/brain/core/session_view_service.py`](../task-pilot-agent/brain/core/session_view_service.py) |

## Layer Map

```mermaid
flowchart TD
  Browser["Web UI"] --> AgentAPI["/agent APIs"]
  Browser --> AuthAPI["/auth APIs"]
  Browser --> FileAPI["/file/v1 APIs"]

  AgentAPI --> Auth["Auth dependency"]
  AgentAPI --> SessionMsg["Session message service"]
  AgentAPI --> Runtime["AutoAgentRuntime"]
  AgentAPI --> View["Session view service"]

  Runtime --> TaskStore["TaskStore"]
  Runtime --> SessionStore["SessionStore"]
  Runtime --> AgentRegistry["AgentRegistry"]
  Runtime --> Memory["Memory context loader"]
  Runtime --> Gateway["ToolGateway"]
  Runtime --> HandlerFactory["AgentHandlerFactory"]

  HandlerFactory --> ReactHandler["ReactHandler"]
  HandlerFactory --> SupervisorHandler["SupervisorHandler"]
  ReactHandler --> AgentCore["ReActAgentImp + SummaryAgent"]
  SupervisorHandler --> AgentCore

  AgentCore --> ToolCollection["ToolCollection"]
  ToolCollection --> BuiltinTools["Built-in runtime tools"]
  ToolCollection --> MCPTools["MCPTool wrappers"]
  MCPTools --> MCPRegistry["MCP registry"]
  MCPRegistry --> LocalMCP["Local MCP tools"]
  MCPRegistry --> RemoteMCP["Remote MCP servers"]

  TaskStore --> Replay["Events/artifacts/plan snapshots"]
  SessionStore --> Replay
  Replay --> Browser
```

## Startup Sequence

```mermaid
sequenceDiagram
  participant Main as main.py
  participant MCPProc as mcp_process.py
  participant LocalMCP as mcp_local/mcp_server.py
  participant API as app_main.py
  participant Registry as aggre_mcp_market runtime
  participant Recovery as task_recovery.py

  Main->>MCPProc: start_mcp_subprocess()
  MCPProc->>LocalMCP: mcp_run_async(transport)
  LocalMCP->>LocalMCP: register_all_tools(mcp)
  Main->>API: uvicorn app_main:app
  API->>Registry: init_mcp_market_registry()
  Registry->>Registry: load_registry_from_yaml() and refresh tools
  API->>Recovery: recover_incomplete_agent_tasks()
  API-->>Main: FastAPI ready
```

Implementation notes:

- `main.py` starts the local MCP subprocess before Uvicorn.
- `app_main.py` registers `/aggre_mcp_market`, `/auth`, `/agent`, and
  `/file/v1`.
- The app lifespan initializes the MCP registry and recovers queued or
  interrupted Agent tasks.

Source evidence:

| Behavior | Code |
| --- | --- |
| Start local MCP subprocess | [`task-pilot-agent/main.py`](../task-pilot-agent/main.py), [`task-pilot-agent/mcp_process.py`](../task-pilot-agent/mcp_process.py) |
| Register FastAPI routers | [`task-pilot-agent/app_main.py`](../task-pilot-agent/app_main.py) |
| Initialize MCP market registry | [`task-pilot-agent/tools/aggre_mcp_market/app.py`](../task-pilot-agent/tools/aggre_mcp_market/app.py), [`task-pilot-agent/tools/aggre_mcp_market/service/runtime.py`](../task-pilot-agent/tools/aggre_mcp_market/service/runtime.py) |
| Register local MCP tools | [`task-pilot-agent/tools/mcp_local/mcp_server.py`](../task-pilot-agent/tools/mcp_local/mcp_server.py), [`task-pilot-agent/tools/mcp_local/tool_registrars/all_tools.py`](../task-pilot-agent/tools/mcp_local/tool_registrars/all_tools.py) |

## Main Session Run Sequence

```mermaid
sequenceDiagram
  participant UI as Web UI
  participant API as brain/app.py
  participant MsgSvc as session_message_service.py
  participant Runtime as autoagent_runtime.py
  participant Stores as SessionStore/TaskStore
  participant Gateway as ToolGateway
  participant Handler as React/Supervisor Handler
  participant Agent as ReActAgentImp
  participant Tools as ToolCollection

  UI->>API: POST /agent/sessions/{session_id}/messages
  API->>API: require_current_user()
  API->>MsgSvc: add_session_message()
  MsgSvc->>Stores: create user message and mark session running
  MsgSvc->>Runtime: run_autoagent() in background
  Runtime->>Stores: create task and sync session run
  Runtime->>Stores: add task_created/user_message_created/task_running events
  Runtime->>Runtime: build AgentContext
  Runtime->>Stores: add memory_context_loaded and runtime_boundary_applied
  Runtime->>Gateway: build_collection(ctx)
  Gateway-->>Runtime: ToolCollection with available and blocked tools
  Runtime->>Stores: add tool_policy_applied
  Runtime->>Handler: create handler by mode/agent type
  Handler->>Agent: run ReAct loop
  Agent->>Tools: execute tool or finish
  Tools->>Stores: emit tool_call/tool_result through SSE event sink
  Agent-->>Handler: answer/evidence
  Handler-->>Runtime: final output or waiting state
  Runtime->>Stores: complete/fail/wait and sync session run/message
  UI->>API: GET events or WebSocket/SSE stream
  API-->>UI: replay same persisted events
```

Key points:

- The main user path is session-based: `POST /agent/sessions/{id}/messages`.
- Each new message creates a run ID and then calls `run_autoagent()`.
- The runtime still creates a task record because task events are the primary
  execution ledger.
- Frontend live display and historical replay come from the same event records.

Source evidence:

| Behavior | Code |
| --- | --- |
| Session message creates run | [`task-pilot-agent/brain/core/session_message_service.py`](../task-pilot-agent/brain/core/session_message_service.py) |
| Runtime creates session run and task | [`task-pilot-agent/brain/core/autoagent_runtime.py`](../task-pilot-agent/brain/core/autoagent_runtime.py) |
| Runtime records stream events into task events | [`task-pilot-agent/brain/core/autoagent_runtime.py`](../task-pilot-agent/brain/core/autoagent_runtime.py) |
| WebSocket/SSE session event replay | [`task-pilot-agent/brain/app.py`](../task-pilot-agent/brain/app.py) |
| Frontend merges replay and live events | [`task-pilot-agent/frontend/src/App.vue`](../task-pilot-agent/frontend/src/App.vue) |

## Interface Layer

All paths below are mounted by `app_main.py`.

### `/agent` APIs

| Method | Path | Function |
| --- | --- | --- |
| GET | `/agent/agents` | List configured Agents for the UI. |
| GET | `/agent/agents/diagnostics` | Return Agent config diagnostics. |
| GET | `/agent/agents/{agent_id}` | Read one Agent config snapshot. |
| GET | `/agent/tools` | List currently visible tools with policy/risk metadata. |
| GET | `/agent/mcp/servers` | List MCP server status. |
| POST | `/agent/mcp/tools/refresh` | Refresh all MCP tools from the registry. |
| POST | `/agent/mcp/servers/{server_id}/refresh` | Refresh one MCP server. |
| POST | `/agent/mcp/tools/{tool_id}/dry-run` | Test one MCP tool through the same policy checks. |
| POST | `/agent/sessions` | Create a user session. |
| GET | `/agent/sessions` | List user sessions. |
| GET | `/agent/sessions/{session_id}` | Read session detail, messages, runs, events, artifacts, and pending approval. |
| PATCH | `/agent/sessions/{session_id}` | Update session metadata such as title. |
| POST | `/agent/sessions/{session_id}/archive` | Archive a session. |
| DELETE | `/agent/sessions/{session_id}` | Soft-delete/archive a session and cancel active work. |
| GET | `/agent/sessions/{session_id}/messages` | Page through session messages. |
| POST | `/agent/sessions/{session_id}/messages` | Add a user message and start/resume a run. |
| GET | `/agent/sessions/{session_id}/events` | List session run events for replay. |
| GET | `/agent/sessions/{session_id}/stream` | SSE stream for session events. |
| GET | `/agent/sessions/{session_id}/runs/current` | Get current run. |
| GET | `/agent/sessions/{session_id}/runs` | List session runs. |
| GET | `/agent/sessions/{session_id}/runs/{run_id}` | Read one session run. |
| GET | `/agent/sessions/{session_id}/runs/{run_id}/plan` | Read latest run plan snapshot. |
| POST | `/agent/sessions/{session_id}/runs/{run_id}/cancel` | Cancel a running run. |
| POST | `/agent/sessions/{session_id}/runs/{run_id}/retry` | Retry a run using stored input and metadata. |
| POST | `/agent/sessions/{session_id}/runs/{run_id}/approval` | Approve or reject a pending high-risk operation. |
| GET | `/agent/sessions/{session_id}/artifacts` | List session artifacts. |
| GET | `/agent/sessions/{session_id}/artifacts/{artifact_id}` | Download a session artifact. |
| GET | `/agent/sessions/{session_id}/runs/{run_id}/artifacts` | List artifacts for one run. |
| GET | `/agent/sessions/{session_id}/tools` | List tools available in a session context. |
| POST | `/agent/sessions/{session_id}/tools/test` | Test a tool in a session context. |
| GET | `/agent/agents/{agent_id}/evals` | List Agent eval cases. |
| POST | `/agent/agents/{agent_id}/evals/run` | Run all evals for an Agent. |
| POST | `/agent/agents/{agent_id}/evals/{case_id}/run` | Run one eval case. |
| POST | `/agent/tasks/{task_id}/eval-result` | Evaluate a completed task result. |
| GET | `/agent/tasks` | Compatibility task list. |
| POST | `/agent/tasks` | Compatibility task creation. |
| GET | `/agent/tasks/{task_id}` | Compatibility task detail. |
| GET | `/agent/tasks/{task_id}/events` | Compatibility task event list. |
| POST | `/agent/tasks/{task_id}/cancel` | Compatibility task cancel. |
| DELETE | `/agent/tasks/{task_id}` | Compatibility task delete. |
| POST | `/agent/tasks/{task_id}/retry` | Compatibility task retry. |
| POST | `/agent/tasks/{task_id}/input` | Resume a task waiting for user input. |
| GET | `/agent/tasks/{task_id}/artifacts` | Compatibility task artifacts. |
| GET | `/agent/tasks/{task_id}/artifacts/{artifact_id}` | Compatibility task artifact download. |
| GET | `/agent/web/assets/{asset_path}` | Serve built frontend assets. |
| GET | `/agent/web/autoagent` | Serve the web UI. |
| POST | `/agent/autoagent` | Legacy direct autoagent request path. |
| WS | `/agent/ws/sessions/{session_id}` | WebSocket session event stream. |
| WS | `/agent/ws/autoagent` | Legacy direct autoagent WebSocket path. |
| GET | `/agent/web/health` | Web UI health check. |

Source evidence: [`task-pilot-agent/brain/app.py`](../task-pilot-agent/brain/app.py).

### `/auth` APIs

| Method | Path | Function |
| --- | --- | --- |
| GET | `/auth/providers` | List enabled auth providers. |
| GET | `/auth/me` | Return current authenticated user. |
| POST | `/auth/logout` | Revoke current session cookie. |
| POST | `/auth/logout-all` | Revoke all sessions for the user. |
| GET | `/auth/users/me` | Read current user profile. |
| PATCH | `/auth/users/me` | Update current user profile. |
| GET | `/auth/users/me/identities` | List linked provider identities. |
| DELETE | `/auth/users/me/identities/{identity_id}` | Unlink one identity. |
| GET | `/auth/admin/users` | Admin user list. |
| POST | `/auth/admin/users` | Admin user creation. |
| PATCH | `/auth/admin/users/{user_id}` | Admin user update. |
| POST | `/auth/admin/users/{user_id}/disable` | Disable user. |
| DELETE | `/auth/admin/users/{user_id}` | Soft-delete user. |
| POST | `/auth/admin/legacy-users` | Create legacy user mapping input. |
| POST | `/auth/admin/legacy-users/{legacy_user_id}/map` | Map legacy user to TaskPilot user. |
| GET | `/auth/admin/audit-events` | Read auth audit events. |
| POST | `/auth/admin/cleanup` | Cleanup auth records. |
| POST | `/auth/{provider}/link` | Start provider account linking. |
| DELETE | `/auth/{provider}/link/{identity_id}` | Remove provider link. |
| GET | `/auth/{provider}/login` | Start provider login. |
| GET | `/auth/{provider}/callback` | Provider callback handler. |
| GET | `/auth/whoami` | Legacy/debug identity endpoint. |

Source evidence: [`task-pilot-agent/auth/router.py`](../task-pilot-agent/auth/router.py).

### `/aggre_mcp_market` APIs

| Method | Path | Function |
| --- | --- | --- |
| GET | `/aggre_mcp_market/tools` | List aggregated MCP tools, including risk and approval metadata. |
| GET | `/aggre_mcp_market/servers` | List MCP server status. |
| POST | `/aggre_mcp_market/refresh` | Refresh registry tool snapshots. |
| GET | `/aggre_mcp_market/prompt` | Build a prompt fragment from current MCP tools. |
| POST | `/aggre_mcp_market/call_tool` | Call an MCP tool, optionally streaming SSE tool events. |

Source evidence: [`task-pilot-agent/tools/aggre_mcp_market/app.py`](../task-pilot-agent/tools/aggre_mcp_market/app.py).

### `/file/v1` APIs

| Method | Path | Function |
| --- | --- | --- |
| POST | `/file/v1/get_file` | Read file metadata by request/file ID. |
| POST | `/file/v1/upload_file` | Upload a file by JSON payload. |
| POST | `/file/v1/upload_file_data` | Upload raw file data. |
| POST | `/file/v1/upload_file_form` | Upload a multipart browser file. |
| POST | `/file/v1/get_file_list` | List uploaded files for a request. |
| GET | `/file/v1/download_file/{request_id}/{file_name}` | Download uploaded file content. |
| GET | `/file/v1/preview_file/{request_id}/{file_name}` | Preview uploaded file content with ownership checks. |

Source evidence: [`task-pilot-agent/file/file_op.py`](../task-pilot-agent/file/file_op.py).

## Agent Configuration And Runtime Selection

Agent config lives under `config/agents/{agent_id}`:

- `agent.yaml`: identity, type, mode, tools, denied tools, handoffs, memory,
  permissions, and output defaults.
- `system_prompt.md`: the Agent-specific system prompt.
- `evals.yaml`: smoke/regression evals.

Default configured Agents currently include:

- `task-pilot-agent`
- `supervisor_agent`
- `search_agent`
- `browser_agent`
- `data_agent`
- `code_agent`
- `report_agent`

The default Agent is a `react_worker` using `mode: react`. Its config allows
general file, search, browser, media, report, config-read, skill, memory, and
remote MCP tools. It denies `deepsearch`, disables shell execution through
permissions, and requires approval for high-risk tools.

Source evidence:

| Behavior | Code |
| --- | --- |
| Agent model and validation | [`task-pilot-agent/brain/core/agent_registry.py`](../task-pilot-agent/brain/core/agent_registry.py) |
| Default Agent config | [`config/agents/task-pilot-agent/agent.yaml`](../config/agents/task-pilot-agent/agent.yaml) |
| Handler selection | [`task-pilot-agent/brain/core/handlers/factory.py`](../task-pilot-agent/brain/core/handlers/factory.py) |
| React handler | [`task-pilot-agent/brain/core/handlers/react.py`](../task-pilot-agent/brain/core/handlers/react.py) |
| Supervisor handler | [`task-pilot-agent/brain/core/handlers/supervisor.py`](../task-pilot-agent/brain/core/handlers/supervisor.py) |

## Tool System

```mermaid
flowchart TD
  AgentConfig["agent.yaml tools + denied_tools + permissions"] --> Gateway["ToolGateway"]
  RuntimeSelection["selected_tools + approved_tools"] --> Gateway
  Gateway --> Builtins["Add allowed built-in tools"]
  Gateway --> Fetcher["MCPToolFetcher"]
  Fetcher --> Market["MCP registry or market endpoint"]
  Market --> MCPWrappers["MCPTool objects"]
  Builtins --> Collection["ToolCollection"]
  MCPWrappers --> Collection
  Collection --> Execute["execute(name,args)"]
  Execute --> Policy["allowed check + sandbox boundary + approval check + timeout"]
  Policy --> Events["tool_call/tool_result events"]
```

### Built-in runtime tools

| Tool | Purpose | Source |
| --- | --- | --- |
| `builtin:plan_tool` | Create/update/mark/finish visible plan state. | [`builtin_plan_tool.py`](../task-pilot-agent/brain/core/tools/builtin_plan_tool.py) |
| `builtin:set_todo_list` | Project short progress into a visible TODO list. | [`builtin_todo_tool.py`](../task-pilot-agent/brain/core/tools/builtin_todo_tool.py) |
| `builtin:handoff` | Start child work for another allowed Agent. | [`builtin_handoff_tool.py`](../task-pilot-agent/brain/core/tools/builtin_handoff_tool.py) |
| `builtin:request_input` | Pause a run and ask the user for missing input. | [`builtin_request_input_tool.py`](../task-pilot-agent/brain/core/tools/builtin_request_input_tool.py) |

### Local MCP tool groups

These are registered by `register_all_tools(mcp)`.

| Group | Tools |
| --- | --- |
| Filesystem | `file_read`, `file_write`, `file_edit`, `file_list`, `file_stat`, `file_glob`, `file_grep`, `directory_create`, `file_copy`, `file_move`, `file_delete` |
| Web/search/weather | `web_search`, `fetch_url`, `web_reader`, `get_current_weather`, `get_weather_forecast` |
| Browser/media/report/code | `browser_agent`, `audio_tool`, `image_tool`, `video_tool`, `text_to_image`, `report`, `code_interpreter` |
| Process/config/MCP management | `shell_exec`, `process_command_*`, `config_read`, `config_update`, `mcp_manager_*` |
| Skill and memory | `skill_search`, `skill_load`, `skill_install`, `skill_enable/disable`, `memory_search`, `memory_add`, `memory_delete` |
| Messaging/sub-agent | `message_send`, `create_subagent` |

Source evidence:

| Behavior | Code |
| --- | --- |
| Build policy-filtered collection | [`task-pilot-agent/brain/core/tools/gateway.py`](../task-pilot-agent/brain/core/tools/gateway.py) |
| Execute tools and emit events | [`task-pilot-agent/brain/core/tools/collection.py`](../task-pilot-agent/brain/core/tools/collection.py) |
| Wrap MCP tools | [`task-pilot-agent/brain/core/tools/mcp_tool.py`](../task-pilot-agent/brain/core/tools/mcp_tool.py) |
| Register local MCP tools | [`task-pilot-agent/tools/mcp_local/tool_registrars/all_tools.py`](../task-pilot-agent/tools/mcp_local/tool_registrars/all_tools.py) |
| Filesystem sandbox behavior | [`task-pilot-agent/tools/mcp_local/tool/filesystem.py`](../task-pilot-agent/tools/mcp_local/tool/filesystem.py) |

## MCP Architecture

```mermaid
sequenceDiagram
  participant Runtime as ToolGateway/MCPToolFetcher
  participant Registry as aggre_mcp_market runtime
  participant Client as MCP client
  participant Local as Local MCP server
  participant Remote as Remote MCP server

  Runtime->>Registry: list_tools()
  Registry->>Client: refresh/list tools per configured server
  Client->>Local: streamable HTTP local MCP
  Client->>Remote: SSE or streamable HTTP remote MCP
  Registry-->>Runtime: ToolInfo with full name, schema, risk, approval metadata
  Runtime->>Runtime: wrap as MCPTool
  Runtime->>Registry: call_tool(name,args)
  Registry->>Client: route by full tool name
  Client-->>Registry: ToolCallResult or stream events
```

Current implementation details:

- The local MCP server still runs as its own subprocess.
- The app also keeps an in-process MCP registry object for listing, refreshing,
  and calling tools without routing every internal operation through a route
  handler.
- The registry supports remote MCP clients through the common
  `MCPClientBase` interface.
- Tool metadata includes `risk_level` and `requires_approval`, and the registry
  can infer high-risk status for known dangerous tools.

Source evidence:

| Behavior | Code |
| --- | --- |
| Registry runtime singleton | [`task-pilot-agent/tools/aggre_mcp_market/service/runtime.py`](../task-pilot-agent/tools/aggre_mcp_market/service/runtime.py) |
| Registry refresh, cache, risk inference, call routing | [`task-pilot-agent/tools/aggre_mcp_market/service/registry.py`](../task-pilot-agent/tools/aggre_mcp_market/service/registry.py) |
| MCP client abstraction | [`task-pilot-agent/tools/aggre_mcp_market/mcp_clients/base.py`](../task-pilot-agent/tools/aggre_mcp_market/mcp_clients/base.py) |
| Streamable HTTP MCP client | [`task-pilot-agent/tools/aggre_mcp_market/mcp_clients/http_client.py`](../task-pilot-agent/tools/aggre_mcp_market/mcp_clients/http_client.py) |
| SSE MCP client | [`task-pilot-agent/tools/aggre_mcp_market/mcp_clients/sse_client.py`](../task-pilot-agent/tools/aggre_mcp_market/mcp_clients/sse_client.py) |
| Local MCP subprocess | [`task-pilot-agent/mcp_process.py`](../task-pilot-agent/mcp_process.py) |

## Approval Flow

```mermaid
sequenceDiagram
  participant Agent as ReActAgentImp
  participant Collection as ToolCollection
  participant Runtime as AutoAgentRuntime
  participant Store as TaskStore/SessionStore
  participant UI as Web UI
  participant API as Approval API

  Agent->>Collection: execute(file_write,args)
  Collection->>Collection: detect risk_level/requires_approval
  Collection-->>Runtime: raise ToolApprovalRequired
  Runtime->>Store: add approval_requested
  Runtime->>Store: mark task/session waiting_approval
  Runtime->>Store: add task_waiting_approval and assistant message
  UI->>Store: replay/live events show approval buttons
  UI->>API: POST /agent/sessions/{sid}/runs/{rid}/approval
  API->>Store: add approval_resolved
  alt approved and rerun
    API->>Runtime: retry run with approvedTools
  else rejected
    API->>Store: mark run rejected/cancelled
  end
```

There are two approval checkpoints:

- Before a selected high-risk tool is exposed: `ToolGateway` and Agent config
  can block it and create an approval request.
- At actual execution time: `ToolCollection` checks `risk_level` and
  `requires_approval` on the tool object, so MCP registry metadata is enforced
  even if the tool was exposed.

Source evidence:

| Behavior | Code |
| --- | --- |
| Blocked high-risk selected tool approval | [`task-pilot-agent/brain/core/tools/gateway.py`](../task-pilot-agent/brain/core/tools/gateway.py), [`task-pilot-agent/brain/core/autoagent_runtime.py`](../task-pilot-agent/brain/core/autoagent_runtime.py) |
| Runtime tool approval exception | [`task-pilot-agent/brain/core/tools/collection.py`](../task-pilot-agent/brain/core/tools/collection.py), [`task-pilot-agent/brain/core/agents/ReActAgentImp.py`](../task-pilot-agent/brain/core/agents/ReActAgentImp.py) |
| Approval resolution and rerun | [`task-pilot-agent/brain/core/approval_service.py`](../task-pilot-agent/brain/core/approval_service.py) |
| Approval endpoint | [`task-pilot-agent/brain/app.py`](../task-pilot-agent/brain/app.py) |
| Frontend approval buttons | [`task-pilot-agent/frontend/src/App.vue`](../task-pilot-agent/frontend/src/App.vue) |
| Approval tests | [`task-pilot-agent/tests/tasks/test_task_control_api.py`](../task-pilot-agent/tests/tasks/test_task_control_api.py), [`task-pilot-agent/tests/tasks/test_tool_collection_policy.py`](../task-pilot-agent/tests/tasks/test_tool_collection_policy.py) |

## Memory And Knowledge Retrieval

```mermaid
sequenceDiagram
  participant Runtime as AutoAgentRuntime
  participant Ctx as AgentContext
  participant Loader as task_memory_context.py
  participant Manager as memory_manager
  participant Agent as ReAct/Summary Agent

  Runtime->>Loader: load_task_memory_context(ctx, latest_input)
  Loader->>Loader: read ctx.agent_memory scopes and limits
  Loader->>Manager: unified_search_async(query,user,agent,run)
  Manager-->>Loader: memoryResults + ragResults + warnings
  Loader-->>Ctx: ctx.memory_context
  Runtime->>Store: memory_context_loaded event
  Agent->>Ctx: compose_system_prompt()
  Ctx-->>Agent: language + agent prompt + memory snippets + base prompt
```

Implementation details:

- Agent config controls memory read scopes.
- `AgentContext.compose_system_prompt()` injects language, Agent prompt, and
  memory/knowledge snippets.
- Memory has graceful degradation. If mem0 or vector search is unavailable,
  disabled/fallback clients return empty results and warnings instead of
  crashing the run.
- Memory can also be used as MCP tools: `memory_search`, `memory_add`, and
  `memory_delete`.

Source evidence:

| Behavior | Code |
| --- | --- |
| Runtime memory loading | [`task-pilot-agent/brain/core/autoagent_runtime.py`](../task-pilot-agent/brain/core/autoagent_runtime.py) |
| Memory scope, search, summarization | [`task-pilot-agent/brain/core/task_memory_context.py`](../task-pilot-agent/brain/core/task_memory_context.py) |
| Prompt injection | [`task-pilot-agent/brain/core/context.py`](../task-pilot-agent/brain/core/context.py) |
| Memory manager and fallbacks | [`task-pilot-agent/memory/memory_mgr.py`](../task-pilot-agent/memory/memory_mgr.py) |
| RAG retriever | [`task-pilot-agent/memory/rag_retriever.py`](../task-pilot-agent/memory/rag_retriever.py) |
| Memory MCP tools | [`task-pilot-agent/tools/mcp_local/tool/management_tools.py`](../task-pilot-agent/tools/mcp_local/tool/management_tools.py) |

## Plan Flow

```mermaid
sequenceDiagram
  participant Agent as ReActAgentImp
  participant PlanTool as builtin:plan_tool
  participant Printer as SSEPrinter
  participant Store as TaskStore
  participant View as Session view
  participant UI as Web UI

  Agent->>PlanTool: create/update/mark_step/finish
  PlanTool->>PlanTool: update deterministic PlanFunctionTool state
  PlanTool->>Printer: emit plan + typed plan event
  Printer->>Store: persist plan event
  Store->>Store: update latest plan metadata snapshot
  View->>Store: read plan events/snapshot
  UI->>View: GET /runs/{run_id}/plan or event replay
```

Implementation details:

- Planning is a tool inside ReAct/Supervisor, not a standalone executor mode.
- `run_events.py` defines plan event names and maps plan commands to typed
  events such as `plan_created`, `plan_step_completed`, and `plan_completed`.
- `ReActAgentImp` can sync plan step status after tool results.
- `session_view_service.py` exposes the latest plan for a run.

Source evidence:

| Behavior | Code |
| --- | --- |
| Plan tool | [`task-pilot-agent/brain/core/tools/builtin_plan_tool.py`](../task-pilot-agent/brain/core/tools/builtin_plan_tool.py), [`task-pilot-agent/brain/core/tools/plan_tool.py`](../task-pilot-agent/brain/core/tools/plan_tool.py) |
| Plan event taxonomy | [`task-pilot-agent/brain/core/run_events.py`](../task-pilot-agent/brain/core/run_events.py) |
| Latest plan snapshots | [`task-pilot-agent/brain/core/plan_snapshots.py`](../task-pilot-agent/brain/core/plan_snapshots.py) |
| Plan step sync after tools | [`task-pilot-agent/brain/core/agents/ReActAgentImp.py`](../task-pilot-agent/brain/core/agents/ReActAgentImp.py) |
| Plan replay API | [`task-pilot-agent/brain/app.py`](../task-pilot-agent/brain/app.py), [`task-pilot-agent/brain/core/session_view_service.py`](../task-pilot-agent/brain/core/session_view_service.py) |

## Skill Flow

```mermaid
sequenceDiagram
  participant Agent as Agent via MCP tool
  participant SkillTool as skill_search/load/install tools
  participant Registry as TaskSkillRegistry
  participant FS as Task workspace skills

  Agent->>SkillTool: skill_search(query)
  SkillTool->>Registry: search built-in Agent skills and task-local skills
  Registry-->>Agent: metadata and descriptions
  Agent->>SkillTool: skill_load(skill_id)
  SkillTool->>Registry: bounded read of SKILL.md if enabled
  Registry-->>Agent: skill instructions
  Agent->>SkillTool: skill_install(content)
  SkillTool->>FS: write task-local SKILL.md and manifest metadata
```

Implementation details:

- Skills are exposed through local MCP management tools, not as a separate
  runtime mode.
- Task-local skills are stored under the task work directory and bounded by safe
  IDs and size limits.
- Agent config can allow or deny skill tools like any other tool.

Source evidence:

| Behavior | Code |
| --- | --- |
| Task-local skill registry | [`task-pilot-agent/tools/mcp_local/tool/skill_registry.py`](../task-pilot-agent/tools/mcp_local/tool/skill_registry.py) |
| Skill MCP tools | [`task-pilot-agent/tools/mcp_local/tool/management_tools.py`](../task-pilot-agent/tools/mcp_local/tool/management_tools.py) |
| Tool registration | [`task-pilot-agent/tools/mcp_local/tool_registrars/all_tools.py`](../task-pilot-agent/tools/mcp_local/tool_registrars/all_tools.py) |
| Tool policy exposure | [`task-pilot-agent/brain/core/tools/gateway.py`](../task-pilot-agent/brain/core/tools/gateway.py) |

## Frontend Replay And Controls

The Vue UI does not own the source of truth. It:

- creates sessions and posts messages,
- opens WebSocket first and falls back to SSE,
- merges persisted events with live events,
- renders progress items, tools, approvals, artifacts, markdown final answers,
  status chips, errors, and retry/cancel controls.

Source evidence:

| Behavior | Code |
| --- | --- |
| WebSocket/SSE client | [`task-pilot-agent/frontend/src/App.vue`](../task-pilot-agent/frontend/src/App.vue) |
| Progress and event rendering | [`task-pilot-agent/frontend/src/App.vue`](../task-pilot-agent/frontend/src/App.vue) |
| Approval buttons | [`task-pilot-agent/frontend/src/App.vue`](../task-pilot-agent/frontend/src/App.vue) |
| Styles | [`task-pilot-agent/frontend/src/styles.css`](../task-pilot-agent/frontend/src/styles.css) |

## Current Implementation Guarantees

- Auth-protected routes resolve the current user before reading sessions,
  tasks, files, or artifacts.
- Session APIs are the main product path; task APIs are retained for
  compatibility.
- Tool exposure goes through `ToolGateway`.
- Tool execution goes through `ToolCollection`.
- MCP tools carry risk and approval metadata into the Agent runtime.
- High-risk tool use can pause the run and require user approval.
- Memory/RAG degradation does not crash normal Agent runs.
- Planning is represented as structured events and latest snapshots.
- The frontend renders from persisted events so refresh/reconnect can recover
  progress.

## Tests That Cover The Architecture

| Area | Tests |
| --- | --- |
| Session/task control APIs, approval, retry, tool listing | [`task-pilot-agent/tests/tasks/test_task_control_api.py`](../task-pilot-agent/tests/tasks/test_task_control_api.py) |
| Tool collection policy and execution approval | [`task-pilot-agent/tests/tasks/test_tool_collection_policy.py`](../task-pilot-agent/tests/tasks/test_tool_collection_policy.py) |
| Tool gateway behavior | [`task-pilot-agent/tests/tasks/test_tool_gateway.py`](../task-pilot-agent/tests/tasks/test_tool_gateway.py) |
| Agent config validation | [`task-pilot-agent/tests/tasks/test_agent_registry.py`](../task-pilot-agent/tests/tasks/test_agent_registry.py) |
| Session store | [`task-pilot-agent/tests/tasks/test_session_store.py`](../task-pilot-agent/tests/tasks/test_session_store.py) |
| Task store and artifacts | [`task-pilot-agent/tests/tasks/test_task_store.py`](../task-pilot-agent/tests/tasks/test_task_store.py) |
| Frontend source-level behavior | [`task-pilot-agent/tests/tasks/test_autoagent_web.py`](../task-pilot-agent/tests/tasks/test_autoagent_web.py) |
| Local MCP filesystem sandbox | [`task-pilot-agent/tests/tasks/test_mcp_filesystem_tools.py`](../task-pilot-agent/tests/tasks/test_mcp_filesystem_tools.py) |
| Memory degradation | [`task-pilot-agent/tests/memory/test_memory_degradation.py`](../task-pilot-agent/tests/memory/test_memory_degradation.py) |
| Auth and ownership | [`task-pilot-agent/tests/auth/`](../task-pilot-agent/tests/auth/) |
