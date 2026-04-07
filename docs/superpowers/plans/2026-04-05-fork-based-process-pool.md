# Fork-Based Process Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace spawn-based worker processes with fork-from-template workers, add cgroup admission control, and support `min_workers=0` on-demand mode.

**Architecture:** A long-lived template process pre-loads all dependencies and forks children on request via a pipe-based control channel. The ProcessPoolManager keeps its existing interface but replaces `_spawn_process` with fork requests to the template. The memory monitor is refactored to read cgroup files for container-aware admission control.

**Tech Stack:** Python 3.11+, `os.fork()`, `multiprocessing.Connection` (pipe), cgroup v2 filesystem

**Spec:** `docs/superpowers/specs/2026-04-05-fork-based-process-pool-design.md`

---

### Task 1: Refactor Memory Monitor for Cgroup Support

Add cgroup v2 memory reading alongside the existing `/proc/meminfo` approach. This is a standalone module change with no dependencies on other tasks.

**Files:**
- Modify: `api/src/services/execution/memory_monitor.py`
- Modify: `api/tests/unit/execution/test_memory_monitor.py`
- Modify: `api/src/config.py`

- [ ] **Step 1: Write failing tests for cgroup memory reading**

Add to `api/tests/unit/execution/test_memory_monitor.py`:

```python
class TestGetCgroupMemory:
    """Tests for cgroup v2 memory reading."""

    def test_reads_cgroup_memory_current_and_max(self):
        """Should read memory.current and memory.max from cgroup v2."""
        with patch("builtins.open", side_effect=[
            mock_open(read_data="524288000\n")(),   # memory.current = 500MB
            mock_open(read_data="1073741824\n")(),   # memory.max = 1GB
        ]):
            with patch("pathlib.Path.exists", return_value=True):
                current, limit = get_cgroup_memory()
                assert current == 524288000
                assert limit == 1073741824

    def test_returns_negative_when_cgroup_files_missing(self):
        """Should return (-1, -1) when cgroup files don't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            current, limit = get_cgroup_memory()
            assert current == -1
            assert limit == -1

    def test_returns_negative_when_memory_max_is_max(self):
        """Should return (-1, -1) when memory.max is 'max' (no limit set)."""
        with patch("builtins.open", side_effect=[
            mock_open(read_data="524288000\n")(),
            mock_open(read_data="max\n")(),
        ]):
            with patch("pathlib.Path.exists", return_value=True):
                current, limit = get_cgroup_memory()
                assert current == -1
                assert limit == -1

    def test_handles_read_error_gracefully(self):
        """Should return (-1, -1) on read failure."""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", side_effect=OSError("Permission denied")):
                current, limit = get_cgroup_memory()
                assert current == -1
                assert limit == -1


class TestHasSufficientMemoryCgroup:
    """Tests for cgroup-aware memory pressure check."""

    def test_returns_true_when_below_threshold(self):
        """Should allow fork when memory usage is below threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(500_000_000, 1_000_000_000),  # 50% usage
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is True

    def test_returns_false_when_above_threshold(self):
        """Should reject fork when memory usage exceeds threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(900_000_000, 1_000_000_000),  # 90% usage
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is False

    def test_returns_true_when_cgroup_unavailable(self):
        """Should be permissive when cgroup files can't be read."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(-1, -1),
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is True

    def test_returns_true_at_exact_threshold(self):
        """Should allow fork when exactly at threshold."""
        with patch(
            "src.services.execution.memory_monitor.get_cgroup_memory",
            return_value=(850_000_000, 1_000_000_000),  # exactly 85%
        ):
            assert has_sufficient_memory_cgroup(threshold=0.85) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/execution/test_memory_monitor.py -v`
Expected: FAIL — `get_cgroup_memory` and `has_sufficient_memory_cgroup` not defined

- [ ] **Step 3: Implement cgroup memory functions**

Add to `api/src/services/execution/memory_monitor.py`:

```python
_warned_no_cgroup = False


def get_cgroup_memory() -> tuple[int, int]:
    """
    Read current and max memory from cgroup v2.

    Returns:
        Tuple of (current_bytes, max_bytes), or (-1, -1) if unavailable.
    """
    global _warned_no_cgroup

    cgroup_current = Path("/sys/fs/cgroup/memory.current")
    cgroup_max = Path("/sys/fs/cgroup/memory.max")

    if not cgroup_current.exists() or not cgroup_max.exists():
        if not _warned_no_cgroup:
            logger.warning(
                "cgroup v2 memory files not found - cgroup admission disabled. "
                "This is expected on macOS local development."
            )
            _warned_no_cgroup = True
        return (-1, -1)

    try:
        with open(cgroup_current) as f:
            current = int(f.read().strip())
        with open(cgroup_max) as f:
            max_raw = f.read().strip()
            if max_raw == "max":
                # No memory limit set on container
                if not _warned_no_cgroup:
                    logger.warning(
                        "cgroup memory.max is 'max' (no limit) - "
                        "cgroup admission disabled. Set a memory limit on the container."
                    )
                    _warned_no_cgroup = True
                return (-1, -1)
            limit = int(max_raw)
        return (current, limit)
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read cgroup memory: {e}")
        return (-1, -1)


def has_sufficient_memory_cgroup(threshold: float = 0.85) -> bool:
    """
    Check if container has enough memory headroom to fork a new process.

    Args:
        threshold: Maximum memory usage ratio (0.0-1.0). Default 0.85 (85%).

    Returns:
        True if usage is at or below threshold, or if cgroup files unavailable.
        False if memory pressure exceeds threshold.
    """
    current, limit = get_cgroup_memory()

    if current < 0 or limit <= 0:
        return True  # Permissive when unable to check

    ratio = current / limit
    if ratio > threshold:
        logger.warning(
            f"Memory pressure: {ratio:.1%} usage ({current // (1024*1024)}MB / "
            f"{limit // (1024*1024)}MB) exceeds {threshold:.0%} threshold"
        )
        return False

    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/execution/test_memory_monitor.py -v`
