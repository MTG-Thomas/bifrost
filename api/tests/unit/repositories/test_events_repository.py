from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.enums import EventDeliveryStatus, EventStatus
from src.repositories.events import EventDeliveryRepository


class _AllResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({EventDeliveryStatus.PENDING: 1}, EventStatus.PROCESSING),
        ({EventDeliveryStatus.QUEUED: 1}, EventStatus.PROCESSING),
        ({EventDeliveryStatus.FAILED: 1}, EventStatus.FAILED),
        ({EventDeliveryStatus.SUCCESS: 2}, EventStatus.COMPLETED),
    ],
)
async def test_update_event_status_derives_status_from_delivery_counts(
    counts,
    expected,
):
    event = SimpleNamespace(status=EventStatus.RECEIVED)
    session = AsyncMock()
    session.get.return_value = event
    session.execute.return_value = _AllResult(list(counts.items()))
    repo = EventDeliveryRepository(session)

    await repo.update_event_status(uuid4())

    assert event.status == expected
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_event_status_leaves_missing_or_empty_events_unchanged():
    session = AsyncMock()
    session.get.return_value = None
    repo = EventDeliveryRepository(session)

    await repo.update_event_status(uuid4())

    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()

    event = SimpleNamespace(status=EventStatus.RECEIVED)
    session = AsyncMock()
    session.get.return_value = event
    session.execute.return_value = _AllResult([])
    repo = EventDeliveryRepository(session)

    await repo.update_event_status(uuid4())

    assert event.status == EventStatus.RECEIVED
    session.flush.assert_awaited_once()
