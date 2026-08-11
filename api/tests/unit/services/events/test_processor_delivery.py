import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.enums import EventDeliveryStatus, EventStatus
from src.services.events import processor as p
from src.services.webhooks.protocol import Deliver, Rejected, ValidationResponse, WebhookRequest


def _make_event(event_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=event_id or uuid.uuid4(),
        event_source_id=uuid.uuid4(),
        event_type="ticket.created",
        organization_id=uuid.uuid4(),
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        headers={},
        data={},
        source_ip="10.0.0.5",
        status=EventStatus.PROCESSING,
        event_source=SimpleNamespace(
            id=uuid.uuid4(),
            organization_id=None,
            schedule_source=None,
        ),
    )


def _make_delivery(
    *,
    status: EventDeliveryStatus = EventDeliveryStatus.PENDING,
    event: SimpleNamespace | None = None,
    target_type: str = "workflow",
) -> SimpleNamespace:
    workflow_id = uuid.uuid4()
    subscription = SimpleNamespace(
        target_type=target_type,
        agent_id=uuid.uuid4() if target_type == "agent" else None,
        input_mapping=None,
        agent=None,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        event_id=event.id if event else uuid.uuid4(),
        event=event,
        subscription=subscription,
        workflow_id=workflow_id if target_type == "workflow" else None,
        workflow=SimpleNamespace(id=workflow_id, organization_id=None)
        if target_type == "workflow"
        else None,
        execution_id=uuid.uuid4(),
        status=status,
        error_message=None,
        attempt_count=0,
        completed_at=None,
    )


def _make_processor(event: SimpleNamespace | None, deliveries: list[SimpleNamespace]):
    session = AsyncMock()
    processor = p.EventProcessor(session)
    processor._event_repo = AsyncMock()
    processor._event_repo.get_by_id = AsyncMock(return_value=event)
    processor._delivery_repo = AsyncMock()
    processor._delivery_repo.get_by_event = AsyncMock(return_value=deliveries)
    processor._broadcast_event_update = AsyncMock()
    return processor, session


@pytest.mark.asyncio
async def test_queue_event_deliveries_returns_zero_when_event_missing():
    delivery = _make_delivery()
    processor, session = _make_processor(event=None, deliveries=[delivery])

    queued = await processor.queue_event_deliveries(uuid.uuid4())

    assert queued == 0
    assert delivery.status == EventDeliveryStatus.PENDING
    session.flush.assert_not_awaited()
    processor._broadcast_event_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_event_deliveries_marks_failed_when_queueing_raises():
    event = _make_event()
    delivery = _make_delivery(event=event)
    processor, session = _make_processor(event=event, deliveries=[delivery])
    processor._queue_workflow_execution = AsyncMock(
        side_effect=RuntimeError("queue is unavailable")
    )

    queued = await processor.queue_event_deliveries(event.id)

    assert queued == 0
    assert delivery.status == EventDeliveryStatus.FAILED
    assert delivery.error_message == "queue is unavailable"
    session.flush.assert_awaited_once()
    processor._broadcast_event_update.assert_awaited_once_with(
        event_source_id=event.event_source_id,
        event=event,
        update_type="deliveries_queued",
        success_count=0,
        failed_count=1,
        queued_count=0,
        pending_count=0,
    )


@pytest.mark.asyncio
async def test_queue_event_deliveries_routes_pending_workflow_and_agent_deliveries():
    event = _make_event()
    workflow_delivery = _make_delivery(event=event, target_type="workflow")
    agent_delivery = _make_delivery(event=event, target_type="agent")
    already_done = _make_delivery(status=EventDeliveryStatus.SUCCESS, event=event)
    processor, session = _make_processor(
        event=event,
        deliveries=[workflow_delivery, agent_delivery, already_done],
    )
    processor._queue_workflow_execution = AsyncMock()
    processor._queue_agent_run = AsyncMock()

    queued = await processor.queue_event_deliveries(event.id)

    assert queued == 2
    assert workflow_delivery.status == EventDeliveryStatus.QUEUED
    assert agent_delivery.status == EventDeliveryStatus.QUEUED
    assert already_done.status == EventDeliveryStatus.SUCCESS
    processor._queue_workflow_execution.assert_awaited_once_with(
        workflow_delivery,
        event,
    )
    processor._queue_agent_run.assert_awaited_once_with(agent_delivery, event)
    session.flush.assert_awaited_once()
    processor._broadcast_event_update.assert_awaited_once_with(
        event_source_id=event.event_source_id,
        event=event,
        update_type="deliveries_queued",
        success_count=1,
        failed_count=0,
        queued_count=2,
        pending_count=0,
    )


