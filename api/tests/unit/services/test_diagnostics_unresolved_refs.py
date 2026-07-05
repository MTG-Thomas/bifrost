"""
Unit tests for unresolved reference notification helpers.

Tests scan_for_unresolved_refs and clear_unresolved_refs_notification.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.file_storage.diagnostics import (
    DiagnosticsService,
    FileDiagnosticInfo,
)


class TestUnresolvedRefNotifications:
    """Tests for unresolved ref notification helpers."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.fixture
    def diagnostics_service(self, mock_db):
        """Create diagnostics service with mock db."""
        return DiagnosticsService(mock_db)

    @pytest.mark.asyncio
    async def test_creates_notification_for_unresolved_refs(
        self, diagnostics_service
    ):
        """When unresolved refs exist, creates a notification."""
        unresolved = ["workflows/missing.py::func1", "workflows/gone.py::func2"]

        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(return_value=None)
            mock_service.create_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await diagnostics_service.scan_for_unresolved_refs(
                path="apps/my-app/pages/index.tsx",
                entity_type="app_file",
                unresolved_refs=unresolved,
            )

            mock_service.create_notification.assert_called_once()
            call_args = mock_service.create_notification.call_args
            assert "Unresolved Workflow Refs" in call_args.kwargs["request"].title
            assert "index.tsx" in call_args.kwargs["request"].title

    @pytest.mark.asyncio
    async def test_clears_notification_when_no_unresolved_refs(
        self, diagnostics_service
    ):
        """When no unresolved refs, clears any existing notification."""
        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            await diagnostics_service.scan_for_unresolved_refs(
                path="apps/my-app/pages/index.tsx",
                entity_type="app_file",
                unresolved_refs=[],
            )

            # Should not create notification
            mock_service.create_notification.assert_not_called()

    @pytest.mark.asyncio
    async def test_clears_existing_notification(self, diagnostics_service):
        """clear_unresolved_refs_notification dismisses existing notification."""
        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_notification = MagicMock()
            mock_notification.id = "notif-123"

            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(
                return_value=mock_notification
            )
            mock_service.dismiss_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await diagnostics_service.clear_unresolved_refs_notification(
                path="apps/my-app/pages/index.tsx"
            )

            mock_service.dismiss_notification.assert_called_once_with(
                "notif-123", user_id="system"
            )

    @pytest.mark.asyncio
    async def test_skips_duplicate_notification(self, diagnostics_service):
        """Does not create duplicate notification if one already exists."""
        unresolved = ["workflows/missing.py::func"]

        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_existing = MagicMock()

            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(
                return_value=mock_existing
            )
            mock_service.create_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await diagnostics_service.scan_for_unresolved_refs(
                path="apps/my-app/pages/index.tsx",
                entity_type="app_file",
                unresolved_refs=unresolved,
            )

            # Should not create when existing
            mock_service.create_notification.assert_not_called()


