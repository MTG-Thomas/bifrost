"""Accountability contracts for reviewed Workspace source commits."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from src.models.contracts.workspace_promotions import (
    WorkspaceSourceReleaseDeclareRequest,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionRelease,
    WorkspaceSourceRelease,
)
from src.services.workspace_source_releases import (
    WorkspaceSourceReleaseService,
    reconcile_source_releases_after_lock,
    source_release_response,
    sweep_overdue_workspace_releases,
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Database:
    def __init__(self, scalar_batches):
        self._scalar_batches = list(scalar_batches)
        self.flushes = 0

    async def scalars(self, _statement):
        return _Scalars(self._scalar_batches.pop(0))

    async def flush(self):
        self.flushes += 1


class _CountRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ListDatabase:
    def __init__(self, records, counts, scalar_counts):
        self._records = records
        self._counts = counts
        self._scalar_counts = list(scalar_counts)

    async def scalars(self, _statement):
        return _Scalars(self._records)

    async def execute(self, _statement):
        return _CountRows(self._counts)

    async def scalar(self, _statement):
        return self._scalar_counts.pop(0)


class _RacingDeclareDatabase:
    def __init__(self, concurrent_record):
        self._scalar_rows = [None, concurrent_record]
        self.rollback_called = False

    async def scalar(self, _statement):
        return self._scalar_rows.pop(0)

    def add(self, _record):
        pass

    async def commit(self):
        raise IntegrityError("insert", {}, Exception("unique violation"))

    async def rollback(self):
        self.rollback_called = True


def _source_record(*, disposition="pending", due_at=None):
    now = datetime.now(timezone.utc)
    return WorkspaceSourceRelease(
        id=uuid4(),
        organization_id=uuid4(),
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
        paths={"features/example.py": "c" * 64},
        disposition=disposition,
        due_at=due_at,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )


def test_pending_declaration_requires_exact_paths() -> None:
    with pytest.raises(ValidationError, match="exact path hashes"):
        WorkspaceSourceReleaseDeclareRequest(
            source_commit_sha="a" * 40,
            source_tree_sha="b" * 40,
            paths={},
            disposition="pending",
        )


def test_non_production_declaration_requires_reason() -> None:
    with pytest.raises(ValidationError, match="requires a reason"):
        WorkspaceSourceReleaseDeclareRequest(
            source_commit_sha="a" * 40,
            source_tree_sha="b" * 40,
            disposition="non_production",
        )


def test_response_exposes_overdue_pending_as_attention() -> None:
    now = datetime.now(timezone.utc)
    response = source_release_response(
        _source_record(due_at=now - timedelta(seconds=1)), now=now
    )

    assert response.disposition == "pending"
    assert response.overdue is True
    assert response.requires_attention is True


@pytest.mark.asyncio
async def test_list_counts_full_backlog_not_only_bounded_records() -> None:
    record = _source_record()
    database = _ListDatabase(
        [record],
        [("pending", 120), ("attention_required", 3), ("released", 400)],
        [2, 5],
    )
    service = WorkspaceSourceReleaseService(database, record.organization_id)

    response = await service.list(limit=1)

    assert len(response.records) == 1
    assert response.total == 523
    assert response.pending == 120
    assert response.attention_required == 5
    assert response.overdue == 5
    assert response.tracking_state == "active"


@pytest.mark.asyncio
async def test_concurrent_exact_declaration_is_idempotent() -> None:
    record = _source_record()
    database = _RacingDeclareDatabase(record)
    service = WorkspaceSourceReleaseService(database, record.organization_id)
    request = WorkspaceSourceReleaseDeclareRequest(
        source_commit_sha=record.source_commit_sha,
        source_tree_sha=record.source_tree_sha,
        paths=record.paths,
        disposition="pending",
    )

    response = await service.declare(request, created_by=uuid4())

    assert response.id == record.id
    assert database.rollback_called is True


@pytest.mark.asyncio
async def test_release_closes_only_after_runtime_and_history_match() -> None:
    record = _source_record()
    release_row_id = uuid4()
    runtime = {"features/example.py": "c" * 64}
    database = _Database([[record]])

    completed = await reconcile_source_releases_after_lock(
        database,
        organization_id=record.organization_id,
        release_row_id=release_row_id,
        release_id="sha256:" + "d" * 64,
        runtime_hashes=runtime,
        history_commit_sha="e" * 40,
        history_hashes={"features/example.py": "f" * 64},
    )

    assert completed == []
    assert record.disposition == "pending"

    database = _Database([[record]])
    completed = await reconcile_source_releases_after_lock(
        database,
        organization_id=record.organization_id,
        release_row_id=release_row_id,
        release_id="sha256:" + "d" * 64,
        runtime_hashes=runtime,
        history_commit_sha="e" * 40,
        history_hashes=runtime,
    )

    assert completed == [record.id]
    assert record.disposition == "released"
    assert record.release_row_id == release_row_id
    assert record.completion_evidence["runtime_sha256"] == runtime
    assert record.completion_evidence["history"]["file_sha256"] == runtime
    assert record.completion_evidence["evidence_id"].startswith("sha256:")


@pytest.mark.asyncio
async def test_sweep_marks_source_and_unmirrored_live_release_attention() -> None:
    now = datetime.now(timezone.utc)
    source = _source_record(due_at=now - timedelta(seconds=1))
    release = WorkspacePromotionRelease(
        id=uuid4(),
        organization_id=source.organization_id,
        artifact_id=uuid4(),
        activation_state="live",
        lock_state="queued",
        attention_deadline=now - timedelta(seconds=1),
        lock_in_job_id=uuid4(),
        created_by=uuid4(),
    )
    database = _Database([[source], [release]])

    result = await sweep_overdue_workspace_releases(database, now=now)

    assert result == {
        "source_release_ids": [str(source.id)],
        "workspace_release_ids": [str(release.id)],
    }
    assert source.disposition == "attention_required"
    assert "not reached verified production" in source.reason
    assert release.lock_state == "attention_required"
    assert release.error_code == "workspace_release_history_overdue"
    assert database.flushes == 1
