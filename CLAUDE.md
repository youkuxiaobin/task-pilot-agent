# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TaskPilotAgent is a sophisticated AI agent orchestration framework built with Python and FastAPI. It implements a multi-agent system that can plan, execute, and summarize complex tasks using various LLM providers (OpenAI, Claude, Gemini) and extensible MCP (Model Context Protocol) tools.

## Development Commands

### Running the Application

```bash
# Start the main application (recommended method)
cd task-pilot-agent && uv run main.py

# Using uvicorn directly
cd task-pilot-agent && uv run uvicorn app_main:app --host 0.0.0.0 --port 9010
```

The application starts two servers:
- MCP Server: Port 9009 (tool marketplace and local tools)
- Web/API Server: Port 9010 (FastAPI application)

### Running Tests

```bash
# Run all tests
cd task-pilot-agent && uv run pytest -v --tb=short tests/

# Run tests using the test runner script
cd task-pilot-agent && uv run python tests/run_tests.py

# Run specific test directory
cd task-pilot-agent && uv run pytest tests/memory/
cd task-pilot-agent && uv run pytest tests/llm_test/
cd task-pilot-agent && uv run pytest tests/gaia/

# Run a single test file
cd task-pilot-agent && uv run pytest tests/memory/test_memory_mgr.py -v
```

### Dependency Management

This project uses UV as the package manager:

```bash
# Install dependencies
uv sync

# Add a new dependency
uv add <package-name>

# Update dependencies
uv lock --upgrade
```

## Architecture Overview

### Core System Flow: Plan-Solve-Summarize

The system uses a three-phase approach for handling user requests:

1. **Planning Phase** (`PlanningAgent`)
   - Receives user query and context
   - Generates a structured task breakdown plan
   - Stores plan in memory system
   - Entry: `task-pilot-agent/brain/core/agents/planning_agent.py`

2. **Execution Phase** (`ExecutorAgent`)
   - Iterates through each plan step
   - Uses ReAct pattern (think → act loop)
   - Calls MCP tools as needed
   - Supports dynamic replanning based on execution results
   - Entry: `task-pilot-agent/brain/core/agents/executor_agent.py`

3. **Summarization Phase** (`SummaryAgent`)
   - Aggregates execution results
   - Generates final user-facing summary
   - Supports multiple output formats (markdown, HTML, PPT, Excel)
   - Entry: `task-pilot-agent/brain/core/agents/summary_agent.py`

### Request Flow

```
HTTP Request → FastAPI (brain/app.py:autoagent)
    ↓
AgentContext initialization (loads conversation history, file references, memory)
    ↓
ToolCollection built (MCPToolFetcher fetches from MCP Market)
    ↓
AgentHandlerFactory selects handler (factory.py)
    ↓
PlanSolveHandler orchestrates the three phases
    ↓
SSE Stream responses to client in real-time
```

### Tool System Architecture

**MCP (Model Context Protocol) Integration:**
- `tools/mcp_local/` - Local MCP server with built-in tools
- `tools/aggre_mcp_market/` - Aggregates multiple MCP servers
- Tools are dynamically fetched and registered in `ToolCollection`
- Each tool is wrapped in `MCPTool` adapter for uniform interface

**Built-in Tools:**
- `code_interpreter` - Executes Python code in sandboxed environment
- `deepsearch` - Multi-source information retrieval (Jina, Bing, DuckDuckGo)
- `report` - Generates reports in markdown/HTML/PPT formats
- `weather` - Weather information queries
- `planing` - Planning and task management utilities

Tool execution path: `brain/core/tools/collection.py:execute()` → `MCPTool` → HTTP call to MCP server → actual tool implementation

### LLM Provider System

The LLM system provides a unified interface across multiple providers:

**Entry Point:** `llm/manager.py:LLMManager`

**Supported Providers:**
- OpenAI (and OpenAI-compatible APIs like SiliconFlow)
- Claude (Anthropic)
- Gemini (Google)

