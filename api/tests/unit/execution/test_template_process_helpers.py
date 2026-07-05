from __future__ import annotations

from queue import Empty
from unittest.mock import Mock, patch

import pytest

from src.services.execution.template_process import (
    CMD_FORK,
    CMD_SHUTDOWN,
    TemplateProcess,
    _RecvQueue,
    _SendQueue,
    _reap_exited_children,
)


class FakeConnection:
    def __init__(self, *, items=None, poll_result=True, close_error: OSError | None = None):
        self.items = list(items or [])
        self.poll_result = poll_result
        self.close_error = close_error
        self.sent = []
        self.closed = False
        self.poll_calls = []

    def send(self, item):
        self.sent.append(item)

    def recv(self):
        return self.items.pop(0)

    def poll(self, timeout=0):
        self.poll_calls.append(timeout)
        return self.poll_result

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


def test_send_queue_sends_and_ignores_close_errors() -> None:
    conn = FakeConnection(close_error=OSError("already closed"))
    queue = _SendQueue(conn)

    queue.put("one")
    queue.put_nowait("two")
    queue.close()

    assert conn.sent == ["one", "two"]
    assert conn.closed is True


def test_recv_queue_gets_items_and_raises_empty_for_nonblocking_miss() -> None:
    conn = FakeConnection(items=["result"], poll_result=True)
    queue = _RecvQueue(conn)

    assert queue.get(timeout=0.25) == "result"
    assert conn.poll_calls == [0.25]

    empty = _RecvQueue(FakeConnection(poll_result=False))
    with pytest.raises(Empty):
        empty.get(block=False)


def test_recv_queue_ignores_close_errors() -> None:
    conn = FakeConnection(close_error=OSError("already closed"))

    _RecvQueue(conn).close()

    assert conn.closed is True


def test_reap_exited_children_stops_on_no_children_and_oserror() -> None:
    with patch("src.services.execution.template_process.os.waitpid", side_effect=ChildProcessError):
        _reap_exited_children()

    with patch("src.services.execution.template_process.os.waitpid", side_effect=OSError("wait failed")):
        _reap_exited_children()


def test_template_process_fork_requires_live_template() -> None:
    template = TemplateProcess()

    with pytest.raises(RuntimeError, match="not running"):
        template.fork()


def test_template_process_start_timeout_kills_process() -> None:
    parent_conn = FakeConnection(poll_result=False)
    child_conn = FakeConnection()
    process = Mock()
    process.pid = 1234
    process.is_alive.return_value = True

    ctx = Mock()
    ctx.Process.return_value = process

    with (
        patch("src.services.execution.template_process.multiprocessing.get_context", return_value=ctx),
        patch("src.services.execution.template_process.multiprocessing.Pipe", return_value=(parent_conn, child_conn)),
    ):
        template = TemplateProcess()
        with pytest.raises(RuntimeError, match="failed to start"):
            template.start()

    process.start.assert_called_once()
    process.kill.assert_called_once()


def test_template_process_start_error_message_raises() -> None:
    parent_conn = FakeConnection(items=[{"status": "error", "error": "boom"}], poll_result=True)
    child_conn = FakeConnection()
    process = Mock()
    process.pid = 1234

    ctx = Mock()
    ctx.Process.return_value = process

    with (
        patch("src.services.execution.template_process.multiprocessing.get_context", return_value=ctx),
        patch("src.services.execution.template_process.multiprocessing.Pipe", return_value=(parent_conn, child_conn)),
    ):
        template = TemplateProcess()
        with pytest.raises(RuntimeError, match="startup failed: boom"):
            template.start()


def test_template_process_fork_sends_command_and_wraps_queues() -> None:
    control = FakeConnection(items=[{"status": "forked", "child_pid": 4321}], poll_result=True)
    work_recv = FakeConnection()
    work_send = FakeConnection()
    result_recv = FakeConnection()
    result_send = FakeConnection()
    template = TemplateProcess()
    template._pipe = control
    template._process = Mock()
    template._process.is_alive.return_value = True

    with patch(
        "src.services.execution.template_process.multiprocessing.Pipe",
        side_effect=[(work_recv, work_send), (result_recv, result_send)],
    ):
        child_pid, work_queue, result_queue = template.fork(
            worker_id="worker-1",
            persistent=True,
        )

    assert child_pid == 4321
    assert isinstance(work_queue, _SendQueue)
    assert isinstance(result_queue, _RecvQueue)
    assert control.sent == [
        {
            "action": CMD_FORK,
            "worker_id": "worker-1",
            "persistent": True,
            "work_recv": work_recv,
            "result_send": result_send,
        }
    ]
    assert work_recv.closed is True
    assert result_send.closed is True


def test_template_process_shutdown_sends_command_and_clears_state() -> None:
    control = FakeConnection()
    process = Mock()
    process.is_alive.return_value = False
    template = TemplateProcess()
    template._pipe = control
    template._process = process
    template.pid = 1234

    template.shutdown()

    assert control.sent == [{"action": CMD_SHUTDOWN}]
    process.join.assert_called_once_with(timeout=10)
    process.kill.assert_not_called()
    assert template._pipe is None
    assert template._process is None
    assert template.pid is None
