from __future__ import annotations

import sys
import types
from queue import Empty
from unittest.mock import Mock, patch

import pytest

from src.services.execution.template_process import (
    CMD_FORK,
    CMD_SHUTDOWN,
    TemplateProcess,
    _RecvQueue,
    _SendQueue,
    _load_execution_infrastructure,
    _reap_children,
    _run_forked_child,
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


def test_recv_queue_timeout_miss_raises_empty() -> None:
    conn = FakeConnection(poll_result=False)
    queue = _RecvQueue(conn)

    with pytest.raises(Empty):
        queue.get(timeout=0.25)

    assert conn.poll_calls == [0.25]


def test_recv_queue_ignores_close_errors() -> None:
    conn = FakeConnection(close_error=OSError("already closed"))

    _RecvQueue(conn).close()

    assert conn.closed is True


def test_reap_children_stops_on_no_children_and_oserror() -> None:
    exit_statuses: dict[int, int] = {}
    with (
        patch("src.services.execution.template_process.os.WNOHANG", 1, create=True),
        patch("src.services.execution.template_process.os.waitpid", side_effect=ChildProcessError),
    ):
        _reap_children(exit_statuses)

    with (
        patch("src.services.execution.template_process.os.WNOHANG", 1, create=True),
        patch("src.services.execution.template_process.os.waitpid", side_effect=OSError("wait failed")),
    ):
        _reap_children(exit_statuses)

    assert exit_statuses == {}


def test_reap_children_drains_exited_children() -> None:
    exit_statuses: dict[int, int] = {}
    with (
        patch("src.services.execution.template_process.os.WNOHANG", 1, create=True),
        patch(
            "src.services.execution.template_process.os.waitpid",
            side_effect=[(101, 0), (102, 0), (0, 0)],
        ) as waitpid,
    ):
        _reap_children(exit_statuses)

    assert waitpid.call_count == 3
    assert exit_statuses == {101: 0, 102: 0}


def test_load_execution_infrastructure_installs_requirements_and_user_site(monkeypatch) -> None:
    install = Mock()
    hook = Mock()
    user_site = "C:/fake-user-site"
    simple_worker = types.SimpleNamespace(install_requirements=install)
    virtual_import = types.SimpleNamespace(install_virtual_import_hook=hook)

    monkeypatch.setitem(sys.modules, "src.services.execution.simple_worker", simple_worker)
    monkeypatch.setitem(sys.modules, "src.services.execution.virtual_import", virtual_import)
    monkeypatch.setattr("site.ENABLE_USER_SITE", True)
    monkeypatch.setattr("site.getusersitepackages", lambda: user_site)
    monkeypatch.setattr(
        "src.services.execution.template_process.os.path.exists",
        lambda path: path == user_site,
    )
    monkeypatch.setattr("src.services.execution.template_process.sys.path", [])

    deferred_hook = _load_execution_infrastructure(install_requirements_on_startup=True)

    install.assert_called_once()
    hook.assert_not_called()
    assert deferred_hook is hook
    assert sys.path[0] == user_site


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


def test_template_process_start_success_sets_pid() -> None:
    parent_conn = FakeConnection(items=[{"status": "ready", "pid": 5678}], poll_result=True)
    child_conn = FakeConnection()
    process = Mock()
    process.pid = 1234

    ctx = Mock()
    ctx.Process.return_value = process

    with (
        patch("src.services.execution.template_process.multiprocessing.get_context", return_value=ctx),
        patch("src.services.execution.template_process.multiprocessing.Pipe", return_value=(parent_conn, child_conn)),
    ):
        template = TemplateProcess(install_requirements_on_startup=False)
        template.start()

    process.start.assert_called_once()
    assert template.pid == 5678


def test_template_process_start_is_noop_when_already_alive() -> None:
    process = Mock()
    process.is_alive.return_value = True
    template = TemplateProcess()
    template._process = process
    template.pid = 1234

    template.start()

    process.start.assert_not_called()
    assert template.pid == 1234


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


def test_template_process_fork_timeout_raises() -> None:
    control = FakeConnection(poll_result=False)
    template = TemplateProcess()
    template._pipe = control
    template._process = Mock()
    template._process.is_alive.return_value = True

    with patch(
        "src.services.execution.template_process.multiprocessing.Pipe",
        side_effect=[
            (FakeConnection(), FakeConnection()),
            (FakeConnection(), FakeConnection()),
        ],
    ):
        with pytest.raises(RuntimeError, match="did not respond"):
            template.fork(worker_id="worker-timeout")


def test_template_process_fork_unexpected_response_raises() -> None:
    control = FakeConnection(items=[{"status": "error"}], poll_result=True)
    template = TemplateProcess()
    template._pipe = control
    template._process = Mock()
    template._process.is_alive.return_value = True

    with patch(
        "src.services.execution.template_process.multiprocessing.Pipe",
        side_effect=[
            (FakeConnection(), FakeConnection()),
            (FakeConnection(), FakeConnection()),
        ],
    ):
        with pytest.raises(RuntimeError, match="Unexpected fork response"):
            template.fork(worker_id="worker-error")


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


def test_template_process_shutdown_kills_process_that_stays_alive() -> None:
    control = FakeConnection()
    process = Mock()
    process.is_alive.side_effect = [True, False]
    template = TemplateProcess()
    template._pipe = control
    template._process = process
    template.pid = 1234

    template.shutdown()

    assert control.sent == [{"action": CMD_SHUTDOWN}]
    process.join.assert_any_call(timeout=10)
    process.kill.assert_called_once()
    process.join.assert_any_call(timeout=5)
    assert template._pipe is None
    assert template._process is None
    assert template.pid is None


def test_run_forked_child_reports_success_with_rss(monkeypatch) -> None:
    context = {"organization_id": "org-1"}
    work_recv = FakeConnection(items=[("exec-12345678", context)], poll_result=True)
    result_send = FakeConnection()
    simple_worker = types.SimpleNamespace(
        _execute_sync=Mock(
            return_value={
                "execution_id": "exec-12345678",
                "success": True,
                "metrics": {},
            }
        ),
        _get_process_rss=Mock(return_value=4096),
    )
    logging_module = types.SimpleNamespace(
        clear_sequence_counter=Mock(),
    )

    monkeypatch.setitem(sys.modules, "src.services.execution.simple_worker", simple_worker)
    monkeypatch.setitem(sys.modules, "bifrost._logging", logging_module)

    _run_forked_child(work_recv, result_send, "worker-1", persistent=False)

    simple_worker._execute_sync.assert_called_once_with(
        "exec-12345678", "worker-1", context
    )
    logging_module.clear_sequence_counter.assert_called_once_with("exec-12345678")
    assert result_send.sent == [
        {
            "execution_id": "exec-12345678",
            "success": True,
            "metrics": {"process_rss_bytes": 4096},
            "process_rss_bytes": 4096,
        }
    ]


def test_run_forked_child_sends_error_result_for_execution_failure(monkeypatch) -> None:
    work_recv = FakeConnection(items=[("exec-failure", {})], poll_result=True)
    result_send = FakeConnection()
    simple_worker = types.SimpleNamespace(
        _execute_sync=Mock(side_effect=ValueError("boom")),
    )

    monkeypatch.setitem(sys.modules, "src.services.execution.simple_worker", simple_worker)

    _run_forked_child(work_recv, result_send, "worker-err", persistent=False)

    assert result_send.sent == [
        {
            "execution_id": "exec-failure",
            "success": False,
            "error": "boom",
            "error_type": "ValueError",
            "duration_ms": 0,
            "worker_id": "worker-err",
        }
    ]