def test_delivery_status_from_execution_maps_known_and_unknown_statuses():
    assert p._delivery_status_from_execution("Success") == EventDeliveryStatus.SUCCESS
    assert p._delivery_status_from_execution("Failed") == EventDeliveryStatus.FAILED
    assert p._delivery_status_from_execution("Timeout") == EventDeliveryStatus.FAILED
    assert p._delivery_status_from_execution("Cancelled") == EventDeliveryStatus.FAILED
    assert p._delivery_status_from_execution("Unexpected") == EventDeliveryStatus.FAILED


def test_delivery_failure_target_uses_agent_id_for_agent_subscriptions():
    delivery = _make_delivery(target_type="agent")

    target_type, target_id = p._delivery_failure_target(delivery)

    assert target_type == "agent"
    assert target_id == delivery.subscription.agent_id


def test_render_template_preserves_single_value_types_and_substitutes_mixed_text():
    context = {
        "payload": {
            "ticket": {"id": 123, "title": "Cannot log in"},
            "tags": ["urgent", "vip"],
        }
    }

    assert p._render_template("{{ payload.ticket.id }}", context) == 123
    assert p._render_template("Ticket {{ payload.ticket.id }}: {{ payload.ticket.title }}", context) == (
        "Ticket 123: Cannot log in"
    )
    assert p._render_template("{{ payload.missing }}", context) == "{{ payload.missing }}"
    assert p._render_template("tags={{ payload.tags }}", context) == "tags=['urgent', 'vip']"


def test_process_input_mapping_builds_event_parameters_with_schedule_context():
    event = _make_event()
    event.headers = {"x-source": "halo"}
    event.data = {"ticket": {"id": 123, "summary": "Cannot log in"}}
    event.event_source.schedule_source = SimpleNamespace()
    event.event_source.schedule_source.cron_expression = "0 6 * * *"
    subscription = SimpleNamespace(id=uuid.uuid4())

    mapped = p._process_input_mapping(
        {
            "ticket_id": "{{ payload.ticket.id }}",
            "summary": "Ticket {{ payload.ticket.summary }}",
            "source": "{{ headers.x-source }}",
            "cron": "{{ cron_expression }}",
            "static": {"priority": "high"},
        },
        event,
        subscription,
    )

    assert mapped == {
        "ticket_id": 123,
        "summary": "Ticket Cannot log in",
        "source": "halo",
        "cron": "0 6 * * *",
        "static": {"priority": "high"},
    }


@pytest.mark.asyncio
async def test_process_webhook_handles_adapter_result_types_and_errors(monkeypatch):
    session = AsyncMock()
    processor = p.EventProcessor(session)
    event_source = SimpleNamespace(id=uuid.uuid4())
    webhook_source = SimpleNamespace(
        adapter_name="generic",
        config={"secret": "s"},
        state={"token": "t"},
    )
    request = WebhookRequest(
        method="POST",
        path="/webhooks/source",
        headers={},
        query_params={},
        body=b"{}",
        client_ip="10.0.0.1",
    )
    adapter = SimpleNamespace(handle_request=AsyncMock())
    monkeypatch.setattr(p, "get_adapter", lambda _name: adapter)

    validation = ValidationResponse(status_code=202, body="ok")
    adapter.handle_request.return_value = validation
    assert await processor.process_webhook(event_source, webhook_source, request) is validation

    rejected = Rejected(message="bad signature", status_code=401)
    adapter.handle_request.return_value = rejected
    assert await processor.process_webhook(event_source, webhook_source, request) is rejected

    deliver = Deliver(data={"id": 1}, event_type="ticket.created")
    processor._process_delivery = AsyncMock(return_value=deliver)
    adapter.handle_request.return_value = deliver
    assert await processor.process_webhook(event_source, webhook_source, request) is deliver
    processor._process_delivery.assert_awaited_once_with(
        webhook_source=webhook_source,
        event_source=event_source,
        deliver=deliver,
        request=request,
    )

    adapter.handle_request.side_effect = RuntimeError("adapter exploded")
    result = await processor.process_webhook(event_source, webhook_source, request)
    assert isinstance(result, Rejected)
    assert result.status_code == 500


