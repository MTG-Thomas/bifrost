from __future__ import annotations

# ruff: noqa: E402

import asyncio
import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.modules.setdefault(
    "resource",
    types.SimpleNamespace(
        RUSAGE_SELF=0,
        getrusage=lambda _who: SimpleNamespace(
            ru_maxrss=0,
            ru_utime=0,
            ru_stime=0,
        ),
    ),
)

from src.services.execution import process_pool  # noqa: E402
from src.services.execution.process_pool import (
    ExecutionInfo,
    ProcessHandle,
    ProcessPoolAdmissionRejected,
    ProcessPoolManager,
    ProcessState,
)  # noqa: E402
from src.services.execution.simple_worker import FailedPackage, RequirementsInstallResult  # noqa: E402


def _handle(
    *,
    process_id: str = "process-1",
    alive: bool = True,
    state: ProcessState = ProcessState.BUSY,
    execution_id: str | None = "exec-1",
    result_reported: bool = False,
    killed_at: datetime | None = None,
) -> ProcessHandle:
    current_execution = None
    if execution_id:
        current_execution = ExecutionInfo(
            execution_id=execution_id,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=2),
            timeout_seconds=1,
        )
    return ProcessHandle(
        id=process_id,
        process=SimpleNamespace(is_alive=lambda: alive, exitcode=9),
        pid=123,
        state=state,
        work_queue=SimpleNamespace(put_nowait=lambda _item: None),
        result_queue=SimpleNamespace(get_nowait=lambda: None),
        started_at=datetime.now(timezone.utc) - timedelta(seconds=3),
        current_execution=current_execution,
        result_reported=result_reported,
        killed_at=killed_at,
    )


@pytest.mark.asyncio
async def test_notify_requirements_failures_skips_success(monkeypatch):
    service = SimpleNamespace(
        find_admin_notification_by_title=AsyncMock(),
        create_notification=AsyncMock(),
    )
    monkeypatch.setattr(process_pool, "get_notification_service", lambda: service)

    await process_pool._notify_requirements_failures(RequirementsInstallResult())

    service.find_admin_notification_by_title.assert_not_called()
    service.create_notification.assert_not_called()


@pytest.mark.asyncio
async def test_notify_requirements_failures_creates_deduped_admin_notification(monkeypatch):
    service = SimpleNamespace(
        find_admin_notification_by_title=AsyncMock(return_value=None),
        create_notification=AsyncMock(),
    )
    monkeypatch.setattr(process_pool, "get_notification_service", lambda: service)
    result = RequirementsInstallResult(
        attempted=["ok", "bad"],
        installed=["ok"],
        failed=[FailedPackage(package="bad", error="build failed")],
    )

    await process_pool._notify_requirements_failures(result)

    service.find_admin_notification_by_title.assert_awaited_once()
    service.create_notification.assert_awaited_once()
    kwargs = service.create_notification.await_args.kwargs
    assert kwargs["user_id"] == "system"
    assert kwargs["for_admins"] is True
    assert kwargs["request"].title == "Workflow package install failed"
    assert kwargs["request"].metadata["failed"] == [
        {"package": "bad", "error": "build failed"}
    ]


@pytest.mark.asyncio
async def test_start_template_reuses_manager_installed_requirements(monkeypatch):
    created: list[bool] = []

    class FakeTemplate:
        pid = 123

        def __init__(self, *, install_requirements_on_startup: bool) -> None:
            created.append(install_requirements_on_startup)

        def start(self) -> None:
            return None

    monkeypatch.setattr(process_pool, "TemplateProcess", FakeTemplate)
    pool = ProcessPoolManager()

    await pool._start_template()

    assert created == [False]
    assert isinstance(pool._template, FakeTemplate)


def test_get_installed_packages_returns_json_on_success(monkeypatch):
    monkeypatch.setattr(
        process_pool.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            "pip list",
            0,
            stdout=json.dumps([{"name": "pytest", "version": "9"}]),
            stderr="",
        ),
    )

    assert process_pool._get_installed_packages() == [
        {"name": "pytest", "version": "9"}
    ]


def test_get_installed_packages_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr(
        process_pool.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess("pip list", 1, stdout="[]", stderr="no"),
    )

    assert process_pool._get_installed_packages() == []


