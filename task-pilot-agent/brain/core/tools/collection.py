from __future__ import annotations
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from langfuse import observe

from .base import BaseTool
from utils.logger import get_logger


logger = get_logger(__name__)


@dataclass
class ToolCollection:
    tool_map: Dict[str, BaseTool] = field(default_factory=dict)
    agentContext: "AgentContext" = None  # type: ignore
    currentTask: Optional[str] = None
    digitalEmployees: Optional[Dict[str, str]] = None

    def add_tool(self, tool: BaseTool) -> None:
        self.tool_map[tool.name] = tool

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self.tool_map.get(name)

    @observe(name="tool_execute")
    async def execute(self, name: str, input_obj: Dict[str, Any]) -> Optional[str]:
        tool = self.tool_map.get(name)
        if not tool:
            return None
        logger.debug("execute tool %s with argument keys=%s", name, sorted(input_obj.keys()))
        result = await tool.execute(input_obj)
        return result

    def to_openai_tools(self) -> List[Dict[str, Any]]:
        """将ToolCollection中的MCPTool转换为OpenAI function call格式"""
        tools = []
        
        for tool in self.tool_map.values():
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
            tools_str += f"<tool_name>{tool.name}</tool_name><tool_description>{tool.description}</tool_description>\n"
        return tools_str
    
    def updateDigitalEmployee(self, employees: Dict[str, str]) -> None:
        self.digitalEmployees = employees

    def getDigitalEmployee(self, tool_name: str) -> Optional[str]:
        if not self.digitalEmployees:
            return None
        return self.digitalEmployees.get(tool_name)
