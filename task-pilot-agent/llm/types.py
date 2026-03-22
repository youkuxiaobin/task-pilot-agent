from dataclasses import dataclass, Field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import json



class RoleType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


#@dataclass
#class ToolFunction:
#    name: str
#    arguments: str


#@dataclass
#class ToolCall:
#    id: str
#    type: str
#    function: ToolFunction

@dataclass
class ToolCall:
    """Structured representation of a single tool invocation."""
    name: str
    arguments: Dict[str, Any]
    id: Optional[str] = None
    

@dataclass
class LLMResponse:
    content: str
    tool_calls: List[ToolCall] = None

@dataclass
class LLMMessage:
    role: str
    content: str
    base64Image: Optional[str] = None
    toolCallId: Optional[str] = None
    toolCalls: Optional[List[ToolCall]] = None

    def to_dict(self) -> Dict[str, str]:
        out: Dict[str, str] = {
			"role": getattr(self.role, "value", str(self.role)) or "",
			"content": self.content or "",
		}
        if self.base64Image:
            out["base64Image"] = self.base64Image
        if self.toolCallId:
            out["toolCallId"] = self.toolCallId
        if self.toolCalls:
            out["toolCalls"] = json.dumps([tc.__dict__ for tc in self.toolCalls], ensure_ascii=False)
        return out

    @staticmethod
    def user(content: str, base64_image: Optional[str] = None) -> "LLMMessage":
        return LLMMessage(role=RoleType.USER.value, content=content, base64Image=base64_image)

    @staticmethod
    def system(content: str, base64_image: Optional[str] = None) -> "LLMMessage":
        return LLMMessage(role=RoleType.SYSTEM.value, content=content, base64Image=base64_image)

    @staticmethod
    def assistant(content: str, base64_image: Optional[str] = None) -> "LLMMessage":
        return LLMMessage(role=RoleType.ASSISTANT.value, content=content, base64Image=base64_image)

    @staticmethod
    def tool(content: str, tool_call_id: str, base64_image: Optional[str] = None) -> "LLMMessage":
        return LLMMessage(role=RoleType.TOOL.value, content=content, toolCallId=tool_call_id, base64Image=base64_image)
        

@dataclass
class LLMResponse:
    text: str
    model: str
    raw: Any = None


Messages = List[LLMMessage]

def parse_openai_tool_calls(resp: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """
    返回:
      - content: 助手自然语言内容
      - tool_calls: [{'id': str, 'name': str, 'arguments': dict}, ...]
    """
    # 常见 Provider 会兼容这两种
    content = getattr(resp, "content", None) or getattr(resp, "text", "")

    raw = getattr(resp, "raw", None)
    tool_calls = []

    # 直接读取通用属性
    if hasattr(resp, "tool_calls") and resp.tool_calls:
        for tc in resp.tool_calls:
            name = getattr(tc.function, "name", None) if hasattr(tc, "function") else None
            args = getattr(tc.function, "arguments", "{}") if hasattr(tc, "function") else "{}"
            try:
                args = json.loads(args) if isinstance(args, str) else (args or {})
            except Exception:
                args = {}
            tool_calls.append({
                "id": getattr(tc, "id", ""),
                "name": name,
                "arguments": args,
            })
        return content, tool_calls

    # 兜底：从 raw.chat_completion 里取
    try:
        ch0 = raw.choices[0]
        msg = ch0.message
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                name = getattr(tc.function, "name", None)
                args = getattr(tc.function, "arguments", "{}")
                try:
                    args = json.loads(args) if isinstance(args, str) else (args or {})
                except Exception:
                    args = {}
                tool_calls.append({
                    "id": getattr(tc, "id", ""),
                    "name": name,
                    "arguments": args,
                })
        # 覆盖 content（以模型返回为准）
        content = getattr(msg, "content", content) or content
    except Exception:
        pass

    return content or "", tool_calls