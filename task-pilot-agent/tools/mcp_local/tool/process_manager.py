from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.mcp_local.tool.filesystem import DEFAULT_MAX_OUTPUT_CHARS, _resolve_path


MAX_BUFFER_BYTES = 256 * 1024
_PROCESSES: Dict[str, "ManagedProcess"] = {}


@dataclass
class ManagedProcess:
    process_id: str
    command: str
    working_dir: str
    process: asyncio.subprocess.Process
    started_at: float = field(default_factory=time.time)
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    stdout_offset: int = 0
    stderr_offset: int = 0
    reader_tasks: List[asyncio.Task[Any]] = field(default_factory=list)


async def _read_stream(stream: Optional[asyncio.StreamReader], buffer: bytearray) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > MAX_BUFFER_BYTES:
            del buffer[: len(buffer) - MAX_BUFFER_BYTES]


def _tail_text(buffer: bytearray, max_chars: int) -> str:
    safe_limit = max(1000, min(int(max_chars or DEFAULT_MAX_OUTPUT_CHARS), 100_000))
    data = bytes(buffer[-safe_limit:])
    return data.decode("utf-8", errors="replace")


def _get_process(process_id: str) -> ManagedProcess:
    managed = _PROCESSES.get(str(process_id or ""))
    if managed is None:
        raise KeyError(f"process not found: {process_id}")
    return managed


async def start_process_command(
    command: str,
    *,
    working_dir: str = ".",
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    if not str(command or "").strip():
        raise ValueError("command is required")
    cwd = _resolve_path(
        working_dir or ".",
        work_dir=work_dir,
        require_workspace=bool(work_dir),
        must_exist=True,
    )
    if not cwd.is_dir():
        raise NotADirectoryError(str(cwd))

    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    process_id = uuid.uuid4().hex
    managed = ManagedProcess(
        process_id=process_id,
        command=command,
        working_dir=str(cwd),
        process=process,
    )
    managed.reader_tasks = [
        asyncio.create_task(_read_stream(process.stdout, managed.stdout)),
        asyncio.create_task(_read_stream(process.stderr, managed.stderr)),
    ]
    _PROCESSES[process_id] = managed
    return {
        "processId": process_id,
        "command": command,
        "workingDir": str(cwd),
        "pid": process.pid,
        "running": True,
    }


async def poll_process_command(
    process_id: str,
    *,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> Dict[str, Any]:
    managed = _get_process(process_id)
    return_code = managed.process.returncode
    if return_code is None:
        return_code = managed.process.returncode
    running = return_code is None
    return {
        "processId": managed.process_id,
        "command": managed.command,
        "workingDir": managed.working_dir,
        "running": running,
        "exitCode": return_code,
        "startedAt": int(managed.started_at * 1000),
        "stdout": _tail_text(managed.stdout, max_output_chars),
        "stderr": _tail_text(managed.stderr, max_output_chars),
    }


async def write_process_command(process_id: str, input_text: str) -> Dict[str, Any]:
    managed = _get_process(process_id)
    if managed.process.returncode is not None:
        raise RuntimeError("process has already exited")
    if managed.process.stdin is None:
        raise RuntimeError("process stdin is not available")
    data = str(input_text or "").encode("utf-8")
    managed.process.stdin.write(data)
    await managed.process.stdin.drain()
    return {"processId": managed.process_id, "bytesWritten": len(data)}


async def stop_process_command(process_id: str, *, kill: bool = False) -> Dict[str, Any]:
    managed = _get_process(process_id)
    if managed.process.returncode is None:
        if kill:
            managed.process.kill()
        else:
            managed.process.terminate()
        try:
            await asyncio.wait_for(managed.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            managed.process.kill()
            await managed.process.wait()
    for task in managed.reader_tasks:
        if not task.done():
            task.cancel()
    _PROCESSES.pop(managed.process_id, None)
    return {
        "processId": managed.process_id,
        "stopped": True,
        "exitCode": managed.process.returncode,
    }


async def list_process_commands() -> Dict[str, Any]:
    items = []
    for managed in list(_PROCESSES.values()):
        items.append(
            {
                "processId": managed.process_id,
                "command": managed.command,
                "workingDir": managed.working_dir,
                "running": managed.process.returncode is None,
                "exitCode": managed.process.returncode,
                "startedAt": int(managed.started_at * 1000),
            }
        )
    return {"processes": items, "count": len(items)}
