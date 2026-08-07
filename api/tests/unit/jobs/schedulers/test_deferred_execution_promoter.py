"""Unit tests for the deferred execution promoter."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


PATH_PUBLISH = "src.jobs.schedulers.deferred_execution_promoter._publish_pending"
PATH_DB_CTX = "src.jobs.schedulers.deferred_execution_promoter.get_db_context"
PATH_DATETIME = "src.jobs.schedulers.deferred_execution_promoter.datetime"


def _new_scheduled(when: datetime) -> Execution:
    # workflow_id/executed_by left None to avoid FK constraints in unit tests.
    return Execution(
        id=uuid4(),
        workflow_id=None,
        workflow_name="demo",
        status=ExecutionStatus.SCHEDULED,
        parameters={"k": 1},
        scheduled_at=when,
        executed_by=None,
        executed_by_name="user",
    )


async def _cancel_existing_scheduled(session: AsyncSession) -> None:
    """Keep assertions independent from rows committed by earlier tests."""
    await session.execute(
        update(Execution)
        .where(Execution.status == ExecutionStatus.SCHEDULED)
        .values(status=ExecutionStatus.CANCELLED)
    )
    await session.commit()


class _DbCtx:
    """Async context manager that yields the test's session.

    Ensures the promoter runs against the same engine/event loop as the
    test fixture, avoiding cross-loop task errors from the global engine.
    """

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_promotes_due_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    await _cancel_existing_scheduled(db_session)
    # Keep the row in the real scheduler's future, then advance only the clock
    # used by the promoter under test. The Docker test stack runs the scheduler
    # every two seconds, so a genuinely overdue row can otherwise be consumed
    # between this commit and the direct promoter call below.
    now = datetime.now(timezone.utc)
    due = _new_scheduled(now + timedelta(hours=1))
    due.execution_context = {
        "is_platform_admin": False,
        "is_provider_org": True,
        "is_external": True,
    }
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_DATETIME) as promoter_datetime,
        patch(PATH_PUBLISH, new=AsyncMock()) as pub,
    ):
        promoter_datetime.now.return_value = now + timedelta(hours=2)
        promoted, failed = await promote_due_executions()

    assert promoted == 1
    assert failed == 0
    pub.assert_awaited_once()
    assert pub.await_args.kwargs["is_provider_org"] is True
    assert pub.await_args.kwargs["is_external"] is True

    await db_session.refresh(due)
    assert due.status == ExecutionStatus.PENDING


@pytest.mark.asyncio
async def test_leaves_future_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    await _cancel_existing_scheduled(db_session)
    future = _new_scheduled(datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(future)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()) as pub,
    ):
        promoted, failed = await promote_due_executions()

    assert promoted == 0
    pub.assert_not_awaited()

    await db_session.refresh(future)
    assert future.status == ExecutionStatus.SCHEDULED


@pytest.mark.asyncio
async def test_skips_cancelled_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    await _cancel_existing_scheduled(db_session)
    cancelled = _new_scheduled(datetime.now(timezone.utc) - timedelta(minutes=1))
    cancelled.status = ExecutionStatus.CANCELLED
    db_session.add(cancelled)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()),
    ):
        promoted, _ = await promote_due_executions()

    assert promoted == 0
    await db_session.refresh(cancelled)
    assert cancelled.status == ExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_reverts_on_publish_failure(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    await _cancel_existing_scheduled(db_session)
    now = datetime.now(timezone.utc)
    due = _new_scheduled(now + timedelta(hours=1))
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_DATETIME) as promoter_datetime,
        patch(PATH_PUBLISH, new=AsyncMock(side_effect=RuntimeError("rabbit down"))),
    ):
        promoter_datetime.now.return_value = now + timedelta(hours=2)
        promoted, failed = await promote_due_executions()

    assert promoted == 0
    assert failed == 1

    await db_session.refresh(due)
    # Reverted so next tick can retry.
    assert due.status == ExecutionStatus.SCHEDULED