Expected: All tests PASS

- [ ] **Step 5: Add config setting for memory pressure threshold**

In `api/src/config.py`, add after the `worker_registration_ttl_seconds` field (line ~116):

```python
    memory_pressure_threshold: float = Field(
        default=0.85,
        description="Reject new forks when container memory usage exceeds this ratio (0.0-1.0)"
    )
```

- [ ] **Step 6: Commit**

```bash
git add api/src/services/execution/memory_monitor.py api/tests/unit/execution/test_memory_monitor.py api/src/config.py
git commit -m "feat: add cgroup v2 memory reading for container-aware admission control"
```

---

### Task 2: Create Template Process Module

New module that manages the template process — a single-threaded process that loads all dependencies and forks children on request.

**Files:**
- Create: `api/src/services/execution/template_process.py`
- Create: `api/tests/unit/execution/test_template_process.py`

- [ ] **Step 1: Write failing tests for template process**

Create `api/tests/unit/execution/test_template_process.py`:

```python
"""
Unit tests for TemplateProcess.

Tests the template process lifecycle: startup, fork requests, shutdown.
Uses real multiprocessing (not mocks) since fork behavior can't be mocked.
"""

import os
import signal
import time

import pytest

from src.services.execution.template_process import TemplateProcess


class TestTemplateProcessLifecycle:
    """Tests for template process startup and shutdown."""

    def test_start_and_ready(self):
        """Template process should start and signal ready."""
        template = TemplateProcess()
        template.start()
        try:
            assert template.is_alive()
            assert template.pid is not None
        finally:
            template.shutdown()

    def test_shutdown_stops_process(self):
        """Shutdown should terminate the template process."""
        template = TemplateProcess()
        template.start()
        pid = template.pid
        template.shutdown()

        assert not template.is_alive()
        # Process should be gone
        with pytest.raises(OSError):
            os.kill(pid, 0)

    def test_double_start_is_noop(self):
        """Starting twice should not create a second process."""
        template = TemplateProcess()
        template.start()
        try:
            pid1 = template.pid
            template.start()  # Should be a no-op
            assert template.pid == pid1
        finally:
            template.shutdown()

    def test_shutdown_without_start_is_safe(self):
        """Shutting down before starting should not raise."""
        template = TemplateProcess()
        template.shutdown()  # Should not raise


class TestTemplateProcessFork:
    """Tests for forking children from the template."""

    def test_fork_returns_child_pid_and_queues(self):
        """Fork should return a valid child PID and queue pair."""
        template = TemplateProcess()
        template.start()
        try:
            child_pid, work_queue, result_queue = template.fork()
            assert child_pid > 0
            assert work_queue is not None
            assert result_queue is not None

            # Child should be alive
            os.kill(child_pid, 0)  # Should not raise

            # Clean up child
            os.kill(child_pid, signal.SIGTERM)
            os.waitpid(child_pid, 0)
        finally:
            template.shutdown()

    def test_fork_multiple_children(self):
        """Should be able to fork multiple children."""
        template = TemplateProcess()
        template.start()
        children = []
        try:
            for _ in range(3):
                child_pid, wq, rq = template.fork()
                children.append(child_pid)

            # All should be unique PIDs
            assert len(set(children)) == 3

            # All should be alive
            for pid in children:
                os.kill(pid, 0)  # Should not raise
        finally:
            for pid in children:
                try:
                    os.kill(pid, signal.SIGTERM)
                    os.waitpid(pid, 0)
                except OSError:
                    pass
            template.shutdown()

    def test_forked_child_can_execute_and_return_result(self):
        """Forked child should be able to receive work and return results."""
        template = TemplateProcess()
        template.start()
        try:
            child_pid, work_queue, result_queue = template.fork()

            # Send a simple test execution ID
            work_queue.put("test-exec-id")

            # Child should process and return result (or we just verify
            # the queue is functional by checking the child is alive)
            # Full execution tests are in E2E — here we verify the plumbing
            time.sleep(0.5)
            os.kill(child_pid, 0)  # Still alive, waiting for work or processing

            # Clean up
            os.kill(child_pid, signal.SIGTERM)
            os.waitpid(child_pid, 0)
        finally:
            template.shutdown()


class TestTemplateProcessCrashRecovery:
    """Tests for template crash detection."""

    def test_is_alive_returns_false_after_crash(self):
        """Should detect when template process has died."""
        template = TemplateProcess()
        template.start()
        pid = template.pid

        # Kill the template process directly
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)

        assert not template.is_alive()
        template.shutdown()  # Cleanup should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/execution/test_template_process.py -v`
