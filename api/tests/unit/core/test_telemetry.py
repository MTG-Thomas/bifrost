from src.core import telemetry


class _FallbackFlushRaises:
    def __init__(self):
        self.calls: list[tuple[str, int | None]] = []

    def force_flush(self, timeout_millis=None):
        self.calls.append(("force_flush", timeout_millis))
        if timeout_millis is not None:
            raise TypeError("timeout unsupported")
        raise RuntimeError("fallback failed")


class _FlushSucceeds:
    def __init__(self):
        self.calls: list[int | None] = []

    def force_flush(self, timeout_millis=None):
        self.calls.append(timeout_millis)


def test_flush_opentelemetry_logs_fallback_failure_and_continues(monkeypatch, caplog):
    metric_provider = _FallbackFlushRaises()
    trace_provider = _FlushSucceeds()
    monkeypatch.setattr(telemetry, "_meter_provider", metric_provider)
    monkeypatch.setattr(telemetry, "_trace_provider", trace_provider)

    telemetry.flush_opentelemetry(timeout_millis=123)

    assert metric_provider.calls == [("force_flush", 123), ("force_flush", None)]
    assert trace_provider.calls == [123]
    assert "OpenTelemetry metric flush failed: fallback failed" in caplog.text
