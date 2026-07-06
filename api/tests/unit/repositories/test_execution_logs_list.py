"""
Unit tests for ExecutionLogRepository.list_logs method.

Tests the database operations for listing execution logs with filtering and pagination.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.repositories import execution_logs
from src.repositories.execution_logs import ExecutionLogRepository


class TestExecutionLogRepositoryListLogs:
    """Tests for ExecutionLogRepository.list_logs method."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def repository(self, mock_session):
        """Create repository with mock session."""
        return ExecutionLogRepository(mock_session)

    @pytest.fixture
    def mock_log(self):
        """Create a mock log with joined execution and organization data."""
        log = MagicMock()
        log.id = 1
        log.execution_id = uuid4()
        log.level = "ERROR"
        log.message = "Connection failed"
        log.timestamp = datetime.now(timezone.utc)
        log.execution = MagicMock()
        log.execution.workflow_name = "test-workflow"
        log.execution.organization = MagicMock()
        log.execution.organization.name = "Test Org"
        return log

    @pytest.mark.asyncio
    async def test_list_logs_returns_paginated_results(
        self, repository, mock_session, mock_log
    ):
        """Test that list_logs returns logs with execution and org data."""
        # Arrange - return limit+1 to indicate more pages
        mock_log2 = MagicMock()
        mock_log2.id = 2
        mock_log2.execution_id = mock_log.execution_id
        mock_log2.level = "INFO"
        mock_log2.message = "Processing started"
        mock_log2.timestamp = datetime.now(timezone.utc)
        mock_log2.execution = mock_log.execution

        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = [mock_log, mock_log2]
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        logs, next_token = await repository.list_logs(limit=1, offset=0)

        # Assert
        assert len(logs) == 1  # Should only return limit, not limit+1
        assert next_token == "1"  # Next offset
        assert logs[0]["workflow_name"] == "test-workflow"
        assert logs[0]["organization_name"] == "Test Org"
        assert logs[0]["level"] == "ERROR"

    @pytest.mark.asyncio
    async def test_list_logs_no_more_pages(self, repository, mock_session, mock_log):
        """Test that list_logs returns None token when no more pages."""
        # Arrange - return exactly limit (no more pages)
        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = [mock_log]
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        logs, next_token = await repository.list_logs(limit=2, offset=0)

        # Assert
        assert len(logs) == 1
        assert next_token is None  # No more pages

    @pytest.mark.asyncio
    async def test_list_logs_empty_results(self, repository, mock_session):
        """Test that list_logs handles empty results."""
        # Arrange
        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = []
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        logs, next_token = await repository.list_logs(limit=10, offset=0)

        # Assert
        assert len(logs) == 0
        assert next_token is None

    @pytest.mark.asyncio
    async def test_list_logs_with_org_filter(self, repository, mock_session, mock_log):
        """Test that list_logs accepts organization_id filter."""
        # Arrange
        org_id = uuid4()
        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = [mock_log]
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        logs, next_token = await repository.list_logs(organization_id=org_id, limit=10)

        # Assert
        assert len(logs) == 1
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_logs_with_level_filter(
        self, repository, mock_session, mock_log
    ):
        """Test that list_logs accepts levels filter."""
        # Arrange
        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = [mock_log]
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        logs, next_token = await repository.list_logs(levels=["ERROR", "WARNING"])

        # Assert
        assert len(logs) == 1
        assert logs[0]["level"] == "ERROR"

    @pytest.mark.asyncio
    async def test_list_logs_with_workflow_filter(
        self, repository, mock_session, mock_log
    ):
        """Test that list_logs accepts workflow_name filter."""
        # Arrange
        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = [mock_log]
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        logs, next_token = await repository.list_logs(workflow_name="test")

        # Assert
        assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_list_logs_with_message_search(
        self, repository, mock_session, mock_log
    ):
        """Test that list_logs accepts message_search filter."""
        # Arrange
        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = [mock_log]
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        logs, next_token = await repository.list_logs(message_search="Connection")

        # Assert
        assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_list_logs_with_date_range(self, repository, mock_session, mock_log):
        """Test that list_logs accepts start_date and end_date filters."""
        # Arrange
        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = [mock_log]
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        start = datetime(2024, 1, 1)
        end = datetime(2024, 12, 31)

        # Act
        logs, next_token = await repository.list_logs(start_date=start, end_date=end)

        # Assert
        assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_list_logs_null_organization(self, repository, mock_session):
        """Test that list_logs handles logs with null organization."""
        # Arrange
        mock_log = MagicMock()
        mock_log.id = 1
        mock_log.execution_id = uuid4()
        mock_log.level = "INFO"
        mock_log.message = "Test message"
        mock_log.timestamp = datetime.now(timezone.utc)
        mock_log.execution = MagicMock()
        mock_log.execution.workflow_name = "test-workflow"
        mock_log.execution.organization = None  # No organization

        mock_result = MagicMock()
        mock_unique = MagicMock()
        mock_unique.all.return_value = [mock_log]
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_unique
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Act
        logs, next_token = await repository.list_logs()

        # Assert
        assert len(logs) == 1
        assert logs[0]["organization_name"] is None
        assert logs[0]["workflow_name"] == "test-workflow"


