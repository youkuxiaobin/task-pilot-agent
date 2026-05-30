from __future__ import annotations
import fnmatch
import logging
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
    tool_allowed_checker: Optional[Callable[[str], bool]] = None
    blocked_tools: List[str] = field(default_factory=list)

    def set_allowed_tool_patterns(self, patterns: Optional[List[str]]) -> None:
        self.allowed_tool_patterns = [pattern for pattern in (patterns or []) if pattern]

    def set_tool_allowed_checker(self, checker: Optional[Callable[[str], bool]]) -> None:
        self.tool_allowed_checker = checker

    def is_tool_allowed(self, name: str) -> bool:
        if self.tool_allowed_checker is not None:
            return self.tool_allowed_checker(name)
        patterns = self.allowed_tool_patterns
        if not patterns:
            return True
        for pattern in patterns:
            if pattern in {"*", "all"}:
                return True
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

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
        if not self.is_tool_allowed(name):
            message = f"tool `{name}` is not allowed for this agent"
            logger.warning(message)
            self._emit_tool_blocked(name, input_obj, message)
            return message

        tool = self.tool_map.get(name)
        if not tool:
            return None
        logger.debug("execute tool %s with argument keys=%s", name, sorted(input_obj.keys()))
        self._emit_tool_call(name, input_obj)
        result = await tool.execute(input_obj)
        return result

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
            },
            None,
            False,
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
