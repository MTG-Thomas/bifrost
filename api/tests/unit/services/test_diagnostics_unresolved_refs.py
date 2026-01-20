"""
Unit tests for unresolved reference notification helpers.

Tests scan_for_unresolved_refs and clear_unresolved_refs_notification.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.file_storage.diagnostics import DiagnosticsService


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
