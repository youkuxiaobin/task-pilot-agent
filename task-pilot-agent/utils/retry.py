from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar


T = TypeVar("T")


def run_with_retries(
    func: Callable[[], T],
    *,
    attempts: int = 3,
    delay: float = 1.0,
    logger: Optional[Any] = None,
    action_name: str = "operation",
) -> T:
    """Execute a synchronous callable with simple retry/backoff logic."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - bubble up after retries
            last_exc = exc
            if logger is not None:
                logger.warning(
                    "%s attempt %d/%d failed: %s",
                    action_name,
                    attempt,
                    attempts,
                    exc,
                )
            if attempt < attempts and delay > 0:
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def async_run_with_retries(
    func: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    delay: float = 1.0,
    logger: Optional[Any] = None,
    action_name: str = "operation",
) -> T:
    """Execute an async callable with retry/backoff logic."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except Exception as exc:  # noqa: BLE001 - bubble up after retries
            last_exc = exc
            if logger is not None:
                logger.warning(
                    "%s attempt %d/%d failed: %s",
                    action_name,
                    attempt,
                    attempts,
                    exc,
                )
            if attempt < attempts and delay > 0:
                await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