@pytest.mark.asyncio
async def test_report_timeout_cancellation_crash_and_orphan_shape_results():
    observed = []

    async def on_result(payload):
        observed.append(payload)

    pool = ProcessPoolManager(on_result=on_result)

    timeout_handle = _handle(execution_id="exec-timeout")
    await pool._report_timeout(timeout_handle)
    cancel_handle = _handle(execution_id="exec-cancel")
    await pool._report_cancellation(cancel_handle)
    crash_handle = _handle(execution_id="exec-crash")
    await pool._report_crash(crash_handle)
    orphan_handle = _handle(execution_id="exec-orphan")
    await pool._report_orphan(orphan_handle)

    assert [item["error_type"] for item in observed] == [
        "TimeoutError",
        "CancelledError",
        "ProcessCrashError",
        "OrphanedExecution",
    ]
    assert [item["execution_id"] for item in observed] == [
        "exec-timeout",
        "exec-cancel",
        "exec-crash",
        "exec-orphan",
    ]
    assert all(item["success"] is False for item in observed)
    assert timeout_handle.result_reported is True
    assert orphan_handle.result_reported is True


@pytest.mark.asyncio
async def test_handle_result_frees_slot_and_forwards_callback():
    observed = []

    async def on_result(payload):
        observed.append(payload)

    pool = ProcessPoolManager(on_result=on_result)
    notified = []
    pool._notify_slot_free = AsyncMock(side_effect=lambda: notified.append(True))
    handle = _handle(execution_id="exec-1")
    pool.processes[handle.id] = handle

    await pool._handle_result(handle, {"execution_id": "exec-1", "success": True})

    assert handle.result_reported is True
    assert handle.current_execution is None
    assert handle.executions_completed == 1
    assert pool.processes == {}
    assert observed == [{"execution_id": "exec-1", "success": True}]
    assert notified == [True]


