import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.execution.agent_run_service import (
    enqueue_agent_run,
    enqueue_agent_run_once,
)


@pytest.fixture(autouse=True)
def mock_agent_run_database():
    db = MagicMock()
    db.execute = AsyncMock()
    stored = {}

    def add(row):
        stored["row"] = row

    async def get(_model, _row_id):
        return stored.get("row")

    db.add.side_effect = add
    db.get = AsyncMock(side_effect=get)
    db.commit = AsyncMock()

    @asynccontextmanager
    async def db_context():
        yield db

    with patch("src.core.database.get_db_context", side_effect=db_context):
        yield db


class TestEnqueueAgentRun:
    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_returns_run_id(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="event",
            input_data={"ticket_id": 123},
        )

        assert run_id is not None
        mock_publish.assert_called_once()

        # Verify queue name
        call_args = mock_publish.call_args
        assert call_args[0][0] == "agent-runs"

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_stores_context_in_redis(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        org_id = str(uuid4())
        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            input_data={"task": "analyze"},
            output_schema={"action": {"type": "string"}},
            org_id=org_id,
            caller_user_id=str(uuid4()),
        )

        mock_redis.set.assert_called_once()
        context = json.loads(mock_redis.set.call_args.args[1])
        assert context["caller"]["organization_id"] == org_id

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_uses_provided_run_id(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        expected_run_id = str(uuid4())
        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            run_id=expected_run_id,
        )

        assert run_id == expected_run_id

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_message_contains_sync_flag(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            sync=True,
        )

        call_args = mock_publish.call_args
        message = call_args[0][1]
        assert message["sync"] is True

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_retry_reuses_durable_run_without_republishing(
        self,
        mock_get_redis,
        mock_publish,
        mock_agent_run_database,
    ):
        expected_run_id = str(uuid4())
        mock_agent_run_database.get.side_effect = None
        mock_agent_run_database.get.return_value = object()

        run_id, reused = await enqueue_agent_run_once(
            agent_id=str(uuid4()),
            trigger_type="delegation",
            run_id=expected_run_id,
        )

        assert run_id == expected_run_id
        assert reused is True
        mock_get_redis.assert_not_called()
        mock_publish.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_scheduled_retry_publishes_once_and_marks_queued(
        self,
        mock_get_redis,
        mock_publish,
        mock_agent_run_database,
    ):
        expected_run_id = str(uuid4())
        scheduled = MagicMock(status="scheduled")
        mock_agent_run_database.get.side_effect = [scheduled, scheduled]
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        run_id, reused = await enqueue_agent_run_once(
            agent_id=str(uuid4()),
            trigger_type="delegation",
            run_id=expected_run_id,
        )

        assert run_id == expected_run_id
        assert reused is True
        assert scheduled.status == "queued"
        mock_publish.assert_awaited_once()
        mock_agent_run_database.commit.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(
        "src.services.execution.agent_run_service.publish_message",
        side_effect=RuntimeError("broker unavailable"),
    )
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_publication_failure_leaves_scheduled_run_retryable(
        self,
        mock_get_redis,
        mock_publish,
        mock_agent_run_database,
    ):
        scheduled = MagicMock(status="scheduled")
        mock_agent_run_database.get.side_effect = [None, scheduled]
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        with pytest.raises(RuntimeError, match="broker unavailable"):
            await enqueue_agent_run_once(
                agent_id=str(uuid4()),
                trigger_type="delegation",
                run_id=str(uuid4()),
            )

        assert scheduled.status == "scheduled"
        mock_publish.assert_awaited_once()
        # Only the durable pre-publication row was committed.
        mock_agent_run_database.commit.assert_awaited_once()
