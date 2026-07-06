from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.schedulers import event_cleanup
from src.models.enums import EventDeliveryStatus, EventStatus


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


@pytest.mark.asyncio
async def test_cleanup_old_events_commits_deleted_count(monkeypatch):
    db = AsyncMock()
    repo = AsyncMock()
    repo.delete_old_events.return_value = 7

    @asynccontextmanager
    async def fake_db_context():
        yield db

    monkeypatch.setattr(event_cleanup, "get_db_context", fake_db_context)
    monkeypatch.setattr(event_cleanup, "EventRepository", lambda session: repo)

    result = await event_cleanup.cleanup_old_events()

    assert result["events_deleted"] == 7
    assert result["errors"] == []
    repo.delete_old_events.assert_awaited_once_with(
        older_than_days=event_cleanup.EVENT_RETENTION_DAYS
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_old_events_returns_error_without_commit(monkeypatch):
    db = AsyncMock()
    repo = AsyncMock()
    repo.delete_old_events.side_effect = RuntimeError("delete failed")

    @asynccontextmanager
    async def fake_db_context():
        yield db

    monkeypatch.setattr(event_cleanup, "get_db_context", fake_db_context)
    monkeypatch.setattr(event_cleanup, "EventRepository", lambda session: repo)

    result = await event_cleanup.cleanup_old_events()

    assert result["events_deleted"] == 0
    assert result["errors"] == [{"error": "delete failed"}]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_stuck_events_marks_deliveries_updates_events_and_broadcasts(
    monkeypatch,
):
    source_id = uuid4()
    event_id = uuid4()
    delivery = SimpleNamespace(
        status=EventDeliveryStatus.QUEUED,
        error_message=None,
        completed_at=None,
        event_id=event_id,
        event=SimpleNamespace(
            event_source_id=source_id,
            event_type="ticket.created",
            status=EventStatus.PROCESSING,
        ),
    )
    stale_event = SimpleNamespace(id=uuid4())

    db = AsyncMock()
    db.execute.return_value = _ScalarResult([stale_event])

    repo = AsyncMock()
    repo.get_stuck_deliveries.return_value = [delivery]
    repo.get_by_event.return_value = [
        SimpleNamespace(status=EventDeliveryStatus.SUCCESS),
        SimpleNamespace(status=EventDeliveryStatus.FAILED),
    ]
    manager = AsyncMock()

    @asynccontextmanager
    async def fake_db_context():
        yield db

    monkeypatch.setattr(event_cleanup, "get_db_context", fake_db_context)
    monkeypatch.setattr(event_cleanup, "EventDeliveryRepository", lambda session: repo)
    monkeypatch.setattr("src.core.pubsub.manager", manager)

    result = await event_cleanup.cleanup_stuck_events()

    assert result["deliveries_failed"] == 1
    assert result["events_updated"] == 1
    assert result["stale_events_fixed"] == 1
    assert result["errors"] == []
    assert delivery.status is EventDeliveryStatus.FAILED
    assert "Execution timeout" in delivery.error_message
    repo.update_event_status.assert_any_await(event_id)
    repo.update_event_status.assert_any_await(stale_event.id)
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    manager.publish.assert_awaited_once()
    assert manager.publish.await_args.kwargs["channel"] == f"event-source:{source_id}"
