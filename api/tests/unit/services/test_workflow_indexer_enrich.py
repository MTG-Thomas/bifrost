"""Test WorkflowIndexer enrich-only behavior."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy import Update, Insert


SAMPLE_WORKFLOW = '''
from bifrost import workflow

@workflow(name="My Workflow")
def my_workflow(name: str, count: int = 5):
    """A sample workflow."""
    pass
'''


def _is_statement_type(stmt, *types) -> bool:
    """Check if a SQLAlchemy statement is one of the given types."""
    return isinstance(stmt, types)


@pytest.mark.asyncio
async def test_indexer_skips_unregistered_workflow():
    """WorkflowIndexer should NOT create DB records for unregistered functions."""
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    mock_db = AsyncMock()

    # No existing workflow in DB for this path+function
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    indexer = WorkflowIndexer(mock_db)
    await indexer.index_python_file("workflows/new.py", SAMPLE_WORKFLOW.encode())

    # Should have queried for existing workflow but NOT inserted/updated
    assert mock_db.execute.call_count >= 1
    # Verify no INSERT or UPDATE was issued (only SELECT)
    for call in mock_db.execute.call_args_list:
        stmt = call[0][0]
        assert not _is_statement_type(stmt, Insert), "Unexpected INSERT statement"
        assert not _is_statement_type(stmt, Update), "Unexpected UPDATE statement"


@pytest.mark.asyncio
async def test_indexer_enriches_registered_workflow():
    """WorkflowIndexer should UPDATE existing records with content-derived fields."""
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    mock_db = AsyncMock()
    existing_wf = MagicMock()
    existing_wf.id = uuid4()
    existing_wf.is_active = True
    existing_wf.endpoint_enabled = False
    existing_wf.name = "My Workflow"
    existing_wf.path = "workflows/existing.py"
    existing_wf.timeout_seconds = 1800
    existing_wf.time_saved = 0
    existing_wf.value = 0.0
    existing_wf.execution_mode = "async"

    # Return existing workflow on lookup (scalar_one_or_none) and re-fetch (scalar_one)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_wf
    mock_result.scalar_one.return_value = existing_wf
    mock_db.execute.return_value = mock_result

    indexer = WorkflowIndexer(mock_db)

    await indexer.index_python_file("workflows/existing.py", SAMPLE_WORKFLOW.encode())

    # Should have issued an UPDATE (not INSERT)
    calls = mock_db.execute.call_args_list
    update_issued = any(_is_statement_type(call[0][0], Update) for call in calls)
    assert update_issued, "Expected an UPDATE statement for existing workflow"

    # Verify NO INSERT was issued
    insert_issued = any(_is_statement_type(call[0][0], Insert) for call in calls)
    assert not insert_issued, "Should NOT have issued an INSERT"


@pytest.mark.asyncio
async def test_indexer_reactivates_inactive_workflow():
    """WorkflowIndexer should reactivate an inactive workflow and set is_orphaned=False."""
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    mock_db = AsyncMock()
    existing_wf = MagicMock()
    existing_wf.id = uuid4()
    existing_wf.is_active = False  # Deactivated workflow
    existing_wf.endpoint_enabled = False
    existing_wf.name = "My Workflow"
    existing_wf.path = "workflows/existing.py"
    existing_wf.timeout_seconds = 1800
    existing_wf.time_saved = 0
    existing_wf.value = 0.0
    existing_wf.execution_mode = "async"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_wf
    mock_result.scalar_one.return_value = existing_wf
    mock_db.execute.return_value = mock_result

    indexer = WorkflowIndexer(mock_db)
    await indexer.index_python_file("workflows/existing.py", SAMPLE_WORKFLOW.encode())

    # Should have issued an UPDATE
    calls = mock_db.execute.call_args_list
    update_issued = any(_is_statement_type(call[0][0], Update) for call in calls)
    assert update_issued, "Expected an UPDATE statement to reactivate workflow"

    # Verify the UPDATE sets is_active=True and is_orphaned=False
    for call in calls:
        stmt = call[0][0]
        if _is_statement_type(stmt, Update):
            params = stmt.compile().params
            assert params.get("is_active") is True, "Should set is_active=True"
            assert params.get("is_orphaned") is False, "Should set is_orphaned=False"
            break
