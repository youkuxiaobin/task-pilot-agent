from __future__ import annotations

from brain.core.task_runner import InProcessTaskRunner


class FakeWorker:
    def __init__(self) -> None:
        self.cancelled = False
        self.done_state = False
        self.callbacks = []

    def done(self) -> bool:
        return self.done_state

    def cancel(self) -> None:
        self.cancelled = True

    def add_done_callback(self, callback) -> None:
        self.callbacks.append(callback)

    def finish(self) -> None:
        self.done_state = True
        for callback in list(self.callbacks):
            callback(self)


async def _noop() -> None:
    return None


def test_in_process_task_runner_registers_and_cleans_up_finished_worker():
    running = {}
    created = []
    worker = FakeWorker()

    def fake_create_task(coro):
        created.append(coro)
        return worker

    runner = InProcessTaskRunner(running, create_task=fake_create_task)
    runner.start("run-1", _noop())

    assert runner.get("run-1") is worker
    assert list(runner.active_run_ids()) == ["run-1"]

    worker.finish()

    assert runner.get("run-1") is None
    created[0].close()


def test_in_process_task_runner_cancel_can_keep_or_remove_registry_entry():
    running = {}
    created = []
    first_worker = FakeWorker()
    second_worker = FakeWorker()
    workers = [first_worker, second_worker]

    def fake_create_task(coro):
        created.append(coro)
        return workers.pop(0)

    runner = InProcessTaskRunner(running, create_task=fake_create_task)

    runner.start("run-keep", _noop())
    cancelled = runner.cancel("run-keep")

    assert cancelled is first_worker
    assert first_worker.cancelled is True
    assert runner.get("run-keep") is first_worker

    runner.start("run-remove", _noop())
    removed = runner.cancel("run-remove", remove=True)

    assert removed is second_worker
    assert second_worker.cancelled is True
    assert runner.get("run-remove") is None

    for coro in created:
        coro.close()


def test_in_process_task_runner_invokes_start_and_done_callbacks():
    events = []
    worker = FakeWorker()

    def fake_create_task(coro):
        return worker

    runner = InProcessTaskRunner(
        {},
        create_task=fake_create_task,
        on_start=lambda run_id, item: events.append(("start", run_id, item)),
        on_done=lambda run_id, item: events.append(("done", run_id, item)),
    )
    coro = _noop()
    runner.start("run-callback", coro)
    worker.finish()

    assert events == [
        ("start", "run-callback", worker),
        ("done", "run-callback", worker),
    ]
    coro.close()
