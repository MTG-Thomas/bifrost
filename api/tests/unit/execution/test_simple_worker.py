"""
Unit tests for simple_worker module.

Tests the simple worker process that runs executions one at a time
in a loop, reading from work_queue and writing to result_queue.

The simple worker is designed to be spawned by a process pool and
provides a straightforward alternative to the thread-based worker.
"""

import json
import queue
import signal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.execution.simple_worker import (
    _capture_resource_metrics,
    _execute_async,
    _execute_sync,
    _install_requirements_from_cache_sync,
    _read_context_from_redis,
    run_worker_process,
)


class TestCaptureResourceMetrics:
    """Tests for resource metrics capture."""

    def test_returns_expected_keys(self):
        """Should return dict with expected metric keys."""
        metrics = _capture_resource_metrics()

        assert "peak_memory_bytes" in metrics
        assert "cpu_user_seconds" in metrics
        assert "cpu_system_seconds" in metrics
        assert "cpu_total_seconds" in metrics

    def test_peak_memory_is_positive(self):
        """Peak memory should be a positive integer."""
        metrics = _capture_resource_metrics()

        assert isinstance(metrics["peak_memory_bytes"], int)
        assert metrics["peak_memory_bytes"] > 0

    def test_cpu_values_are_floats(self):
        """CPU values should be floats."""
        metrics = _capture_resource_metrics()

        assert isinstance(metrics["cpu_user_seconds"], float)
        assert isinstance(metrics["cpu_system_seconds"], float)
        assert isinstance(metrics["cpu_total_seconds"], float)

    def test_cpu_total_is_sum(self):
        """CPU total should be sum of user and system."""
        metrics = _capture_resource_metrics()

        expected_total = metrics["cpu_user_seconds"] + metrics["cpu_system_seconds"]
        assert abs(metrics["cpu_total_seconds"] - expected_total) < 0.0001


class TestReadContextFromRedis:
    """Tests for reading execution context from Redis."""

    @pytest.mark.asyncio
    @patch("src.config.get_settings")
    async def test_returns_context_when_found(self, mock_settings):
        """Should return parsed context when found in Redis."""
        mock_settings.return_value.redis_url = "redis://localhost:6379/0"

        context_data = {
            "execution_id": "exec-123",
            "name": "test_workflow",
            "parameters": {"key": "value"},
        }

        with patch("redis.asyncio.from_url") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=json.dumps(context_data))
            mock_redis.aclose = AsyncMock()
            mock_redis_factory.return_value = mock_redis

            result = await _read_context_from_redis("exec-123")

            assert result == context_data
            mock_redis.get.assert_called_once_with("bifrost:exec:exec-123:context")
            mock_redis.aclose.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.config.get_settings")
    async def test_returns_none_when_not_found(self, mock_settings):
        """Should return None when context not found in Redis."""
        mock_settings.return_value.redis_url = "redis://localhost:6379/0"

        with patch("redis.asyncio.from_url") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.aclose = AsyncMock()
            mock_redis_factory.return_value = mock_redis

            result = await _read_context_from_redis("exec-missing")

            assert result is None

    @pytest.mark.asyncio
    @patch("src.config.get_settings")
    async def test_returns_none_on_error(self, mock_settings):
        """Should return None and log error on Redis error."""
        mock_settings.return_value.redis_url = "redis://localhost:6379/0"

        with patch("redis.asyncio.from_url") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(side_effect=Exception("Connection error"))
            mock_redis.aclose = AsyncMock()
            mock_redis_factory.return_value = mock_redis

            result = await _read_context_from_redis("exec-error")

            assert result is None


