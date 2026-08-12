from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.core.workspace_writer import (
    WorkspaceWriterBusy,
    WorkspaceWriterLeaseLost,
    assert_workspace_writer_access,
    checkpoint_workspace_writer_lease,
    workspace_writer_identity,
)


class FakeDB:
    def __init__(self, job):
        result = Mock()
        result.scalar_one_or_none.return_value = job
        self.execute = AsyncMock(side_effect=[Mock(), result])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "waiting", "cancel_requested"])
async def test_direct_writer_is_rejected_while_durable_writer_is_active(status):
    job = SimpleNamespace(id=uuid4(), status=status, phase=status.title())
    with pytest.raises(WorkspaceWriterBusy, match=str(job.id)):
        await assert_workspace_writer_access(FakeDB(job))


@pytest.mark.asyncio
async def test_current_writer_cannot_mutate_after_lease_expiry():
    job_id = uuid4()
    token = uuid4()
    job = SimpleNamespace(
        id=job_id,
        status="running",
        phase="Remote verification",
        lease_token=token,
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    with workspace_writer_identity(job_id, token, label="changeset:stale"):
        with pytest.raises(WorkspaceWriterLeaseLost):
            await assert_workspace_writer_access(FakeDB(job))


@pytest.mark.asyncio
async def test_stale_writer_identity_cannot_fall_back_to_direct_writer_access():
    with workspace_writer_identity(uuid4(), uuid4(), label="changeset:stale"):
        with pytest.raises(WorkspaceWriterLeaseLost):
            await assert_workspace_writer_access(FakeDB(None))


@pytest.mark.asyncio
async def test_current_fenced_job_may_write_while_lease_is_live():
    job_id = uuid4()
    token = uuid4()
    job = SimpleNamespace(
        id=job_id,
        status="running",
        phase="Activating",
        lease_token=token,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    with workspace_writer_identity(job_id, token, label="changeset:test"):
        await assert_workspace_writer_access(FakeDB(job))


@pytest.mark.asyncio
async def test_durable_writer_checkpoint_verifies_token_and_closes_transaction():
    job_id = uuid4()
    token = uuid4()
    job = SimpleNamespace(
        id=job_id,
        status="running",
        phase="Git push",
        lease_token=token,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    db = FakeDB(job)
    db.commit = AsyncMock()

    with workspace_writer_identity(job_id, token, label="changeset:test"):
        await checkpoint_workspace_writer_lease(db)

    db.commit.assert_awaited_once()
