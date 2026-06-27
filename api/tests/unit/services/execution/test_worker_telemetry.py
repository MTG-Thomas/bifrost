import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.models.enums import ExecutionStatus


if "resource" not in sys.modules:
    resource = ModuleType("resource")
    resource.RUSAGE_SELF = 0
    resource.getrusage = lambda _who: SimpleNamespace(ru_maxrss=123, ru_utime=1.0, ru_stime=0.5)
    sys.modules["resource"] = resource

from src.services.execution import worker  # noqa: E402


class _FakeSpan:
    def __init__(self, name: str, attributes: dict):
        self.name = name
        self.attributes = dict(attributes)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def set_attribute(self, key: str, value):
        self.attributes[key] = value


class _FakeTracer:
    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def start_as_current_span(self, name: str, attributes: dict):
        span = _FakeSpan(name, attributes)
        self.spans.append(span)
        return span


@pytest.mark.asyncio
async def test_run_execution_emits_worker_span(monkeypatch):
    fake_tracer = _FakeTracer()
    monkeypatch.setattr(worker, "tracer", fake_tracer)

    result = SimpleNamespace(
        status=ExecutionStatus.SUCCESS,
        result={"ok": True},
        duration_ms=42,
        logs=[],
        variables={},
        integration_calls=[],
        roi=None,
        error_message=None,
        error_type=None,
        cached=False,
        cache_expires_at=None,
        execution_context={"execution_id": "exec-1"},
    )

    context_data = {
        "code": "result = {'ok': True}",
        "name": "script_one",
        "caller": {"user_id": "user-1", "email": "user@example.test", "name": "User One"},
        "organization": {"id": "org-1", "name": "Org One"},
        "parameters": {"x": 1},
        "tags": ["workflow"],
        "timeout_seconds": 30,
        "cache_ttl_seconds": 300,
        "transient": False,
        "no_cache": False,
        "is_platform_admin": False,
    }

    with (
        patch("bifrost.credentials.is_token_expired", return_value=False),
        patch("src.core.module_cache_sync.set_solution_context"),
        patch("src.core.module_cache_sync.clear_solution_context"),
        patch("src.services.execution.engine.execute", new=AsyncMock(return_value=result)),
    ):
        payload = await worker._run_execution("exec-1", context_data)

    assert payload["status"] == ExecutionStatus.SUCCESS.value
    assert len(fake_tracer.spans) == 1
    span = fake_tracer.spans[0]
    assert span.name == "bifrost.worker.execute"
    assert span.attributes["bifrost.execution.id"] == "exec-1"
    assert span.attributes["bifrost.workflow.name"] == "script_one"
    assert span.attributes["bifrost.execution.organization_id"] == "org-1"
    assert span.attributes["bifrost.worker.is_script"] is True
    assert span.attributes["bifrost.worker.has_file_path"] is False
    assert span.attributes["bifrost.worker.status"] == ExecutionStatus.SUCCESS.value
    assert span.attributes["bifrost.worker.duration_ms"] == 42
    assert span.attributes["bifrost.worker.peak_memory_bytes"] >= 0
    assert span.attributes["bifrost.worker.cpu_total_seconds"] >= 0