**Provider Implementation Pattern:**
Each provider inherits from `llm/providers/base.py:LLMProvider` and implements:
- `ask()` - Basic completion
- `ask_tool()` - Function calling / tool use
- `generate()` - Streaming generation

**Key Features:**
- Automatic message compression when approaching context limits (`llm/compressor.py`)
- Token counting utilities (`llm/tokenizer.py`)
- Prompt template system (`llm/prompt_template.py`)

### Memory System

**Components:**
- `MemoryManager` (mem0ai integration) - Stores conversation context in vector DB
- `MessageManager` - Manages conversation history with MySQL persistence
- `RAGRetriever` - Retrieves relevant context from past interactions
- `PlanManager` - Stores and retrieves plan states

**Memory Flow:**
1. User messages stored in MySQL via `MessageManager`
2. Important context extracted and embedded via mem0ai
3. Vectors stored in Qdrant (configurable to Milvus)
4. RAG retrieval happens before each agent execution
5. Retrieved context added to agent's working memory

**Configuration:** Set in `config/config.yaml` under `memory:` and `vector_store:` sections

### File Management

File operations are handled through `file/file_op.py`:
- Upload files: `/file/v1/upload`
- Retrieve files: `/file/v1/download/{file_id}`
- Database tracking: `file/file_table_op.py` with SQLModel ORM
- Supported types defined in `file/file_type.py`

Files are referenced in agent requests via the `files` array and automatically loaded into context.

## Configuration System

### Main Configuration File: `config/config.yaml`

**Critical Settings:**

```yaml
core:
  planer_max_steps: 20              # Max planning steps
  executor_max_steps: 10            # Max execution steps per task
  planner_replan_each_step: true    # Whether to replan after each step
  planner_replan_on_failure: true   # Whether to replan on execution failure

llm:
  provider: "openai"                # openai | claude | gemini
  config:
    api_key: "sk-xxx"
    site_url: "https://api.siliconflow.cn/v1"
    model: "Pro/deepseek-ai/DeepSeek-V3.2-Exp"
    context_length: 160000

mcp:
  mcp_local:
    port: 9009                      # Local MCP server port
  mcp_market:
    mcp_servers:                    # External MCP servers to aggregate
      - url: "http://127.0.0.1:9009/mcp"
        tool_prefix: "mcp_local"
```

### Environment Variables

Configuration can be overridden via environment variables or `.env` file:
- `APP_CONFIG_FILE` - Path to config.yaml (set in main.py)
- Database credentials, API keys can be set in `.env`

### Prompt Templates

Located in `config/prompt.yaml` (Chinese) and `config/prompt_en.yaml` (English):
- System prompts for each agent type
- Tool usage instructions
- Output formatting guidelines

Language is controlled by `lang: en` or `lang: ch` in config.yaml.

## Key Implementation Details

### Agent State Management

All agents inherit from `BaseAgent` (`brain/core/agents/base_agent.py`):
- States: `IDLE`, `RUNNING`, `FINISHED`, `ERROR`
- Each agent maintains message history (`messages` list)
- `step()` method is the core execution unit (abstract, implemented by subclasses)
- `run()` orchestrates the main loop with max step limits

### ReAct Pattern Implementation

`ExecutorAgent` and `ReActAgent` implement the ReAct (Reasoning + Acting) pattern:

```python
# Simplified flow in brain/core/agents/react_agent.py
async def step(self):
    thought = await self.think()  # LLM generates reasoning
    action = await self.act()      # LLM decides tool to use
    observation = await self.execute_tool(action)
    return observation
```

This creates a loop: think → act → observe → think → ...

### SSE (Server-Sent Events) Streaming

Real-time updates are sent via SSE (`brain/app.py:sse_stream()`):
- Events: `step_start`, `step_end`, `tool_call`, `plan_created`, `summary`, etc.
- `SSEPrinter` (`brain/core/printer.py`) abstracts event emission
- Allows UI to show progress in real-time

