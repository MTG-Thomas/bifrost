"""Scheduler alert contract for incomplete Workspace releases."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.jobs.schedulers import workspace_release_accountability as accountability


class _Database:
    def __init__(self, counts):
        self._counts = list(counts)
        self.commit = AsyncMock()

    async def scalar(self, _statement):
        return self._counts.pop(0)


@pytest.mark.asyncio
async def test_attention_rows_create_one_admin_alert(monkeypatch) -> None:
    database = _Database([2, 1])

    @asynccontextmanager
    async def db_context():
        yield database

    notifications = SimpleNamespace(
        find_admin_notification_by_title=AsyncMock(return_value=None),
        create_notification=AsyncMock(),
        update_notification=AsyncMock(),
        dismiss_notification=AsyncMock(),
    )
    monkeypatch.setattr(accountability, "get_db_context", db_context)
    monkeypatch.setattr(
        accountability,
        "sweep_overdue_workspace_releases",
        AsyncMock(
            return_value={
                "source_release_ids": ["source-1"],
                "workspace_release_ids": ["release-1"],
            }
        ),
    )
    monkeypatch.setattr(
        accountability, "get_notification_service", lambda: notifications
    )

    result = await accountability.check_workspace_release_accountability()

    assert result["source_release_count"] == 2
    assert result["history_release_count"] == 1
    notifications.create_notification.assert_awaited_once()
    request = notifications.create_notification.await_args.kwargs["request"]
    assert request.title == accountability.NOTIFICATION_TITLE
    assert request.metadata["source_release_ids"] == ["source-1"]
    assert notifications.create_notification.await_args.kwargs["for_admins"] is True
    notifications.dismiss_notification.assert_not_awaited()
    notifications.update_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolved_rows_clear_existing_admin_alert(monkeypatch) -> None:
    database = _Database([0, 0])

    @asynccontextmanager
    async def db_context():
        yield database

    existing = SimpleNamespace(id="notification-1")
    notifications = SimpleNamespace(
        find_admin_notification_by_title=AsyncMock(return_value=existing),
        create_notification=AsyncMock(),
        update_notification=AsyncMock(),
        dismiss_notification=AsyncMock(),
    )
    monkeypatch.setattr(accountability, "get_db_context", db_context)
    monkeypatch.setattr(
        accountability,
        "sweep_overdue_workspace_releases",
        AsyncMock(
            return_value={
                "source_release_ids": [],
                "workspace_release_ids": [],
            }
        ),
    )
    monkeypatch.setattr(
        accountability, "get_notification_service", lambda: notifications
    )

    await accountability.check_workspace_release_accountability()

    notifications.dismiss_notification.assert_awaited_once_with(
        "notification-1", user_id="system"
    )
    notifications.create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_attention_alert_refreshes_current_counts(monkeypatch) -> None:
    database = _Database([3, 2])

    @asynccontextmanager
    async def db_context():
        yield database

    existing = SimpleNamespace(id="notification-1")
    notifications = SimpleNamespace(
        find_admin_notification_by_title=AsyncMock(return_value=existing),
        create_notification=AsyncMock(),
        update_notification=AsyncMock(),
        dismiss_notification=AsyncMock(),
    )
    monkeypatch.setattr(accountability, "get_db_context", db_context)
    monkeypatch.setattr(
        accountability,
        "sweep_overdue_workspace_releases",
        AsyncMock(
            return_value={
                "source_release_ids": [],
                "workspace_release_ids": [],
            }
        ),
    )
    monkeypatch.setattr(
        accountability, "get_notification_service", lambda: notifications
    )

    await accountability.check_workspace_release_accountability()

    notifications.update_notification.assert_awaited_once()
    update = notifications.update_notification.await_args.args[1]
    assert update.status.value == "awaiting_action"
    assert "3 reviewed source release(s)" in update.description
    assert "2 Live history projection(s)" in update.description
