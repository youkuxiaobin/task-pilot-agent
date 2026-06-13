from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional


CreateTask = Callable[[Awaitable[Any]], Any]
RunnerCallback = Callable[[str, Any], None]


class InProcessTaskRunner:
    """Process-local background task registry.

    This keeps today's asyncio implementation behind a small boundary so the
    app can later swap in a durable queue without scattering task registration
    and cancellation logic through API handlers.
    """

    def __init__(
        self,
        running_tasks: Optional[Dict[str, Any]] = None,
        *,
        create_task: Optional[CreateTask] = None,
        on_start: Optional[RunnerCallback] = None,
        on_done: Optional[RunnerCallback] = None,
    ) -> None:
        self.running_tasks: Dict[str, Any] = running_tasks if running_tasks is not None else {}
        self._create_task = create_task or asyncio.create_task
        self._on_start = on_start
        self._on_done = on_done

    def start(self, run_id: str, coro: Awaitable[Any]) -> Any:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id is required")
        worker = self._create_task(coro)
        self._notify(self._on_start, normalized_run_id, worker)
        self.running_tasks[normalized_run_id] = worker
        self._attach_cleanup(normalized_run_id, worker)
        return worker

    def get(self, run_id: str) -> Optional[Any]:
        return self.running_tasks.get(str(run_id or "").strip())

    def cancel(self, run_id: str, *, remove: bool = False) -> Optional[Any]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        worker = self.running_tasks.pop(normalized_run_id, None) if remove else self.running_tasks.get(normalized_run_id)
        if worker and not self._is_done(worker):
            worker.cancel()
        return worker

    def discard(self, run_id: str, worker: Optional[Any] = None) -> None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return
        current = self.running_tasks.get(normalized_run_id)
        if worker is None or current is worker:
            self.running_tasks.pop(normalized_run_id, None)

    def active_run_ids(self) -> Iterable[str]:
        return tuple(self.running_tasks.keys())

    def _attach_cleanup(self, run_id: str, worker: Any) -> None:
        add_done_callback = getattr(worker, "add_done_callback", None)
        if not callable(add_done_callback):
            if self._is_done(worker):
                self._notify(self._on_done, run_id, worker)
                self.discard(run_id, worker)
            return
        add_done_callback(lambda finished: self._complete_worker(run_id, finished))

    def _complete_worker(self, run_id: str, worker: Any) -> None:
        self._notify(self._on_done, run_id, worker)
        self.discard(run_id, worker)

    @staticmethod
    def _is_done(worker: Any) -> bool:
        done = getattr(worker, "done", None)
        if not callable(done):
            return False
        try:
            return bool(done())
        except Exception:
            return False

    @staticmethod
    def _notify(callback: Optional[RunnerCallback], run_id: str, worker: Any) -> None:
        if not callback:
            return
        try:
            callback(run_id, worker)
        except Exception:
            return