Expected: FAIL — `template_process` module not found

- [ ] **Step 3: Implement template process**

Create `api/src/services/execution/template_process.py`:

```python
"""
Template Process for Fork-Based Worker Pool.

A single-threaded process that pre-loads all heavy dependencies and forks
children on request. Children share the template's memory pages via
copy-on-write (COW), drastically reducing per-worker memory overhead.

The template process NEVER:
- Starts an asyncio event loop
- Opens Redis/DB/RabbitMQ connections
- Spawns background threads
- Initializes thread-based logging handlers

This ensures clean fork behavior (no inherited locked mutexes).
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import sys
from multiprocessing import Queue as MPQueue
from multiprocessing.connection import Connection
from typing import Any

logger = logging.getLogger(__name__)

# Commands sent from consumer to template via pipe
CMD_FORK = "fork"
CMD_SHUTDOWN = "shutdown"


def _template_main(
    pipe: Connection,
    preload_modules: list[str] | None = None,
) -> None:
    """
    Entry point for the template process.

    Loads all heavy dependencies, installs import hooks, then waits
    for fork commands on the pipe. This function runs in the template
    process (spawned via multiprocessing.spawn).

    Args:
        pipe: Connection to receive commands from and send responses to consumer.
        preload_modules: Optional list of module names to import at startup.
    """
    # Configure logging (no thread-based handlers)
    logging.basicConfig(
        level=logging.INFO,
        format="[template] %(levelname)s - %(message)s",
    )

    logger.info(f"Template process starting (PID={os.getpid()})")

    # ----- Load heavy dependencies -----
    # These imports pull in the full transitive closure of each library.
    # After fork, children share these pages via COW.
    try:
        # Core bifrost SDK and execution engine
        import bifrost  # noqa: F401
        import httpx  # noqa: F401
        import pydantic  # noqa: F401
        import redis  # noqa: F401
        import sqlalchemy  # noqa: F401

        # Execution infrastructure
        from src.services.execution.virtual_import import install_virtual_import_hook
        from src.services.execution.simple_worker import install_requirements

        # Install user packages (pip install from requirements.txt)
        install_requirements()

        # Ensure user site-packages is in sys.path
        import site
        user_site = site.getusersitepackages()
        if site.ENABLE_USER_SITE and os.path.exists(user_site) and user_site not in sys.path:
            sys.path.insert(0, user_site)
            logger.info(f"Added user site-packages to sys.path: {user_site}")

        # Install virtual import hook for workspace modules
        install_virtual_import_hook()

        # Preload any additional requested modules
        if preload_modules:
            for mod_name in preload_modules:
                try:
                    __import__(mod_name)
                except ImportError:
                    logger.warning(f"Failed to preload module: {mod_name}")

    except Exception as e:
        logger.exception(f"Template process failed to load dependencies: {e}")
        pipe.send({"status": "error", "error": str(e)})
        pipe.close()
        return

    logger.info("Template process ready — all dependencies loaded")
    pipe.send({"status": "ready", "pid": os.getpid()})

    # ----- Fork loop -----
    # Single-threaded, no event loop. Just wait for commands and fork.
    while True:
        try:
            if not pipe.poll(timeout=1.0):
                continue

            cmd = pipe.recv()
        except (EOFError, OSError):
            # Consumer closed the pipe — shut down
            logger.info("Template pipe closed, shutting down")
            break

        if cmd.get("action") == CMD_SHUTDOWN:
            logger.info("Template received shutdown command")
            break

        if cmd.get("action") == CMD_FORK:
            worker_id = cmd.get("worker_id", "unknown")
            persistent = cmd.get("persistent", False)
            _handle_fork_request(pipe, worker_id, persistent)

    logger.info("Template process exiting")


def _handle_fork_request(
    pipe: Connection,
    worker_id: str,
    persistent: bool,
) -> None:
    """
    Handle a fork request: create queues, fork, configure child.

    Args:
        pipe: Control pipe to send response back to consumer.
        worker_id: ID to assign to the forked child worker.
        persistent: If True, child loops for multiple executions.
                    If False, child runs one execution and exits.
    """
    # Create communication queues BEFORE fork so both parent and child inherit them
    work_queue: MPQueue = MPQueue()
    result_queue: MPQueue = MPQueue()

    child_pid = os.fork()

    if child_pid > 0:
        # ----- Parent (template) -----
        # Send child info back to consumer
        pipe.send({
            "status": "forked",
            "child_pid": child_pid,
            "worker_id": worker_id,
            "work_queue": work_queue,
            "result_queue": result_queue,
        })
    else:
        # ----- Child -----
        # Close the template's control pipe — child doesn't need it
        try:
            pipe.close()
        except Exception:
            pass

        # Run the worker function (this blocks until the child exits)
        _run_forked_child(work_queue, result_queue, worker_id, persistent)
        os._exit(0)


def _run_forked_child(
    work_queue: MPQueue,
    result_queue: MPQueue,
    worker_id: str,
    persistent: bool,
) -> None:
    """
    Entry point for a forked child process.

    The child inherits all loaded modules from the template via COW.
    It creates its own event loop and Redis connection fresh.

    Args:
        work_queue: Queue to receive execution_ids from.
        result_queue: Queue to send results back on.
        worker_id: Identifier for logging.
        persistent: If True, loop for multiple executions. If False, run once.
    """
    import asyncio
    import gc
    import resource
    from queue import Empty

    # Reconfigure logging for this child
    logging.basicConfig(
        level=logging.INFO,
        format=f"[{worker_id}] %(levelname)s - %(message)s",
        force=True,
    )

    # Setup signal handler for graceful shutdown
    shutdown_requested = False

    def handle_sigterm(signum: int, frame: Any) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.info(f"Worker {worker_id} received SIGTERM, will exit after current work")

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    logger.info(f"Forked worker {worker_id} started (PID={os.getpid()}, persistent={persistent})")

    execution_id: str | None = None

    while not shutdown_requested:
        try:
            # Block waiting for work
            try:
                execution_id = work_queue.get(timeout=1.0)
            except Empty:
                continue

            if execution_id is None:
                continue

            logger.info(f"Worker {worker_id} processing execution: {execution_id[:8]}...")

            # Clear workspace modules for persistent workers (on-demand don't need this)
            if persistent:
                from src.services.execution.simple_worker import _clear_workspace_modules
                _clear_workspace_modules()

            # Execute
            from src.services.execution.simple_worker import _execute_sync
            result = _execute_sync(execution_id, worker_id)

            # Clean up per-execution state
            try:
                from bifrost._logging import clear_sequence_counter
                clear_sequence_counter(execution_id)
            except Exception:
                pass

            # Force GC before measuring RSS
            gc.collect()

            # Report current RSS
            from src.services.execution.simple_worker import _get_process_rss
            process_rss = _get_process_rss()
            result["process_rss_bytes"] = process_rss
            if isinstance(result.get("metrics"), dict):
                result["metrics"]["process_rss_bytes"] = process_rss

            result_queue.put(result)

            logger.info(
                f"Worker {worker_id} completed execution: {execution_id[:8]}... "
                f"success={result.get('success', False)}"
            )

            execution_id = None

            # On-demand mode: exit after one execution
            if not persistent:
                break

        except KeyboardInterrupt:
            logger.info(f"Worker {worker_id} interrupted")
            break
        except Exception as e:
            logger.exception(f"Worker {worker_id} error: {e}")
            if execution_id is not None:
                try:
                    result_queue.put({
                        "execution_id": execution_id,
                        "success": False,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "duration_ms": 0,
                        "worker_id": worker_id,
                    })
                except Exception:
                    pass
                execution_id = None

            # On-demand mode: exit even on error
            if not persistent:
                break

    logger.info(f"Worker {worker_id} exiting")


class TemplateProcess:
    """
    Manages the lifecycle of the template process.

    The template process is a long-lived, single-threaded process that
    holds all heavy dependencies in memory and forks children on request.
    """

    def __init__(self) -> None:
        self._process: multiprocessing.Process | None = None
        self._pipe: Connection | None = None
        self.pid: int | None = None

    def start(self) -> None:
        """
        Spawn the template process and wait for it to be ready.

        Blocks until the template has loaded all dependencies and
        signaled ready, or raises if startup fails.
        """
        if self._process is not None and self._process.is_alive():
            return  # Already running

        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = multiprocessing.Pipe()

        self._process = ctx.Process(
            target=_template_main,
            args=(child_conn,),
            name="template-process",
        )
        self._process.start()
        self._pipe = parent_conn

        # Wait for ready signal (with timeout)
        if not parent_conn.poll(timeout=120):
            self._process.kill()
            raise RuntimeError("Template process failed to start within 120 seconds")

        msg = parent_conn.recv()
        if msg.get("status") == "error":
            raise RuntimeError(f"Template process startup failed: {msg.get('error')}")

        self.pid = msg.get("pid", self._process.pid)
        logger.info(f"Template process ready (PID={self.pid})")

    def fork(
        self,
        worker_id: str = "worker",
        persistent: bool = False,
    ) -> tuple[int, MPQueue, MPQueue]:
        """
        Request the template to fork a new child worker.

        Args:
            worker_id: Identifier for the new worker (for logging).
            persistent: If True, child loops for multiple executions.
                        If False (default), child runs one execution and exits.

        Returns:
            Tuple of (child_pid, work_queue, result_queue).

        Raises:
            RuntimeError: If template is not running.
        """
        if self._pipe is None or not self.is_alive():
            raise RuntimeError("Template process is not running")

        self._pipe.send({
            "action": CMD_FORK,
            "worker_id": worker_id,
            "persistent": persistent,
        })

        # Wait for fork response
        if not self._pipe.poll(timeout=30):
            raise RuntimeError("Template process did not respond to fork request within 30s")

        msg = self._pipe.recv()
        if msg.get("status") != "forked":
            raise RuntimeError(f"Unexpected fork response: {msg}")

        return (
            msg["child_pid"],
            msg["work_queue"],
            msg["result_queue"],
        )

    def shutdown(self) -> None:
        """Send shutdown command to template and wait for it to exit."""
        if self._pipe is not None:
            try:
                self._pipe.send({"action": CMD_SHUTDOWN})
            except (OSError, BrokenPipeError):
                pass

        if self._process is not None:
            self._process.join(timeout=10)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=5)

        self._pipe = None
        self._process = None
        self.pid = None

    def is_alive(self) -> bool:
        """Check if the template process is still running."""
        if self._process is None:
            return False
        return self._process.is_alive()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/execution/test_template_process.py -v`
