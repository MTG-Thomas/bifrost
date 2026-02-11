"""Tests for file_index reconciler."""
import pytest
from unittest.mock import AsyncMock, MagicMock


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
    db_result.all.return_value = [("workflows/a.py",)]
    mock_db.execute = AsyncMock(return_value=db_result)

    stats = await reconcile_file_index(mock_db, mock_repo_storage)

    assert stats["added"] >= 1


@pytest.mark.asyncio
async def test_reconciler_reverse_syncs_db_only_entries(mock_db, mock_repo_storage):
    """file_index entries not in S3 should be reverse-synced to S3."""
    from src.services.file_index_reconciler import reconcile_file_index

    # S3 has one file
    mock_repo_storage.list.return_value = ["workflows/a.py"]
    mock_repo_storage.read.return_value = b"print('hello')"

    # DB has two (one only in DB)
    db_result = MagicMock()
    db_result.all.return_value = [("workflows/a.py",), ("workflows/db_only.py",)]

    # For the reverse-sync content read, return actual content
    content_result = MagicMock()
    content_result.scalar_one_or_none.return_value = "print('db content')"

    mock_db.execute = AsyncMock(side_effect=[
        db_result,   # select FileIndex.path
        db_result,   # select FileIndex.path (re-read in add loop - no files to add)
        content_result,  # select FileIndex.content for reverse-sync
    ])

    stats = await reconcile_file_index(mock_db, mock_repo_storage)

    assert stats["reverse_synced"] >= 1


@pytest.mark.asyncio
async def test_reconciler_removes_orphaned_null_content(mock_db, mock_repo_storage):
    """DB entries with NULL content and no S3 file should be removed."""
    from src.services.file_index_reconciler import reconcile_file_index

    mock_repo_storage.list.return_value = []

    db_result = MagicMock()
    db_result.all.return_value = [("workflows/orphaned.py",)]

    # content is None — orphaned row with no content
    content_result = MagicMock()
    content_result.scalar_one_or_none.return_value = None

    delete_result = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[
        db_result,       # select FileIndex.path
        content_result,  # select FileIndex.content for reverse-sync (returns None)
        delete_result,   # delete FileIndex where path = ...
    ])

    stats = await reconcile_file_index(mock_db, mock_repo_storage)

    assert stats["removed"] >= 1
    assert stats["added"] == 0


@pytest.mark.asyncio
async def test_reconciler_handles_empty_s3_with_db_content(mock_db, mock_repo_storage):
    """Empty S3 but DB entries with content should reverse-sync."""
    from src.services.file_index_reconciler import reconcile_file_index

    mock_repo_storage.list.return_value = []

    db_result = MagicMock()
    db_result.all.return_value = [("workflows/old.py",)]

    content_result = MagicMock()
    content_result.scalar_one_or_none.return_value = "print('old code')"

    mock_db.execute = AsyncMock(side_effect=[
        db_result,       # select FileIndex.path
        content_result,  # select FileIndex.content for reverse-sync
    ])

    stats = await reconcile_file_index(mock_db, mock_repo_storage)

    assert stats["reverse_synced"] >= 1
    assert stats["added"] == 0