@pytest.mark.asyncio
async def test_process_webhook_rejects_missing_and_unknown_adapter(monkeypatch):
    processor = p.EventProcessor(AsyncMock())
    event_source = SimpleNamespace(id=uuid.uuid4())
    webhook_source = SimpleNamespace(adapter_name="missing", config=None, state=None)
    request = WebhookRequest("POST", "/webhooks/source", {}, {}, b"{}")
    monkeypatch.setattr(p, "get_adapter", lambda _name: None)

    missing = await processor.process_webhook(event_source, webhook_source, request)
    assert isinstance(missing, Rejected)
    assert missing.status_code == 500

    adapter = SimpleNamespace(handle_request=AsyncMock(return_value=object()))
    monkeypatch.setattr(p, "get_adapter", lambda _name: adapter)
    unknown = await processor.process_webhook(event_source, webhook_source, request)
    assert isinstance(unknown, Rejected)
    assert unknown.status_code == 500


@pytest.mark.asyncio
async def test_process_delivery_returns_the_exact_persisted_event_id():
    session = AsyncMock()
    session.add = MagicMock()
    processor = p.EventProcessor(session)
    processor._subscription_repo.get_active_for_event = AsyncMock(return_value=[])
    processor._broadcast_event_update = AsyncMock()
    event_source = SimpleNamespace(id=uuid.uuid4())
    webhook_source = SimpleNamespace()
    incoming = Deliver(data={"id": 1}, event_type="ticket.created")
    request = WebhookRequest("POST", "/webhooks/source", {}, {}, b"{}")

    result = await processor._process_delivery(
        webhook_source=webhook_source,
        event_source=event_source,
        deliver=incoming,
        request=request,
    )

    persisted_event = session.add.call_args.args[0]
    assert result.event_id == persisted_event.id


@pytest.mark.asyncio
async def test_emit_topic_noops_without_active_source_and_skips_invalid_subscriptions():
    source_id = uuid.uuid4()
    event_source_id = uuid.uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    processor = p.EventProcessor(session)
    processor._source_repo.get_by_topic = AsyncMock(
        return_value=SimpleNamespace(id=source_id, organization_id=event_source_id)
    )
    valid_workflow = SimpleNamespace(
        id=uuid.uuid4(),
        target_type="workflow",
        workflow_id=uuid.uuid4(),
        workflow=SimpleNamespace(id=uuid.uuid4()),
    )
    missing_workflow = SimpleNamespace(
        id=uuid.uuid4(),
        target_type="workflow",
        workflow_id=None,
        workflow=None,
    )
    valid_agent = SimpleNamespace(
        id=uuid.uuid4(),
        target_type="agent",
        agent_id=uuid.uuid4(),
        workflow_id=None,
    )
    missing_agent = SimpleNamespace(
        id=uuid.uuid4(),
        target_type="agent",
        agent_id=None,
        workflow_id=None,
    )
    processor._subscription_repo.get_active_for_event = AsyncMock(
        return_value=[valid_workflow, missing_workflow, valid_agent, missing_agent]
    )

    event_id, notified = await processor.emit_topic(topic="ticket.created", data={"id": 123})

    assert isinstance(event_id, uuid.UUID)
    assert notified == 4
    assert session.add.call_count == 3
    assert session.flush.await_count == 3

    processor._source_repo.get_by_topic.return_value = None
    event_id, notified = await processor.emit_topic(topic="ticket.closed", data={})
    assert isinstance(event_id, uuid.UUID)
    assert notified == 0


