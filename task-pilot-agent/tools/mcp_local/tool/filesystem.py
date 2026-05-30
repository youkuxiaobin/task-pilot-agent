# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_MAX_READ_BYTES = 128 * 1024
DEFAULT_MAX_OUTPUT_CHARS = 12_000


def _workspace_root(work_dir: Optional[str]) -> Path:
    if work_dir:
        return Path(work_dir).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_path(
    path: str,
    *,
    work_dir: Optional[str] = None,
    require_workspace: bool = False,
    must_exist: bool = False,
) -> Path:
    if not str(path or "").strip():
        raise ValueError("path is required")

    root = _workspace_root(work_dir)
    raw_path = Path(path).expanduser()
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    resolved = candidate.resolve(strict=False)

    if require_workspace:
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"path must stay inside task workspace: {resolved}") from exc

    if must_exist and not resolved.exists():
        raise FileNotFoundError(str(resolved))

    return resolved


def _file_entry(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "type": "directory" if path.is_dir() else "file",
        "size": stat.st_size,
        "modifiedAt": int(stat.st_mtime * 1000),
    }


async def read_file(
    path: str,
    *,
    encoding: str = "utf-8",
    max_bytes: int = DEFAULT_MAX_READ_BYTES,
    offset: int = 0,
    as_base64: bool = False,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    target = _resolve_path(path, work_dir=work_dir, must_exist=True)
    if not target.is_file():
        raise IsADirectoryError(str(target))

    safe_offset = max(0, int(offset or 0))
    safe_limit = max(1, min(int(max_bytes or DEFAULT_MAX_READ_BYTES), 1024 * 1024))
    size = target.stat().st_size

    with target.open("rb") as file_obj:
        file_obj.seek(safe_offset)
        data = file_obj.read(safe_limit + 1)

    truncated = len(data) > safe_limit or safe_offset + len(data) < size
    data = data[:safe_limit]

    if as_base64:
        content = base64.b64encode(data).decode("ascii")
        content_type = "base64"
    else:
        content = data.decode(encoding or "utf-8", errors="replace")
        content_type = "text"

    return {
        "path": str(target),
        "size": size,
        "offset": safe_offset,
        "bytesRead": len(data),
        "truncated": truncated,
        "contentType": content_type,
        "encoding": None if as_base64 else (encoding or "utf-8"),
        "content": content,
    }


async def write_file(
    path: str,
    content: str,
    *,
    mode: str = "overwrite",
    encoding: str = "utf-8",
    create_dirs: bool = True,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    target = _resolve_path(path, work_dir=work_dir, require_workspace=True)
    if target.exists() and target.is_dir():
        raise IsADirectoryError(str(target))

    if create_dirs:
        target.parent.mkdir(parents=True, exist_ok=True)

    normalized_mode = (mode or "overwrite").strip().lower()
    if normalized_mode not in {"overwrite", "append"}:
        raise ValueError("mode must be overwrite or append")

    file_mode = "a" if normalized_mode == "append" else "w"
    with target.open(file_mode, encoding=encoding or "utf-8") as file_obj:
        written = file_obj.write(content or "")

    return {
        "path": str(target),
        "mode": normalized_mode,
        "charactersWritten": written,
        "size": target.stat().st_size,
    }


async def list_directory(
    path: str = ".",
    *,
    recursive: bool = False,
    max_entries: int = 200,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    target = _resolve_path(path or ".", work_dir=work_dir, must_exist=True)
    if not target.is_dir():
        raise NotADirectoryError(str(target))

    safe_limit = max(1, min(int(max_entries or 200), 1000))
    iterator = target.rglob("*") if recursive else target.iterdir()
    entries: List[Dict[str, Any]] = []
    truncated = False
    for item in iterator:
        if len(entries) >= safe_limit:
            truncated = True
            break
        entries.append(_file_entry(item))

    entries.sort(key=lambda item: (item["type"], item["path"]))
    return {
        "path": str(target),
        "recursive": bool(recursive),
        "truncated": truncated,
        "entries": entries,
    }


async def file_stat(path: str, *, work_dir: Optional[str] = None) -> Dict[str, Any]:
    target = _resolve_path(path, work_dir=work_dir, must_exist=True)
    stat = target.stat()
    return {
        "path": str(target),
        "exists": True,
        "type": "directory" if target.is_dir() else "file",
        "size": stat.st_size,
        "createdAt": int(getattr(stat, "st_ctime", 0) * 1000),
        "modifiedAt": int(stat.st_mtime * 1000),
        "permissions": oct(stat.st_mode & 0o777),
    }


async def create_directory(
    path: str,
    *,
    exist_ok: bool = True,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    target = _resolve_path(path, work_dir=work_dir, require_workspace=True)
    target.mkdir(parents=True, exist_ok=bool(exist_ok))
    return {"path": str(target), "created": True}


async def copy_file(
    source: str,
    destination: str,
    *,
    overwrite: bool = False,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    source_path = _resolve_path(source, work_dir=work_dir, must_exist=True)
    target_path = _resolve_path(destination, work_dir=work_dir, require_workspace=True)
    if not source_path.is_file():
        raise IsADirectoryError(str(source_path))
    if target_path.exists() and not overwrite:
        raise FileExistsError(str(target_path))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return {"source": str(source_path), "destination": str(target_path), "size": target_path.stat().st_size}


async def move_file(
    source: str,
    destination: str,
    *,
    overwrite: bool = False,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    source_path = _resolve_path(source, work_dir=work_dir, require_workspace=True, must_exist=True)
    target_path = _resolve_path(destination, work_dir=work_dir, require_workspace=True)
    if target_path.exists():
        if not overwrite:
            raise FileExistsError(str(target_path))
        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(target_path))
    return {"source": str(source_path), "destination": str(target_path)}


async def delete_path(
    path: str,
    *,
    recursive: bool = False,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    target = _resolve_path(path, work_dir=work_dir, require_workspace=True, must_exist=True)
    root = _workspace_root(work_dir)
    if target == root:
        raise PermissionError("refusing to delete task workspace root")

    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()
    return {"path": str(target), "deleted": True}


async def shell_exec(
    command: str,
    *,
    working_dir: Optional[str] = None,
    timeout: int = 30,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
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

    safe_timeout = max(1, min(int(timeout or 30), 120))
    safe_output_limit = max(1000, min(int(max_output_chars or DEFAULT_MAX_OUTPUT_CHARS), 100_000))

    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=safe_timeout)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    combined_length = len(stdout) + len(stderr)
    truncated = combined_length > safe_output_limit
    if truncated:
        stdout = stdout[:safe_output_limit]
        remaining = max(0, safe_output_limit - len(stdout))
        stderr = stderr[:remaining]

    return {
        "command": command,
        "workingDir": str(cwd),
        "exitCode": process.returncode,
        "timedOut": timed_out,
        "truncated": truncated,
        "stdout": stdout,
        "stderr": stderr,
    }
