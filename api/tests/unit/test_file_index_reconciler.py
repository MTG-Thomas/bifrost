"""Tests for file_index reconciler."""
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_repo_storage():
    storage = AsyncMock()
    storage.write = AsyncMock(return_value="fakehash")
    return storage


@pytest.mark.asyncio
async def test_reconciler_adds_missing_files(mock_db, mock_repo_storage):
    """Files in S3 but not in file_index should be added."""
    from src.services.file_index_reconciler import reconcile_file_index

    # S3 has two files
    mock_repo_storage.list.return_value = ["workflows/a.py", "workflows/b.py"]
    mock_repo_storage.read.return_value = b"print('hello')"

    # DB has only one
    db_result = MagicMock()
    db_result.all.return_value = [("workflows/a.py", "existing-hash")]
    mock_db.execute = AsyncMock(return_value=db_result)

    stats = await reconcile_file_index(mock_db, mock_repo_storage)

    assert stats["added"] >= 1


@pytest.mark.asyncio
async def test_reconciler_removes_db_only_entries(mock_db, mock_repo_storage):
    """file_index entries not in durable storage must be removed, not restored."""
    from src.services.file_index_reconciler import reconcile_file_index

    # S3 has one file
    mock_repo_storage.list.return_value = ["workflows/a.py"]
    mock_repo_storage.read.return_value = b"print('hello')"

    # DB has two (one only in DB)
    db_result = MagicMock()
    db_result.all.return_value = [
        ("workflows/a.py", hashlib.sha256(b"print('hello')").hexdigest()),
        ("workflows/db_only.py", "stale-hash"),
    ]

    mock_db.execute = AsyncMock(side_effect=[db_result, MagicMock()])

    stats = await reconcile_file_index(mock_db, mock_repo_storage)

    assert stats["removed"] == 1
    mock_repo_storage.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_removes_orphaned_null_content(mock_db, mock_repo_storage):
    """DB entries with NULL content and no S3 file should be removed."""
    from src.services.file_index_reconciler import reconcile_file_index

    mock_repo_storage.list.return_value = []

    db_result = MagicMock()
    db_result.all.return_value = [("workflows/orphaned.py", None)]

    delete_result = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[db_result, delete_result])

    stats = await reconcile_file_index(mock_db, mock_repo_storage)

    assert stats["removed"] >= 1
    assert stats["added"] == 0


@pytest.mark.asyncio
async def test_reconciler_treats_empty_storage_as_authoritative(
    mock_db, mock_repo_storage
):
    """Empty durable storage removes index rows rather than recreating source."""
    from src.services.file_index_reconciler import reconcile_file_index

    mock_repo_storage.list.return_value = []

    db_result = MagicMock()
    db_result.all.return_value = [("workflows/old.py", "stale-hash")]

    mock_db.execute = AsyncMock(side_effect=[db_result, MagicMock()])

    stats = await reconcile_file_index(mock_db, mock_repo_storage)

    assert stats["removed"] == 1
    assert stats["added"] == 0
    mock_repo_storage.write.assert_not_awaited()