### Handler Selection Logic

`AgentHandlerFactory` (`brain/core/handlers/factory.py`) selects the appropriate handler:
- Checks for special modes in agent request (e.g., `agent_mode`)
- Defaults to `PlanSolveHandler` for complex tasks
- `ReActHandler` available for simpler single-agent flows

### Replanning Mechanism

Controlled by configuration flags:
- `planner_replan_each_step`: If true, regenerates plan after each execution step
- `planner_replan_on_failure`: If true, replans when a step fails
- Implementation in `brain/core/handlers/plan_solve.py`

This allows dynamic adaptation to changing circumstances during execution.

## Adding New Components

### Adding a New Agent Type

1. Create agent class in `brain/core/agents/`
2. Inherit from `BaseAgent` or `ReActAgent`
3. Implement required methods: `step()`, and optionally `think()` / `act()`
4. Register in appropriate handler

Example skeleton:
```python
from brain.core.agents.base_agent import BaseAgent

class MyCustomAgent(BaseAgent):
    async def step(self):
        # Your agent logic here
        pass
```

### Adding a New Tool

1. Add tool implementation to `tools/mcp_local/tool/`
2. Register tool in `tools/mcp_local/mcp_server.py`
3. Tool will be automatically discovered by MCP market

Example:
```python
# In mcp_server.py
from tool.my_tool import my_tool_function

@mcp.tool()
async def my_tool(param: str) -> str:
    """Tool description for LLM"""
    return await my_tool_function(param)
```

### Adding a New LLM Provider

1. Create provider class in `llm/providers/`
2. Inherit from `LLMProvider` base class
3. Implement: `ask()`, `ask_tool()`, `generate()`
4. Register in `llm/manager.py`

### Adding a New Handler

1. Create handler class in `brain/core/handlers/`
2. Inherit from `AgentHandlerService` protocol
3. Implement `handle()` method
4. Add selection logic to `AgentHandlerFactory`

## Important Notes

### Configuration Precedence

Settings are loaded in this order (highest to lowest priority):
1. Environment variables
2. `.env` file
3. `config/config.yaml`
4. Default values in code

### API Key Management

**Never commit API keys.** Use one of:
- Environment variables: `export LLM_API_KEY=sk-xxx`
- `.env` file (add to `.gitignore`)
- Override config.yaml values at runtime

### Database Setup

The system requires MySQL for file tracking and conversation history:
- Connection configured in `config/config.yaml` under `db:`
- Tables auto-created via SQLModel on first run
- File operations in `file/file_table_op.py`

### Vector Database Setup

For memory system:
- Default: Qdrant (configurable to Milvus)
- Runs on `localhost:6333` by default
- Collection name and embedding dims in config.yaml
- Start Qdrant: `docker run -p 6333:6333 qdrant/qdrant`

### MCP Server Ports

Two servers run simultaneously:
- Port 9009: MCP tool server (streamable-http transport)
- Port 9010: FastAPI web server

Ensure both ports are available before starting.

## Code Navigation Reference

**Main entry point:** `task-pilot-agent/main.py:31` (async main function)

**Request handling:** `task-pilot-agent/brain/app.py:135` (autoagent endpoint)

**Agent orchestration:** `task-pilot-agent/brain/core/handlers/plan_solve.py:32` (PlanSolveHandler.handle)

**Tool execution:** `task-pilot-agent/brain/core/tools/collection.py:45` (ToolCollection.execute)

**LLM calls:** `task-pilot-agent/llm/manager.py:67` (LLMManager.ask_tool)

**Memory operations:** `task-pilot-agent/memory/memory_mgr.py:42` (MemoryManager.add / search)

**Plan management:** `task-pilot-agent/brain/core/tools/plan_state.py` (PlanState class)

**MCP server:** `task-pilot-agent/tools/mcp_local/mcp_server.py:120` (mcp_run_async)
