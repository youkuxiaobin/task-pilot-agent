from __future__ import annotations
from typing import List, Optional
from dataclasses import dataclass, field
from enum import Enum


class RoleType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolFunction:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    type: str
    function: ToolFunction


@dataclass
class Message:
    role: RoleType
    content: str
    base64Image: Optional[str] = None
    toolCallId: Optional[str] = None
    toolCalls: Optional[List[ToolCall]] = None

    @staticmethod
    def user(content: str, base64_image: Optional[str] = None) -> "Message":
        return Message(role=RoleType.USER, content=content, base64Image=base64_image)

    @staticmethod
    def system(content: str, base64_image: Optional[str] = None) -> "Message":
        return Message(role=RoleType.SYSTEM, content=content, base64Image=base64_image)

    @staticmethod
    def assistant(content: str, base64_image: Optional[str] = None) -> "Message":
        return Message(role=RoleType.ASSISTANT, content=content, base64Image=base64_image)

    @staticmethod
    def tool(content: str, tool_call_id: str, base64_image: Optional[str] = None) -> "Message":
        return Message(role=RoleType.TOOL, content=content, toolCallId=tool_call_id, base64Image=base64_image)


@dataclass
class Memory:
    messages: List[Message] = field(default_factory=list)

    def add(self, msg: Message) -> None:
        self.messages.append(msg)

    def add_all(self, msgs: List[Message]) -> None:
        self.messages.extend(msgs)

    def last(self) -> Optional[Message]:
        return self.messages[-1] if self.messages else None

    def clear_tool_context(self) -> None:
        self.messages = [m for m in self.messages if not (
            m.role == RoleType.TOOL or (m.role == RoleType.ASSISTANT and m.toolCalls)
        )]

