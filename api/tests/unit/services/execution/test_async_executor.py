from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock, patch

import pytest

from src.services.execution import async_executor
from src.services.execution.async_executor import (
    _dispatch_request_identity,
    _pending_dispatch_envelope,
    _persist_execution_pin,
    _publish_pending,
    _validated_pending_dispatch,
    enqueue_workflow_execution_once,
)
from src.services.solutions.deployment_manifest import canonical_json, sha256_digest


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
async def test_retry_reuses_execution_without_republishing():
    context = AsyncMock()
    context.solution_deployment_id = None
    context.event = None

    with (
        patch(
            "src.services.execution.async_executor._persist_execution_pin",
            new=AsyncMock(return_value=({}, False)),
        ),
        patch(
            "src.services.execution.async_executor._publish_scheduled_once",
            new_callable=AsyncMock,
        ) as publish,
    ):
        execution_id, reused = await enqueue_workflow_execution_once(
            context=context,
            workflow_id="11111111-1111-1111-1111-111111111111",
            parameters={"ticket_id": 42},
            execution_id="22222222-2222-2222-2222-222222222222",
        )

    assert execution_id == "22222222-2222-2222-2222-222222222222"
    assert reused is True
    publish.assert_awaited_once()


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        solution_deployment_id=None,
        event=None,
        org_id="33333333-3333-3333-3333-333333333333",
        user_id="44444444-4444-4444-4444-444444444444",
        name="Operator",
        email="operator@example.test",
        startup=None,
        form_inputs={"field": "value"},
        embed={"ticket_id": "1001"},
        is_platform_admin=True,
        is_provider_org=False,
        is_external=False,
    )


def test_pending_dispatch_replays_durable_runtime_and_rejects_parameter_drift():
    context = _context()
    execution_id = "22222222-2222-2222-2222-222222222222"
    workflow_id = "11111111-1111-1111-1111-111111111111"
    request = _dispatch_request_identity(
        context,
        execution_id,
        workflow_id,
        {"ticket_id": 42},
        form_id=None,
        sync=False,
        api_key_id=None,
        file_path="features/demo.py",
        org_id_override=None,
    )
    old_runtime = {"workspace_release_id": "sha256:" + "a" * 64}
    envelope = _pending_dispatch_envelope(
        request,
        solution_deployment_id=None,
        runtime_evidence=old_runtime,
        runtime_mode="workspace-release-v1",
    )
    execution = SimpleNamespace(
        id=UUID(execution_id),
        workflow_id=UUID(workflow_id),
        parameters={"ticket_id": 42},
        organization_id=UUID(context.org_id),
        executed_by=UUID(context.user_id),
        executed_by_name=context.name,
        form_id=None,
        api_key_id=None,
        runtime_evidence=old_runtime,
        runtime_evidence_hash=sha256_digest(canonical_json(old_runtime)),
        runtime_mode="workspace-release-v1",
        solution_deployment_id=None,
        dispatch_evidence=envelope,
        dispatch_evidence_hash=sha256_digest(canonical_json(envelope)),
    )

    publish = _validated_pending_dispatch(execution, request)

    assert publish["runtime_evidence"] == old_runtime
    changed = {**request, "parameters": {"ticket_id": 99}}
    with pytest.raises(ValueError, match="different dispatch evidence"):
        _validated_pending_dispatch(execution, changed)


