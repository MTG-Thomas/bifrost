from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers import maintenance


@pytest.mark.asyncio
async def test_index_documentation_returns_skipped_response(monkeypatch) -> None:
    async def index_platform_docs():
        return {"status": "skipped", "reason": "embeddings disabled"}

    monkeypatch.setattr(
        "src.services.docs_indexer.index_platform_docs",
        index_platform_docs,
    )

    response = await maintenance.index_documentation(ctx=None, user=None, db=None)

    assert response.status == "skipped"
    assert response.files_indexed == 0
    assert response.files_unchanged == 0
    assert response.files_deleted == 0
    assert response.message == "embeddings disabled"


@pytest.mark.asyncio
async def test_index_documentation_returns_complete_summary(monkeypatch) -> None:
    async def index_platform_docs():
        return {
            "status": "complete",
            "indexed": 3,
            "skipped": 2,
            "deleted": 1,
        }

    monkeypatch.setattr(
        "src.services.docs_indexer.index_platform_docs",
        index_platform_docs,
    )

    response = await maintenance.index_documentation(ctx=None, user=None, db=None)

    assert response.status == "complete"
    assert response.files_indexed == 3
    assert response.files_unchanged == 2
    assert response.files_deleted == 1
    assert response.message == "Indexed 3 files, 2 unchanged, 1 orphaned removed"


@pytest.mark.asyncio
async def test_index_documentation_returns_failed_for_unexpected_result(monkeypatch) -> None:
    async def index_platform_docs():
        return {"status": "weird", "detail": "unexpected"}

    monkeypatch.setattr(
        "src.services.docs_indexer.index_platform_docs",
        index_platform_docs,
    )

    response = await maintenance.index_documentation(ctx=None, user=None, db=None)

    assert response.status == "failed"
    assert "Unexpected result" in response.message


@pytest.mark.asyncio
async def test_index_documentation_maps_exception_to_http_500(monkeypatch) -> None:
    async def index_platform_docs():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.services.docs_indexer.index_platform_docs",
        index_platform_docs,
    )

    with pytest.raises(HTTPException) as exc_info:
        await maintenance.index_documentation(ctx=None, user=None, db=None)

    assert exc_info.value.status_code == 500
    assert "Failed to index documentation: boom" == exc_info.value.detail


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


@pytest.mark.asyncio
async def test_get_maintenance_status_counts_python_files() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_ScalarResult(7))

    response = await maintenance.get_maintenance_status(ctx=None, user=None, db=db)

    assert response.total_files == 7
    assert response.last_reindex is None


@pytest.mark.asyncio
async def test_get_maintenance_status_maps_db_error_to_http_500() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(HTTPException) as exc_info:
        await maintenance.get_maintenance_status(ctx=None, user=None, db=db)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to get maintenance status"


@pytest.mark.asyncio
async def test_cleanup_orphaned_deactivates_workflows_missing_from_file_index() -> None:
    active_id = uuid4()
    missing_id = uuid4()
    active = SimpleNamespace(
        id=active_id,
        name="active",
        display_name="Active",
        path="workflows/active.py",
        is_active=True,
        is_orphaned=False,
    )
    missing = SimpleNamespace(
        id=missing_id,
        name="missing",
        display_name=None,
        path="workflows/missing.py",
        is_active=True,
        is_orphaned=False,
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _RowsResult([("workflows/active.py",)]),
            _RowsResult([active, missing]),
        ]
    )
    db.commit = AsyncMock()

    response = await maintenance.cleanup_orphaned(user=None, db=db)

    assert response.success is True
    assert response.count == 1
    assert response.cleaned[0].entity_id == str(missing_id)
    assert response.cleaned[0].entity_name == "missing"
    assert active.is_active is True
    assert missing.is_active is False
    assert missing.is_orphaned is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_orphaned_maps_errors_to_http_500() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("select failed"))

    with pytest.raises(HTTPException) as exc_info:
        await maintenance.cleanup_orphaned(user=None, db=db)

    assert exc_info.value.status_code == 500
    assert "Failed to clean up orphaned entities" in exc_info.value.detail
