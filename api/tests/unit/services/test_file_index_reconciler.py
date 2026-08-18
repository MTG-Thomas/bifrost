from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services import file_index_reconciler
from src.services.file_index_reconciler import reconcile_file_index


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.fixture
def db_session():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_reconcile_adds_text_files_missing_from_index(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = SimpleNamespace(
        list=AsyncMock(return_value=["workflows/a.py", "assets/logo.png"]),
        read=AsyncMock(return_value=b"print('hello')"),
    )
    db_session.execute.return_value = _RowsResult([])
    monkeypatch.setattr(
        file_index_reconciler, "_is_text_file", lambda path: path.endswith(".py")
    )

    stats = await reconcile_file_index(db_session, repo)

    assert stats == {
        "added": 1,
        "removed": 0,
        "updated": 0,
        "unchanged": 0,
    }
    repo.read.assert_awaited_once_with("workflows/a.py")
    assert db_session.execute.await_count == 2
    db_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_removes_db_rows_missing_from_durable_repo(db_session) -> None:
    repo = SimpleNamespace(
        list=AsyncMock(return_value=[]),
        write=AsyncMock(return_value="hash"),
    )
    db_session.execute.side_effect = [
        _RowsResult([("workflows/a.py", "stale-hash")]),
        MagicMock(),
    ]

    stats = await reconcile_file_index(db_session, repo)

    assert stats["removed"] == 1
    repo.write.assert_not_awaited()
    db_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_removes_orphaned_row_without_content(db_session) -> None:
    repo = SimpleNamespace(
        list=AsyncMock(return_value=[]),
        write=AsyncMock(),
    )
    db_session.execute.side_effect = [
        _RowsResult([("workflows/missing.py", None)]),
        MagicMock(),
    ]

    stats = await reconcile_file_index(db_session, repo)

    assert stats["removed"] == 1
    repo.write.assert_not_awaited()
    assert db_session.execute.await_count == 2


@pytest.mark.asyncio
async def test_reconcile_continues_when_durable_read_fails(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = SimpleNamespace(
        list=AsyncMock(return_value=["new.py"]),
        read=AsyncMock(side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")),
        write=AsyncMock(side_effect=RuntimeError("storage offline")),
    )
    db_session.execute.side_effect = [
        _RowsResult([("old.py", "old-hash")]),
        MagicMock(),
    ]
    monkeypatch.setattr(file_index_reconciler, "_is_text_file", lambda path: True)

    stats = await reconcile_file_index(db_session, repo)

    assert stats == {
        "added": 0,
        "removed": 1,
        "updated": 0,
        "unchanged": 0,
    }
    repo.read.assert_awaited_once_with("new.py")
    repo.write.assert_not_awaited()
    db_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_replaces_stale_index_content_from_durable_repo(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = SimpleNamespace(
        list=AsyncMock(return_value=["modules/halopsa.py"]),
        read=AsyncMock(return_value=b"reviewed source\n"),
        write=AsyncMock(),
    )
    db_session.execute.side_effect = [
        _RowsResult([("modules/halopsa.py", "stale-hash")]),
        MagicMock(),
    ]
    monkeypatch.setattr(file_index_reconciler, "_is_text_file", lambda path: True)

    stats = await reconcile_file_index(db_session, repo)

    assert stats == {"added": 0, "removed": 0, "updated": 1, "unchanged": 0}
    repo.write.assert_not_awaited()