@pytest.mark.asyncio
async def test_existing_execution_is_rehydrated_before_current_runtime_is_pinned(
    monkeypatch,
):
    context = _context()
    execution_id = "22222222-2222-2222-2222-222222222222"
    workflow_id = "11111111-1111-1111-1111-111111111111"
    request = _dispatch_request_identity(
        context,
        execution_id,
        workflow_id,
        {"ticket_id": 42},
        form_id=None,
        sync=False,
        api_key_id=None,
        file_path=None,
        org_id_override=None,
    )
    runtime = {"workspace_release_id": "sha256:" + "a" * 64}
    execution = SimpleNamespace(
        id=UUID(execution_id),
        workflow_id=UUID(workflow_id),
        parameters={"ticket_id": 42},
        organization_id=UUID(context.org_id),
        executed_by=UUID(context.user_id),
        executed_by_name=context.name,
        form_id=None,
        api_key_id=None,
        runtime_evidence=runtime,
        runtime_evidence_hash=sha256_digest(canonical_json(runtime)),
        runtime_mode="workspace-release-v1",
        solution_deployment_id=None,
        dispatch_evidence=_pending_dispatch_envelope(
            request,
            solution_deployment_id=None,
            runtime_evidence=runtime,
            runtime_mode="workspace-release-v1",
        ),
        dispatch_evidence_hash=None,
    )
    execution.dispatch_evidence_hash = sha256_digest(
        canonical_json(execution.dispatch_evidence)
    )

    class Database:
        async def execute(self, _statement, _parameters=None):
            return None

        async def get(self, _model, identity):
            assert identity == UUID(execution_id)
            return execution

    @asynccontextmanager
    async def db_context():
        yield Database()

    deployment_pin = AsyncMock(
        side_effect=AssertionError("retry must not resolve current runtime")
    )
    workspace_pin = AsyncMock(
        side_effect=AssertionError("retry must not resolve current runtime")
    )
    monkeypatch.setattr("src.core.database.get_db_context", db_context)
    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.pin_workflow_runtime",
        deployment_pin,
    )
    monkeypatch.setattr(
        "src.services.workspace_release_runtime.pin_workspace_runtime",
        workspace_pin,
    )

    publish, created = await _persist_execution_pin(
        context,
        execution_id,
        workflow_id,
        {"ticket_id": 42},
        None,
        form_id=None,
        sync=False,
        api_key_id=None,
        file_path=None,
    )

    assert created is False
    assert publish["runtime_evidence"] == runtime
    deployment_pin.assert_not_awaited()
    workspace_pin.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_pending_writes_redis_then_publishes():
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client", return_value=redis
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue", new=AsyncMock()
        ) as q,
        patch(
            "src.services.execution.async_executor.publish_message", new=AsyncMock()
        ) as pub,
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
            form_inputs={"field": "value"},
            embed={"ticket_id": "1001"},
            api_key_id=None,
            sync=False,
            is_platform_admin=False,
            is_provider_org=False,
            is_external=True,
            file_path=None,
        )

    redis.set_pending_execution.assert_awaited_once()
    assert redis.set_pending_execution.await_args.kwargs["is_provider_org"] is False
    assert redis.set_pending_execution.await_args.kwargs["is_external"] is True
    assert redis.set_pending_execution.await_args.kwargs["form_inputs"] == {
        "field": "value"
    }
    assert redis.set_pending_execution.await_args.kwargs["embed"] == {
        "ticket_id": "1001"
    }
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
        patch(
            "src.services.execution.async_executor.get_redis_client", return_value=redis
        ),
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
            form_inputs={},
            embed={},
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
        patch(
            "src.services.execution.async_executor.get_redis_client", return_value=redis
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue",
            new=AsyncMock(side_effect=RuntimeError("full")),
        ),
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
                form_inputs={},
                embed={},
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
        patch(
            "src.services.execution.async_executor.get_redis_client", return_value=redis
        ),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock()),
        patch(
            "src.services.execution.async_executor.publish_message", new=AsyncMock()
        ) as pub,
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
            form_inputs={},
            embed={},
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
        from src.services.execution.async_executor import (
            enqueue_system_workflow_execution,
        )

        execution_id = await enqueue_system_workflow_execution(
            workflow_id="wf-1",
            parameters={"apply": True},
            source="Event System",
        )

    assert execution_id == "exec-1"
    enqueue.assert_awaited_once()
    assert (
        enqueue.await_args.kwargs["org_id_override"]
        == "00000000-0000-0000-0000-000000000002"
    )


@pytest.mark.asyncio
async def test_enqueue_system_workflow_execution_preserves_explicit_org():
    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new=AsyncMock(return_value="exec-2"),
        ) as enqueue,
    ):
        from src.services.execution.async_executor import (
            enqueue_system_workflow_execution,
        )

        await enqueue_system_workflow_execution(
            workflow_id="wf-1",
            parameters={},
            source="Event System",
            org_id="11111111-1111-1111-1111-111111111111",
        )

    assert (
        enqueue.await_args.kwargs["org_id_override"]
        == "11111111-1111-1111-1111-111111111111"
    )