class TestExecuteAsync:
    """Tests for async execution logic."""

    @pytest.mark.asyncio
    @patch("src.services.execution.simple_worker._read_context_from_redis")
    async def test_returns_error_when_context_missing(self, mock_read_context):
        """Should return error result when context not found."""
        mock_read_context.return_value = None

        result = await _execute_async("exec-missing", "worker-1")

        assert result["execution_id"] == "exec-missing"
        assert result["success"] is False
        assert result["error_type"] == "ContextNotFound"
        assert "context not found" in result["error"].lower()
        assert result["worker_id"] == "worker-1"

    @pytest.mark.asyncio
    @patch("src.services.execution.simple_worker._read_context_from_redis")
    @patch("src.services.execution.worker._run_execution")
    async def test_returns_success_result(self, mock_run_execution, mock_read_context):
        """Should return success result when execution succeeds."""
        mock_read_context.return_value = {
            "execution_id": "exec-123",
            "name": "test_workflow",
            "parameters": {},
        }

        mock_run_execution.return_value = {
            "status": "Success",
            "result": {"data": "value"},
            "duration_ms": 1500,
            "logs": [],
            "variables": None,
            "integration_calls": [],
            "roi": {"time_saved": 0, "value": 0.0},
            "error_message": None,
            "error_type": None,
            "cached": False,
            "cache_expires_at": None,
            "metrics": {"peak_memory_bytes": 1024, "cpu_total_seconds": 0.1},
        }

        result = await _execute_async("exec-123", "worker-1")

        assert result["execution_id"] == "exec-123"
        assert result["success"] is True
        assert result["status"] == "Success"
        assert result["result"] == {"data": "value"}
        assert result["worker_id"] == "worker-1"

    @pytest.mark.asyncio
    @patch("src.services.execution.simple_worker._read_context_from_redis")
    @patch("src.services.execution.worker._run_execution")
    async def test_returns_failure_result(self, mock_run_execution, mock_read_context):
        """Should return failure result when execution fails."""
        mock_read_context.return_value = {
            "execution_id": "exec-456",
            "name": "test_workflow",
            "parameters": {},
        }

        mock_run_execution.return_value = {
            "status": "Failed",
            "result": None,
            "duration_ms": 500,
            "logs": [],
            "variables": None,
            "error_message": "Something went wrong",
            "error_type": "RuntimeError",
        }

        result = await _execute_async("exec-456", "worker-1")

        assert result["execution_id"] == "exec-456"
        assert result["success"] is False
        assert result["status"] == "Failed"
        assert result["error"] == "Something went wrong"
        assert result["error_type"] == "RuntimeError"

    @pytest.mark.asyncio
    @patch("src.services.execution.simple_worker._read_context_from_redis")
    @patch("src.services.execution.worker._run_execution")
    async def test_handles_exception_in_execution(self, mock_run_execution, mock_read_context):
        """Should handle exception from _run_execution."""
        mock_read_context.return_value = {
            "execution_id": "exec-error",
            "name": "test_workflow",
            "parameters": {},
        }

        mock_run_execution.side_effect = Exception("Unexpected error")

        result = await _execute_async("exec-error", "worker-1")

        assert result["execution_id"] == "exec-error"
        assert result["success"] is False
        assert result["error"] == "Unexpected error"
        assert result["error_type"] == "Exception"
        assert result["duration_ms"] >= 0


class TestExecuteSync:
    """Tests for sync execution wrapper."""

    @patch("src.services.execution.simple_worker._execute_async")
    def test_calls_async_execute(self, mock_execute_async):
        """Should call _execute_async via asyncio.run()."""
        expected_result = {
            "execution_id": "exec-123",
            "success": True,
            "result": {"data": "value"},
        }

        async def async_result(*args):
            return expected_result

        mock_execute_async.side_effect = async_result

        result = _execute_sync("exec-123", "worker-1")

        assert result == expected_result
        mock_execute_async.assert_called_once_with("exec-123", "worker-1")

    @patch("src.services.execution.simple_worker._execute_async")
    def test_handles_exception(self, mock_execute_async):
        """Should catch exception and return error result."""
        async def async_error(*args):
            raise RuntimeError("Async execution failed")

        mock_execute_async.side_effect = async_error

        result = _execute_sync("exec-error", "worker-1")

        assert result["execution_id"] == "exec-error"
        assert result["success"] is False
        assert result["error"] == "Async execution failed"
        assert result["error_type"] == "RuntimeError"
        assert result["worker_id"] == "worker-1"


