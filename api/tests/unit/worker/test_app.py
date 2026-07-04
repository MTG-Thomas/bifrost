from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.worker import app as worker_app


class FakeConsumer:
    def __init__(self, queue_name: str = "queue", *, fail_start: bool = False) -> None:
        self.queue_name = queue_name
        self.fail_start = fail_start
        self.started = 0
        self.stopped = 0
        self.drained: list[float] = []

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("start failed")
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def drain(self, *, deadline: float) -> None:
        self.drained.append(deadline)


@pytest.fixture
def settings() -> SimpleNamespace:
    return SimpleNamespace(environment="test")


@pytest.mark.asyncio
async def test_start_initializes_db_starts_consumers_and_waits_for_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    init_db = AsyncMock()
    monkeypatch.setattr(worker_app, "init_db", init_db)

    worker = worker_app.Worker()

    async def start_consumers() -> None:
        worker._shutdown_event.set()

    worker._start_consumers = start_consumers  # type: ignore[method-assign]

    await worker.start()

    init_db.assert_awaited_once()
    assert worker.running is True


@pytest.mark.asyncio
async def test_start_cleans_up_partial_start_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_app, "init_db", AsyncMock())
    worker = worker_app.Worker()
    cleanup = AsyncMock()
    worker._cleanup_after_failed_start = cleanup  # type: ignore[method-assign]

    async def fail_start_consumers() -> None:
        raise RuntimeError("consumer boot failed")

    worker._start_consumers = fail_start_consumers  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="consumer boot failed"):
        await worker.start()

    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_after_failed_start_stops_consumers_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    close_db = AsyncMock()
    rabbit_close = AsyncMock()
    monkeypatch.setattr(worker_app, "close_db", close_db)
    monkeypatch.setattr(worker_app.rabbitmq, "close", rabbit_close)
    good = FakeConsumer("good")

    class BadStop(FakeConsumer):
        async def stop(self) -> None:
            raise RuntimeError("stop failed")

    worker = worker_app.Worker()
    worker._consumers = [good, BadStop("bad")]

    await worker._cleanup_after_failed_start()

    assert good.stopped == 1
    rabbit_close.assert_awaited_once()
    close_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_consumers_creates_and_starts_all_consumers(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    created: list[FakeConsumer] = []

    def factory(name: str):
        def create() -> FakeConsumer:
            consumer = FakeConsumer(name)
            created.append(consumer)
            return consumer

        return create

    monkeypatch.setattr(worker_app, "WorkflowExecutionConsumer", factory("workflow"))
    monkeypatch.setattr(worker_app, "PackageInstallConsumer", factory("packages"))
    monkeypatch.setattr(worker_app, "AgentRunConsumer", factory("agent"))
    monkeypatch.setattr(worker_app, "SummarizeConsumer", factory("summarize"))
    monkeypatch.setattr(worker_app, "SummarizeBackfillConsumer", factory("backfill"))
    monkeypatch.setattr(worker_app, "TuneChatConsumer", factory("tune"))

    worker = worker_app.Worker()
    await worker._start_consumers()

    assert [consumer.queue_name for consumer in created] == [
        "workflow",
        "packages",
        "agent",
        "summarize",
        "backfill",
        "tune",
    ]
    assert all(consumer.started == 1 for consumer in created)
    assert worker._consumers == created


@pytest.mark.asyncio
async def test_start_consumers_raises_when_consumer_start_fails(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_app, "WorkflowExecutionConsumer", lambda: FakeConsumer("workflow"))
    monkeypatch.setattr(worker_app, "PackageInstallConsumer", lambda: FakeConsumer("packages", fail_start=True))
    monkeypatch.setattr(worker_app, "AgentRunConsumer", lambda: FakeConsumer("agent"))
    monkeypatch.setattr(worker_app, "SummarizeConsumer", lambda: FakeConsumer("summarize"))
    monkeypatch.setattr(worker_app, "SummarizeBackfillConsumer", lambda: FakeConsumer("backfill"))
    monkeypatch.setattr(worker_app, "TuneChatConsumer", lambda: FakeConsumer("tune"))

    with pytest.raises(RuntimeError, match="start failed"):
        await worker_app.Worker()._start_consumers()


@pytest.mark.asyncio
async def test_stop_drains_consumers_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    monkeypatch.setenv("BIFROST_DRAIN_DEADLINE_SECONDS", "12.5")
    close_db = AsyncMock()
    rabbit_close = AsyncMock()
    monkeypatch.setattr(worker_app, "close_db", close_db)
    monkeypatch.setattr(worker_app.rabbitmq, "close", rabbit_close)
    consumers = [FakeConsumer("one"), FakeConsumer("two")]
    worker = worker_app.Worker()
    worker.running = True
    worker._consumers = consumers

    await worker.stop()
    await worker.stop()

    assert worker.running is False
    assert worker._stopping is True
    assert worker._shutdown_event.is_set()
    assert [consumer.drained for consumer in consumers] == [[12.5], [12.5]]
    rabbit_close.assert_awaited_once()
    close_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_uses_default_deadline_for_invalid_env(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    monkeypatch.setenv("BIFROST_DRAIN_DEADLINE_SECONDS", "0")
    monkeypatch.setattr(worker_app, "close_db", AsyncMock())
    monkeypatch.setattr(worker_app.rabbitmq, "close", AsyncMock())
    consumer = FakeConsumer("one")
    worker = worker_app.Worker()
    worker._consumers = [consumer]

    await worker.stop()

    assert consumer.drained == [300.0]


@pytest.mark.asyncio
async def test_stop_logs_drain_exceptions_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    close_db = AsyncMock()
    rabbit_close = AsyncMock()
    monkeypatch.setattr(worker_app, "close_db", close_db)
    monkeypatch.setattr(worker_app.rabbitmq, "close", rabbit_close)
    worker = worker_app.Worker()
    worker._consumers = [FakeConsumer("broken")]

    async def fail_drain(consumer, deadline: float) -> None:
        raise RuntimeError("drain failed")

    worker._drain_consumer = fail_drain  # type: ignore[method-assign]

    await worker.stop()

    rabbit_close.assert_awaited_once()
    close_db.assert_awaited_once()
    assert worker._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_handle_signal_schedules_single_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    worker = worker_app.Worker()
    stop_calls = 0

    async def stop() -> None:
        nonlocal stop_calls
        stop_calls += 1

    worker.stop = stop  # type: ignore[method-assign]

    worker.handle_signal(15, None)
    first_task = worker._shutdown_task
    worker.handle_signal(15, None)
    assert worker._shutdown_task is first_task
    await first_task
    assert stop_calls == 1


@pytest.mark.asyncio
async def test_main_registers_signal_handlers_and_starts_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = AsyncMock()

    class FakeWorker:
        def handle_signal(self, signum: int, frame) -> None:
            return None

        start = started

    class FakeLoop:
        def __init__(self) -> None:
            self.handlers: list[int] = []

        def add_signal_handler(self, sig, callback) -> None:
            self.handlers.append(sig)

    loop = FakeLoop()
    monkeypatch.setattr(worker_app, "Worker", FakeWorker)
    monkeypatch.setattr(worker_app.asyncio, "get_running_loop", lambda: loop)

    await worker_app.main()

    assert len(loop.handlers) == 2
    started.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_hard_exits_on_worker_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWorker:
        def handle_signal(self, signum: int, frame) -> None:
            return None

        async def start(self) -> None:
            raise RuntimeError("boom")

    class FakeLoop:
        def add_signal_handler(self, sig, callback) -> None:
            return None

    exit_mock = Mock(side_effect=SystemExit(1))
    monkeypatch.setattr(worker_app, "Worker", FakeWorker)
    monkeypatch.setattr(worker_app.asyncio, "get_running_loop", lambda: FakeLoop())
    monkeypatch.setattr(worker_app.os, "_exit", exit_mock)

    with pytest.raises(SystemExit):
        await worker_app.main()

    exit_mock.assert_called_once_with(1)