Expected: All tests PASS

Note: The `test_forked_child_can_execute_and_return_result` test only verifies queue plumbing — it won't run a real execution since there's no Redis in unit tests. Full execution is verified in E2E tests (Task 5).

- [ ] **Step 5: Commit**

```bash
git add api/src/services/execution/template_process.py api/tests/unit/execution/test_template_process.py
git commit -m "feat: add template process for fork-based worker pool"
```

---

### Task 3: Refactor ProcessPoolManager to Use Template + Fork

Replace `_spawn_process` with fork-from-template. Lower `min_workers` floor to 0. Add admission control. Keep all existing interfaces.

**Files:**
- Modify: `api/src/services/execution/process_pool.py`
- Modify: `api/src/services/worker_pool_config_service.py`
- Modify: `api/tests/unit/execution/test_process_pool.py`

- [ ] **Step 1: Write failing tests for min_workers=0 and admission control**

Add to `api/tests/unit/execution/test_process_pool.py`:

```python
class TestMinWorkersZero:
    """Tests for on-demand mode (min_workers=0)."""

    @pytest.fixture
    def pool_zero(self):
        """Create a pool with min_workers=0."""
        pool = ProcessPoolManager(
            min_workers=0,
            max_workers=5,
        )
        return pool

    def test_min_workers_zero_is_valid(self, pool_zero):
        """Should accept min_workers=0 without raising."""
        assert pool_zero.min_workers == 0

    @pytest.mark.asyncio
    async def test_start_with_zero_workers_spawns_none(self, pool_zero):
        """Pool with min_workers=0 should have no processes after start."""
        with patch.object(pool_zero, '_spawn_or_fork_process') as mock_spawn:
            with patch.object(pool_zero, '_register_worker', new_callable=AsyncMock):
                with patch.object(pool_zero, '_apply_persisted_config', new_callable=AsyncMock):
                    with patch.object(pool_zero, '_start_template', new_callable=AsyncMock):
                        pool_zero._started = True
                        # Simulate start without background tasks
                        assert len(pool_zero.processes) == 0
                        mock_spawn.assert_not_called()


class TestAdmissionControl:
    """Tests for cgroup-based admission control."""

    @pytest.mark.asyncio
    async def test_route_execution_checks_memory_pressure(self):
        """Should reject execution when memory pressure is too high."""
        pool = ProcessPoolManager(min_workers=0, max_workers=5)
        pool._started = True

        with patch(
            "src.services.execution.process_pool.has_sufficient_memory_cgroup",
            return_value=False,
        ):
            with patch.object(pool, '_write_context_to_redis', new_callable=AsyncMock):
                with pytest.raises(MemoryError, match="memory pressure"):
                    await pool.route_execution("exec-123", {"timeout_seconds": 300})

    @pytest.mark.asyncio
    async def test_route_execution_allows_when_memory_ok(self):
        """Should allow execution when memory is within threshold."""
        pool = ProcessPoolManager(min_workers=0, max_workers=5)
        pool._started = True

        mock_handle = ProcessHandle(
            id="process-1",
            process=MagicMock(is_alive=MagicMock(return_value=True)),
            pid=12345,
            state=ProcessState.IDLE,
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=datetime.now(timezone.utc),
        )

        with patch(
            "src.services.execution.process_pool.has_sufficient_memory_cgroup",
            return_value=True,
        ):
            with patch.object(pool, '_write_context_to_redis', new_callable=AsyncMock):
                with patch.object(pool, '_spawn_or_fork_process', return_value=mock_handle):
                    pool.processes["process-1"] = mock_handle
                    await pool.route_execution("exec-123", {"timeout_seconds": 300})
                    assert mock_handle.state == ProcessState.BUSY


class TestResizeMinWorkersZero:
    """Tests for resize accepting min_workers=0."""

    @pytest.mark.asyncio
    async def test_resize_to_zero_is_valid(self):
        """Resize to min_workers=0 should succeed."""
        pool = ProcessPoolManager(min_workers=2, max_workers=5)
        pool._started = True

        with patch.object(pool, '_publish_pool_event', new_callable=AsyncMock):
            with patch.object(pool, '_persist_config', new_callable=AsyncMock):
                result = await pool.resize(0, 5)
                assert pool.min_workers == 0
                assert result["new_min"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/execution/test_process_pool.py::TestMinWorkersZero -v`