@pytest.mark.asyncio
async def test_broadcast_event_update_swallows_pubsub_failures(monkeypatch):
    processor = p.EventProcessor(AsyncMock())
    event = _make_event()
    manager = SimpleNamespace(broadcast=AsyncMock(side_effect=RuntimeError("socket down")))
    monkeypatch.setattr("src.core.pubsub.manager", manager)

    await processor._broadcast_event_update(
        event_source_id=event.event_source_id,
        event=event,
        update_type="event_updated",
        success_count=1,
        failed_count=2,
        queued_count=3,
        pending_count=4,
    )

    manager.broadcast.assert_awaited_once()
    channel, message = manager.broadcast.call_args[0]
    assert channel == f"event-source:{event.event_source_id}"
    assert message["event"]["delivery_count"] == 10


@pytest.mark.asyncio
async def test_broadcast_event_status_update_noops_without_event_or_source(monkeypatch):
    db = AsyncMock()
    delivery = _make_delivery()
    event_repo = MagicMock()
    delivery_repo = MagicMock()
    manager = SimpleNamespace(broadcast=AsyncMock())
    monkeypatch.setattr(p, "EventRepository", MagicMock(return_value=event_repo))
    monkeypatch.setattr("src.core.pubsub.manager", manager)

    event_repo.get_by_id = AsyncMock(return_value=None)
    await p._broadcast_event_status_update(db, delivery_repo, delivery)
    manager.broadcast.assert_not_awaited()

    event = _make_event(delivery.event_id)
    event.event_source = None
    event_repo.get_by_id = AsyncMock(return_value=event)
    await p._broadcast_event_status_update(db, delivery_repo, delivery)
    manager.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_event_status_update_counts_deliveries_and_tolerates_failure(
    monkeypatch,
):
    db = AsyncMock()
    event = _make_event()
    event.event_source = SimpleNamespace(id=event.event_source_id)
    delivery = _make_delivery(event=event)
    delivery_repo = MagicMock()
    delivery_repo.get_by_event = AsyncMock(
        return_value=[
            _make_delivery(status=EventDeliveryStatus.SUCCESS, event=event),
            _make_delivery(status=EventDeliveryStatus.FAILED, event=event),
            _make_delivery(status=EventDeliveryStatus.PENDING, event=event),
        ]
    )
    event_repo = MagicMock()
    event_repo.get_by_id = AsyncMock(return_value=event)
    manager = SimpleNamespace(broadcast=AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(p, "EventRepository", MagicMock(return_value=event_repo))
    monkeypatch.setattr("src.core.pubsub.manager", manager)

    await p._broadcast_event_status_update(db, delivery_repo, delivery)

    manager.broadcast.assert_awaited_once()
    channel, message = manager.broadcast.call_args[0]
    assert channel == f"event-source:{event.event_source_id}"
    assert message["event"]["success_count"] == 1
    assert message["event"]["failed_count"] == 1
    assert message["event"]["delivery_count"] == 3


@pytest.mark.asyncio
async def test_emit_delivery_retry_exhausted_loads_event_and_emits_builtin(monkeypatch):
    db = AsyncMock()
    event = _make_event()
    delivery = _make_delivery(event=None, target_type="workflow")
    delivery.event_id = event.id
    delivery.attempt_count = 3

    event_repo = MagicMock()
    event_repo.get_by_id = AsyncMock(return_value=event)
    monkeypatch.setattr(p, "EventRepository", MagicMock(return_value=event_repo))

    emit_retry_exhausted = AsyncMock()
    monkeypatch.setattr(
        "src.services.events.builtins.emit_event_delivery_retry_exhausted",
        emit_retry_exhausted,
    )

    await p._emit_delivery_retry_exhausted(db, delivery, "final failure")

    event_repo.get_by_id.assert_awaited_once_with(event.id)
    emit_retry_exhausted.assert_awaited_once_with(
        event_id=event.id,
        event_type=event.event_type,
        source_id=event.event_source_id,
        organization_id=event.organization_id,
        delivery_id=delivery.id,
        target_type="workflow",
        target_id=delivery.workflow_id,
        attempt=3,
        max_attempts=3,
        error_type="DeliveryError",
        error_message="final failure",
    )


@pytest.mark.asyncio
async def test_run_delivery_execution_update_failed_emits_retry_and_broadcasts():
    execution_id = uuid.uuid4()
    event = _make_event()
    delivery = _make_delivery(event=event)
    delivery.execution_id = execution_id

    scalar_result = MagicMock()
    scalar_result.unique.return_value.scalar_one_or_none.return_value = delivery
    db = AsyncMock()
    db.execute = AsyncMock(return_value=scalar_result)

    delivery_repo = MagicMock()
    delivery_repo.update_event_status = AsyncMock()

    with (
        patch("src.services.events.processor.EventDeliveryRepository", return_value=delivery_repo),
        patch(
            "src.services.events.processor._emit_delivery_retry_exhausted",
            new_callable=AsyncMock,
        ) as emit_retry,
        patch(
            "src.services.events.processor._broadcast_event_status_update",
            new_callable=AsyncMock,
        ) as broadcast,
    ):
        await p._run_delivery_execution_update(
            db,
            str(execution_id),
            EventDeliveryStatus.FAILED,
            "workflow failed",
        )

    assert delivery.status == EventDeliveryStatus.FAILED
    assert delivery.error_message == "workflow failed"
    assert delivery.attempt_count == 1
    assert delivery.completed_at is not None
    db.flush.assert_awaited_once()
    delivery_repo.update_event_status.assert_awaited_once_with(event.id)
    emit_retry.assert_awaited_once_with(db, delivery, "workflow failed")
    broadcast.assert_awaited_once_with(db, delivery_repo, delivery)


@pytest.mark.asyncio
async def test_run_delivery_execution_update_returns_when_delivery_missing():
    scalar_result = MagicMock()
    scalar_result.unique.return_value.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=scalar_result)

    await p._run_delivery_execution_update(
        db,
        str(uuid.uuid4()),
        EventDeliveryStatus.SUCCESS,
        None,
    )

    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_delivery_from_execution_uses_session_factory(monkeypatch):
    calls = []

    class SessionContext:
        async def __aenter__(self):
            return "db-session"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class SessionFactory:
        def __call__(self):
            return SessionContext()

    db = SimpleNamespace(commit=AsyncMock())

    async def enter(self):
        return db

    SessionContext.__aenter__ = enter

    monkeypatch.setattr("src.core.database.get_session_factory", lambda: SessionFactory())

    async def run_update(session, execution_id, delivery_status, error_message):
        calls.append((session, execution_id, delivery_status, error_message))

    monkeypatch.setattr(p, "_run_delivery_execution_update", run_update)
    execution_id = str(uuid.uuid4())

    await p.update_delivery_from_execution(execution_id, "Success", "ignored")

    assert calls == [(db, execution_id, EventDeliveryStatus.SUCCESS, "ignored")]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_delivery_from_execution_uses_provided_session_without_commit(
    monkeypatch,
):
    db = SimpleNamespace(commit=AsyncMock())
    calls = []

    async def run_update(session, execution_id, delivery_status, error_message):
        calls.append((session, execution_id, delivery_status, error_message))

    monkeypatch.setattr(p, "_run_delivery_execution_update", run_update)
    execution_id = str(uuid.uuid4())

    await p.update_delivery_from_execution(
        execution_id,
        "Cancelled",
        "operator stopped it",
        session=db,
    )

    assert calls == [
        (db, execution_id, EventDeliveryStatus.FAILED, "operator stopped it")
    ]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_workflow_execution_uses_mapping_event_context_and_source_org(
    monkeypatch,
):
    event = _make_event()
    event.data = {"ticket": {"id": 123}, "raw": "kept"}
    event.headers = {"x-source": "halo"}
    event.organization_id = None
    event.event_source.organization_id = uuid.uuid4()
    delivery = _make_delivery(event=event, target_type="workflow")
    delivery.subscription.input_mapping = {
        "ticket_id": "{{ payload.ticket.id }}",
        "source": "{{ headers.x-source }}",
    }
    delivery.workflow.organization_id = None
    execution_id = str(uuid.uuid4())
    enqueue = AsyncMock(return_value=execution_id)
    monkeypatch.setattr(
        "src.services.execution.async_executor.enqueue_system_workflow_execution",
        enqueue,
    )

    await p.EventProcessor(AsyncMock())._queue_workflow_execution(delivery, event)

    enqueue.assert_awaited_once()
    kwargs = enqueue.await_args.kwargs
    assert kwargs["workflow_id"] == str(delivery.workflow.id)
    assert kwargs["source"] == "Event System"
    assert kwargs["org_id"] == str(event.event_source.organization_id)
    assert kwargs["parameters"]["ticket_id"] == 123
    assert kwargs["parameters"]["source"] == "halo"
    assert kwargs["parameters"]["_event"]["body"] == event.data
    assert kwargs["event"].id == str(event.id)
    assert delivery.execution_id == uuid.UUID(execution_id)


