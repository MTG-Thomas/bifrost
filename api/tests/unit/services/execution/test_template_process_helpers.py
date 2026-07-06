from __future__ import annotations

from queue import Empty

import pytest

from src.services.execution import template_process


class _FakeConnection:
    def __init__(self, values=None, *, close_error: OSError | None = None):
        self.values = list(values or [])
        self.sent = []
        self.closed = False
        self.close_error = close_error

    def send(self, item):
        self.sent.append(item)

    def poll(self, timeout=0):
        return bool(self.values)

    def recv(self):
        return self.values.pop(0)

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


def test_send_queue_delegates_put_and_ignores_close_errors():
    conn = _FakeConnection(close_error=OSError("already closed"))
    queue = template_process._SendQueue(conn)

    queue.put({"execution_id": "exec-1"})
    queue.put_nowait({"execution_id": "exec-2"})
    queue.close()

    assert conn.sent == [{"execution_id": "exec-1"}, {"execution_id": "exec-2"}]
    assert conn.closed is True


def test_recv_queue_get_modes_and_close_error():
    conn = _FakeConnection(["first", "second"], close_error=OSError("already closed"))
    queue = template_process._RecvQueue(conn)

    assert queue.get(block=False) == "first"
    assert queue.get(timeout=0.01) == "second"
    with pytest.raises(Empty):
        queue.get(block=False)
    with pytest.raises(Empty):
        queue.get(timeout=0.01)

    queue.close()
    assert conn.closed is True


def test_flush_child_telemetry_logs_failure(monkeypatch, caplog):
    monkeypatch.setattr(
        "src.core.telemetry.flush_opentelemetry",
        lambda: (_ for _ in ()).throw(RuntimeError("flush failed")),
    )

    template_process._flush_child_telemetry()

    assert "OpenTelemetry child flush failed: flush failed" in caplog.text


def test_template_process_is_alive_false_before_start():
    proc = template_process.TemplateProcess()

    assert proc.is_alive() is False


def test_template_process_shutdown_clears_state_with_dead_process():
    class DeadProcess:
        def __init__(self):
            self.join_calls = []
            self.kill_calls = 0

        def join(self, timeout=None):
            self.join_calls.append(timeout)

        def is_alive(self):
            return False

        def kill(self):
            self.kill_calls += 1

    pipe = _FakeConnection(close_error=OSError("closed"))
    process = DeadProcess()
    proc = template_process.TemplateProcess()
    proc._pipe = pipe
    proc._process = process
    proc.pid = 123

    proc.shutdown()

    assert pipe.sent == [{"action": template_process.CMD_SHUTDOWN}]
    assert process.join_calls == [10]
    assert process.kill_calls == 0
    assert proc._pipe is None
    assert proc._process is None
    assert proc.pid is None
