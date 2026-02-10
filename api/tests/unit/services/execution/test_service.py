"""Tests for execution service functions."""

import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


class TestGetWorkflowForExecution:
    """Test get_workflow_for_execution with optional session."""

    @pytest.mark.asyncio
    async def test_uses_provided_session(self):
        """Should use provided session instead of creating new one."""
        from src.services.execution.service import get_workflow_for_execution

        workflow_id = str(uuid4())
        mock_session = AsyncMock()

        # Create mock workflow record
        mock_workflow = MagicMock()
        mock_workflow.name = "test_workflow"
        mock_workflow.function_name = "run"
        mock_workflow.path = "workflows/test.py"
        mock_workflow.timeout_seconds = 300
        mock_workflow.time_saved = 5
        mock_workflow.value = 10.0
        mock_workflow.execution_mode = "async"
        mock_workflow.organization_id = uuid4()

        # First execute: select(Workflow) -> returns workflow
        mock_wf_result = MagicMock()
        mock_wf_result.scalar_one_or_none.return_value = mock_workflow
        # Second execute: select(FileIndex.content) -> returns code
        mock_fi_result = MagicMock()
        mock_fi_result.scalar_one_or_none.return_value = "def run(): pass"
        mock_session.execute = AsyncMock(side_effect=[mock_wf_result, mock_fi_result])

        result = await get_workflow_for_execution(workflow_id, db=mock_session)

        assert result["name"] == "test_workflow"
        assert result["code"] == "def run(): pass"
        assert mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_creates_session_when_not_provided(self):
        """Should create own session when none provided."""
        from src.services.execution.service import get_workflow_for_execution

        workflow_id = str(uuid4())

        mock_workflow = MagicMock()
        mock_workflow.name = "test_workflow"
        mock_workflow.function_name = "run"
        mock_workflow.path = "workflows/test.py"
        mock_workflow.timeout_seconds = 300
        mock_workflow.time_saved = 5
        mock_workflow.value = 10.0
        mock_workflow.execution_mode = "async"
        mock_workflow.organization_id = uuid4()

        # First execute: select(Workflow) -> returns workflow
        mock_wf_result = MagicMock()
        mock_wf_result.scalar_one_or_none.return_value = mock_workflow
        # Second execute: select(FileIndex.content) -> returns code
        mock_fi_result = MagicMock()
        mock_fi_result.scalar_one_or_none.return_value = "def run(): pass"

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[mock_wf_result, mock_fi_result])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        # Patch at src.core.database since it's imported inside the function
        with patch("src.core.database.get_session_factory", return_value=mock_factory):
            result = await get_workflow_for_execution(workflow_id)

        assert result["name"] == "test_workflow"
        assert result["code"] == "def run(): pass"
        mock_factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_code_none_logs_info(self, caplog):
        """Should log INFO when file_index returns no code (worker fallback)."""
        from src.services.execution.service import get_workflow_for_execution

        workflow_id = str(uuid4())
        mock_session = AsyncMock()

        mock_workflow = MagicMock()
        mock_workflow.name = "test_workflow"
        mock_workflow.function_name = "run"
        mock_workflow.path = "workflows/test.py"
        mock_workflow.timeout_seconds = 300
        mock_workflow.time_saved = 5
        mock_workflow.value = 10.0
        mock_workflow.execution_mode = "async"
        mock_workflow.organization_id = uuid4()

        # First execute: select(Workflow) -> returns workflow
        mock_wf_result = MagicMock()
        mock_wf_result.scalar_one_or_none.return_value = mock_workflow
        # Second execute: select(FileIndex.content) -> returns None
        mock_fi_result = MagicMock()
        mock_fi_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(side_effect=[mock_wf_result, mock_fi_result])

        with caplog.at_level(logging.INFO, logger="src.services.execution.service"):
            result = await get_workflow_for_execution(workflow_id, db=mock_session)

        assert result["code"] is None
        assert any("No code in file_index" in r.message for r in caplog.records)
        assert any("fall back to Redis/S3" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_file_index_error_logs_error(self, caplog):
        """Should log ERROR with exc_info when file_index query fails."""
        from src.services.execution.service import get_workflow_for_execution

        workflow_id = str(uuid4())
        mock_session = AsyncMock()

        mock_workflow = MagicMock()
        mock_workflow.name = "test_workflow"
        mock_workflow.function_name = "run"
        mock_workflow.path = "workflows/test.py"
        mock_workflow.timeout_seconds = 300
        mock_workflow.time_saved = 5
        mock_workflow.value = 10.0
        mock_workflow.execution_mode = "async"
        mock_workflow.organization_id = uuid4()

        # First execute: select(Workflow) -> returns workflow
        mock_wf_result = MagicMock()
        mock_wf_result.scalar_one_or_none.return_value = mock_workflow
        # Second execute: file_index query raises an error
        mock_session.execute = AsyncMock(
            side_effect=[mock_wf_result, Exception("DB connection lost")]
        )

        with caplog.at_level(logging.ERROR, logger="src.services.execution.service"):
            result = await get_workflow_for_execution(workflow_id, db=mock_session)

        assert result["code"] is None
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("Failed to load code from file_index" in r.message for r in error_records)
        # Verify exc_info was included
        assert any(r.exc_info is not None for r in error_records)
