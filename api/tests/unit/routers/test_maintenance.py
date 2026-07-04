from __future__ import annotations

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