@pytest.mark.asyncio
async def test_check_process_health_reports_crash_and_orphan(monkeypatch):
    observed = []

    async def on_result(payload):
        observed.append(payload)

    pool = ProcessPoolManager(on_result=on_result, graceful_shutdown_seconds=0)
    pool._notify_slot_free = AsyncMock()
    crashed = _handle(process_id="crashed", alive=False, execution_id="exec-crash")
    orphaned = _handle(
        process_id="orphaned",
        alive=True,
        state=ProcessState.KILLED,
        execution_id="exec-orphan",
        killed_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    too_recent = _handle(
        process_id="recent",
        alive=True,
        state=ProcessState.KILLED,
        execution_id="exec-recent",
        killed_at=datetime.now(timezone.utc),
    )
    pool.processes = {
        crashed.id: crashed,
        orphaned.id: orphaned,
        too_recent.id: too_recent,
    }

    await pool._check_process_health()

    assert [item["error_type"] for item in observed] == [
        "ProcessCrashError",
        "OrphanedExecution",
    ]
    assert set(pool.processes) == {"recent"}
    pool._notify_slot_free.assert_awaited_once()


def test_build_heartbeat_includes_admission_and_capacity(monkeypatch):
    pool = ProcessPoolManager(max_workers=3)
    pool.worker_id = "worker-1"
    pool._started_at = datetime(2026, 7, 5, tzinfo=timezone.utc)
    pool._requirements_installed = 1
    pool._requirements_total = 2
    pool._admission_attempts = 4
    pool._admission_successes = 3
    pool._admission_rejections["slot_timeout"] = 1
    handle = _handle(process_id="process-1", execution_id="exec-1")
    pool.processes[handle.id] = handle

    monkeypatch.setattr(process_pool, "_get_private_dirty_kb", lambda _pid: 2048)
    monkeypatch.setattr(process_pool, "get_cgroup_memory", lambda: (10, 100))

    heartbeat = pool._build_heartbeat()

    assert heartbeat["type"] == "worker_heartbeat"
    assert heartbeat["worker_id"] == "worker-1"
    assert heartbeat["configured_capacity"] == 3
    assert heartbeat["busy_count"] == 1
    assert heartbeat["available_slots"] == 2
    assert heartbeat["requirements_installed"] == 1
    assert heartbeat["requirements_total"] == 2
    assert heartbeat["memory_current_bytes"] == 10
    assert heartbeat["memory_max_bytes"] == 100
    assert heartbeat["admission"]["attempts"] == 4
    assert heartbeat["admission"]["successes"] == 3
    assert heartbeat["admission"]["rejections"]["slot_timeout"] == 1
    assert heartbeat["processes"][0]["memory_mb"] == 2
    assert heartbeat["processes"][0]["execution"]["execution_id"] == "exec-1"


@pytest.mark.asyncio
async def test_route_execution_records_success_and_sends_execution_id(monkeypatch):
    pool = ProcessPoolManager(max_workers=2, execution_timeout_seconds=33)
    pool._started = True
    pool._template = SimpleNamespace(is_alive=lambda: True)
    handle = _handle(execution_id=None)
    sent: list[tuple[str, dict[str, object]]] = []
    handle.work_queue = SimpleNamespace(put_nowait=sent.append)

    monkeypatch.setattr(
        process_pool,
        "get_settings",
        lambda: SimpleNamespace(memory_pressure_threshold=0.9),
    )
    monkeypatch.setattr(process_pool, "has_sufficient_memory_cgroup", lambda threshold: True)
    pool._write_context_to_redis = AsyncMock()
    pool._fork_process = lambda: handle
    pool._register_result_reader = Mock()

    await pool.route_execution("exec-route", {"timeout_seconds": 12})

    pool._write_context_to_redis.assert_awaited_once_with(
        "exec-route",
        {"timeout_seconds": 12},
    )
    assert sent == [("exec-route", {"timeout_seconds": 12})]
    assert handle.current_execution is not None
    assert handle.current_execution.execution_id == "exec-route"
    assert handle.current_execution.timeout_seconds == 12
    assert pool._admission_attempts == 1
    assert pool._admission_successes == 1
    assert pool._admission_rejections == {"slot_timeout": 0, "memory_pressure": 0}


class _ObservedAsyncLock:
    """Async lock that exposes when a second task is queued behind its owner."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.waiter_blocked = asyncio.Event()

    async def __aenter__(self):
        if self._lock.locked():
            self.waiter_blocked.set()
        await self._lock.acquire()
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()


@pytest.mark.asyncio
async def test_route_fork_is_atomic_with_generation_recycle(monkeypatch):
    """A recycle arriving after route readiness cannot kill its template."""
    pool = ProcessPoolManager(max_workers=2)
    pool._started = True
    observed_lock = _ObservedAsyncLock()
    pool._restart_lock = observed_lock  # type: ignore[assignment]

    template = SimpleNamespace(alive=True)
    pool._template = SimpleNamespace(is_alive=lambda: template.alive)
    context_ready = asyncio.Event()
    release_context = asyncio.Event()
    recycle_holds_lock = asyncio.Event()
    release_recycle = asyncio.Event()
    forked = asyncio.Event()
    events: list[str] = []

    async def write_context(_execution_id: str, _context: dict) -> None:
        assert not observed_lock.locked()
        context_ready.set()
        await release_context.wait()

    async def recycle_for_generation_change() -> None:
        async with observed_lock:
            template.alive = False
            events.append("template_down")
            recycle_holds_lock.set()
            await release_recycle.wait()
            template.alive = True
            events.append("template_ready")

    handle = _handle(execution_id=None)

    def fork_process() -> ProcessHandle:
        forked.set()
        assert observed_lock.locked()
        assert template.alive
        events.append("fork")
        pool.processes[handle.id] = handle
        return handle

    pool._write_context_to_redis = write_context  # type: ignore[method-assign]
    pool._fork_process = fork_process  # type: ignore[method-assign]
    pool._register_result_reader = Mock()
    monkeypatch.setattr(
        process_pool,
        "get_settings",
        lambda: SimpleNamespace(memory_pressure_threshold=0.9),
    )
    monkeypatch.setattr(
        process_pool,
        "has_sufficient_memory_cgroup",
        lambda threshold: True,
    )

    route_task = asyncio.create_task(pool.route_execution("exec-race", {}))
    await context_ready.wait()
    recycle_task = asyncio.create_task(recycle_for_generation_change())
    await recycle_holds_lock.wait()

    release_context.set()
    lock_waiter = asyncio.create_task(observed_lock.waiter_blocked.wait())
    fork_waiter = asyncio.create_task(forked.wait())
    completed, pending = await asyncio.wait(
        {lock_waiter, fork_waiter},
        return_when=asyncio.FIRST_COMPLETED,
    )
    assert lock_waiter in completed
    assert not forked.is_set()
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    release_recycle.set()
    await recycle_task
    await route_task

    assert events == ["template_down", "template_ready", "fork"]
    assert handle.current_execution is not None
    assert handle.current_execution.execution_id == "exec-race"


@pytest.mark.asyncio
async def test_normal_drain_waits_for_newly_forked_execution(monkeypatch):
    """The protected fork still participates in the normal drain path."""
    pool = ProcessPoolManager(max_workers=2)
    pool._started = True
    pool._template = SimpleNamespace(is_alive=lambda: True)
    handle = _handle(execution_id=None)
    pool._write_context_to_redis = AsyncMock()

    def fork_process() -> ProcessHandle:
        pool.processes[handle.id] = handle
        return handle

    pool._fork_process = fork_process  # type: ignore[method-assign]
    pool._register_result_reader = Mock()
    monkeypatch.setattr(
        process_pool,
        "get_settings",
        lambda: SimpleNamespace(memory_pressure_threshold=0.9),
    )
    monkeypatch.setattr(
        process_pool,
        "has_sufficient_memory_cgroup",
        lambda threshold: True,
    )

    await pool.route_execution("exec-drain", {})

    drain_waiting = asyncio.Event()
    release_drain = asyncio.Event()

    async def controlled_drain_wait(_seconds: float) -> None:
        drain_waiting.set()
        await release_drain.wait()

    monkeypatch.setattr(process_pool.asyncio, "sleep", controlled_drain_wait)
    pool.restart_template = AsyncMock()
    pool._terminate_process = AsyncMock()

    drain_task = asyncio.create_task(pool.drain_and_restart_template())
    await drain_waiting.wait()
    pool.restart_template.assert_not_awaited()

    pool.processes.pop(handle.id)
    release_drain.set()
    await drain_task

    pool._terminate_process.assert_not_awaited()
    pool.restart_template.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_route_heals_dead_template_left_by_failed_restart(monkeypatch):
    pool = ProcessPoolManager(max_workers=2)
    pool._started = True
    dead_template = SimpleNamespace(is_alive=lambda: False)
    replacement_template = SimpleNamespace(is_alive=lambda: True)
    pool._template = dead_template
    pool._write_context_to_redis = AsyncMock()
    handle = _handle(execution_id=None)

    async def start_replacement() -> None:
        assert pool._restart_lock.locked()
        pool._template = replacement_template

    def fork_process() -> ProcessHandle:
        assert pool._restart_lock.locked()
        assert pool._template is replacement_template
        return handle

    pool._start_template = AsyncMock(side_effect=start_replacement)
    pool._fork_process = fork_process  # type: ignore[method-assign]
    pool._register_result_reader = Mock()
    monkeypatch.setattr(
        process_pool,
        "get_settings",
        lambda: SimpleNamespace(memory_pressure_threshold=0.9),
    )
    monkeypatch.setattr(
        process_pool,
        "has_sufficient_memory_cgroup",
        lambda threshold: True,
    )

    await pool.route_execution("exec-healed", {})

    pool._start_template.assert_awaited_once_with()
    assert handle.current_execution is not None
    assert handle.current_execution.execution_id == "exec-healed"


@pytest.mark.asyncio
async def test_route_propagates_replacement_template_start_failure(monkeypatch):
    pool = ProcessPoolManager(max_workers=2)
    pool._started = True
    pool._template = SimpleNamespace(is_alive=lambda: False)
    pool._write_context_to_redis = AsyncMock()
    pool._start_template = AsyncMock(
        side_effect=RuntimeError("replacement template preload failed")
    )
    pool._fork_process = Mock(side_effect=AssertionError("must not fork"))
    monkeypatch.setattr(
        process_pool,
        "get_settings",
        lambda: SimpleNamespace(memory_pressure_threshold=0.9),
    )
    monkeypatch.setattr(
        process_pool,
        "has_sufficient_memory_cgroup",
        lambda threshold: True,
    )

    with pytest.raises(RuntimeError, match="replacement template preload failed"):
        await pool.route_execution("exec-start-failed", {})

    pool._start_template.assert_awaited_once_with()
    pool._fork_process.assert_not_called()


@pytest.mark.asyncio
async def test_route_does_not_restart_pool_stopped_while_waiting(monkeypatch):
    pool = ProcessPoolManager(max_workers=2)
    pool._started = True
    pool._template = SimpleNamespace(is_alive=lambda: False)
    pool._write_context_to_redis = AsyncMock()
    observed_lock = _ObservedAsyncLock()
    pool._restart_lock = observed_lock  # type: ignore[assignment]
    pool._start_template = AsyncMock()
    pool._fork_process = Mock(side_effect=AssertionError("must not fork"))
    monkeypatch.setattr(
        process_pool,
        "get_settings",
        lambda: SimpleNamespace(memory_pressure_threshold=0.9),
    )
    monkeypatch.setattr(
        process_pool,
        "has_sufficient_memory_cgroup",
        lambda threshold: True,
    )

    async with observed_lock:
        route_task = asyncio.create_task(pool.route_execution("exec-stopped", {}))
        await observed_lock.waiter_blocked.wait()
        pool._shutdown = True
        pool._started = False

    with pytest.raises(RuntimeError, match="process pool is not running"):
        await route_task

    pool._start_template.assert_not_awaited()
    pool._fork_process.assert_not_called()


@pytest.mark.asyncio
async def test_process_health_skips_template_pipe_during_restart_lock():
    pool = ProcessPoolManager()
    pool._collect_child_exit_statuses = Mock()

    async with pool._restart_lock:
        await pool._check_process_health()
    pool._collect_child_exit_statuses.assert_not_called()

    await pool._check_process_health()
    pool._collect_child_exit_statuses.assert_called_once_with()


@pytest.mark.asyncio
async def test_route_execution_rejects_memory_pressure_and_deletes_context(monkeypatch):
    deleted: list[str] = []
    redis = SimpleNamespace(delete=AsyncMock(side_effect=lambda key: deleted.append(key)))
    pool = ProcessPoolManager(max_workers=1)
    pool._write_context_to_redis = AsyncMock()
    pool._get_redis = AsyncMock(return_value=redis)
    pool._fork_process = lambda: pytest.fail("route should reject before forking")

    monkeypatch.setattr(
        process_pool,
        "get_settings",
        lambda: SimpleNamespace(memory_pressure_threshold=0.75),
    )
    monkeypatch.setattr(process_pool, "has_sufficient_memory_cgroup", lambda threshold: False)

    with pytest.raises(MemoryError, match="memory pressure"):
        await pool.route_execution("exec-memory", {})

    pool._write_context_to_redis.assert_awaited_once_with("exec-memory", {})
    redis.delete.assert_awaited_once_with("bifrost:exec:exec-memory:context")
    assert deleted == ["bifrost:exec:exec-memory:context"]
    assert pool._admission_attempts == 1
    assert pool._admission_successes == 0
    assert pool._admission_rejections["memory_pressure"] == 1


@pytest.mark.asyncio
async def test_route_execution_rejects_when_slot_wait_times_out(monkeypatch):
    pool = ProcessPoolManager(max_workers=1)
    pool.processes["busy"] = _handle(process_id="busy")
    pool._write_context_to_redis = AsyncMock()
    pool._wait_for_slot = AsyncMock(return_value=False)
    pool._fork_process = lambda: pytest.fail("route should reject before forking")

    monkeypatch.setattr(
        process_pool,
        "get_settings",
        lambda: SimpleNamespace(memory_pressure_threshold=0.9),
    )
    monkeypatch.setattr(process_pool, "has_sufficient_memory_cgroup", lambda threshold: True)

    with pytest.raises(ProcessPoolAdmissionRejected, match="No worker slot"):
        await pool.route_execution("exec-slot", {})

    pool._wait_for_slot.assert_awaited_once()
    assert pool._admission_attempts == 1
    assert pool._admission_successes == 0
    assert pool._admission_rejections["slot_timeout"] == 1


@pytest.mark.asyncio
async def test_handle_command_dispatches_recycle_actions_and_ignores_unknown():
    pool = ProcessPoolManager()
    process_commands: list[dict[str, object]] = []
    all_commands: list[dict[str, object]] = []
    generation_commands: list[dict[str, object]] = []
    pool._handle_recycle_process_command = AsyncMock(
        side_effect=lambda command: process_commands.append(command)
    )
    pool._handle_recycle_all_command = AsyncMock(
        side_effect=lambda command: all_commands.append(command)
    )
    pool._handle_workspace_generation_changed_command = AsyncMock(
        side_effect=lambda command: generation_commands.append(command)
    )

    recycle_process = {"action": "recycle_process", "pid": 123}
    recycle_all = {"action": "recycle_all", "reason": "operator"}
    generation_changed = {
        "action": "workspace_generation_changed",
        "generation": "generation-2",
    }
    await pool._handle_command(recycle_process)
    await pool._handle_command(recycle_all)
    await pool._handle_command(generation_changed)
    await pool._handle_command({"action": "unknown"})

    assert process_commands == [recycle_process]
    assert all_commands == [recycle_all]
    assert generation_commands == [generation_changed]


@pytest.mark.asyncio
async def test_workspace_generation_change_drains_and_restarts_template():
    pool = ProcessPoolManager()
    pool.drain_and_restart_template = AsyncMock()

    await pool._handle_workspace_generation_changed_command(
        {
            "action": "workspace_generation_changed",
            "generation": "generation-2",
            "reason": "workspace_changeset_activated",
        }
    )

    pool.drain_and_restart_template.assert_awaited_once_with()
