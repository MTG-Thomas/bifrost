import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.enums import EventDeliveryStatus, EventStatus
from src.services.events import processor as p


def _make_event(event_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=event_id or uuid.uuid4(),
        event_source_id=uuid.uuid4(),
        event_type="ticket.created",
        organization_id=uuid.uuid4(),
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_ip="10.0.0.5",
        status=EventStatus.PROCESSING,
        event_source=SimpleNamespace(id=uuid.uuid4()),
    )


def _make_delivery(
    *,
    status: EventDeliveryStatus = EventDeliveryStatus.PENDING,
    event: SimpleNamespace | None = None,
    target_type: str = "workflow",
) -> SimpleNamespace:
    subscription = SimpleNamespace(
        target_type=target_type,
        agent_id=uuid.uuid4() if target_type == "agent" else None,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        event_id=event.id if event else uuid.uuid4(),
        event=event,
        subscription=subscription,
        workflow_id=uuid.uuid4() if target_type == "workflow" else None,
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