@pytest.mark.asyncio
async def test_queue_workflow_execution_defaults_to_provider_org_for_global_event(
    monkeypatch,
):
    event = _make_event()
    event.data = {"ticket_id": 123}
    event.organization_id = None
    event.event_source.organization_id = None
    delivery = _make_delivery(event=event, target_type="workflow")
    enqueue = AsyncMock(return_value=str(uuid.uuid4()))
    monkeypatch.setattr(
        "src.services.execution.async_executor.enqueue_system_workflow_execution",
        enqueue,
    )

    await p.EventProcessor(AsyncMock())._queue_workflow_execution(delivery, event)

    kwargs = enqueue.await_args.kwargs
    assert kwargs["org_id"] == "00000000-0000-0000-0000-000000000002"
    assert kwargs["parameters"]["ticket_id"] == 123
    assert kwargs["parameters"]["_event"]["id"] == str(event.id)


@pytest.mark.asyncio
async def test_queue_agent_run_uses_mapping_and_agent_org(monkeypatch):
    event = _make_event()
    event.data = {"ticket": {"id": 123}, "summary": "Cannot log in"}
    event.headers = {"x-source": "halo"}
    delivery = _make_delivery(event=event, target_type="agent")
    agent = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    delivery.subscription.agent = agent
    delivery.subscription.input_mapping = {
        "ticket_id": "{{ payload.ticket.id }}",
        "source": "{{ headers.x-source }}",
    }
    enqueue = AsyncMock(return_value=str(uuid.uuid4()))
    monkeypatch.setattr(
        "src.services.execution.agent_run_service.enqueue_agent_run",
        enqueue,
    )

    await p.EventProcessor(AsyncMock())._queue_agent_run(delivery, event)

    enqueue.assert_awaited_once_with(
        agent_id=str(agent.id),
        trigger_type="event",
        trigger_source="event: ticket.created",
        input_data={
            "ticket_id": 123,
            "source": "halo",
            "_event": {
                "id": str(event.id),
                "type": event.event_type,
                "body": event.data,
                "headers": event.headers,
                "received_at": event.received_at.isoformat(),
                "source_ip": event.source_ip,
            },
        },
        org_id=str(agent.organization_id),
        event_delivery_id=str(delivery.id),
    )


@pytest.mark.asyncio
async def test_queue_agent_run_raises_when_subscription_agent_missing():
    event = _make_event()
    delivery = _make_delivery(event=event, target_type="agent")
    delivery.subscription.agent = None

    with pytest.raises(ValueError, match="subscription has no agent"):
        await p.EventProcessor(AsyncMock())._queue_agent_run(delivery, event)