class TestSdkAndFileDiagnosticNotifications:
    """Behavior tests for SDK and file diagnostic notification lifecycles."""

    @pytest.fixture
    def diagnostics_service(self):
        return DiagnosticsService(AsyncMock())

    @pytest.mark.asyncio
    async def test_scan_for_sdk_issues_creates_actionable_admin_notification(
        self,
        diagnostics_service,
    ):
        issues = [
            SimpleNamespace(issue_type="config", key="missing_api_key", line_number=7),
            SimpleNamespace(issue_type="integration", key="halo", line_number=11),
            SimpleNamespace(issue_type="config", key="region", line_number=13),
            SimpleNamespace(issue_type="integration", key="ninja", line_number=17),
        ]

        with patch(
            "src.services.file_storage.diagnostics.SDKReferenceScanner"
        ) as scanner_cls:
            scanner_cls.return_value.scan_file = AsyncMock(return_value=issues)
            with patch(
                "src.services.file_storage.diagnostics.get_notification_service"
            ) as get_service:
                service = MagicMock()
                service.find_admin_notification_by_title = AsyncMock(return_value=None)
                service.create_notification = AsyncMock()
                get_service.return_value = service

                await diagnostics_service.scan_for_sdk_issues(
                    "workflows/sync.py",
                    b"config.get('missing_api_key')",
                )

        scanner_cls.return_value.scan_file.assert_awaited_once_with(
            "workflows/sync.py",
            "config.get('missing_api_key')",
        )
        service.create_notification.assert_awaited_once()
        call = service.create_notification.await_args
        request = call.kwargs["request"]
        assert request.title == "Missing SDK References: sync.py"
        assert request.description == "4 missing: missing_api_key, halo, region..."
        assert request.metadata["action"] == "view_file"
        assert request.metadata["file_path"] == "workflows/sync.py"
        assert request.metadata["line_number"] == 7
        assert request.metadata["issues"][1] == {
            "type": "integration",
            "key": "halo",
            "line": 11,
        }
        assert call.kwargs["for_admins"] is True

    @pytest.mark.asyncio
    async def test_scan_for_sdk_issues_clears_notification_when_clean(
        self,
        diagnostics_service,
    ):
        diagnostics_service.clear_sdk_issues_notification = AsyncMock()

        with patch(
            "src.services.file_storage.diagnostics.SDKReferenceScanner"
        ) as scanner_cls:
            scanner_cls.return_value.scan_file = AsyncMock(return_value=[])

            await diagnostics_service.scan_for_sdk_issues(
                "workflows/clean.py",
                b"print('ok')",
            )

        diagnostics_service.clear_sdk_issues_notification.assert_awaited_once_with(
            "workflows/clean.py"
        )

    @pytest.mark.asyncio
    async def test_scan_for_sdk_issues_ignores_undecodable_content(
        self,
        diagnostics_service,
    ):
        class BadBytes(bytes):
            def decode(self, *args, **kwargs):
                raise UnicodeError("bad bytes")

        with patch(
            "src.services.file_storage.diagnostics.SDKReferenceScanner"
        ) as scanner_cls:
            await diagnostics_service.scan_for_sdk_issues(
                "workflows/bad.py",
                BadBytes(b"x"),
            )

        scanner_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_file_diagnostic_notification_includes_errors_and_context(
        self,
        diagnostics_service,
    ):
        diagnostics = [
            FileDiagnosticInfo(
                severity="warning",
                message="style issue",
                line=2,
                column=4,
                source="ruff",
            ),
            FileDiagnosticInfo(
                severity="error",
                message="workflow id conflict",
                line=9,
                column=12,
                source="indexing",
            ),
            FileDiagnosticInfo(
                severity="error",
                message="missing decorator",
                source="syntax",
            ),
        ]

        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as get_service:
            service = MagicMock()
            service.find_admin_notification_by_title = AsyncMock(return_value=None)
            service.create_notification = AsyncMock()
            get_service.return_value = service

            await diagnostics_service.create_diagnostic_notification(
                "workflows/problem.py",
                diagnostics,
            )

        service.create_notification.assert_awaited_once()
        request = service.create_notification.await_args.kwargs["request"]
        assert request.title == "File issues: problem.py"
        assert request.description == "workflow id conflict; missing decorator"
        assert request.metadata["file_path"] == "workflows/problem.py"
        assert request.metadata["line_number"] == 9
        assert request.metadata["diagnostics"][0]["severity"] == "warning"
        assert request.metadata["diagnostics"][1]["source"] == "indexing"

    @pytest.mark.asyncio
    async def test_file_diagnostic_notification_ignores_non_errors(
        self,
        diagnostics_service,
    ):
        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as get_service:
            await diagnostics_service.create_diagnostic_notification(
                "docs/readme.md",
                [FileDiagnosticInfo(severity="warning", message="heads up")],
            )

        get_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_clear_sdk_and_file_diagnostics_dismiss_existing_notifications(
        self,
        diagnostics_service,
    ):
        existing = SimpleNamespace(id="notif-42")
        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as get_service:
            service = MagicMock()
            service.find_admin_notification_by_title = AsyncMock(return_value=existing)
            service.dismiss_notification = AsyncMock()
            get_service.return_value = service

            await diagnostics_service.clear_sdk_issues_notification(
                "workflows/sync.py"
            )
            await diagnostics_service.clear_diagnostic_notification(
                "workflows/sync.py"
            )

        assert service.dismiss_notification.await_count == 2
        service.dismiss_notification.assert_any_await("notif-42", user_id="system")
