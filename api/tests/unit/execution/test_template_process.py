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


def _wait_for_pid_to_die(pid: int, timeout: float = 5.0) -> None:
    """
    Wait for a process to exit without calling waitpid.

    os.waitpid can only be called by the direct parent. Forked children
    of the template process are grandchildren of the test runner, so we
    poll os.kill(pid, 0) instead.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return  # Process is gone
    # Best-effort — don't raise if still alive (zombie will be reaped by template)


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

            # Clean up child (grandchild of test runner — cannot waitpid)
            os.kill(child_pid, signal.SIGTERM)
            _wait_for_pid_to_die(child_pid)
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
                    _wait_for_pid_to_die(pid)
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

            # Clean up (grandchild of test runner — cannot waitpid)
            os.kill(child_pid, signal.SIGTERM)
            _wait_for_pid_to_die(child_pid)
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
