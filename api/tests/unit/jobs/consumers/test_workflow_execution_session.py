"""Tests for WorkflowExecutionConsumer persistent DB session."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestConsumerSessionLifecycle:
    """Test persistent DB session management."""

    @pytest.fixture
    def mock_session_factory(self):
        """Create mock session factory."""
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock())

        factory = MagicMock()
        factory.return_value = mock_session
        return factory, mock_session

    @pytest.mark.asyncio
    async def test_start_creates_db_session(self, mock_session_factory):
        """Consumer.start() should create persistent DB session."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        factory, mock_session = mock_session_factory

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._pool = AsyncMock()
            consumer._pool.start = AsyncMock()
            consumer._pool_started = False
            consumer._session_factory = factory
            consumer._db_session = None

            # Mock parent start
            with patch.object(
                WorkflowExecutionConsumer.__bases__[0], "start", AsyncMock()
            ):
                await consumer.start()

            assert consumer._db_session is not None
            factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_closes_db_session(self, mock_session_factory):
        """Consumer.stop() should close persistent DB session."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        factory, mock_session = mock_session_factory

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._pool = AsyncMock()
            consumer._pool.stop = AsyncMock()
            consumer._pool_started = True
            consumer._db_session = mock_session

            # Mock parent stop
            with patch.object(
                WorkflowExecutionConsumer.__bases__[0], "stop", AsyncMock()
            ):
                await consumer.stop()

            mock_session.close.assert_called_once()
            assert consumer._db_session is None


class TestConsumerSessionReconnection:
    """Test session health check and reconnection."""

    @pytest.mark.asyncio
    async def test_get_db_session_returns_healthy_session(self):
        """_get_db_session() returns existing session when healthy."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._db_session = mock_session
            consumer._session_factory = MagicMock()

            result = await consumer._get_db_session()

            assert result is mock_session
            mock_session.execute.assert_called_once()  # Health check ran

    @pytest.mark.asyncio
    async def test_get_db_session_reconnects_on_stale(self):
        """_get_db_session() reconnects when session is stale."""
        from sqlalchemy.exc import DBAPIError
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        stale_session = AsyncMock()
        stale_session.execute = AsyncMock(side_effect=DBAPIError("connection closed", None, None))
        stale_session.close = AsyncMock()

        fresh_session = AsyncMock()
        fresh_session.execute = AsyncMock(return_value=MagicMock())

        factory = MagicMock()
        factory.return_value = fresh_session

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._db_session = stale_session
            consumer._session_factory = factory

            result = await consumer._get_db_session()

            assert result is fresh_session
            stale_session.close.assert_called_once()
            factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_db_session_creates_when_none(self):
        """_get_db_session() creates session when None."""
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        new_session = AsyncMock()
        new_session.execute = AsyncMock(return_value=MagicMock())

        factory = MagicMock()
        factory.return_value = new_session

        with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
            consumer = WorkflowExecutionConsumer()
            consumer._db_session = None
            consumer._session_factory = factory

            result = await consumer._get_db_session()

            assert result is new_session
            factory.assert_called_once()