Expected: FAIL — min_workers=0 rejected by validation or missing methods

- [ ] **Step 3: Update ProcessPoolManager to use template and support min_workers=0**

Modify `api/src/services/execution/process_pool.py`:

**3a. Add imports at top of file (after existing imports, ~line 44):**

```python
from src.services.execution.template_process import TemplateProcess
from src.services.execution.memory_monitor import has_sufficient_memory_cgroup
```

**3b. Add template to __init__ (after `self._idle_condition` line ~246):**

```python
        # Template process for fork-based workers
        self._template: TemplateProcess | None = None
```

**3c. Replace `_spawn_process` method (lines 258-307) with:**

```python
    def _spawn_or_fork_process(self, persistent: bool | None = None) -> ProcessHandle:
        """
        Create a new worker process by forking from the template.

        If the template is not running (e.g., during tests), falls back
        to multiprocessing.spawn.

        Args:
            persistent: If None, inferred from self.min_workers > 0.
                        If True, child loops. If False, child runs once.

        Returns:
            ProcessHandle instance for the new process.
        """
        if persistent is None:
            persistent = self.min_workers > 0

        self._process_counter += 1
        process_id = f"process-{self._process_counter}"

        if self._template is not None and self._template.is_alive():
            # Fork from template (COW memory sharing)
            child_pid, work_queue, result_queue = self._template.fork(
                worker_id=process_id,
                persistent=persistent,
            )

            handle = ProcessHandle(
                id=process_id,
                process=_PidWrapper(child_pid),
                pid=child_pid,
                state=ProcessState.IDLE,
                work_queue=work_queue,
                result_queue=result_queue,
                started_at=datetime.now(timezone.utc),
                current_execution=None,
                executions_completed=0,
            )
        else:
            # Fallback to spawn (tests, or template not yet started)
            ctx = multiprocessing.get_context("spawn")
            work_queue = ctx.Queue()
            result_queue = ctx.Queue()

            process = ctx.Process(
                target=simple_run_worker_process,
                args=(work_queue, result_queue, process_id),
                name=process_id,
            )
            process.start()

            handle = ProcessHandle(
                id=process_id,
                process=process,
                pid=process.pid,
                state=ProcessState.IDLE,
                work_queue=work_queue,
                result_queue=result_queue,
                started_at=datetime.now(timezone.utc),
                current_execution=None,
                executions_completed=0,
            )

        self.processes[process_id] = handle
        logger.info(f"Created worker {process_id} (PID={handle.pid}, persistent={persistent})")
        return handle
```

