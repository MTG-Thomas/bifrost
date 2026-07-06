from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.jobs.schedulers import knowledge_storage_refresh


class _ExecuteResult:
    def __init__(self, *, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_refresh_knowledge_storage_daily_inserts_aggregated_rows(monkeypatch):
    added_rows = []
    committed = False
    db = SimpleNamespace(
        execute=None,
        add_all=lambda rows: added_rows.extend(rows),
        commit=None,
    )

    async def execute(_statement):
        if execute.calls == 0:
            execute.calls += 1
            return _ExecuteResult(rowcount=2)
        return _ExecuteResult(
            rows=[
                SimpleNamespace(
                    organization_id="org-a",
                    namespace="default",
                    doc_count=3,
                    size_bytes=1048576,
                ),
                SimpleNamespace(
                    organization_id="org-b",
                    namespace="kb",
                    doc_count=None,
                    size_bytes=None,
                ),
            ]
        )

    execute.calls = 0

    async def commit():
        nonlocal committed
        committed = True

    db.execute = execute
    db.commit = commit

    @asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr(
        knowledge_storage_refresh, "get_session_factory", lambda: fake_session
    )

    result = await knowledge_storage_refresh.refresh_knowledge_storage_daily()

    assert result["rows"] == 2
    assert result["total_documents"] == 3
    assert result["total_size_bytes"] == 1048576
    assert result["total_size_mb"] == 1.0
    assert len(added_rows) == 2
    assert added_rows[0].namespace == "default"
    assert added_rows[0].document_count == 3
    assert added_rows[1].document_count == 0
    assert committed is True


@pytest.mark.asyncio
async def test_refresh_knowledge_storage_daily_skips_commit_when_no_rows(monkeypatch):
    committed = False
    db = SimpleNamespace(execute=None, add_all=lambda rows: None, commit=None)

    async def execute(_statement):
        if execute.calls == 0:
            execute.calls += 1
            return _ExecuteResult(rowcount=0)
        return _ExecuteResult(rows=[])

    execute.calls = 0

    async def commit():
        nonlocal committed
        committed = True

    db.execute = execute
    db.commit = commit

    @asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr(
        knowledge_storage_refresh, "get_session_factory", lambda: fake_session
    )

    result = await knowledge_storage_refresh.refresh_knowledge_storage_daily()

    assert result["rows"] == 0
    assert result["total_documents"] == 0
    assert result["total_size_bytes"] == 0
    assert committed is False


@pytest.mark.asyncio
async def test_refresh_knowledge_storage_daily_returns_error(monkeypatch):
    @asynccontextmanager
    async def fake_session():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(
        knowledge_storage_refresh, "get_session_factory", lambda: fake_session
    )

    result = await knowledge_storage_refresh.refresh_knowledge_storage_daily()

    assert result == {"error": "database unavailable"}
