from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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

    def get_job(self, _task_id: str):
        return None


@pytest.fixture
def settings() -> SimpleNamespace:
    return SimpleNamespace(
        environment="test",
        redis_url="redis://example/0",
        deferred_execution_promoter_interval_seconds=7,
    )


@pytest.fixture
def scheduler(monkeypatch: pytest.MonkeyPatch, settings: SimpleNamespace):
    monkeypatch.setattr(scheduler_main, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler_main, "configure_sentry", Mock())
    return scheduler_main.Scheduler()


@pytest.mark.asyncio
async def test_start_initializes_control_loops_and_waits_for_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    init_db = AsyncMock()
    monkeypatch.setattr(scheduler_main, "init_db", init_db)
    monkeypatch.setattr(scheduler_main, "close_db", AsyncMock())
    monkeypatch.setattr(scheduler_main, "remove_scheduler_replica", AsyncMock())
    leadership_gate = scheduler_main.asyncio.Event()

    async def leadership_loop() -> None:
        await leadership_gate.wait()

    async def diagnostics_loop() -> None:
        scheduler._shutdown_event.set()

    scheduler._job_slots = 0
    scheduler._leadership_loop = leadership_loop  # type: ignore[method-assign]
    scheduler._diagnostics_heartbeat_loop = diagnostics_loop  # type: ignore[method-assign]
    monkeypatch.setattr(scheduler_main, "heartbeat_loop", AsyncMock())

    await scheduler.start()

    init_db.assert_awaited_once()
    assert scheduler.running is True
    await scheduler.stop()


@pytest.mark.asyncio
async def test_start_scheduler_adds_core_jobs_and_starts_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    FakeApscheduler.instances = []
    monkeypatch.setattr(scheduler_main, "AsyncIOScheduler", FakeApscheduler)
    publish_states = AsyncMock()
    monkeypatch.setattr(scheduler_main, "publish_task_states", publish_states)

    await scheduler._start_scheduler()

    fake = FakeApscheduler.instances[0]
    assert fake.started is True
    assert scheduler._scheduler is fake
    job_ids = {job["id"] for job in fake.jobs}
    assert "schedule_processor" in job_ids
    assert "deferred_execution_promoter" in job_ids
    assert "execution_cleanup" in job_ids
    from src.scheduler.registry import SCHEDULED_TASKS_BY_ID

    assert job_ids == set(SCHEDULED_TASKS_BY_ID)
    promoter = next(
        job for job in fake.jobs if job["id"] == "deferred_execution_promoter"
    )
    assert promoter["trigger"].interval.total_seconds() == 7


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
async def test_handle_git_operation_reports_missing_or_incomplete_config(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    class FakeDbContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(scheduler_main, "get_db_context", lambda: FakeDbContext())
    publish = AsyncMock()
    monkeypatch.setattr(scheduler_main, "publish_git_op_completed", publish)

    with patch("src.services.github_config.get_github_config", AsyncMock(return_value=None)):
        await scheduler._handle_git_operation(
            {"type": "git_status", "jobId": "job-missing", "orgId": "org-1"}
        )

    publish.assert_awaited_once_with(
        "job-missing",
        status="failed",
        result_type="status",
        error="GitHub not configured",
    )

    publish.reset_mock()
    config = SimpleNamespace(token="", repo_url="https://github.com/org/repo", branch="main")
    with patch("src.services.github_config.get_github_config", AsyncMock(return_value=config)):
        await scheduler._handle_git_operation(
            {"type": "git_status", "jobId": "job-incomplete", "orgId": "org-1"}
        )

    publish.assert_awaited_once_with(
        "job-incomplete",
        status="failed",
        result_type="status",
        error="GitHub token or repository not configured",
    )


@pytest.mark.asyncio
async def test_handle_git_operation_status_unknown_and_exception_paths(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    class FakeDbContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeStatus:
        def model_dump(self):
            return {"clean": True}

    class FakeSyncService:
        def __init__(self, **_kwargs) -> None:
            return None

        async def desktop_status(self):
            return FakeStatus()

    config = SimpleNamespace(
        token="token",
        repo_url="https://github.com/org/repo.git",
        branch="main",
    )
    publish = AsyncMock()
    monkeypatch.setattr(scheduler_main, "get_db_context", lambda: FakeDbContext())
    monkeypatch.setattr(scheduler_main, "publish_git_op_completed", publish)
    monkeypatch.setattr("src.services.github_sync.GitHubSyncService", FakeSyncService)

    with patch("src.services.github_config.get_github_config", AsyncMock(return_value=config)):
        await scheduler._handle_git_operation({"type": "git_status", "jobId": "job-status"})
        await scheduler._handle_git_operation({"type": "git_unknown", "jobId": "job-unknown"})

    assert publish.await_args_list[0].kwargs == {
        "status": "success",
        "result_type": "status",
        "data": {"clean": True},
    }
    assert publish.await_args_list[0].args == ("job-status",)
    assert publish.await_args_list[1].kwargs == {
        "status": "failed",
        "result_type": "unknown",
        "error": "Unknown operation type: git_unknown",
    }

    publish.reset_mock()
    with patch(
        "src.services.github_config.get_github_config",
        AsyncMock(side_effect=RuntimeError("config failed")),
    ):
        await scheduler._handle_git_operation({"type": "git_status", "jobId": "job-error"})

    publish.assert_awaited_once_with(
        "job-error",
        status="failed",
        result_type="status",
        error="config failed",
    )


@pytest.mark.asyncio
async def test_stop_stops_scheduler_and_db(
    monkeypatch: pytest.MonkeyPatch,
    scheduler,
) -> None:
    close_db = AsyncMock()
    monkeypatch.setattr(scheduler_main, "close_db", close_db)
    monkeypatch.setattr(scheduler_main, "remove_scheduler_replica", AsyncMock())
    scheduler.running = True

    await scheduler.stop()

    assert scheduler.running is False
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
