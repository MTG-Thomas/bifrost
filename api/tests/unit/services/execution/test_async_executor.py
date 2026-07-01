from unittest.mock import AsyncMock, patch

import pytest

from src.services.execution import async_executor
from src.services.execution.async_executor import _publish_pending


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
async def test_publish_pending_writes_redis_then_publishes():
    redis = AsyncMock()
    with (
        patch("src.services.execution.async_executor.get_redis_client", return_value=redis),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock()) as q,
        patch("src.services.execution.async_executor.publish_message", new=AsyncMock()) as pub,
    ):
        await _publish_pending(
            execution_id="e1",
            workflow_id="wf",
            parameters={"x": 1},
            org_id="org",
            user_id="u",
            user_name="Name",
            user_email="n@e",
            form_id=None,
            startup=None,
            api_key_id=None,
            sync=False,
            is_platform_admin=False,
            file_path=None,
        )

    redis.set_pending_execution.assert_awaited_once()
    q.assert_awaited_once_with("e1")
    pub.assert_awaited_once()
    queue_name, message = pub.await_args.args
    assert queue_name == "workflow-executions"
    assert message == {"execution_id": "e1", "workflow_id": "wf", "sync": False}


@pytest.mark.asyncio
async def test_publish_pending_emits_enqueue_span(monkeypatch):
    fake_tracer = _FakeTracer()
    monkeypatch.setattr(async_executor, "tracer", fake_tracer)
    redis = AsyncMock()
    with (
        patch("src.services.execution.async_executor.get_redis_client", return_value=redis),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock()),
        patch("src.services.execution.async_executor.publish_message", new=AsyncMock()),
    ):
        await _publish_pending(
            execution_id="e1",
            workflow_id="wf",
            parameters={},
            org_id="org",
            user_id="u",
            user_name="Name",
            user_email="n@e",
            form_id=None,
            startup=None,
            api_key_id=None,
            sync=True,
            is_platform_admin=False,
            file_path="workflows/foo.py",
            event={"source": "topic"},
        )

    assert len(fake_tracer.spans) == 1
    span = fake_tracer.spans[0]
    assert span.name == "bifrost.workflow.enqueue"
    assert span.attributes["bifrost.execution.id"] == "e1"
    assert span.attributes["bifrost.workflow.id"] == "wf"
    assert span.attributes["bifrost.execution.organization_id"] == "org"
    assert span.attributes["bifrost.execution.sync"] is True
    assert span.attributes["bifrost.execution.has_file_path"] is True
    assert span.attributes["bifrost.execution.event.source"] == "topic"
    assert span.attributes["bifrost.execution.enqueue.status"] == "queued"


@pytest.mark.asyncio
async def test_publish_pending_marks_enqueue_span_failed(monkeypatch):
    fake_tracer = _FakeTracer()
    monkeypatch.setattr(async_executor, "tracer", fake_tracer)
    redis = AsyncMock()
    with (
        patch("src.services.execution.async_executor.get_redis_client", return_value=redis),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock(side_effect=RuntimeError("full"))),
        patch("src.services.execution.async_executor.publish_message", new=AsyncMock()),
    ):
        with pytest.raises(RuntimeError):
            await _publish_pending(
                execution_id="e1",
                workflow_id="wf",
                parameters={},
                org_id="org",
                user_id="u",
                user_name="Name",
                user_email="n@e",
                form_id=None,
                startup=None,
                api_key_id=None,
                sync=False,
                is_platform_admin=False,
                file_path=None,
            )

    span = fake_tracer.spans[0]
    assert span.attributes["bifrost.execution.enqueue.status"] == "failed"
    assert span.attributes["bifrost.execution.error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_publish_pending_includes_file_path_when_present():
    redis = AsyncMock()
    with (
        patch("src.services.execution.async_executor.get_redis_client", return_value=redis),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock()),
        patch("src.services.execution.async_executor.publish_message", new=AsyncMock()) as pub,
    ):
        await _publish_pending(
            execution_id="e1",
            workflow_id="wf",
            parameters={},
            org_id="org",
            user_id="u",
            user_name="n",
            user_email="",
            form_id=None,
            startup=None,
            api_key_id=None,
            sync=True,
            is_platform_admin=False,
            file_path="workflows/foo.py",
        )
    _, message = pub.await_args.args
    assert message["file_path"] == "workflows/foo.py"
    assert message["sync"] is True


@pytest.mark.asyncio
async def test_enqueue_system_workflow_execution_defaults_to_provider_org():
    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new=AsyncMock(return_value="exec-1"),
        ) as enqueue,
    ):
        from src.services.execution.async_executor import enqueue_system_workflow_execution

        execution_id = await enqueue_system_workflow_execution(
            workflow_id="wf-1",
            parameters={"apply": True},
            source="Event System",
        )

    assert execution_id == "exec-1"
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["org_id_override"] == "00000000-0000-0000-0000-000000000002"


@pytest.mark.asyncio
async def test_enqueue_system_workflow_execution_preserves_explicit_org():
    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new=AsyncMock(return_value="exec-2"),
        ) as enqueue,
    ):
        from src.services.execution.async_executor import enqueue_system_workflow_execution

        await enqueue_system_workflow_execution(
            workflow_id="wf-1",
            parameters={},
            source="Event System",
            org_id="11111111-1111-1111-1111-111111111111",
        )

    assert enqueue.await_args.kwargs["org_id_override"] == "11111111-1111-1111-1111-111111111111"
