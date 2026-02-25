"""
Unit tests for POST /api/email/send SDK endpoint.

Tests workflow lookup, execution, missing config error.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.routers.email_config import (
    EmailSendRequest,
    EmailSendResponse,
    send_email_sdk,
)


class TestEmailSendRequestModel:
    """Test EmailSendRequest validation."""

    def test_required_fields(self):
        req = EmailSendRequest(
            recipient="user@example.com",
            subject="Test",
            body="Hello",
        )
        assert req.recipient == "user@example.com"
        assert req.html_body is None
        assert req.scope is None

    def test_all_fields(self):
        req = EmailSendRequest(
            recipient="user@example.com",
            subject="Test",
            body="Hello",
            html_body="<h1>Hello</h1>",
            scope="org-123",
        )
        assert req.html_body == "<h1>Hello</h1>"
        assert req.scope == "org-123"


class TestEmailSendResponseModel:
    """Test EmailSendResponse shape."""

    def test_success(self):
        resp = EmailSendResponse(success=True, execution_id="exec-1")
        assert resp.success is True
        assert resp.error is None

    def test_failure(self):
        resp = EmailSendResponse(success=False, error="Not configured")
        assert resp.success is False
        assert resp.error == "Not configured"


class TestSendEmailEndpoint:
    """Test send_email_sdk endpoint."""

    @pytest.mark.asyncio
    async def test_returns_error_when_not_configured(self):
        """Returns error when email workflow is not configured."""
        db = AsyncMock()
        user = MagicMock()
        req = EmailSendRequest(
            recipient="user@example.com",
            subject="Test",
            body="Hello",
        )

        with patch("src.routers.email_config.EmailService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.get_config = AsyncMock(return_value=None)
            mock_svc_class.return_value = mock_svc

            result = await send_email_sdk(req, db, user)

        assert result.success is False
        assert "not configured" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_success_on_workflow_success(self):
        """Returns success when workflow executes successfully."""
        db = AsyncMock()
        user = MagicMock()
        req = EmailSendRequest(
            recipient="user@example.com",
            subject="Test",
            body="Hello",
            scope="org-abc",
        )

        workflow_id = str(uuid4())
        mock_config = MagicMock()
        mock_config.workflow_id = workflow_id

        mock_result = MagicMock()
        mock_result.status = "Success"
        mock_result.execution_id = "exec-123"

        with (
            patch("src.routers.email_config.EmailService") as mock_svc_class,
            patch("src.routers.email_config.send_email_sdk.__module__", create=True),
        ):
            mock_svc = MagicMock()
            mock_svc.get_config = AsyncMock(return_value=mock_config)
            mock_svc_class.return_value = mock_svc

            # We need to patch the imports inside the function
            with (
                patch("src.sdk.context.ExecutionContext") as mock_ctx_class,
                patch("src.services.execution.service.run_workflow", new_callable=AsyncMock) as mock_run,
                patch("src.config.get_settings") as mock_settings,
            ):
                mock_settings.return_value.public_url = "https://test.bifrost.io"
                mock_ctx_class.return_value = MagicMock()
                mock_run.return_value = mock_result

                result = await send_email_sdk(req, db, user)

        assert result.success is True
        assert result.execution_id == "exec-123"

    @pytest.mark.asyncio
    async def test_returns_error_on_workflow_failure(self):
        """Returns error when workflow execution fails."""
        db = AsyncMock()
        user = MagicMock()
        req = EmailSendRequest(
            recipient="user@example.com",
            subject="Test",
            body="Hello",
        )

        mock_config = MagicMock()
        mock_config.workflow_id = str(uuid4())

        mock_result = MagicMock()
        mock_result.status = "Failed"
        mock_result.execution_id = "exec-456"
        mock_result.error = "SMTP connection refused"

        with patch("src.routers.email_config.EmailService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.get_config = AsyncMock(return_value=mock_config)
            mock_svc_class.return_value = mock_svc

            with (
                patch("src.sdk.context.ExecutionContext"),
                patch("src.services.execution.service.run_workflow", new_callable=AsyncMock) as mock_run,
                patch("src.config.get_settings") as mock_settings,
            ):
                mock_settings.return_value.public_url = "https://test.bifrost.io"
                mock_run.return_value = mock_result

                result = await send_email_sdk(req, db, user)

        assert result.success is False
        assert "SMTP connection refused" in result.error

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self):
        """Returns error when an exception occurs during execution."""
        db = AsyncMock()
        user = MagicMock()
        req = EmailSendRequest(
            recipient="user@example.com",
            subject="Test",
            body="Hello",
        )

        mock_config = MagicMock()
        mock_config.workflow_id = str(uuid4())

        with patch("src.routers.email_config.EmailService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.get_config = AsyncMock(return_value=mock_config)
            mock_svc_class.return_value = mock_svc

            with (
                patch("src.sdk.context.ExecutionContext", side_effect=Exception("boom")),
                patch("src.config.get_settings") as mock_settings,
            ):
                mock_settings.return_value.public_url = "https://test.bifrost.io"

                result = await send_email_sdk(req, db, user)

        assert result.success is False
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_passes_scope_to_execution_context(self):
        """Scope from request is passed to ExecutionContext."""
        db = AsyncMock()
        user = MagicMock()
        req = EmailSendRequest(
            recipient="user@example.com",
            subject="Test",
            body="Hello",
            scope="org-xyz",
        )

        mock_config = MagicMock()
        mock_config.workflow_id = str(uuid4())

        mock_result = MagicMock()
        mock_result.status = "Success"
        mock_result.execution_id = "exec-789"

        with patch("src.routers.email_config.EmailService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.get_config = AsyncMock(return_value=mock_config)
            mock_svc_class.return_value = mock_svc

            with (
                patch("src.sdk.context.ExecutionContext") as mock_ctx_class,
                patch("src.services.execution.service.run_workflow", new_callable=AsyncMock) as mock_run,
                patch("src.config.get_settings") as mock_settings,
            ):
                mock_settings.return_value.public_url = "https://test.bifrost.io"
                mock_run.return_value = mock_result

                await send_email_sdk(req, db, user)

                # Verify scope was passed
                call_kwargs = mock_ctx_class.call_args[1]
                assert call_kwargs["scope"] == "org-xyz"

    @pytest.mark.asyncio
    async def test_defaults_scope_to_global(self):
        """Scope defaults to GLOBAL when not provided."""
        db = AsyncMock()
        user = MagicMock()
        req = EmailSendRequest(
            recipient="user@example.com",
            subject="Test",
            body="Hello",
        )

        mock_config = MagicMock()
        mock_config.workflow_id = str(uuid4())

        mock_result = MagicMock()
        mock_result.status = "Success"
        mock_result.execution_id = "exec-000"

        with patch("src.routers.email_config.EmailService") as mock_svc_class:
            mock_svc = MagicMock()
            mock_svc.get_config = AsyncMock(return_value=mock_config)
            mock_svc_class.return_value = mock_svc

            with (
                patch("src.sdk.context.ExecutionContext") as mock_ctx_class,
                patch("src.services.execution.service.run_workflow", new_callable=AsyncMock) as mock_run,
                patch("src.config.get_settings") as mock_settings,
            ):
                mock_settings.return_value.public_url = "https://test.bifrost.io"
                mock_run.return_value = mock_result

                await send_email_sdk(req, db, user)

                call_kwargs = mock_ctx_class.call_args[1]
                assert call_kwargs["scope"] == "GLOBAL"
