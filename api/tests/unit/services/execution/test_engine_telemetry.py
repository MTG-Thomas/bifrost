from __future__ import annotations

import pytest

from src.models.enums import ExecutionStatus
from src.sdk.context import ExecutionContext, Organization
from src.services.execution import engine


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


class _FakeCounter:
    def __init__(self):
        self.calls: list[tuple[int, dict]] = []

    def add(self, value: int, *, attributes: dict):
        self.calls.append((value, dict(attributes)))


class _FakeHistogram:
    def __init__(self):
        self.calls: list[tuple[float, dict]] = []

    def record(self, value: float, *, attributes: dict):
        self.calls.append((value, dict(attributes)))


def _context() -> ExecutionContext:
    return ExecutionContext(
        user_id="user-1",
        email="user@example.com",
        name="User One",
        scope="org-1",
        organization=Organization(id="org-1", name="Org One"),
        is_platform_admin=False,
        is_function_key=False,
        execution_id="exec-1",
        workflow_name="status_snapshot",
    )


@pytest.mark.asyncio
async def test_workflow_execution_emits_success_span(monkeypatch):
    fake_tracer = _FakeTracer()
    fake_counter = _FakeCounter()
    fake_duration = _FakeHistogram()
    monkeypatch.setattr(engine, "tracer", fake_tracer)
    monkeypatch.setattr(engine, "workflow_execution_counter", fake_counter)
    monkeypatch.setattr(engine, "workflow_execution_duration", fake_duration)

    async def workflow(context):
        return {"ok": True}

    result, _, _ = await engine._execute_workflow_with_trace(
        workflow,
        _context(),
        {},
        execution_id="exec-1",
    )

    assert result == {"ok": True}
    assert len(fake_tracer.spans) == 1
    span = fake_tracer.spans[0]
    assert span.name == "bifrost.workflow.execute"
    assert span.attributes["bifrost.execution.id"] == "exec-1"
    assert span.attributes["bifrost.workflow.name"] == "status_snapshot"
    assert span.attributes["bifrost.workflow.function"] == "workflow"
    assert span.attributes["bifrost.execution.organization_id"] == "org-1"
    assert span.attributes["bifrost.execution.status"] == ExecutionStatus.SUCCESS.value
    assert fake_counter.calls == [
        (
            1,
            {
                "bifrost.workflow.name": "status_snapshot",
                "bifrost.workflow.function": "workflow",
                "bifrost.execution.scope": "org-1",
                "bifrost.execution.is_platform_admin": False,
                "bifrost.execution.status": ExecutionStatus.SUCCESS.value,
            },
        )
    ]
    assert len(fake_duration.calls) == 1
    duration, attributes = fake_duration.calls[0]
    assert duration >= 0
    assert attributes["bifrost.execution.status"] == ExecutionStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_workflow_execution_emits_failed_span(monkeypatch):
    fake_tracer = _FakeTracer()
    fake_counter = _FakeCounter()
    fake_duration = _FakeHistogram()
    monkeypatch.setattr(engine, "tracer", fake_tracer)
    monkeypatch.setattr(engine, "workflow_execution_counter", fake_counter)
    monkeypatch.setattr(engine, "workflow_execution_duration", fake_duration)

    async def workflow(context):
        raise RuntimeError("boom")

    with pytest.raises(Exception):
        await engine._execute_workflow_with_trace(
            workflow,
            _context(),
            {},
        )

    assert len(fake_tracer.spans) == 1
    span = fake_tracer.spans[0]
    assert span.attributes["bifrost.execution.id"] == "exec-1"
    assert span.attributes["bifrost.execution.status"] == ExecutionStatus.FAILED.value
    assert span.attributes["bifrost.execution.error_type"] == "RuntimeError"
    assert fake_counter.calls == [
        (
            1,
            {
                "bifrost.workflow.name": "status_snapshot",
                "bifrost.workflow.function": "workflow",
                "bifrost.execution.scope": "org-1",
                "bifrost.execution.is_platform_admin": False,
                "bifrost.execution.status": ExecutionStatus.FAILED.value,
            },
        )
    ]
    assert len(fake_duration.calls) == 1
    duration, attributes = fake_duration.calls[0]
    assert duration >= 0
    assert attributes["bifrost.execution.status"] == ExecutionStatus.FAILED.value