class TestExecutionLogRepositoryCore:
    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def repository(self, mock_session):
        return ExecutionLogRepository(mock_session)

    @pytest.mark.asyncio
    async def test_append_log_uppercases_level_and_flushes(self, repository, mock_session):
        execution_id = uuid4()
        timestamp = datetime(2026, 7, 4, tzinfo=timezone.utc)

        log = await repository.append_log(
            execution_id,
            "warning",
            "careful",
            metadata={"source": "worker"},
            timestamp=timestamp,
        )

        assert log.execution_id == execution_id
        assert log.level == "WARNING"
        assert log.message == "careful"
        assert log.log_metadata == {"source": "worker"}
        assert log.timestamp == timestamp
        mock_session.add.assert_called_once_with(log)
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_append_logs_batch_defaults_missing_values(self, repository, mock_session):
        execution_id = uuid4()
        timestamp = datetime(2026, 7, 4, 12, tzinfo=timezone.utc)

        logs = await repository.append_logs_batch(
            execution_id,
            [
                {"level": "debug", "message": "one", "data": {"n": 1}, "timestamp": timestamp},
                {},
            ],
        )

        assert [log.level for log in logs] == ["DEBUG", "INFO"]
        assert logs[0].message == "one"
        assert logs[0].log_metadata == {"n": 1}
        assert logs[1].message == ""
        assert mock_session.add.call_count == 2
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_logs_applies_filters_and_returns_scalars(self, repository, mock_session):
        log = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [log]
        mock_session.execute.return_value = result

        logs = await repository.get_logs(
            uuid4(),
            since_timestamp=datetime(2026, 7, 4, tzinfo=timezone.utc),
            level_filter=["warning", "error"],
            limit=25,
        )

        assert logs == [log]
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_logs_as_dicts_formats_timestamp_and_metadata(self, repository, mock_session):
        timestamp = datetime(2026, 7, 4, 12, 30, tzinfo=timezone.utc)
        log = MagicMock()
        log.id = 123
        log.timestamp = timestamp
        log.level = "INFO"
        log.message = "hello"
        log.log_metadata = {"a": 1}
        result = MagicMock()
        result.scalars.return_value.all.return_value = [log]
        mock_session.execute.return_value = result

        logs = await repository.get_logs_as_dicts(
            uuid4(),
            since_timestamp=timestamp,
            exclude_levels=["debug"],
            limit=10,
        )

        assert logs == [
            {
                "id": 123,
                "timestamp": timestamp.isoformat(),
                "level": "INFO",
                "message": "hello",
                "data": {"a": 1},
            }
        ]

    @pytest.mark.asyncio
    async def test_count_and_delete_logs(self, repository, mock_session):
        count_result = MagicMock()
        count_result.scalar_one.return_value = 4
        delete_result = MagicMock()
        delete_result.rowcount = 3
        mock_session.execute.side_effect = [count_result, delete_result]
        execution_id = uuid4()

        assert await repository.count_logs(execution_id) == 4
        assert await repository.delete_logs(execution_id) == 3
        assert mock_session.execute.await_count == 2
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_compat_repository_reuses_session_and_merges_source_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.execute = AsyncMock()
        session.close = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute.return_value = result
        monkeypatch.setattr("src.core.database.get_session_factory", lambda: lambda: session)

        compat = execution_logs.get_execution_logs_repository()
        execution_id = uuid4()

        await compat.append_log(
            str(execution_id),
            "info",
            "started",
            metadata={"job": "sync"},
            source="worker",
        )
        await compat.get_logs(str(execution_id), level_filter=["info"], limit=5)
        await compat.get_logs_as_dicts(str(execution_id), exclude_levels=["debug"], limit=5)
        await compat.close()

        added_log = session.add.call_args.args[0]
        assert added_log.execution_id == execution_id
        assert added_log.log_metadata == {"job": "sync", "source": "worker"}
        assert session.close.await_count == 1
