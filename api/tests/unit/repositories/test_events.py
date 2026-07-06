from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus
from src.repositories.events import (
    EventDeliveryRepository,
    EventRepository,
    EventSourceRepository,
    EventSubscriptionRepository,
    WebhookSourceRepository,
)


class Result:
    def __init__(self, *, rows=None, scalar_value=None, rowcount=0):
        self.rows = rows or []
        self.scalar_value = scalar_value
        self.rowcount = rowcount

    def unique(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar(self):
        return self.scalar_value

    def scalar_one_or_none(self):
        return self.scalar_value


class CapturingSession:
    def __init__(self, *results: Result):
        self.results = list(results)
        self.statements = []
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.get = AsyncMock()

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


def sql(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": False}))


@pytest.mark.asyncio
async def test_event_sources_apply_scope_filters_and_pagination() -> None:
    source = SimpleNamespace(name="Topic")
    org_id = uuid4()
    session = CapturingSession(Result(rows=[source]))
    repo = EventSourceRepository(session)  # type: ignore[arg-type]

    result = await repo.get_by_organization(
        org_id,
        source_type=EventSourceType.TOPIC,
        include_global=True,
        active_only=True,
        limit=25,
        offset=50,
    )

    compiled = sql(session.statements[0])
    assert result == [source]
    assert "event_sources.is_active IS true" in compiled
    assert "event_sources.organization_id =" in compiled
    assert "event_sources.organization_id IS NULL" in compiled
    assert "event_sources.source_type = :source_type_1" in compiled
    assert "ORDER BY event_sources.name" in compiled
    assert "LIMIT :param_1 OFFSET :param_2" in compiled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("organization_id", "include_global", "expected_sql"),
    [
        (uuid4(), False, "event_sources.organization_id ="),
        (None, True, "event_sources.organization_id IS NULL"),
    ],
)
async def test_count_sources_uses_requested_organization_scope(
    organization_id,
    include_global,
    expected_sql,
) -> None:
    session = CapturingSession(Result(scalar_value=7))
    repo = EventSourceRepository(session)  # type: ignore[arg-type]

    count = await repo.count_by_organization(
        organization_id,
        include_global=include_global,
        active_only=True,
    )

    compiled = sql(session.statements[0])
    assert count == 7
    assert "count(event_sources.id)" in compiled
    assert "event_sources.is_active IS true" in compiled
    assert expected_sql in compiled


@pytest.mark.asyncio
async def test_topic_source_queries_only_active_topic_sources() -> None:
    source = SimpleNamespace(event_type="ticket.created")
    session = CapturingSession(
        Result(scalar_value=source),
        Result(rows=[("ticket.created",), ("ticket.updated",)]),
    )
    repo = EventSourceRepository(session)  # type: ignore[arg-type]

    found = await repo.get_by_topic("ticket.created")
    topics = await repo.get_distinct_topic_types()

    by_topic_sql = sql(session.statements[0])
    distinct_sql = sql(session.statements[1])
    assert found is source
    assert topics == ["ticket.created", "ticket.updated"]
    assert "event_sources.source_type = :source_type_1" in by_topic_sql
    assert "event_sources.event_type = :event_type_1" in by_topic_sql
    assert "event_sources.is_active IS true" in by_topic_sql
    assert "SELECT DISTINCT event_sources.event_type" in distinct_sql
    assert "event_sources.event_type IS NOT NULL" in distinct_sql
    assert "ORDER BY event_sources.event_type" in distinct_sql


@pytest.mark.asyncio
async def test_webhook_expiry_query_uses_active_sources_and_cutoff() -> None:
    webhook = SimpleNamespace(adapter_name="ninjaone")
    session = CapturingSession(Result(rows=[webhook]))
    repo = WebhookSourceRepository(session)  # type: ignore[arg-type]

    result = await repo.get_expiring_soon(within_hours=6)

    compiled = sql(session.statements[0])
    assert result == [webhook]
    assert "webhook_sources.expires_at IS NOT NULL" in compiled
    assert "webhook_sources.expires_at <=" in compiled
    assert "JOIN event_sources" in compiled
    assert "event_sources.is_active IS true" in compiled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "expected_sql"),
    [
        (
            "ticket.created",
            "event_subscriptions.event_type IS NULL OR "
            "event_subscriptions.event_type = :event_type_1",
        ),
        (None, "event_subscriptions.event_type IS NULL"),
    ],
)
async def test_active_subscriptions_match_typed_and_untyped_events(
    event_type,
    expected_sql,
) -> None:
    subscription = SimpleNamespace(event_type=event_type)
    session = CapturingSession(Result(rows=[subscription]))
    repo = EventSubscriptionRepository(session)  # type: ignore[arg-type]

    result = await repo.get_active_for_event(uuid4(), event_type=event_type)

    compiled = sql(session.statements[0])
    assert result == [subscription]
    assert "event_subscriptions.event_source_id = :event_source_id_1" in compiled
    assert "event_subscriptions.is_active IS true" in compiled
    assert expected_sql in compiled


