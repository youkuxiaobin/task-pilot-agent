from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from brain.core.printer import Printer
from brain.core.tools.collection import ToolCollection
from llm.types import LLMMessage


@dataclass
class FileItem:
    fileName: str
    description: Optional[str] = None
    ossUrl: Optional[str] = None
    domainUrl: Optional[str] = None
    fileSize: Optional[int] = None
    isInternalFile: bool = False


@dataclass
class AgentContext:
    requestId: str
    sessionId: str
    user_id: str
    agent_id: str
    run_id: str
    query: str
    task: Optional[str]
    printer: Printer  # type: ignore
    toolCollection: ToolCollection  # type: ignore
    dateInfo: str
    messages: List[LLMMessage] = field(default_factory=list)

    productFiles: List[FileItem] = field(default_factory=list)
    isStream: bool = True
    streamMessageType: Optional[str] = None
    taskProductFiles: List[FileItem] = field(default_factory=list)
    mode: str = "plans_executor"
    outputStyle: str = "markdown"
    task_id: Optional[str] = None
    work_dir: Optional[str] = None
    agent_system_prompt: Optional[str] = None
    selected_tools: Optional[List[str]] = None

    def serialize_messages(self) -> List[Dict[str, str]]:
        """Convert stored conversation messages into a simple list for downstream prompts."""
        serialized: List[Dict[str, str]] = []
        for msg in self.messages or []:
            if isinstance(msg, dict):
                role = str(msg.get("role") or "")
                content = str(msg.get("content") or "")
            else:
                role = str(getattr(msg, "role", "") or "")
                content = str(getattr(msg, "content", "") or "")

            content = content.strip()
            if not content:
                continue
            serialized.append({"role": role, "content": content})
        return serialized