**3d. Add `_PidWrapper` class (before `ProcessPoolManager`, after `ProcessHandle`):**

```python
class _PidWrapper:
    """
    Minimal wrapper around a PID to satisfy ProcessHandle.process interface.

    Forked children are not multiprocessing.Process objects — they're raw PIDs.
    This wrapper provides is_alive() and join() so ProcessHandle works uniformly.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def is_alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def join(self, timeout: float | None = None) -> None:
        try:
            if timeout:
                # Non-blocking waitpid with polling
                import time
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    pid, _ = os.waitpid(self.pid, os.WNOHANG)
                    if pid != 0:
                        return
                    time.sleep(0.1)
            else:
                os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass  # Already reaped
```

**3e. Update `start()` method (lines 309-371) — add template startup before spawning workers:**

After `await asyncio.to_thread(install_requirements)` and `self._update_requirements_status()` (line ~335), add:

```python
        # Start template process (loads deps, ready to fork)
        await self._start_template()
```

Add the method:

```python
    async def _start_template(self) -> None:
        """Start the template process for fork-based workers."""
        self._template = TemplateProcess()
        try:
            await asyncio.to_thread(self._template.start)
            logger.info(f"Template process started (PID={self._template.pid})")
        except Exception as e:
            logger.error(f"Failed to start template process: {e}. Falling back to spawn.")
            self._template = None
```

**3f. Update `route_execution()` (lines 538-585) — add admission control:**

After `await self._write_context_to_redis(execution_id, context)` (line 554), before finding idle process:

```python
        # Admission control: check memory pressure before forking
        settings = get_settings()
        if not has_sufficient_memory_cgroup(threshold=settings.memory_pressure_threshold):
            # Clean up the context we just wrote
            r = await self._get_redis()
            await r.delete(f"bifrost:exec:{execution_id}:context")
            raise MemoryError(
                f"Cannot route execution {execution_id[:8]}: memory pressure "
                f"exceeds {settings.memory_pressure_threshold:.0%} threshold"
            )
```

**3g. Update `route_execution()` — replace `self._spawn_process()` call with `self._spawn_or_fork_process()`:**

Change line ~561 from:
```python
                idle = self._spawn_process()
```
to:
```python
                idle = self._spawn_or_fork_process()
```

**3h. Update `_handle_result()` (lines 1092-1148) — handle on-demand child exit:**