class TestRunWorkerProcess:
    """Tests for the main worker process loop.

    These tests use threading to run the worker in a controlled way and
    ensure clean termination after verifying expectations.
    """

    @patch("src.services.execution.simple_worker._install_requirements_from_cache_sync")
    @patch("src.services.execution.virtual_import.install_virtual_import_hook")
    @patch("src.services.execution.simple_worker._execute_sync")
    def test_executes_and_returns_result(self, mock_execute, mock_install_hook, mock_install_reqs):
        """Worker should execute work and return result."""
        import threading

        work_q: queue.Queue[str] = queue.Queue()
        result_q: queue.Queue[dict[str, Any]] = queue.Queue()
        completed = threading.Event()

        expected_result = {
            "execution_id": "exec-123",
            "success": True,
            "result": {"data": "value"},
            "worker_id": "worker-1",
        }

        def execute_and_signal(*args):
            completed.set()
            return expected_result

        mock_execute.side_effect = execute_and_signal

        def run_worker():
            try:
                run_worker_process(work_q, result_q, "worker-1")  # type: ignore
            except (KeyboardInterrupt, SystemExit):
                pass

        # Start worker thread
        with patch("signal.signal"):
            work_q.put("exec-123")
            thread = threading.Thread(target=run_worker)
            thread.daemon = True
            thread.start()

            # Wait for execution to complete
            assert completed.wait(timeout=2.0), "Execution did not complete in time"

        # Should have installed virtual import hook
        mock_install_hook.assert_called_once()

        # Should have result in queue
        result = result_q.get_nowait()
        assert result == expected_result

    @patch("src.services.execution.simple_worker._install_requirements_from_cache_sync")
    @patch("src.services.execution.virtual_import.install_virtual_import_hook")
    @patch("src.services.execution.simple_worker._execute_sync")
    def test_handles_missing_context(self, mock_execute, mock_install_hook, mock_install_reqs):
        """Worker should handle missing context gracefully."""
        import threading

        work_q: queue.Queue[str] = queue.Queue()
        result_q: queue.Queue[dict[str, Any]] = queue.Queue()
        completed = threading.Event()

        error_result = {
            "execution_id": "exec-missing",
            "success": False,
            "error": "Execution context not found in Redis",
            "error_type": "ContextNotFound",
            "worker_id": "worker-1",
        }

        def execute_and_signal(*args):
            completed.set()
            return error_result

        mock_execute.side_effect = execute_and_signal

        def run_worker():
            try:
                run_worker_process(work_q, result_q, "worker-1")  # type: ignore
            except (KeyboardInterrupt, SystemExit):
                pass

        with patch("signal.signal"):
            work_q.put("exec-missing")
            thread = threading.Thread(target=run_worker)
            thread.daemon = True
            thread.start()

            assert completed.wait(timeout=2.0), "Execution did not complete in time"

        result = result_q.get_nowait()
        assert result["success"] is False
        assert result["error_type"] == "ContextNotFound"

    @patch("src.services.execution.simple_worker._install_requirements_from_cache_sync")
    @patch("src.services.execution.virtual_import.install_virtual_import_hook")
    @patch("src.services.execution.simple_worker._execute_sync")
    def test_handles_execution_error(self, mock_execute, mock_install_hook, mock_install_reqs):
        """Worker should report errors to result queue."""
        import threading

        work_q: queue.Queue[str] = queue.Queue()
        result_q: queue.Queue[dict[str, Any]] = queue.Queue()
        completed = threading.Event()

        def execute_error(*args):
            completed.set()
            raise RuntimeError("Execution crashed")

        mock_execute.side_effect = execute_error

        def run_worker():
            try:
                run_worker_process(work_q, result_q, "worker-1")  # type: ignore
            except (KeyboardInterrupt, SystemExit):
                pass

        with patch("signal.signal"):
            work_q.put("exec-error")
            thread = threading.Thread(target=run_worker)
            thread.daemon = True
            thread.start()

            assert completed.wait(timeout=2.0), "Execution did not complete in time"

        # Should have error result in queue (use timeout to allow exception handler to run)
        import time
        time.sleep(0.1)  # Allow exception handler to put result in queue
        result = result_q.get(timeout=1.0)
        assert result["execution_id"] == "exec-error"
        assert result["success"] is False
        assert result["error"] == "Execution crashed"
        assert result["error_type"] == "RuntimeError"

    @patch("src.services.execution.simple_worker._install_requirements_from_cache_sync")
    @patch("src.services.execution.virtual_import.install_virtual_import_hook")
    def test_loops_for_multiple_executions(self, mock_install_hook, mock_install_reqs):
        """Worker should process multiple executions in a loop."""
        import threading

        work_q: queue.Queue[str] = queue.Queue()
        result_q: queue.Queue[dict[str, Any]] = queue.Queue()
        completed = threading.Event()

        # Queue multiple executions
        work_q.put("exec-1")
        work_q.put("exec-2")
        work_q.put("exec-3")

        call_count = 0

        def mock_execute_sync(exec_id, worker_id):
            nonlocal call_count
            call_count += 1
            result = {"execution_id": exec_id, "success": True, "worker_id": worker_id}
            if call_count >= 3:
                completed.set()
            return result

        def run_worker():
            try:
                run_worker_process(work_q, result_q, "worker-1")  # type: ignore
            except (KeyboardInterrupt, SystemExit):
                pass

        with patch("src.services.execution.simple_worker._execute_sync", side_effect=mock_execute_sync):
            with patch("signal.signal"):
                thread = threading.Thread(target=run_worker)
                thread.daemon = True
                thread.start()

                assert completed.wait(timeout=3.0), "Multiple executions did not complete in time"

        # Should have all results
        collected = []
        while not result_q.empty():
            collected.append(result_q.get_nowait())

        assert len(collected) >= 3
        exec_ids = [r["execution_id"] for r in collected]
        assert "exec-1" in exec_ids
        assert "exec-2" in exec_ids
        assert "exec-3" in exec_ids


