import asyncio
import multiprocessing as mp
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

os.environ.setdefault("APP_CONFIG_FILE", str(project_root / "config" / "config.yaml"))

from config.config import agentSettings  # noqa: E402
from tools.mcp_local.mcp_server import mcp_run_async  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def _set_proctitle(title: str) -> None:
    """尽量设置进程名，便于 ps/top 查看；优先 setproctitle，降级 prctl."""
    try:
        import setproctitle

        setproctitle.setproctitle(title)
        return
    except Exception:
        pass

    try:
        import ctypes

        libc = ctypes.cdll.LoadLibrary("libc.so.6")
        PR_SET_NAME = 15
        libc.prctl(PR_SET_NAME, ctypes.c_char_p(title[:15].encode("utf-8")), 0, 0, 0)
    except Exception:
        pass


def _mcp_runner():
    """Module-level runner for MCP subprocess (must be picklable for spawn)."""
    # 忽略 SIGINT，让主进程处理
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _set_proctitle("taskpilotagent-mcp-server")

    try:
        asyncio.run(mcp_run_async(transport=agentSettings.mcp.mcp_local.transport))
    except KeyboardInterrupt:
        logger.error("MCP subprocess interrupted, exiting...")
    except Exception as e:
        logger.error(f"MCP subprocess error: {e}")



def _local_mcp_url() -> str:
    host = agentSettings.mcp.mcp_local.host or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{agentSettings.mcp.mcp_local.port}/mcp"


async def _initialize_mcp_session_once(url: str, transport: str) -> Optional[str]:
    if transport == "streamable-http":
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.client.session import ClientSession

        async with streamablehttp_client(url) as (read, write, get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return get_session_id()

    if transport == "sse":
        from mcp.client.sse import sse_client
        from mcp.client.session import ClientSession

        session_id: Optional[str] = None

        def _on_session_created(sid: str) -> None:
            nonlocal session_id
            session_id = sid

        async with sse_client(url, on_session_created=_on_session_created) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return session_id 

    raise ValueError(f"Unsupported MCP transport: {transport}")


async def _wait_for_mcp_ready(proc: mp.Process, timeout: float) -> str:
    url = _local_mcp_url()
    transport = agentSettings.mcp.mcp_local.transport
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None

    while time.monotonic() < deadline:
        if proc is not None and not proc.is_alive():
            raise RuntimeError("MCP subprocess exited during startup")
        try:
            session_id = await _initialize_mcp_session_once(url, transport)
            if session_id:
                return session_id
            last_error = RuntimeError("initialize() returned without sessionId")
        except Exception as exc:
            last_error = exc
            logger.debug("MCP initialize check failed: %s", exc)
        await asyncio.sleep(0.5)

    if last_error:
        raise TimeoutError(f"MCP subprocess did not return sessionId in time: {last_error}")
    raise TimeoutError("MCP subprocess did not return sessionId in time")


def start_mcp_subprocess(
    block: bool = True,
    timeout: float = 60.0,
) -> mp.Process:
    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=_mcp_runner, daemon=True, name="MCP-Server")
    proc.start()
    logger.info(f"MCP subprocess started pid={proc.pid} (spawn)")

    if block:
        try:
            session_id = asyncio.run(_wait_for_mcp_ready(proc, timeout))
            logger.info(f"MCP subprocess ready sessionId={session_id}")
        except Exception:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
            raise
        if not proc.is_alive():
            raise RuntimeError("MCP subprocess exited during startup")
    else:
        time.sleep(0.5)
        if not proc.is_alive():
            raise RuntimeError("MCP subprocess failed to start")

    return proc


def cleanup_mcp_process(_mcp_process):
    if _mcp_process and _mcp_process.is_alive():
        logger.info(f"Terminating MCP subprocess (pid={_mcp_process.pid})...")
        _mcp_process.terminate()
        _mcp_process.join(timeout=5)

        if _mcp_process.is_alive():
            logger.warning("MCP subprocess did not terminate gracefully, killing...")
            _mcp_process.kill()
            _mcp_process.join()

        logger.info("MCP subprocess terminated")
