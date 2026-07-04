from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.scheduler import main as scheduler_main


class FakeApscheduler:
    instances: list["FakeApscheduler"] = []

    def __init__(self) -> None:
        self.jobs: list[dict] = []
        self.started = False
        self.shutdown_calls: list[bool] = []
        self.__class__.instances.append(self)

    def add_job(self, func, trigger, **kwargs) -> None:
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def start(self) -> None:
        self.started = True

    def shutdown(self, *, wait: bool) -> None:
        self.shutdown_calls.append(wait)


class FakeListener:
    instances: list["FakeListener"] = []

    def __init__(self, *, redis_url, channels, on_message) -> None:
        self.redis_url = redis_url
        self.channels = channels
        self.on_message = on_message
        self.started = 0
        self.stopped = 0
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


@pytest.fixture
def settings() -> SimpleNamespace:
    return SimpleNamespace(environment="test", redis_url="redis://example/0")


@pytest.fixture
def scheduler(monkeypatch: pytest.MonkeyPatch, settings: SimpleNamespace):
    monkeypatch.setattr(scheduler_main, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler_main, "configure_sentry", Mock())
    return scheduler_main.Scheduler()


@pytest.mark.asyncio
async def test_start_initializes_services_and_waits_for_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    init_db = AsyncMock()
    monkeypatch.setattr(scheduler_main, "init_db", init_db)

    async def start_scheduler() -> None:
        return None

    async def start_listener() -> None:
        scheduler._shutdown_event.set()

    scheduler._start_scheduler = start_scheduler  # type: ignore[method-assign]
    scheduler._start_pubsub_listener = start_listener  # type: ignore[method-assign]

    await scheduler.start()

    init_db.assert_awaited_once()
    assert scheduler.running is True


@pytest.mark.asyncio
async def test_start_scheduler_adds_core_jobs_and_starts_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    FakeApscheduler.instances = []
    monkeypatch.setattr(scheduler_main, "AsyncIOScheduler", FakeApscheduler)

    await scheduler._start_scheduler()

    fake = FakeApscheduler.instances[0]
    assert fake.started is True
    assert scheduler._scheduler is fake
    job_ids = {job["id"] for job in fake.jobs}
    assert "schedule_processor" in job_ids
    assert "deferred_execution_promoter" in job_ids
    assert "execution_cleanup" in job_ids


@pytest.mark.asyncio
async def test_start_pubsub_listener_uses_expected_channels(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    FakeListener.instances = []
    monkeypatch.setattr(scheduler_main, "ResilientPubSubListener", FakeListener)

    await scheduler._start_pubsub_listener()

    listener = FakeListener.instances[0]
    assert listener.redis_url == "redis://example/0"
    assert listener.channels == [
        "bifrost:scheduler:git-op",
        "bifrost:scheduler:reimport",
        "bifrost:scheduler:embedding-reindex",
    ]
    assert listener.on_message == scheduler._handle_pubsub_message
    assert listener.started == 1
    assert scheduler._pubsub_listener is listener


@pytest.mark.asyncio
async def test_handle_pubsub_message_dispatches_known_channels(scheduler) -> None:
    scheduler._handle_git_operation = AsyncMock()  # type: ignore[method-assign]
    scheduler._handle_reimport = AsyncMock()  # type: ignore[method-assign]
    scheduler._handle_embedding_reindex = AsyncMock()  # type: ignore[method-assign]

    await scheduler._handle_pubsub_message("bifrost:scheduler:git-op", {"jobId": "git"})
    await scheduler._handle_pubsub_message("bifrost:scheduler:reimport", {"job_id": "reimport"})
    await scheduler._handle_pubsub_message(
        "bifrost:scheduler:embedding-reindex",
        {"notification_id": "note"},
    )
    await scheduler._handle_pubsub_message("unknown", {})

    scheduler._handle_git_operation.assert_awaited_once_with({"jobId": "git"})
    scheduler._handle_reimport.assert_awaited_once_with({"job_id": "reimport"})
    scheduler._handle_embedding_reindex.assert_awaited_once_with({"notification_id": "note"})


def test_build_clone_url_from_config_handles_github_and_owner_repo(scheduler) -> None:
    assert scheduler._build_clone_url_from_config(
        SimpleNamespace(
            repo_url="https://github.com/MTG-Thomas/bifrost.git",
            token="token",
        )
    ) == "https://x-access-token:token@github.com/MTG-Thomas/bifrost.git"
    assert scheduler._build_clone_url_from_config(
        SimpleNamespace(repo_url="MTG-Thomas/bifrost", token="token")
    ) == "https://x-access-token:token@github.com/MTG-Thomas/bifrost.git"


@pytest.mark.asyncio
async def test_handle_embedding_reindex_requires_notification_id(scheduler) -> None:
    await scheduler._handle_embedding_reindex({})


@pytest.mark.asyncio
async def test_stop_stops_listener_scheduler_and_db(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    close_db = AsyncMock()
    monkeypatch.setattr(scheduler_main, "close_db", close_db)
    listener = FakeListener(redis_url="redis://example/0", channels=[], on_message=AsyncMock())
    apscheduler = FakeApscheduler()
    scheduler.running = True
    scheduler._pubsub_listener = listener
    scheduler._scheduler = apscheduler

    await scheduler.stop()

    assert scheduler.running is False
    assert listener.stopped == 1
    assert apscheduler.shutdown_calls == [False]
    close_db.assert_awaited_once()
    assert scheduler._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_handle_signal_schedules_stop(monkeypatch: pytest.MonkeyPatch, scheduler) -> None:
    tasks = []
    stop = AsyncMock()
    scheduler.stop = stop  # type: ignore[method-assign]

    def create_task(coro):
        tasks.append(coro)
        return coro

    monkeypatch.setattr(scheduler_main.asyncio, "create_task", create_task)

    scheduler.handle_signal(15, None)

    assert len(tasks) == 1
    await tasks[0]
    stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_registers_signal_handlers_and_starts_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = AsyncMock()

    class FakeScheduler:
        def handle_signal(self, signum: int, frame) -> None:
            return None

        start = started

    class FakeLoop:
        def __init__(self) -> None:
            self.handlers: list[object] = []

        def add_signal_handler(self, sig, callback, *args) -> None:
            self.handlers.append((sig, callback, args))

    loop = FakeLoop()
    monkeypatch.setattr(scheduler_main, "Scheduler", FakeScheduler)
    monkeypatch.setattr(scheduler_main.asyncio, "get_running_loop", lambda: loop)

    await scheduler_main.main()

    assert len(loop.handlers) == 2
    started.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_exits_on_scheduler_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeScheduler:
        def handle_signal(self, signum: int, frame) -> None:
            return None

        async def start(self) -> None:
            raise RuntimeError("boom")

    class FakeLoop:
        def add_signal_handler(self, sig, callback, *args) -> None:
            return None

    exit_mock = Mock(side_effect=SystemExit(1))
    monkeypatch.setattr(scheduler_main, "Scheduler", FakeScheduler)
    monkeypatch.setattr(scheduler_main.asyncio, "get_running_loop", lambda: FakeLoop())
    monkeypatch.setattr(scheduler_main.sys, "exit", exit_mock)

    with pytest.raises(SystemExit):
        await scheduler_main.main()

    exit_mock.assert_called_once_with(1)