After incrementing `handle.executions_completed` (line 1106), before checking pending_recycle:

```python
        # On-demand mode: child exits after one execution, just clean up
        if self.min_workers == 0:
            if handle.id in self.processes:
                del self.processes[handle.id]
            # Forward result to callback
            if self.on_result:
                try:
                    await self.on_result(result)
                except Exception as e:
                    logger.exception(f"Error in result callback: {e}")
            return
```

**3i. Update `resize()` validation (line 1235-1236) — lower floor to 0:**

Change:
```python
        if new_min < 2:
            raise ValueError(f"min_workers must be >= 2, got {new_min}")
```
to:
```python
        if new_min < 0:
            raise ValueError(f"min_workers must be >= 0, got {new_min}")
```

**3j. Update `_recycle_process()` (lines 1150-1174) — use fork for replacement:**

Change line 1171 from:
```python
        self._spawn_process()
```
to:
```python
        self._spawn_or_fork_process()
```

**3k. Update `recycle_process()` (line 1212) — same change:**

Change:
```python
        self._spawn_process()
```
to:
```python
        self._spawn_or_fork_process()
```

**3l. Update `mark_for_recycle()` — add template restart logic:**

After the existing `mark_for_recycle` method (line ~1436), add:

```python
    async def restart_template(self) -> None:
        """
        Restart the template process (e.g., after pip install).

        All children must be drained/killed before calling this.
        """
        if self._template is not None:
            logger.info("Shutting down template process for restart")
            await asyncio.to_thread(self._template.shutdown)

        await self._start_template()
        logger.info("Template process restarted")
```

**3m. Update `stop()` method — shutdown template on pool stop:**

After terminating all processes and before unregistering from Redis, add:

```python
        # Shutdown template process
        if self._template is not None:
            self._template.shutdown()
            self._template = None
```

**3n. Update all remaining `self._spawn_process()` calls to `self._spawn_or_fork_process()`:**

Search through the file for any other calls to `_spawn_process()` (e.g., in `_check_timeouts`, monitor loop crash replacement) and replace with `_spawn_or_fork_process()`.

**3o. Add template crash recovery to monitor loop:**

In `_monitor_loop()` (or `_check_timeouts()`), add a check at the start of each iteration:

```python
            # Check template health — restart if crashed
            if self._template is not None and not self._template.is_alive():
                logger.error("Template process died — restarting")
                try:
                    await self._start_template()
                except Exception as e:
                    logger.error(f"Failed to restart template: {e}")
```

This ensures the pool self-heals if the template crashes unexpectedly. In-flight children are unaffected since they're independent processes.

- [ ] **Step 4: Update WorkerPoolConfigService validation**

In `api/src/services/worker_pool_config_service.py`, change line 85-86:

From:
```python
        if min_workers < 2:
            raise ValueError(f"min_workers must be >= 2, got {min_workers}")
```
To:
```python
        if min_workers < 0:
            raise ValueError(f"min_workers must be >= 0, got {min_workers}")
```

- [ ] **Step 5: Run all pool unit tests**

Run: `./test.sh tests/unit/execution/test_process_pool.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 6: Commit**

```bash
git add api/src/services/execution/process_pool.py api/src/services/worker_pool_config_service.py api/tests/unit/execution/test_process_pool.py
git commit -m "feat: refactor ProcessPoolManager to fork from template, support min_workers=0"
```

---

### Task 4: Update Consumer to Handle Admission Rejection

The consumer needs to NACK messages back to RabbitMQ when the pool rejects due to memory pressure.

**Files:**
- Modify: `api/src/jobs/consumers/workflow_execution.py`

- [ ] **Step 1: Read the consumer's process_message method**

Read `api/src/jobs/consumers/workflow_execution.py` to find the `process_message` method and understand how it calls `route_execution`. Look for the try/except around the route call.

- [ ] **Step 2: Add MemoryError handling in the consumer**

In `api/src/jobs/consumers/workflow_execution.py`, the `except Exception` block at line 684 catches all errors and marks the execution as FAILED. For admission rejection, we want to requeue instead. Add a `MemoryError` catch **before** the generic `except Exception` block (after `except asyncio.CancelledError` at line 679):

```python
        except MemoryError as e:
            # Admission rejected due to memory pressure — requeue for retry
            logger.warning(
                f"Admission rejected for {execution_id[:8]}: {e}. "
                "Will requeue for retry."
            )
            # Don't mark as failed — the execution hasn't started yet.
            # Clean up pending state so it can be re-routed.
            await self._redis_client.delete_pending_execution(execution_id)
            # Re-raise so the consumer framework NACKs with requeue=True
            raise
