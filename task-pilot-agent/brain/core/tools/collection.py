from __future__ import annotations
import asyncio
import fnmatch
import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Any, Optional, List
from dataclasses import dataclass, field

try:
    from langfuse import observe
except Exception:  # pragma: no cover - optional tracing dependency
    def observe(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]

        def decorator(func):
            return func

        return decorator

from .base import BaseTool

try:
    from utils.logger import get_logger
except Exception:  # pragma: no cover - optional logging dependency
    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)


@dataclass
class ToolCollection:
    tool_map: Dict[str, BaseTool] = field(default_factory=dict)
    agentContext: "AgentContext" = None  # type: ignore
    currentTask: Optional[str] = None
    digitalEmployees: Optional[Dict[str, str]] = None
    allowed_tool_patterns: Optional[List[str]] = None
    tool_timeout_patterns: Dict[str, float] = field(default_factory=dict)
    tool_allowed_checker: Optional[Callable[[str], bool]] = None
    blocked_tools: List[str] = field(default_factory=list)
    last_execution: Optional[Dict[str, Any]] = None

    def set_allowed_tool_patterns(self, patterns: Optional[List[str]]) -> None:
        if patterns is None:
            self.allowed_tool_patterns = None
            return
        self.allowed_tool_patterns = [pattern for pattern in patterns if pattern]

    def set_tool_timeout_patterns(self, timeouts: Optional[Dict[str, float]]) -> None:
        self.tool_timeout_patterns = {}
        for pattern, timeout in (timeouts or {}).items():
            try:
                timeout_value = float(timeout)
            except (TypeError, ValueError):
                continue
            if pattern and timeout_value > 0:
                self.tool_timeout_patterns[str(pattern)] = timeout_value

    def set_tool_allowed_checker(self, checker: Optional[Callable[[str], bool]]) -> None:
        self.tool_allowed_checker = checker

    def is_tool_allowed(self, name: str) -> bool:
        if self.tool_allowed_checker is not None:
            return self.tool_allowed_checker(name)
        patterns = self.allowed_tool_patterns
        if patterns is None:
            return True
        if not patterns:
            return False
        for pattern in patterns:
            if pattern in {"*", "all"}:
                return True
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def timeout_for_tool(self, name: str) -> Optional[float]:
        for pattern, timeout in self.tool_timeout_patterns.items():
            if pattern in {"*", "all"} or fnmatch.fnmatch(name, pattern):
                return timeout
        return None

    def add_tool(self, tool: BaseTool) -> bool:
        if not self.is_tool_allowed(tool.name):
            self.blocked_tools.append(tool.name)
            logger.debug("tool %s blocked by allowed tool policy", tool.name)
            return False
        self.tool_map[tool.name] = tool
        return True

    def get_tool(self, name: str) -> Optional[BaseTool]:
        if not self.is_tool_allowed(name):
            return None
        return self.tool_map.get(name)

    @observe(name="tool_execute")
    async def execute(self, name: str, input_obj: Dict[str, Any]) -> Optional[str]:
        started_at = time.perf_counter()
        started_at_wall = time.time()
        if not self.is_tool_allowed(name):
            message = f"tool `{name}` is not allowed for this agent"
            logger.warning(message)
            self.last_execution = self._execution_metadata(
                name,
                started_at,
                started_at_wall,
                failed=True,
                error=message,
            )
            self._emit_tool_blocked(name, input_obj, message)
            return message

        tool = self.tool_map.get(name)
        if not tool:
            self.last_execution = self._execution_metadata(
                name,
                started_at,
                started_at_wall,
                failed=True,
                error="tool not found",
            )
            return None
        logger.debug("execute tool %s with argument keys=%s", name, sorted(input_obj.keys()))
        self._emit_tool_call(name, input_obj)
        try:
            timeout = self.timeout_for_tool(name)
            if timeout:
                result = await asyncio.wait_for(tool.execute(input_obj), timeout=timeout)
            else:
                result = await tool.execute(input_obj)
            self.last_execution = self._execution_metadata(name, started_at, started_at_wall, result=result)
            return result
        except asyncio.TimeoutError:
            error = f"tool `{name}` timed out"
            self.last_execution = self._execution_metadata(
                name,
                started_at,
                started_at_wall,
                failed=True,
                error=error,
            )
            self._emit_tool_result(name, input_obj)
            raise
        except Exception as exc:
            self.last_execution = self._execution_metadata(
                name,
                started_at,
                started_at_wall,
                failed=True,
                error=str(exc),
            )
            self._emit_tool_result(name, input_obj)
            raise

    def _execution_metadata(
        self,
        name: str,
        started_at: float,
        started_at_wall: float,
        *,
        result: Any = None,
        failed: bool = False,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "tool": name,
            "durationMs": max(0, int((time.perf_counter() - started_at) * 1000)),
            "failed": failed,
            "startedAt": self._iso_time(started_at_wall),
            "completedAt": self._iso_time(time.time()),
        }
        payload.update(self._audit_context())
        if error:
            payload["error"] = error
        if result is not None:
            payload["resultSummary"] = self._summarize_value(result)
        return payload

    @staticmethod
    def _iso_time(value: float) -> str:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    def _audit_context(self) -> Dict[str, str]:
        context = self.agentContext
        if context is None:
            return {}
        printer = getattr(context, "printer", None)
        values = {
            "userId": getattr(context, "user_id", None),
            "agentId": getattr(context, "agent_id", None),
            "taskId": getattr(context, "task_id", None) or getattr(printer, "task_id", None),
            "requestId": getattr(context, "requestId", None),
            "runId": getattr(context, "run_id", None),
            "sessionId": getattr(context, "sessionId", None),
            "runEnvironment": getattr(context, "run_environment", None),
            "workDir": getattr(context, "work_dir", None),
        }
        return {key: str(value) for key, value in values.items() if value}

    @staticmethod
    def _summarize_value(value: Any, limit: int = 500) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                text = str(value)
        text = text.replace("\r", "\n").strip()
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    def _emit_tool_call(self, name: str, input_obj: Dict[str, Any]) -> None:
        printer = getattr(self.agentContext, "printer", None)
        if printer is None:
            return
        printer.send(
            None,
            "tool_call",
            {
                "tool": name,
                "arguments": input_obj,
                **self._audit_context(),
            },
            self.getDigitalEmployee(name),
            False,
        )

    def _emit_tool_blocked(self, name: str, input_obj: Dict[str, Any], message: str) -> None:
        printer = getattr(self.agentContext, "printer", None)
        if printer is None:
            return
        printer.send(
            None,
            "notifications",
            {
                "process_message": message,
                "tool": name,
                "arguments": input_obj,
                **self._audit_context(),
            },
            None,
            False,
        )

    def _emit_tool_result(self, name: str, input_obj: Dict[str, Any]) -> None:
        printer = getattr(self.agentContext, "printer", None)
        if printer is None:
            return
        metadata = self.last_execution if isinstance(self.last_execution, dict) else {}
        printer.send(
            None,
            "tool_result",
            {
                "tool": name,
                "arguments": input_obj,
                **metadata,
            },
            self.getDigitalEmployee(name),
            True,
        )

    def to_openai_tools(self) -> List[Dict[str, Any]]:
        """将ToolCollection中的MCPTool转换为OpenAI function call格式"""
        tools = []
        
        for tool in self.tool_map.values():
            if not self.is_tool_allowed(tool.name):
                continue
            # 检查是否是MCPTool类型
            if hasattr(tool, 'input_schema') and hasattr(tool, 'full_name'):
                # 构建OpenAI function call格式
                openai_tool = {
                    "type": "function",
                    "function": {
                        "name": tool.full_name,  # 使用full_name作为工具名称
                        "description": tool.description,
                        "parameters": tool.input_schema if tool.input_schema else {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                }
                tools.append(openai_tool)
        
        return tools
    def to_str(self) -> str:
        tools_str = ""
        for tool in self.tool_map.values():
            if not self.is_tool_allowed(tool.name):
                continue
            tools_str += f"<tool_name>{tool.name}</tool_name><tool_description>{tool.description}</tool_description>\n"
        return tools_str
    
    def updateDigitalEmployee(self, employees: Dict[str, str]) -> None:
        self.digitalEmployees = employees

    def getDigitalEmployee(self, tool_name: str) -> Optional[str]:
        if not self.digitalEmployees:
            return None
        return self.digitalEmployees.get(tool_name)