class TestSignalHandling:
    """Tests for signal handling behavior."""

    def test_signal_registration(self):
        """Worker should register signal handlers."""
        import threading

        work_q: queue.Queue[str] = queue.Queue()
        result_q: queue.Queue[dict[str, Any]] = queue.Queue()
        completed = threading.Event()

        registered_signals: list[int] = []

        def mock_signal(sig: int, handler: Any) -> Any:
            registered_signals.append(sig)
            return None

        def stop_execute(*args: Any) -> Any:
            completed.set()
            return {"execution_id": "exec-stop", "success": True, "worker_id": "worker-1"}

        def run_worker():
            try:
                run_worker_process(work_q, result_q, "worker-1")  # type: ignore
            except (KeyboardInterrupt, SystemExit):
                pass

        with patch("signal.signal", side_effect=mock_signal):
            with patch("src.services.execution.simple_worker._install_requirements_from_cache_sync"):
                with patch("src.services.execution.virtual_import.install_virtual_import_hook"):
                    with patch("src.services.execution.simple_worker._execute_sync", side_effect=stop_execute):
                        work_q.put("exec-stop")

                        thread = threading.Thread(target=run_worker)
                        thread.daemon = True
                        thread.start()

                        assert completed.wait(timeout=2.0), "Worker did not process work in time"

        # Should have registered SIGTERM and SIGINT
        assert signal.SIGTERM in registered_signals
        assert signal.SIGINT in registered_signals


class TestResultFormat:
    """Tests for result format consistency."""

    @pytest.mark.asyncio
    @patch("src.services.execution.simple_worker._read_context_from_redis")
    @patch("src.services.execution.worker._run_execution")
    async def test_result_includes_all_expected_fields(self, mock_run, mock_read):
        """Result should include all expected fields."""
        mock_read.return_value = {
            "execution_id": "exec-123",
            "name": "test",
            "parameters": {},
        }

        mock_run.return_value = {
            "status": "Success",
            "result": {"data": "value"},
            "duration_ms": 1500,
            "logs": [{"level": "info", "message": "test"}],
            "variables": {"x": 1},
            "integration_calls": [{"tool": "test_tool"}],
            "roi": {"time_saved": 60, "value": 100.0},
            "error_message": None,
            "error_type": None,
            "cached": False,
            "cache_expires_at": None,
            "metrics": {
                "peak_memory_bytes": 1024,
                "cpu_user_seconds": 0.05,
                "cpu_system_seconds": 0.02,
                "cpu_total_seconds": 0.07,
            },
        }

        result = await _execute_async("exec-123", "worker-1")

        # Check all expected fields
        assert "execution_id" in result
        assert "success" in result
        assert "status" in result
        assert "result" in result
        assert "error" in result
        assert "error_type" in result
        assert "duration_ms" in result
        assert "logs" in result
        assert "variables" in result
        assert "integration_calls" in result
        assert "roi" in result
        assert "metrics" in result
        assert "cached" in result
        assert "cache_expires_at" in result
        assert "worker_id" in result

    @pytest.mark.asyncio
    @patch("src.services.execution.simple_worker._read_context_from_redis")
    async def test_error_result_format(self, mock_read):
        """Error result should have consistent format."""
        mock_read.return_value = None

        result = await _execute_async("exec-missing", "worker-1")

        # Error results should still have key fields
        assert result["execution_id"] == "exec-missing"
        assert result["success"] is False
        assert "error" in result
        assert "error_type" in result
        assert result["duration_ms"] >= 0
        assert result["worker_id"] == "worker-1"


