# TaskPilotAgent 代码索引

## 项目概述

TaskPilotAgent 是一个基于 Python 的智能代理系统，采用 FastAPI 框架构建，支持多种 LLM 提供商和 MCP (Model Context Protocol) 工具集成。系统采用分层架构，包含大脑（Brain）、工具（Tools）、LLM 管理和内存管理等核心模块。

## 项目结构

```
仓库根目录/
├── config/                    # 配置文件
│   ├── config.yaml           # 主配置文件
│   └── prompt.yaml           # 提示词配置
├── task-pilot-agent/         # 主项目目录
│   ├── brain/                # 大脑模块（核心智能逻辑）
│   ├── tools/                # 工具模块
│   ├── llm/                  # LLM 管理模块
│   ├── memory/               # 内存管理模块
│   ├── config/               # 配置管理
│   └── main.py              # 主程序入口
└── README.md
```

## 核心模块详解

### 1. 主程序入口 (main.py)

**功能**: 系统启动入口，初始化 MCP 服务器和 FastAPI 应用

**关键组件**:
- `main()`: 异步主函数，启动 MCP 服务器和 Web 服务器
- 集成 MCP 市场路由器和代理路由器
- 配置 uvicorn 服务器

### 2. 大脑模块 (brain/)

#### 2.1 应用层 (app.py)
**功能**: FastAPI 应用的主要路由和 SSE 流处理

**关键函数**:
- `build_tool_collection()`: 构建工具集合，包括本地工具和 MCP 市场工具
- `sse_stream()`: SSE 流处理函数
- `autoagent()`: 主要的代理处理端点

#### 2.2 代理系统 (agents/)

##### BaseAgent (base_agent.py)
**功能**: 所有代理的基类，定义通用接口和行为

**关键属性**:
- `name`: 代理名称
- `description`: 代理描述
- `state`: 代理状态 (IDLE, RUNNING, FINISHED, ERROR)
- `maxSteps`: 最大执行步数
- `messages`: 消息历史

**关键方法**:
- `run()`: 运行代理主循环
- `step()`: 执行单步操作（抽象方法）
- `add_message()`: 添加消息到历史

##### PlanningAgent (planning_agent.py)
**功能**: 规划代理，负责创建和管理任务计划

**特点**:
- 继承自 BaseAgent
- 使用系统提示词模板
- 调用 LLM 生成计划

##### ExecutorAgent (executor_agent.py)
**功能**: 执行代理，负责执行计划步骤

**特点**:
- 继承自 ReActAgent
- 实现 think-act 循环
- 支持工具调用和参数提取

##### ReActAgent (react_agent.py)
**功能**: ReAct 模式代理基类

**关键方法**:
- `think()`: 思考阶段（抽象方法）
- `act()`: 行动阶段（抽象方法）
- `step()`: 执行 think-act 循环

##### SummaryAgent (summary_agent.py)
**功能**: 总结代理，负责总结任务结果

#### 2.3 处理器系统 (handlers/)

##### AgentHandlerFactory (factory.py)
**功能**: 代理处理器工厂，根据上下文和请求选择合适的处理器

##### PlanSolveHandler (plan_solve.py)
**功能**: 计划-解决处理器，协调规划、执行和总结代理

**处理流程**:
1. 创建 PlanningAgent 生成计划
2. 使用 ExecutorAgent 执行计划步骤
3. 使用 SummaryAgent 总结结果

#### 2.4 工具系统 (tools/)

##### ToolCollection (collection.py)
**功能**: 工具集合管理，支持工具注册、查找和执行

**关键方法**:
- `add_tool()`: 添加工具
- `get_tool()`: 获取工具
- `execute()`: 执行工具
- `to_openai_tools()`: 转换为 OpenAI 工具格式

##### MCPTool (mcp_tool.py)
**功能**: MCP 工具适配器，将 MCP 市场工具适配到系统工具接口

**关键类**:
- `MCPTool`: MCP 工具实体
- `MCPToolFetcher`: MCP 工具获取器

### 3. 工具模块 (tools/)

#### 3.1 本地 MCP 工具 (mcp_local/)

##### MCP 服务器 (mcp_server.py)
**功能**: 本地 MCP 服务器，提供各种工具服务

**支持的工具**:
- 代码解释器 (code_interpreter)
- 报告生成 (report)
- 深度搜索 (deepsearch)
- 计划管理 (planing)
- 天气查询 (weather)

#### 3.2 MCP 市场工具 (aggre_mcp_market/)

##### 聚合应用 (app.py)
**功能**: MCP 工具市场聚合服务

**关键端点**:
- `/tools`: 获取可用工具列表
- `/call_tool`: 调用指定工具

### 4. LLM 管理模块 (llm/)

#### 4.1 管理器 (manager.py)
**功能**: LLM 提供商管理和统一接口

**支持的提供商**:
- OpenAI
- Claude (Anthropic)
- Gemini (Google)

**关键方法**:
- `ask()`: 普通对话
- `ask_tool()`: 工具调用对话
- `generate()`: 生成响应

#### 4.2 提供商实现 (providers/)

**基类**: `LLMProvider` (base.py)
**实现类**:
- `OpenAIProvider`: OpenAI API 实现
- `ClaudeProvider`: Claude API 实现
- `GeminiProvider`: Gemini API 实现

### 5. 内存管理模块 (memory/)

#### 5.1 内存管理器 (memory_mgr.py)
**功能**: 基于 mem0 的内存管理系统

**关键组件**:
- `MemoryManager`: 主内存管理器
- `PlanManager`: 计划管理器
- `RAGRetriever`: RAG 检索器

**支持功能**:
- 对话历史存储
- 计划状态管理
- 向量检索

## 配置系统

### 配置文件结构
- `config/config.yaml`: 主配置文件
- `config/prompt.yaml`: 提示词配置

### 关键配置项
- LLM 提供商设置
- MCP 服务器配置
- 内存管理配置
- 代理参数配置

## 数据模型

### 核心数据类
- `Doc`: 文档模型
- `LLMMessage`: LLM 消息模型
- `AgentRequest`: 代理请求模型
- `AgentContext`: 代理上下文模型

## 关键设计模式

1. **策略模式**: 不同代理类型的实现
2. **工厂模式**: 代理处理器和工具创建
3. **适配器模式**: MCP 工具适配
4. **观察者模式**: SSE 事件流
5. **ReAct 模式**: 思考-行动循环

## 扩展点

1. **新增代理类型**: 继承 BaseAgent 或 ReActAgent
2. **新增工具**: 实现 BaseTool 接口
3. **新增 LLM 提供商**: 继承 LLMProvider 基类
4. **新增处理器**: 实现 AgentHandlerService 接口

## 运行方式

```bash
# 启动主程序
cd task-pilot-agent && uv run main.py

# 或直接启动 Web 服务
cd task-pilot-agent && uv run uvicorn app_main:app --host 0.0.0.0 --port 8080
```

## 依赖关系

- FastAPI: Web 框架
- uvicorn: ASGI 服务器
- mem0: 内存管理
- aiohttp: 异步 HTTP 客户端
- pydantic: 数据验证
- mcp: Model Context Protocol 支持
