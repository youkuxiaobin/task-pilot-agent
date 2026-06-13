from __future__ import annotations
import asyncio
import inspect
import json
import logging
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from brain.core.tool_policy import (
    matches_tool_pattern as _matches_tool_pattern,
    tool_name_variants as _tool_name_variants,
)
from .base import BaseTool

try:
    from utils.logger import get_logger
except Exception:  # pragma: no cover - optional logging dependency
    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)


_OPENAI_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_OPENAI_TOOL_NAME_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")


def _to_openai_tool_name(value: str) -> str:
    text = str(value or "").strip().replace(":", "-")
    text = _OPENAI_TOOL_NAME_INVALID_CHARS.sub("_", text).strip("_-")
    return text or "tool"


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
    openai_tool_name_map: Dict[str, str] = field(default_factory=dict)
    tool_openai_name_map: Dict[str, str] = field(default_factory=dict)
    execution_hooks: List[Callable[[Dict[str, Any]], Any]] = field(default_factory=list)

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

    def add_execution_hook(self, hook: Callable[[Dict[str, Any]], Any]) -> None:
        self.execution_hooks.append(hook)

    def is_tool_allowed(self, name: str) -> bool:
        if self.tool_allowed_checker is not None:
            return self.tool_allowed_checker(name)
        patterns = self.allowed_tool_patterns
        if patterns is None:
            return True
        if not patterns:
            return False
        for pattern in patterns:
            if _matches_tool_pattern(name, pattern):
                return True
        return False

    def timeout_for_tool(self, name: str) -> Optional[float]:
        for pattern, timeout in self.tool_timeout_patterns.items():
            if _matches_tool_pattern(name, pattern):
                return timeout
        return None

    def resolve_tool_name(self, name: str) -> str:
        mapped_name = self.openai_tool_name_map.get(str(name or ""))
        if mapped_name:
            return mapped_name
        for candidate in _tool_name_variants(name):
            if candidate in self.tool_map:
                return candidate
        requested_safe_name = _to_openai_tool_name(name)
        for tool_name in self.tool_map:
            if _to_openai_tool_name(tool_name) == requested_safe_name:
                return tool_name
        return name

    def openai_tool_name_for(self, name: str) -> str:
        resolved_name = self.resolve_tool_name(name)
        if resolved_name not in self.tool_openai_name_map:
            self._rebuild_openai_tool_name_maps()
        return self.tool_openai_name_map.get(resolved_name, _to_openai_tool_name(resolved_name))

    def add_tool(self, tool: BaseTool) -> bool:
        if not self.is_tool_allowed(tool.name):
            self.blocked_tools.append(tool.name)
            logger.debug("tool %s blocked by allowed tool policy", tool.name)
            return False
        self.tool_map[tool.name] = tool
        return True

    def get_tool(self, name: str) -> Optional[BaseTool]:
        resolved_name = self.resolve_tool_name(name)
        if not self.is_tool_allowed(resolved_name):
            return None
        return self.tool_map.get(resolved_name)

    async def execute(self, name: str, input_obj: Dict[str, Any]) -> Optional[str]:
        name = self.resolve_tool_name(name)
        started_at = time.perf_counter()
        started_at_wall = time.time()
        if not self.is_tool_allowed(name):
            message = f"tool `{name}` is not allowed for this agent"
            logger.warning(message)
            self.last_execution = self._execution_metadata(
                name,
                input_obj,
                started_at,
                started_at_wall,
                failed=True,
                error=message,
            )
            await self._notify_execution_hooks("blocked", name, input_obj, self.last_execution)
            self._emit_tool_blocked(name, input_obj, message)
            return message

        tool = self.tool_map.get(name)
        if not tool:
            self.last_execution = self._execution_metadata(
                name,
                input_obj,
                started_at,
                started_at_wall,
                failed=True,
                error="tool not found",
            )
            await self._notify_execution_hooks("failed", name, input_obj, self.last_execution)
            self._emit_tool_result(name, input_obj)
            return None
        boundary_error = self._validate_runtime_boundary(input_obj)
        if boundary_error:
            self.last_execution = self._execution_metadata(
                name,
                input_obj,
                started_at,
                started_at_wall,
                failed=True,
                error=boundary_error,
            )
            await self._notify_execution_hooks("blocked", name, input_obj, self.last_execution)
            self._emit_tool_call(name, input_obj)
            self._emit_tool_result(name, input_obj)
            return boundary_error
        logger.debug("execute tool %s with argument keys=%s", name, sorted(input_obj.keys()))
        await self._notify_execution_hooks("before_call", name, input_obj)
        self._emit_tool_call(name, input_obj)
        try:
            timeout = self.timeout_for_tool(name)
            if timeout:
                result = await asyncio.wait_for(tool.execute(input_obj), timeout=timeout)
            else:
                result = await tool.execute(input_obj)
            self.last_execution = self._execution_metadata(name, input_obj, started_at, started_at_wall, result=result)
            await self._notify_execution_hooks("after_call", name, input_obj, self.last_execution)
            self._emit_tool_result(name, input_obj)
            return result
        except asyncio.CancelledError:
            error = f"tool `{name}` cancelled"
            self.last_execution = self._execution_metadata(
                name,
                input_obj,
                started_at,
                started_at_wall,
                failed=True,
                error=error,
                cancelled=True,
            )
            await self._notify_execution_hooks("cancelled", name, input_obj, self.last_execution)
            self._emit_tool_result(name, input_obj)
            raise
        except asyncio.TimeoutError:
            error = f"tool `{name}` timed out"
            self.last_execution = self._execution_metadata(
                name,
                input_obj,
                started_at,
                started_at_wall,
                failed=True,
                error=error,
            )
            await self._notify_execution_hooks("failed", name, input_obj, self.last_execution)
            self._emit_tool_result(name, input_obj)
            raise
        except Exception as exc:
            self.last_execution = self._execution_metadata(
                name,
                input_obj,
                started_at,
                started_at_wall,
                failed=True,
                error=str(exc),
            )
            await self._notify_execution_hooks("failed", name, input_obj, self.last_execution)
            self._emit_tool_result(name, input_obj)
            raise

    def _execution_metadata(
        self,
        name: str,
        input_obj: Dict[str, Any],
        started_at: float,
        started_at_wall: float,
        *,
        result: Any = None,
        failed: bool = False,
        error: Optional[str] = None,
        cancelled: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "tool": name,
            "argumentsSummary": self._summarize_value(input_obj),
            "durationMs": max(0, int((time.perf_counter() - started_at) * 1000)),
            "failed": failed,
            "cancelled": cancelled,
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

    async def _notify_execution_hooks(
        self,
        stage: str,
        name: str,
        input_obj: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.execution_hooks:
            return
        event = {
            "stage": stage,
            "tool": name,
            "argumentsSummary": self._summarize_value(input_obj),
            "metadata": metadata or {},
            **self._audit_context(),
        }
        for hook in list(self.execution_hooks):
            try:
                result = hook(dict(event))
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("tool execution hook failed for %s at %s", name, stage)

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
                "argumentsSummary": self._summarize_value(input_obj),
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
                "argumentsSummary": self._summarize_value(input_obj),
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
        failed = bool(metadata.get("failed"))
        summary = str(metadata.get("error") or metadata.get("resultSummary") or "")
        printer.send(
            None,
            "tool_result",
            {
                "tool": name,
                "arguments": input_obj,
                "type": "tool_call_failed" if failed else "tool_call_completed",
                "ok": not failed,
                "summary": summary,
                "content": summary,
                "metadata": metadata,
                **metadata,
            },
            self.getDigitalEmployee(name),
            True,
        )

    def _validate_runtime_boundary(self, input_obj: Dict[str, Any]) -> Optional[str]:
        context = self.agentContext
        if context is None:
            return None
        if getattr(context, "run_environment", None) != "sandbox":
            return None
        work_dir = getattr(context, "work_dir", None)
        if not work_dir:
            return None

        root = Path(str(work_dir)).expanduser().resolve()
        for key_path, value in self._iter_path_values(input_obj):
            violation = self._path_boundary_violation(root, value)
            if violation:
                return f"path argument `{key_path}` must stay inside task workspace: {violation}"
        return None

    def _iter_path_values(self, value: Any, key_path: str = "") -> List[Tuple[str, str]]:
        values: List[Tuple[str, str]] = []
        if isinstance(value, dict):
            for key, item in value.items():
                next_path = f"{key_path}.{key}" if key_path else str(key)
                key_name = str(key).lower()
                if isinstance(item, str) and self._is_path_argument(key_name, item):
                    values.append((next_path, item))
                else:
                    values.extend(self._iter_path_values(item, next_path))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                values.extend(self._iter_path_values(item, f"{key_path}[{index}]"))
        return values

    @staticmethod
    def _is_path_argument(key_name: str, value: str) -> bool:
        if not value or "://" in value:
            return False
        if any(part in key_name for part in ("path", "directory", "folder", "workspace", "work_dir")):
            return True
        if key_name == "dir" or key_name.endswith("_dir") or key_name.endswith("-dir"):
            return True
        return value.startswith(("/", "~", "../", "./"))

    @staticmethod
    def _path_boundary_violation(root: Path, value: str) -> Optional[str]:
        try:
            raw_path = Path(value).expanduser()
            candidate = raw_path if raw_path.is_absolute() else root / raw_path
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except ValueError:
            return str(Path(value).expanduser())
        except OSError:
            return str(Path(value).expanduser())
        return None

    def to_openai_tools(self) -> List[Dict[str, Any]]:
        """将ToolCollection中的MCPTool转换为OpenAI function call格式"""
        tools = []
        self._rebuild_openai_tool_name_maps()
        
        for tool in self.tool_map.values():
            if not self.is_tool_allowed(tool.name):
                continue
            # 检查是否是MCPTool类型
            if hasattr(tool, 'input_schema') and hasattr(tool, 'full_name'):
                tool_name = self.tool_openai_name_map.get(tool.name, _to_openai_tool_name(tool.name))
                if not _OPENAI_TOOL_NAME_PATTERN.match(tool_name):
                    logger.warning("skip tool with invalid OpenAI name: %s -> %s", tool.name, tool_name)
                    continue
                # 构建OpenAI function call格式
                openai_tool = {
                    "type": "function",
                    "function": {
                        "name": tool_name,
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

    def _rebuild_openai_tool_name_maps(self) -> None:
        self.openai_tool_name_map = {}
        self.tool_openai_name_map = {}
        used_names: Dict[str, str] = {}
        for tool in self.tool_map.values():
            if not self.is_tool_allowed(tool.name):
                continue
            base_name = _to_openai_tool_name(getattr(tool, "full_name", None) or tool.name)
            tool_name = base_name
            suffix = 2
            while tool_name in used_names and used_names[tool_name] != tool.name:
                tool_name = f"{base_name}_{suffix}"
                suffix += 1
            used_names[tool_name] = tool.name
            self.openai_tool_name_map[tool_name] = tool.name
            self.tool_openai_name_map[tool.name] = tool_name

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
