# TaskPilotAgent Code Index

This index is the starting point for reading the current codebase. It focuses on
layer boundaries, the main task path, and the Agent components that participate
in a run.

For the deeper runtime map, see
[`docs/agent-runtime-architecture.md`](../docs/agent-runtime-architecture.md).

## Runtime Layers

```text
Web / API Entry
  -> Auth and Ownership
  -> Session and Task Runtime
  -> Agent Registry and Handler Selection
  -> Agent Core
  -> ToolGateway and ToolCollection
  -> MCP / Built-in Tools
  -> Task Events, Run Events, Messages, Artifacts
  -> Web Replay
```

## Main Entrypoints

- `main.py`
  Starts the MCP subprocess and FastAPI server.

- `app_main.py`
  Builds the FastAPI app, runs startup checks, initializes the MCP market, and
  mounts the auth, agent, file, and MCP routers.

- `brain/app.py`
  Owns the HTTP/WebSocket API boundary for Agents. It should stay focused on
  request parsing, ownership checks, response shaping, and wiring runtime
  dependencies.

- `brain/core/autoagent_runtime.py`
  Owns the durable task/session run lifecycle for normal Agent requests. This is
  the main orchestration boundary for creating a run, attaching event sinks,
  applying tool policy, invoking handlers, and recording completion or failure.

## Data And State Boundaries

- `brain/core/tasks.py`
  Task records, task events, latest plan snapshots, task artifacts, task status,
  usage metrics, and task workspaces. `start_task` is the guarded transition
  into running state so delayed background workers cannot reopen cancelled or
  completed tasks. Parent tasks also keep child task summaries for retry and
  handoff relationships. This should become the primary runtime ledger.

- `brain/core/sessions.py`
  Conversation sessions, user-visible messages, run projections, run events, and
  session-scoped artifacts. This is the web conversation projection over task
  execution.

- `brain/core/session_view_service.py`
  Converts task/run/message/artifact records into payloads used by the web task
  page. It keeps compatibility between the task ledger and session views.

- `brain/core/session_context.py`
  Builds model-facing session context from recent messages and deterministic
  session summaries. This is the first context-budget boundary.

- `brain/core/task_memory_context.py`
  Builds model-facing memory and knowledge snippets according to Agent memory
  read scope, then sanitizes and summarizes them before prompt injection.

- `brain/core/context_budget.py`
  Shared text normalization, truncation, and message budget fitting. Use this
  before adding another context length limit in session, memory, file, or tool
  result prompts.

- `brain/core/task_recovery.py`
  Rebuilds runnable requests from task records and recovers queued or
  interrupted background work through a database lease. It also caps repeated
  recovery attempts and marks exhausted tasks failed with a replayable event.

- `brain/core/task_runner.py`
  Process-local background worker registry. It starts, cancels, and cleans up
  current asyncio workers behind one boundary. Active workers renew a database
  lease through the runtime so startup recovery does not steal healthy long
  tasks. A durable queue can replace this boundary later without changing API
  handlers.

- `brain/core/run_events.py`
  Shared event contract for task lifecycle, Agent, plan, tool, approval,
  message, artifact, replay aliases, schema version, and event categories. Use
  this before adding new runtime event names or frontend event branching.

- `brain/core/plan_snapshots.py`
  Shared plan-event projection. It extracts plan payloads from events, builds
  terminal plan payloads, and produces the latest plan metadata stored on tasks.

- `brain/core/context.py`
  The in-memory `AgentContext` passed into handlers, Agents, and tools. It
  carries user, session, run, task, language, memory, work directory, selected
  tools, approved tools, and the stream printer.

## Agent Configuration

- `config/agents/{agent_id}/agent.yaml`
  Structured Agent configuration. Defines Agent identity, type, mode, tools,
  denied tools, handoff targets, memory scope, permissions, output defaults, and
  eval cases.

- `config/agents/{agent_id}/system_prompt.md`
  Full system prompt loaded by the Agent registry.

- `config/agents/{agent_id}/evals.yaml`
  Smoke and regression cases for that Agent.

- `brain/core/agent_registry.py`
  Loads directory-based Agent configs, validates IDs, prompt paths, handoffs,
  tool policy, memory config, and eval cases. It also performs simple supervisor
  target selection.

## Agent Handlers

- `brain/core/handlers/factory.py`
  Chooses the handler for the current request.

- `brain/core/handlers/react.py`
  Current main ReAct path. It may create a visible plan for complex work, runs
  the ReAct Agent, and calls the Summary Agent when needed.

- `brain/core/handlers/supervisor.py`
  Selects a worker Agent from a supervisor config, rebuilds that worker's tool
  collection, and delegates execution.

- `brain/core/handlers/plan_solve.py`
  Compatibility path for the older plan/execute/summarize flow. New planning
  behavior should move toward `builtin:plan_tool` in ReAct/Supervisor.

## Agent Core Components

- `brain/core/agents/base_agent.py`
  Shared Agent lifecycle: state, max steps, memory writes, and message history
  formatting.

- `brain/core/agents/react_agent.py`
  Abstract think-act loop.

- `brain/core/agents/ReActAgentImp.py`
  Concrete ReAct Agent. It asks the model for either an answer or a tool call,
  executes tools through `ToolCollection`, records evidence, updates running
  plan steps, and stops repeated identical lookup calls.

- `brain/core/agents/planning_agent.py`
  Legacy planning Agent for `plans_executor`.

- `brain/core/agents/executor_agent.py`
  Legacy plan-step executor for `plans_executor`.

