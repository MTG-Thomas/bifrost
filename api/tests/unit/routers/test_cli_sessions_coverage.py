from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response, status

from src.core.principal import UserPrincipal
from src.models.contracts.cli import (
    CLISessionContinueRequest,
    CLISessionLogRequest,
    CLISessionResultRequest,
)
from src.models.enums import ExecutionStatus
from src.routers import cli


def _user() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        name="Operator",
        organization_id=uuid4(),
        is_superuser=False,
    )


def _db(execute_result=None) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add_all = MagicMock()
    return db


def _scalar_result(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


class TestCLISessionHttpBranches:
    @pytest.mark.asyncio
    async def test_get_and_delete_reject_bad_session_ids(self) -> None:
        user = _user()
        db = _db()

        with patch.object(cli, "CLISessionRepository"):
            for call in (
                cli.get_cli_session("not-a-uuid", user, db),
                cli.delete_cli_session("not-a-uuid", user, db),
            ):
                with pytest.raises(HTTPException) as exc:
                    await call
                assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
                assert exc.value.detail == "Invalid session ID format"

    @pytest.mark.asyncio
    async def test_get_delete_and_continue_report_missing_session(self) -> None:
        user = _user()
        db = _db()
        repo = MagicMock()
        repo.get_session_for_user = AsyncMock(return_value=None)

        with patch.object(cli, "CLISessionRepository", return_value=repo):
            for call, expected_detail in (
                (cli.get_cli_session(str(uuid4()), user, db), "Session not found"),
                (cli.delete_cli_session(str(uuid4()), user, db), "Session not found"),
                (
                    cli.continue_cli_session(
                        str(uuid4()),
                        CLISessionContinueRequest(workflow_name="sync", params={}),
                        user,
                        db,
                    ),
                    "No active CLI session. Run `bifrost run <file>` first.",
                ),
            ):
                with pytest.raises(HTTPException) as exc:
                    await call
                assert exc.value.status_code == status.HTTP_404_NOT_FOUND
                assert exc.value.detail == expected_detail

    @pytest.mark.asyncio
    async def test_continue_rejects_unknown_workflow_before_creating_execution(self) -> None:
        user = _user()
        db = _db()
        repo = MagicMock()
        repo.get_session_for_user = AsyncMock(
            return_value=SimpleNamespace(workflows=[{"name": "known"}])
        )

        with (
            patch.object(cli, "CLISessionRepository", return_value=repo),
            patch("src.repositories.executions.ExecutionRepository") as exec_repo_cls,
        ):
            with pytest.raises(HTTPException) as exc:
                await cli.continue_cli_session(
                    str(uuid4()),
                    CLISessionContinueRequest(workflow_name="missing", params={"x": 1}),
                    user,
                    db,
                )

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Workflow 'missing' not found" in exc.value.detail
        exec_repo_cls.assert_not_called()


class TestCLISessionPendingAndResult:
    @pytest.mark.asyncio
    async def test_pending_returns_204_when_session_missing_or_not_ready(self) -> None:
        user = _user()
        db = _db()
        repo = MagicMock()
        repo.get_session_for_user = AsyncMock(
            side_effect=[
                None,
                SimpleNamespace(pending=False),
                SimpleNamespace(pending=True, selected_workflow=None, params={"x": 1}),
                SimpleNamespace(pending=True, selected_workflow="sync", params=None),
            ]
        )

        with patch.object(cli, "CLISessionRepository", return_value=repo):
            for _ in range(4):
                response = await cli.get_pending_execution(str(uuid4()), user, db)
                assert isinstance(response, Response)
                assert response.status_code == status.HTTP_204_NO_CONTENT

        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_promotes_execution_to_running_and_clears_session(self) -> None:
        user = _user()
        session_id = uuid4()
        execution_id = uuid4()
        execution = SimpleNamespace(
            id=execution_id,
            executed_by=user.user_id,
            executed_by_name=user.name,
            workflow_name="sync",
            organization_id=user.organization_id,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db = _db(_scalar_result(execution))
        repo = MagicMock()
        repo.get_session_for_user = AsyncMock(
            return_value=SimpleNamespace(
                pending=True,
                selected_workflow="sync",
                params={"tenant": "contoso"},
            )
        )
        repo.clear_pending = AsyncMock()
        repo.update_last_seen = AsyncMock()
        repo.get_session_with_executions = AsyncMock(return_value=None)
        exec_repo = MagicMock()
        exec_repo.update_execution = AsyncMock()

        with (
            patch.object(cli, "CLISessionRepository", return_value=repo),
            patch("src.repositories.executions.ExecutionRepository", return_value=exec_repo),
            patch.object(cli, "publish_execution_update", AsyncMock()) as publish_execution_update,
            patch.object(cli, "publish_history_update", AsyncMock()) as publish_history_update,
        ):
            result = await cli.get_pending_execution(str(session_id), user, db)

        assert result.execution_id == str(execution_id)
        assert result.workflow_name == "sync"
        assert result.params == {"tenant": "contoso"}
        exec_repo.update_execution.assert_awaited_once_with(
            execution_id=str(execution_id),
            status=ExecutionStatus.RUNNING,
        )
        repo.clear_pending.assert_awaited_once_with(session_id)
        repo.update_last_seen.assert_awaited_once_with(session_id)
        db.commit.assert_awaited_once()
        publish_execution_update.assert_awaited_once_with(str(execution_id), "Running")
        publish_history_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_result_persists_request_logs_and_publishes_updates(self) -> None:
        user = _user()
        session_id = uuid4()
        execution_id = uuid4()
        started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        execution = SimpleNamespace(
            executed_by=user.user_id,
            executed_by_name=user.name,
            workflow_name="sync",
            organization_id=user.organization_id,
            started_at=started_at,
            completed_at=None,
        )
        db = _db(_scalar_result(execution))
        exec_repo = MagicMock()
        exec_repo.update_execution = AsyncMock()
        session_repo = MagicMock()
        session_repo.get_session_with_executions = AsyncMock(return_value=None)

        request = CLISessionResultRequest(
            status="completed",
            result={"ok": True},
            duration_ms=25,
            logs=[
                CLISessionLogRequest(
                    level="info",
                    message="done",
                    timestamp="2026-01-01T00:00:00+00:00",
                    metadata={"step": 1},
                )
            ],
        )

        with (
            patch("src.repositories.executions.ExecutionRepository", return_value=exec_repo),
            patch.object(cli, "CLISessionRepository", return_value=session_repo),
            patch.object(cli, "publish_execution_log", AsyncMock()) as publish_execution_log,
            patch.object(cli, "publish_execution_update", AsyncMock()) as publish_execution_update,
            patch.object(cli, "publish_history_update", AsyncMock()) as publish_history_update,
        ):
            result = await cli.post_cli_result(str(session_id), str(execution_id), request, user, db)

        assert result == {
            "status": "Success",
            "logs_persisted": 1,
            "logs_flushed": 0,
            "total_logs": 1,
        }
        exec_repo.update_execution.assert_awaited_once_with(
            execution_id=str(execution_id),
            status=ExecutionStatus.SUCCESS,
            result={"ok": True},
            error_message=None,
            duration_ms=25,
        )
        db.add_all.assert_called_once()
        db.commit.assert_awaited_once()
        publish_execution_log.assert_awaited_once()
        publish_execution_update.assert_awaited_once_with(
            str(execution_id),
            "Success",
            {"result": {"ok": True}, "durationMs": 25},
        )
        publish_history_update.assert_awaited_once()
