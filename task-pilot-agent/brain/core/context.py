from __future__ import annotations
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional
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
    mode: str = "react"
    outputStyle: str = "markdown"
    task_id: Optional[str] = None
    work_dir: Optional[str] = None
    agent_system_prompt: Optional[str] = None
    selected_tools: Optional[List[str]] = None
    approved_tools: Optional[List[str]] = None
    run_environment: str = "local"
    language: str = "ch"
    memory_context: Dict[str, Any] = field(default_factory=dict)
    agent_memory: Dict[str, Any] = field(default_factory=dict)
    waiting_for_input: bool = False
    waiting_input_prompt: Optional[str] = None

    def allows_memory_write(self, scope: str) -> bool:
        memory_config = self.agent_memory if isinstance(self.agent_memory, dict) else {}
        if "write" not in memory_config:
            return True
        raw_scopes = memory_config.get("write")
        if raw_scopes in (None, ""):
            return False
        if isinstance(raw_scopes, str):
            scopes = {raw_scopes}
        elif isinstance(raw_scopes, list):
            scopes = {str(item) for item in raw_scopes}
        else:
            return False
        normalized = {item.strip().lower() for item in scopes if item.strip()}
        if not normalized:
            return False
        requested = str(scope or "").strip().lower()
        if "*" in normalized or "all" in normalized or requested in normalized:
            return True
        if requested in {"message_history", "agent_step"} and "task_history" in normalized:
            return True
        return False

    def compose_system_prompt(self, base_prompt: str) -> str:
        parts: List[str] = []
        language_prompt = self.language_instruction()
        if language_prompt:
            parts.append(language_prompt)
        agent_prompt = (self.agent_system_prompt or "").strip()
        if agent_prompt:
            parts.append(agent_prompt)
        memory_prompt = self.format_memory_context_for_prompt()
        if memory_prompt:
            parts.append(memory_prompt)
        parts.append(base_prompt)
        return "\n\n".join(part for part in parts if part)

    def normalized_language(self) -> str:
        value = (self.language or "ch").strip().lower()
        if value in {"en", "en-us", "en_us", "english"}:
            return "en"
        return "ch"

    def language_instruction(self) -> str:
        if self.normalized_language() == "en":
            return (
                "Output language: English. Reply to the user in clear, natural English. "
                "Keep tool arguments and file paths unchanged when exact values are required."
            )
        return "输出语言：中文。请用清晰、自然的中文回复用户；必要的工具参数和文件路径保持原样。"

    def format_memory_context_for_prompt(self) -> str:
        context = self.memory_context or {}
        memory_results = context.get("memoryResults") if isinstance(context, dict) else None
        rag_results = context.get("ragResults") if isinstance(context, dict) else None
        if not memory_results and not rag_results:
            return ""

        if self.normalized_language() == "en":
            lines: List[str] = [
                "The following are relevant memory and knowledge-base snippets for this task. Use them only when relevant, and prefer the current user input."
            ]
        else:
            lines = [
                "以下是本次任务可参考的历史记忆和知识库检索摘要。只在相关时使用，并优先以当前用户输入为准。"
            ]
        for label, items in (("memory", memory_results), ("knowledge", rag_results)):
            if not isinstance(items, list):
                continue
            for idx, item in enumerate(items[:5], start=1):
                if not isinstance(item, dict):
                    continue
                snippet = str(item.get("snippet") or "").strip()
                if not snippet:
                    continue
                source = str(item.get("source") or label)
                score = item.get("score")
                suffix = f" score={score}" if score is not None else ""
                metadata = item.get("metadata")
                metadata_text = ""
                if isinstance(metadata, dict) and metadata:
                    try:
                        metadata_text = f" metadata={json.dumps(metadata, ensure_ascii=False)}"
                    except TypeError:
                        metadata_text = f" metadata={str(metadata)}"
                lines.append(f"- [{label}:{idx}] source={source}{suffix}{metadata_text}: {snippet}")
        return "\n".join(lines)

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