- `brain/core/agents/summary_agent.py`
  Streams the final answer and optionally writes message history to memory.

## Tool System

- `brain/core/tool_policy.py`
  Shared tool-selection and tool-name matching rules. This is where colon/hyphen
  MCP aliases and request-level selected tool patterns are normalized.

- `brain/core/tools/gateway.py`
  Policy-aware tool collection builder. It combines Agent config, selected
  tools, approved tools, built-in runtime tools, MCP tools, and handoff support.
  It also owns blocked-tool reasons, high-risk approval requests, and approval
  waiting messages. This should be the only path that exposes tool availability
  decisions to Agents or API views.

- `brain/core/tools/collection.py`
  Runtime tool map. It enforces allowed tools at registration and execution
  time, resolves OpenAI-safe tool names back to real tool IDs, emits tool call
  and result events, applies timeouts, and checks task-workspace path boundaries
  in sandbox mode. It also exposes execution hooks for tool before/after/blocked
  observations so policy and audit extensions have one attachment point.

- `brain/core/tools/builtin_plan_tool.py`
  Built-in planning tool that emits plan and plan-step events.

- `brain/core/tools/builtin_todo_tool.py`
  User-visible TODO/progress projection tool.

- `brain/core/tools/builtin_handoff_tool.py`
  Creates child work through an allowed target Agent.

- `brain/core/tools/builtin_request_input_tool.py`
  Pauses the task and asks the user for missing information.

- `brain/core/tools/mcp_tool.py`
  Wraps MCP tools as TaskPilot tools.

## MCP And Local Tools

- `tools/mcp_local/mcp_server.py`
  Registers local MCP tools.

- `tools/mcp_local/tool/filesystem.py`
  Local file tools. Reads can inspect user-provided paths; writes, edits,
  copies, moves, deletes, and directory creation stay inside the task workspace.

- `tools/mcp_local/tool/process_manager.py`
  Long-running local command management.

- `tools/mcp_local/tool/management_tools.py`
  Config, MCP manager, skill, memory, subagent, and message-management tools.

- `tools/mcp_local/tool/skill_registry.py`
  Task-local skill lifecycle boundary. It scans installed skills, records
  metadata and load usage, enforces enable/disable state, and limits loaded
  skill content before tools expose it to an Agent.

- `tools/aggre_mcp_market/`
  MCP market aggregation and remote MCP client support.

## Auth And Ownership

- `auth/router.py`
  Login, callback, logout, account, provider, admin, and legacy mapping routes.

- `auth/dependencies.py`
  Current-user dependencies for protected APIs and WebSockets.

- `auth/service.py`
  User, identity, session, OAuth state, audit, and legacy mapping operations.

- `auth/hardening.py`
  Startup validation for production auth configuration.

## Frontend

- `frontend/src/App.vue`
  Main task product UI. It creates sessions, submits messages, subscribes to
  run events over WebSocket/SSE, renders timeline items, plan progress, tool
  calls, approvals, artifacts, and final answers.

- `frontend/src/styles.css`
  Product layout and responsive styling.

- `frontend/vite.config.js`
  Serves the built UI under `/agent/web/`.

## Test Map

- `tests/auth/`
  Login, session, provider, hardening, ownership, and protected route behavior.

- `tests/tasks/test_agent_registry.py`
  Agent config loading, policy, handoffs, and eval parsing.

- `tests/tasks/test_tool_gateway.py`
  ToolGateway policy filtering and high-risk approval behavior.

- `tests/tasks/test_tool_policy.py`
  Shared tool-selection and alias matching rules.

- `tests/tasks/test_plan_snapshots.py`
  Shared plan payload extraction, latest plan metadata, and terminal plan
  status behavior.

- `tests/tasks/test_tool_collection_policy.py`
  Runtime tool execution, metadata, blocking, timeouts, and workspace boundary
  checks.

- `tests/tasks/test_task_store.py`
  Task lifecycle, event persistence, artifacts, workspaces, waiting input, and
  autoagent event recording.

- `tests/tasks/test_session_store.py`
  Session, message, run, run-event, and artifact projections.

- `tests/tasks/test_session_context.py`
  Session summary, recent-history context construction, and attached-file
  restoration for model input.

- `tests/tasks/test_task_memory_context.py`
  Agent memory read scopes, memory/RAG context loading, sanitization, and
  degraded lookup behavior.

- `tests/tasks/test_task_runner.py`
  Process-local background worker registration, cancellation, and cleanup.

- `tests/tasks/test_task_recovery.py`
  Startup recovery request reconstruction, recovery events, and background
  restart handoff.

- `tests/tasks/test_react_handler.py`
  ReAct planning behavior, duplicate tool-call guard, plan-step sync, and
  summary streaming.

- `tests/tasks/test_autoagent_web.py`
  Web task page source-level contracts. Prefer real request/event tests for new
  behavior when possible.

## Current Refactoring Direction

The next improvements should keep moving code toward these boundaries:

1. `TaskStore` as the primary run ledger.
2. `SessionStore` as the web conversation projection.
3. `ToolGateway` as the only tool exposure path.
4. `ToolCollection` as the only tool execution path.
5. `AgentRegistry` as the only Agent config loading path.
6. `AutoAgentRuntime` as the only normal request runtime path.
7. `TaskRecovery` as the startup path for queued or interrupted task records.
8. `InProcessTaskRunner` as the only process-local worker registry.
9. Stable task/run event schemas for frontend replay.