@pytest.mark.asyncio
async def test_event_queries_apply_optional_filters_and_cleanup_delete() -> None:
    event = SimpleNamespace(status=EventStatus.RECEIVED)
    session = CapturingSession(
        Result(rows=[event]),
        Result(scalar_value=3),
        Result(rows=[event]),
        Result(rowcount=4),
    )
    repo = EventRepository(session)  # type: ignore[arg-type]
    source_id = uuid4()
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    until = datetime(2026, 7, 5, tzinfo=timezone.utc)

    events = await repo.get_by_source(
        source_id,
        status=EventStatus.RECEIVED,
        event_type="ticket.created",
        since=since,
        until=until,
        limit=10,
        offset=20,
    )
    count = await repo.count_by_source(
        source_id,
        status=EventStatus.RECEIVED,
        event_type="ticket.created",
        since=since,
        until=until,
    )
    old_events = await repo.get_old_events(older_than_days=14, limit=5)
    deleted = await repo.delete_old_events(older_than_days=14)

    filtered_sql = sql(session.statements[0])
    count_sql = sql(session.statements[1])
    old_sql = sql(session.statements[2])
    delete_sql = sql(session.statements[3])
    assert events == [event]
    assert count == 3
    assert old_events == [event]
    assert deleted == 4
    assert "events.event_source_id = :event_source_id_1" in filtered_sql
    assert "events.status = :status_1" in filtered_sql
    assert "events.event_type = :event_type_1" in filtered_sql
    assert "events.received_at >= :received_at_1" in filtered_sql
    assert "events.received_at <= :received_at_2" in filtered_sql
    assert "ORDER BY events.received_at DESC" in filtered_sql
    assert "count(events.id)" in count_sql
    assert "events.created_at < :created_at_1" in old_sql
    assert "DELETE FROM events WHERE events.created_at < :created_at_1" in delete_sql
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_delivery_status_sets_execution_error_and_completion() -> None:
    delivery = SimpleNamespace(
        status=EventDeliveryStatus.PENDING,
        attempt_count=1,
        execution_id=None,
        error_message=None,
        completed_at=None,
    )
    session = CapturingSession()
    repo = EventDeliveryRepository(session)  # type: ignore[arg-type]
    repo.get_by_id = AsyncMock(return_value=delivery)  # type: ignore[method-assign]
    execution_id = uuid4()

    result = await repo.update_status(
        uuid4(),
        EventDeliveryStatus.FAILED,
        execution_id=execution_id,
        error_message="workflow crashed",
    )

    assert result is delivery
    assert delivery.status == EventDeliveryStatus.FAILED
    assert delivery.attempt_count == 2
    assert delivery.execution_id == execution_id
    assert delivery.error_message == "workflow crashed"
    assert delivery.completed_at is not None
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once_with(delivery)


@pytest.mark.asyncio
async def test_update_delivery_status_returns_none_for_missing_delivery() -> None:
    session = CapturingSession()
    repo = EventDeliveryRepository(session)  # type: ignore[arg-type]
    repo.get_by_id = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await repo.update_status(uuid4(), EventDeliveryStatus.SUCCESS)

    assert result is None
    session.flush.assert_not_awaited()
    session.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_retry_and_stuck_queries_select_actionable_records() -> None:
    failed_delivery = SimpleNamespace(status=EventDeliveryStatus.FAILED)
    stuck_delivery = SimpleNamespace(status=EventDeliveryStatus.QUEUED)
    session = CapturingSession(
        Result(rows=[failed_delivery]),
        Result(rows=[stuck_delivery]),
    )
    repo = EventDeliveryRepository(session)  # type: ignore[arg-type]

    retryable = await repo.get_pending_for_retry(limit=25)
    stuck = await repo.get_stuck_deliveries(timeout_minutes=15)

    retry_sql = sql(session.statements[0])
    stuck_sql = sql(session.statements[1])
    assert retryable == [failed_delivery]
    assert stuck == [stuck_delivery]
    assert "event_deliveries.status =" in retry_sql
    assert "event_deliveries.next_retry_at IS NOT NULL" in retry_sql
    assert "event_deliveries.next_retry_at <= :next_retry_at_1" in retry_sql
    assert "event_deliveries.status IN (__[POSTCOMPILE_status_" in stuck_sql
    assert "event_deliveries.created_at <" in stuck_sql
