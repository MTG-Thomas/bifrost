"""Bounded retention contracts for inert Workspace draft uploads."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.jobs.schedulers.workspace_promotion_drafts import (
    WORKSPACE_DRAFT_CLEANUP_BATCH_SIZE,
    delete_expired_workspace_draft_batch,
)


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Database:
    def __init__(self, rows, *, reference=None):
        self.rows = rows
        self.reference = reference
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0
        self.statements = []

    async def scalars(self, statement):
        self.statements.append(statement)
        return _Scalars(self.rows)

    async def execute(self, statement, _parameters=None):
        self.statements.append(statement)

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.reference

    async def delete(self, row):
        self.deleted.append(row)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_cleanup_is_bounded_and_deletes_only_selected_draft_objects() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(
            id=uuid4(),
            organization_id=uuid4(),
            content_id="sha256:" + character * 64,
            expires_at=now - timedelta(minutes=1),
        )
        for character in ("a", "b")
    ]
    database = _Database(rows)
    deleted_objects = []

    class Storage:
        def __init__(self, organization_id, content_id):
            self.identity = (organization_id, content_id)

        async def delete_expired_draft(self):
            deleted_objects.append(self.identity)

    deleted, failures = await delete_expired_workspace_draft_batch(
        database,
        now=now,
        batch_size=2,
        storage_factory=Storage,
    )

    assert deleted == 2
    assert failures == 0
    assert database.deleted == rows
    assert database.commits == 1
    assert deleted_objects == sorted(
        [(row.organization_id, row.content_id) for row in rows],
        key=lambda item: (str(item[0]), item[1]),
    )
    sql = "\n".join(str(statement) for statement in database.statements)
    assert "target_kind" in sql and "expires_at" in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_cleanup_rejects_unbounded_batches() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        await delete_expired_workspace_draft_batch(
            _Database([]),
            now=datetime.now(timezone.utc),
            batch_size=WORKSPACE_DRAFT_CLEANUP_BATCH_SIZE + 1,
        )


@pytest.mark.asyncio
async def test_cleanup_preserves_content_still_referenced_by_an_artifact() -> None:
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        content_id="sha256:" + "c" * 64,
        expires_at=now - timedelta(minutes=1),
    )
    database = _Database([row], reference=uuid4())
    storage_called = False

    class Storage:
        def __init__(self, _organization_id, _content_id):
            nonlocal storage_called
            storage_called = True

    deleted, failures = await delete_expired_workspace_draft_batch(
        database, now=now, storage_factory=Storage
    )

    assert deleted == 1
    assert failures == 0
    assert storage_called is False


@pytest.mark.asyncio
async def test_object_failure_retains_expired_row_for_hourly_retry() -> None:
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        content_id="sha256:" + "d" * 64,
        expires_at=now - timedelta(minutes=1),
    )
    database = _Database([row])

    class Storage:
        def __init__(self, _organization_id, _content_id):
            pass

        async def delete_expired_draft(self):
            raise RuntimeError("storage unavailable")

    deleted, failures = await delete_expired_workspace_draft_batch(
        database, now=now, storage_factory=Storage
    )

    assert deleted == 0
    assert failures == 1
    assert database.deleted == []
    assert database.rollbacks == 1