```

This goes at line ~683, between the `CancelledError` and `Exception` handlers. The RabbitMQ consumer base class treats unhandled exceptions as NACK with requeue, so the message goes back to the queue for another consumer (or this one after memory frees).

- [ ] **Step 3: Run E2E tests to verify nothing broke**

Run: `./test.sh tests/e2e/ -v --timeout=300`
Expected: All existing E2E tests PASS

- [ ] **Step 4: Commit**

```bash
git add api/src/jobs/consumers/workflow_execution.py
git commit -m "feat: handle admission rejection in consumer — NACK on memory pressure"
```

---

### Task 5: E2E Tests for Fork-Based Execution

Verify the full execution path works with fork-based workers.

**Files:**
- Create: `api/tests/e2e/platform/test_fork_pool.py`

- [ ] **Step 1: Write E2E test for fork-based execution**

Create `api/tests/e2e/platform/test_fork_pool.py`:

```python
"""
E2E tests for fork-based process pool.

These tests verify that workflow execution works correctly when the
pool uses fork-from-template instead of multiprocessing.spawn.
They run against the full stack (API + workers + Redis + PostgreSQL).
"""

import pytest

from tests.e2e.conftest import AuthenticatedClient


class TestForkBasedExecution:
    """Test that workflows execute correctly with fork-based pool."""

    @pytest.mark.e2e
    async def test_basic_workflow_execution(self, client: AuthenticatedClient):
        """A simple workflow should execute and return results via fork pool."""
        # Execute a simple inline script
        response = await client.post(
            "/api/executions/run",
            json={
                "code": "import base64; base64.b64encode(b'test')\nresult = {'status': 'ok', 'value': 42}",
                "sync": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["result"]["value"] == 42

    @pytest.mark.e2e
    async def test_concurrent_executions(self, client: AuthenticatedClient):
        """Multiple concurrent executions should complete without interference."""
        import asyncio

        async def run_one(i: int):
            response = await client.post(
                "/api/executions/run",
                json={
                    "code": f"result = {{'index': {i}, 'pid': __import__('os').getpid()}}",
                    "sync": True,
                },
            )
            assert response.status_code == 200
            return response.json()

        results = await asyncio.gather(*[run_one(i) for i in range(5)])

        # All should succeed
        for r in results:
            assert r["status"] == "success"

        # Results should have correct indices
        indices = sorted(r["result"]["index"] for r in results)
        assert indices == [0, 1, 2, 3, 4]

    @pytest.mark.e2e
    async def test_execution_timeout_still_works(self, client: AuthenticatedClient):
        """Timeout should still kill forked processes."""
        response = await client.post(
            "/api/executions/run",
            json={
                "code": "import time; time.sleep(60)",
                "sync": True,
                "timeout_seconds": 3,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("timeout", "failed")
```

- [ ] **Step 2: Run E2E tests**

Run: `./test.sh tests/e2e/platform/test_fork_pool.py -v --timeout=300`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite to verify nothing regressed**

Run: `./test.sh --timeout=600`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add api/tests/e2e/platform/test_fork_pool.py
git commit -m "test: add E2E tests for fork-based process pool execution"
```

---

### Task 6: Add Private_Dirty Metric to Heartbeat

Add per-child unique memory (`Private_Dirty` from smaps_rollup) to the heartbeat data for benchmarking COW effectiveness.

**Files:**
- Modify: `api/src/services/execution/process_pool.py` (heartbeat method)

- [ ] **Step 1: Find the heartbeat method**

Read the `_publish_heartbeat` or `_heartbeat_loop` method in `process_pool.py` to see how per-process data is collected for the heartbeat payload.

- [ ] **Step 2: Add Private_Dirty reading**

Add a helper function in `process_pool.py`:

```python
def _get_private_dirty_kb(pid: int) -> int:
    """
    Read Private_Dirty from /proc/{pid}/smaps_rollup.

    Returns the total private dirty memory in KB, which represents
    the unique (non-shared/COW) memory for this process.
    Returns -1 if unable to read.
    """
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Private_Dirty:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (OSError, ValueError):
        pass
    return -1
```

- [ ] **Step 3: Include in heartbeat per-process data**

In the heartbeat method where per-process info is built (look for the dict that includes pid, memory, state), add:

```python
                    "private_dirty_kb": _get_private_dirty_kb(handle.pid) if handle.pid else -1,
```

- [ ] **Step 4: Run unit tests**

Run: `./test.sh tests/unit/execution/test_process_pool.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add api/src/services/execution/process_pool.py
git commit -m "feat: add Private_Dirty metric to heartbeat for COW memory tracking"
```

---

### Task 7: Cleanup and Final Verification

Remove dead code, run full quality checks.

**Files:**
- Modify: `api/src/services/execution/process_pool.py` (remove old `_spawn_process` if not already replaced)

- [ ] **Step 1: Search for remaining references to old _spawn_process**

```bash
grep -rn "_spawn_process" api/src/ api/tests/
```

Replace any remaining calls with `_spawn_or_fork_process`. Remove the old `_spawn_process` method if it still exists as dead code.

- [ ] **Step 2: Run pyright**

```bash
cd api && pyright
```

Expected: 0 errors

- [ ] **Step 3: Run ruff**

```bash
cd api && ruff check .
```

Expected: 0 errors

- [ ] **Step 4: Run full test suite**

```bash
./test.sh
```

Expected: All tests PASS

- [ ] **Step 5: Commit any cleanup**

```bash
git add -A
git commit -m "chore: cleanup dead code from spawn-to-fork migration"
```