class TestCompletedWithErrorsStatus:
    """Tests for CompletedWithErrors status handling."""

    @pytest.mark.asyncio
    @patch("src.services.execution.simple_worker._read_context_from_redis")
    @patch("src.services.execution.worker._run_execution")
    async def test_completed_with_errors_is_success(self, mock_run, mock_read):
        """CompletedWithErrors status should be treated as success."""
        mock_read.return_value = {
            "execution_id": "exec-123",
            "name": "test",
            "parameters": {},
        }

        mock_run.return_value = {
            "status": "CompletedWithErrors",
            "result": {"partial": "data"},
            "duration_ms": 1000,
            "logs": [],
            "error_message": "Some items failed",
            "error_type": "PartialFailure",
        }

        result = await _execute_async("exec-123", "worker-1")

        # CompletedWithErrors should still be marked as success
        assert result["success"] is True
        assert result["status"] == "CompletedWithErrors"


class TestInstallRequirementsFromCache:
    """Tests for _install_requirements_from_cache_sync function.

    This function is called at worker startup to install packages from
    cached requirements.txt stored in Redis.

    Note: We patch 'redis.from_url' directly because the function imports
    redis locally within its body.
    """

    @patch("subprocess.run")
    @patch("redis.from_url")
    def test_installs_from_cache(self, mock_redis_factory: MagicMock, mock_subprocess: MagicMock):
        """Test successful installation from cached requirements."""
        # Setup mock Redis
        mock_client = MagicMock()
        cached = {"content": "flask==2.3.0\nrequests==2.31.0\n", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client

        # Setup mock subprocess
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Run
        _install_requirements_from_cache_sync("test-worker")

        # Verify Redis was called correctly
        mock_redis_factory.assert_called_once()
        mock_client.get.assert_called_once_with("bifrost:requirements:content")
        mock_client.close.assert_called_once()

        # Verify pip was called
        assert mock_subprocess.called
        call_args = mock_subprocess.call_args
        assert "-m" in call_args[0][0]
        assert "pip" in call_args[0][0]
        assert "install" in call_args[0][0]
        assert "-r" in call_args[0][0]

    @patch("time.sleep")
    @patch("redis.from_url")
    def test_handles_redis_unavailable_with_retry(
        self, mock_redis_factory: MagicMock, mock_sleep: MagicMock
    ):
        """Test graceful handling with retry when Redis is unavailable."""
        import redis

        mock_redis_factory.side_effect = redis.ConnectionError("Connection refused")

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

        # Should have attempted 3 times
        assert mock_redis_factory.call_count == 3

        # Should have slept between retries (2 sleeps for 3 attempts)
        assert mock_sleep.call_count == 2

    @patch("redis.from_url")
    def test_handles_empty_cache(self, mock_redis_factory: MagicMock):
        """Test handling when cache is empty (no data in Redis)."""
        mock_client = MagicMock()
        mock_client.get.return_value = None
        mock_redis_factory.return_value = mock_client

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

        # Redis should have been queried
        mock_client.get.assert_called_once_with("bifrost:requirements:content")
        mock_client.close.assert_called_once()

    @patch("redis.from_url")
    def test_handles_empty_content(self, mock_redis_factory: MagicMock):
        """Test handling when cached content is empty string."""
        mock_client = MagicMock()
        cached = {"content": "", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

        # Redis should have been queried
        mock_client.get.assert_called_once()
        mock_client.close.assert_called_once()

    @patch("redis.from_url")
    def test_handles_whitespace_only_content(self, mock_redis_factory: MagicMock):
        """Test handling when cached content is only whitespace."""
        mock_client = MagicMock()
        cached = {"content": "   \n\n   ", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

        # Redis should have been queried but pip should not be called
        mock_client.get.assert_called_once()

    @patch("redis.from_url")
    def test_handles_invalid_json(self, mock_redis_factory: MagicMock):
        """Test handling when cached data is invalid JSON."""
        mock_client = MagicMock()
        mock_client.get.return_value = "not valid json {"
        mock_redis_factory.return_value = mock_client

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

        # Should have tried to parse JSON
        mock_client.get.assert_called_once()
        mock_client.close.assert_called_once()

    @patch("subprocess.run")
    @patch("redis.from_url")
    def test_handles_pip_failure(
        self, mock_redis_factory: MagicMock, mock_subprocess: MagicMock
    ):
        """Test handling when pip install fails."""
        mock_client = MagicMock()
        cached = {"content": "invalid-package-xyz==999.999.999\n", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client

        # pip returns non-zero
        mock_subprocess.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="ERROR: Could not find a version that satisfies the requirement"
        )

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

        # pip should have been called
        assert mock_subprocess.called

    @patch("subprocess.run")
    @patch("redis.from_url")
    def test_handles_pip_timeout(
        self, mock_redis_factory: MagicMock, mock_subprocess: MagicMock
    ):
        """Test handling when pip install times out."""
        import subprocess

        mock_client = MagicMock()
        cached = {"content": "flask==2.3.0\n", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client

        # pip times out
        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd="pip", timeout=300)

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

    @patch("subprocess.run")
    @patch("redis.from_url")
    def test_cleans_up_temp_file_on_success(
        self, mock_redis_factory: MagicMock, mock_subprocess: MagicMock
    ):
        """Test that temp file is cleaned up after successful install."""
        import os
        import tempfile

        mock_client = MagicMock()
        cached = {"content": "flask==2.3.0\n", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Track created temp files
        created_temp_files: list[str] = []
        original_named_temp = tempfile.NamedTemporaryFile

        def track_temp_file(*args: Any, **kwargs: Any) -> Any:
            kwargs["delete"] = False
            f = original_named_temp(*args, **kwargs)
            created_temp_files.append(f.name)
            return f

        with patch("tempfile.NamedTemporaryFile", track_temp_file):
            _install_requirements_from_cache_sync("test-worker")

        # Temp file should have been created and then deleted
        assert len(created_temp_files) == 1
        assert not os.path.exists(created_temp_files[0])

    @patch("subprocess.run")
    @patch("redis.from_url")
    def test_cleans_up_temp_file_on_failure(
        self, mock_redis_factory: MagicMock, mock_subprocess: MagicMock
    ):
        """Test that temp file is cleaned up even when pip fails."""
        import os
        import tempfile

        mock_client = MagicMock()
        cached = {"content": "flask==2.3.0\n", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="error")

        # Track created temp files
        created_temp_files: list[str] = []
        original_named_temp = tempfile.NamedTemporaryFile

        def track_temp_file(*args: Any, **kwargs: Any) -> Any:
            kwargs["delete"] = False
            f = original_named_temp(*args, **kwargs)
            created_temp_files.append(f.name)
            return f

        with patch("tempfile.NamedTemporaryFile", track_temp_file):
            _install_requirements_from_cache_sync("test-worker")

        # Temp file should have been cleaned up
        assert len(created_temp_files) == 1
        assert not os.path.exists(created_temp_files[0])

    @patch("redis.from_url")
    def test_handles_missing_content_key(self, mock_redis_factory: MagicMock):
        """Test handling when JSON is valid but missing content key."""
        mock_client = MagicMock()
        cached = {"hash": "abc123"}  # No "content" key
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

        mock_client.get.assert_called_once()
        mock_client.close.assert_called_once()

    @patch("subprocess.run")
    @patch("redis.from_url")
    def test_counts_packages_correctly(
        self, mock_redis_factory: MagicMock, mock_subprocess: MagicMock
    ):
        """Test that package count is calculated correctly."""
        mock_client = MagicMock()
        # 3 actual packages, plus empty lines and comments
        cached = {
            "content": "flask==2.3.0\nrequests==2.31.0\n\npandas==2.0.0\n",
            "hash": "abc123"
        }
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Run - we're mainly checking it doesn't crash
        _install_requirements_from_cache_sync("test-worker")

        assert mock_subprocess.called
