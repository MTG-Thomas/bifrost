"""
Unit tests for Events MCP Tools.

Tests the event source, subscription, and webhook adapter tools:
- list_event_sources
- create_event_source
- get_event_source
- update_event_source
- delete_event_source
- list_event_subscriptions
- create_event_subscription
- update_event_subscription
- delete_event_subscription
- list_webhook_adapters
"""

import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest


class ToolResult:
    """Minimal FastMCP ToolResult stand-in for isolated unit tests."""

    def __init__(self, content=None, structured_content=None):
        self.content = content
        self.structured_content = structured_content


fastmcp_module = types.ModuleType("fastmcp")
fastmcp_tools_module = types.ModuleType("fastmcp.tools")
fastmcp_tools_module.ToolResult = ToolResult
fastmcp_module.tools = fastmcp_tools_module
sys.modules.setdefault("fastmcp", fastmcp_module)
sys.modules.setdefault("fastmcp.tools", fastmcp_tools_module)


@dataclass
class MCPContext:
    """Small test context matching the production class name and fields used here."""

    user_id: UUID | str
    org_id: UUID | str | None = None
    is_platform_admin: bool = False
    user_email: str = ""
    user_name: str = ""
    session: object | None = None

    def __post_init__(self):
        if isinstance(self.user_id, str) and self.user_id:
            self.user_id = UUID(self.user_id)
        if isinstance(self.org_id, str) and self.org_id:
            self.org_id = UUID(self.org_id)


def is_error_result(result: ToolResult) -> bool:
    """Check if a ToolResult represents an error."""
    if result.structured_content and "error" in result.structured_content:
        return True
    content = result.content
    if isinstance(content, list):
        content = content[0].text if content else ""
    if content and isinstance(content, str) and content.startswith("Error:"):
        return True
    return False


def get_content_text(result: ToolResult) -> str:
    """Extract text content from a ToolResult."""
    content = result.content
    if isinstance(content, list):
        return content[0].text if content else ""
    return content or ""


class _OneResult:
    def __init__(self, value):
        self._value = value

    def unique(self):
        return self

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _EventDb:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.deleted = []
        self.flushed = 0

    async def execute(self, _query):
        if not self.results:
            raise AssertionError("unexpected execute call")
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    async def flush(self):
        self.flushed += 1


# ==================== Fixtures ====================


@pytest.fixture
def context():
    """Create an MCPContext for testing."""
    return MCPContext(
        user_id=str(uuid4()),
        org_id=str(uuid4()),
        is_platform_admin=True,
        user_email="admin@example.com",
        user_name="Admin User",
    )


@pytest.fixture
def org_user_context():
    """Create an MCPContext for a regular organization user."""
    return MCPContext(
        user_id=str(uuid4()),
        org_id=str(uuid4()),
        is_platform_admin=False,
        user_email="user@example.com",
        user_name="Org User",
    )


# ==================== Event Source Tool Tests ====================


class TestEventToolAuthorization:
    """Event source and subscription MCP tools are platform-admin only."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "args"),
        [
            ("list_event_sources", ()),
            ("create_event_source", ("source", "webhook")),
            ("get_event_source", (str(uuid4()),)),
            ("update_event_source", (str(uuid4()),)),
            ("delete_event_source", (str(uuid4()),)),
            ("list_event_subscriptions", (str(uuid4()),)),
            ("create_event_subscription", (str(uuid4()), str(uuid4()))),
            ("update_event_subscription", (str(uuid4()), str(uuid4()))),
            ("delete_event_subscription", (str(uuid4()), str(uuid4()))),
            ("list_webhook_adapters", ()),
        ],
    )
    async def test_non_admin_event_tools_return_error(self, org_user_context, tool_name, args):
        import src.services.mcp_server.tools.events as events

        result = await getattr(events, tool_name)(org_user_context, *args)

        assert is_error_result(result)
        assert "Platform administrator privileges are required" in result.structured_content["error"]


class TestListEventSources:
    """Tests for list_event_sources tool."""

    @pytest.mark.asyncio
    async def test_invalid_source_type_returns_error(self, context):
        """Should return error for invalid source_type."""
        from src.services.mcp_server.tools.events import list_event_sources

        result = await list_event_sources(context, source_type="invalid_type")
        assert is_error_result(result)
        assert "Invalid source_type" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_sources(self, context):
        """Should return empty list when no sources exist."""
        from src.services.mcp_server.tools.events import list_event_sources

        mock_repo = MagicMock()
        mock_repo.get_by_organization = AsyncMock(return_value=[])

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                result = await list_event_sources(context)
                assert not is_error_result(result)
                assert result.structured_content["sources"] == []
                assert result.structured_content["count"] == 0

    @pytest.mark.asyncio
    async def test_scoped_admin_cannot_list_another_org_sources(self, context):
        """organization_id must not widen an org-scoped admin's event access."""
        from src.services.mcp_server.tools.events import list_event_sources

        context.org_id = uuid4()

        result = await list_event_sources(context, organization_id=str(uuid4()))

        assert is_error_result(result)
        assert "not authorized" in result.structured_content["error"].lower()

    @pytest.mark.asyncio
    async def test_invalid_requested_org_uuid_returns_validation_error(self, context):
        """organization_id is validated before repository access."""
        from src.services.mcp_server.tools.events import list_event_sources

        result = await list_event_sources(context, organization_id="not-a-uuid")

        assert is_error_result(result)
        assert result.structured_content["error"] == "organization_id must be a valid UUID"

    @pytest.mark.asyncio
    async def test_serializes_webhook_and_schedule_sources(self, context):
        """List response includes type-specific webhook and schedule fields."""
        from src.models.enums import EventSourceType
        from src.services.mcp_server.tools.events import list_event_sources

        webhook_id = uuid4()
        schedule_id = uuid4()
        sources = [
            SimpleNamespace(
                id=webhook_id,
                name="Webhook",
                source_type=EventSourceType.WEBHOOK,
                organization_id=None,
                is_active=True,
                webhook_source=SimpleNamespace(adapter_name=None),
                schedule_source=None,
            ),
            SimpleNamespace(
                id=schedule_id,
                name="Schedule",
                source_type=EventSourceType.SCHEDULE,
                organization_id=context.org_id,
                is_active=False,
                webhook_source=None,
                schedule_source=SimpleNamespace(
                    cron_expression="*/5 * * * *",
                    timezone="America/Indianapolis",
                    enabled=True,
                ),
            ),
        ]

        mock_source_repo = MagicMock()
        mock_source_repo.get_by_organization = AsyncMock(return_value=sources)
        mock_sub_repo = MagicMock()
        mock_sub_repo.count_by_source = AsyncMock(side_effect=[3, 1])

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ), patch(
                "src.repositories.events.EventSubscriptionRepository",
                return_value=mock_sub_repo,
            ):
                result = await list_event_sources(context)

        assert not is_error_result(result)
        assert result.structured_content["count"] == 2
        webhook, schedule = result.structured_content["sources"]
        assert webhook["adapter_name"] == "generic"
        assert webhook["callback_url"] == f"/api/hooks/{webhook_id}"
        assert webhook["subscription_count"] == 3
        assert schedule["cron_expression"] == "*/5 * * * *"
        assert schedule["timezone"] == "America/Indianapolis"
        assert schedule["schedule_enabled"] is True
        assert schedule["subscription_count"] == 1

    @pytest.mark.asyncio
    async def test_repository_failure_returns_error_result(self, context):
        """Repository exceptions are serialized as ToolResult errors."""
        from src.services.mcp_server.tools.events import list_event_sources

        mock_source_repo = MagicMock()
        mock_source_repo.get_by_organization = AsyncMock(side_effect=RuntimeError("db down"))

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                result = await list_event_sources(context)

        assert is_error_result(result)
        assert "Error listing event sources: db down" == result.structured_content["error"]


class TestCreateEventSource:
    """Tests for create_event_source tool."""

    @pytest.mark.asyncio
    async def test_invalid_source_type_returns_error(self, context):
        """Should return error for invalid source_type."""
        from src.services.mcp_server.tools.events import create_event_source

        result = await create_event_source(context, name="test", source_type="bogus")
        assert is_error_result(result)
        assert "Invalid source_type" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_schedule_without_cron_returns_error(self, context):
        """Should return error when schedule source missing cron_expression."""
        from src.services.mcp_server.tools.events import create_event_source

        result = await create_event_source(
            context, name="test", source_type="schedule", cron_expression=None
        )
        assert is_error_result(result)
        assert "cron_expression is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_scoped_admin_cannot_create_source_for_another_org(self, context):
        """Event creation must not accept an out-of-scope organization_id."""
        from src.services.mcp_server.tools.events import create_event_source

        context.org_id = uuid4()

        result = await create_event_source(
            context,
            name="cross-org",
            source_type="webhook",
            organization_id=str(uuid4()),
        )

        assert is_error_result(result)
        assert "not authorized" in result.structured_content["error"].lower()

    @pytest.mark.asyncio
    async def test_invalid_workflow_id_returns_validation_error(self, context):
        from src.services.mcp_server.tools.events import create_event_source

        result = await create_event_source(
            context,
            name="with workflow",
            source_type="webhook",
            workflow_id="not-a-uuid",
        )

        assert is_error_result(result)
        assert "Invalid workflow_id" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_session_context_without_org_cannot_create_source(self):
        """Session-backed non-admin contexts pass auth but still need org scope."""
        from src.services.mcp_server.tools.events import create_event_source

        session_context = SimpleNamespace(
            session=object(),
            is_platform_admin=False,
            org_id=None,
            user_email="user@example.com",
        )

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await create_event_source(session_context, "no org", "webhook")

        assert is_error_result(result)
        assert "without an organization scope" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_unknown_webhook_adapter_returns_error(self, context):
        from src.services.mcp_server.tools.events import create_event_source

        registry = MagicMock()
        registry.get.return_value = None

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.services.webhooks.registry.get_adapter_registry",
                return_value=registry,
            ):
                result = await create_event_source(
                    context,
                    name="bad adapter",
                    source_type="webhook",
                    adapter_name="missing",
                )

        assert is_error_result(result)
        assert result.structured_content["error"] == "Unknown webhook adapter: missing"


class TestGetEventSource:
    """Tests for get_event_source tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import get_event_source

        result = await get_event_source(context, "")
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import get_event_source

        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                source_id = str(uuid4())
                result = await get_event_source(context, source_id)
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_scoped_admin_cannot_get_another_org_source(self, context):
        """Direct event source lookup must enforce the caller's org scope."""
        from src.models.enums import EventSourceType
        from src.services.mcp_server.tools.events import get_event_source

        context.org_id = uuid4()
        mock_source = SimpleNamespace(
            id=uuid4(),
            name="Other org source",
            source_type=EventSourceType.INTERNAL,
            organization_id=uuid4(),
            is_active=True,
            error_message=None,
            created_by="admin@example.com",
            created_at=None,
            webhook_source=None,
            schedule_source=None,
        )
        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id_with_details = AsyncMock(return_value=mock_source)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                result = await get_event_source(context, str(mock_source.id))

        assert is_error_result(result)
        assert "not authorized" in result.structured_content["error"].lower()

    @pytest.mark.asyncio
    async def test_serializes_webhook_source_details(self, context):
        """Webhook source details include callback, integration, and expiry fields."""
        from datetime import datetime, timezone

        from src.models.enums import EventSourceType
        from src.services.mcp_server.tools.events import get_event_source

        source_id = uuid4()
        integration_id = uuid4()
        expires_at = datetime(2026, 7, 5, tzinfo=timezone.utc)
        created_at = datetime(2026, 7, 4, tzinfo=timezone.utc)
        mock_source = SimpleNamespace(
            id=source_id,
            name="Webhook",
            source_type=EventSourceType.WEBHOOK,
            organization_id=None,
            is_active=True,
            error_message=None,
            created_by="admin@example.com",
            created_at=created_at,
            webhook_source=SimpleNamespace(
                adapter_name=None,
                integration_id=integration_id,
                external_id="external-1",
                expires_at=expires_at,
            ),
            schedule_source=None,
        )
        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id_with_details = AsyncMock(return_value=mock_source)
        mock_sub_repo = MagicMock()
        mock_sub_repo.count_by_source = AsyncMock(return_value=7)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ), patch(
                "src.repositories.events.EventSubscriptionRepository",
                return_value=mock_sub_repo,
            ):
                result = await get_event_source(context, str(source_id))

        assert not is_error_result(result)
        data = result.structured_content
        assert data["adapter_name"] == "generic"
        assert data["callback_url"] == f"/api/hooks/{source_id}"
        assert data["integration_id"] == str(integration_id)
        assert data["external_id"] == "external-1"
        assert data["expires_at"] == expires_at.isoformat()
        assert data["created_at"] == created_at.isoformat()
        assert data["subscription_count"] == 7

    @pytest.mark.asyncio
    async def test_serializes_schedule_source_details(self, context):
        """Schedule source details include cron, timezone, and enabled state."""
        from src.models.enums import EventSourceType
        from src.services.mcp_server.tools.events import get_event_source

        source_id = uuid4()
        mock_source = SimpleNamespace(
            id=source_id,
            name="Schedule",
            source_type=EventSourceType.SCHEDULE,
            organization_id=None,
            is_active=True,
            error_message="last failure",
            created_by="admin@example.com",
            created_at=None,
            webhook_source=None,
            schedule_source=SimpleNamespace(
                cron_expression="0 * * * *",
                timezone="UTC",
                enabled=False,
            ),
        )
        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id_with_details = AsyncMock(return_value=mock_source)
        mock_sub_repo = MagicMock()
        mock_sub_repo.count_by_source = AsyncMock(return_value=0)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ), patch(
                "src.repositories.events.EventSubscriptionRepository",
                return_value=mock_sub_repo,
            ):
                result = await get_event_source(context, str(source_id))

        assert not is_error_result(result)
        assert result.structured_content["cron_expression"] == "0 * * * *"
        assert result.structured_content["timezone"] == "UTC"
        assert result.structured_content["schedule_enabled"] is False
        assert result.structured_content["error_message"] == "last failure"


class TestUpdateEventSource:
    """Tests for update_event_source tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import update_event_source

        result = await update_event_source(context, "")
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import update_event_source

        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                source_id = str(uuid4())
                result = await update_event_source(context, source_id, name="new name")
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_updates_schedule_fields_and_clears_error_when_reactivated(self, context):
        """Successful schedule updates mutate fields and serialize the reloaded row."""
        from src.models.enums import EventSourceType
        from src.services.mcp_server.tools.events import update_event_source

        source_id = uuid4()
        schedule_source = SimpleNamespace(
            cron_expression="old",
            timezone="UTC",
            enabled=False,
            updated_at=None,
        )
        source = SimpleNamespace(
            id=source_id,
            name="Old",
            source_type=EventSourceType.SCHEDULE,
            organization_id=None,
            is_active=False,
            error_message="failed",
            schedule_source=schedule_source,
        )
        reloaded = SimpleNamespace(
            id=source_id,
            name="New",
            source_type=EventSourceType.SCHEDULE,
            is_active=True,
            schedule_source=schedule_source,
        )
        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=source)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=_OneResult(reloaded))
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                result = await update_event_source(
                    context,
                    str(source_id),
                    name="New",
                    is_active=True,
                    cron_expression="*/10 * * * *",
                    timezone="America/New_York",
                    schedule_enabled=True,
                )

        assert not is_error_result(result)
        assert source.name == "New"
        assert source.is_active is True
        assert source.error_message is None
        assert schedule_source.cron_expression == "*/10 * * * *"
        assert schedule_source.timezone == "America/New_York"
        assert schedule_source.enabled is True
        assert result.structured_content["schedule_enabled"] is True


class TestDeleteEventSource:
    """Tests for delete_event_source tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import delete_event_source

        result = await delete_event_source(context, "")
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import delete_event_source

        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                source_id = str(uuid4())
                result = await delete_event_source(context, source_id)
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_deletes_source_row(self, context):
        """Should permanently delete the event source."""
        from src.models.enums import EventSourceType
        from src.services.mcp_server.tools.events import delete_event_source

        source_id = str(uuid4())
        mock_source = MagicMock()
        mock_source.id = source_id
        mock_source.name = "Test source"
        mock_source.source_type = EventSourceType.INTERNAL
        mock_source.webhook_source = None

        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=mock_source)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                result = await delete_event_source(context, source_id)

        assert not is_error_result(result)
        mock_session.delete.assert_awaited_once_with(mock_source)
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scoped_admin_cannot_delete_another_org_source(self, context):
        """Deleting event sources must be scoped to the caller's org."""
        from src.models.enums import EventSourceType
        from src.services.mcp_server.tools.events import delete_event_source

        context.org_id = uuid4()
        mock_source = SimpleNamespace(
            id=uuid4(),
            name="Other org source",
            source_type=EventSourceType.INTERNAL,
            organization_id=uuid4(),
            webhook_source=None,
        )
        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=mock_source)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                result = await delete_event_source(context, str(mock_source.id))

        assert is_error_result(result)
        assert "not authorized" in result.structured_content["error"].lower()
        mock_session.delete.assert_not_awaited()


# ==================== Subscription Tool Tests ====================


class TestListEventSubscriptions:
    """Tests for list_event_subscriptions tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_source_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import list_event_subscriptions

        result = await list_event_subscriptions(context, "")
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import list_event_subscriptions

        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                source_id = str(uuid4())
                result = await list_event_subscriptions(context, source_id)
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_source_has_no_subscriptions(self, context):
        from src.services.mcp_server.tools.events import list_event_subscriptions

        source_id = uuid4()
        mock_source = SimpleNamespace(id=source_id, name="Source", organization_id=None)
        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=mock_source)
        mock_sub_repo = MagicMock()
        mock_sub_repo.get_by_source = AsyncMock(return_value=[])

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ), patch(
                "src.repositories.events.EventSubscriptionRepository",
                return_value=mock_sub_repo,
            ):
                result = await list_event_subscriptions(context, str(source_id))

        assert not is_error_result(result)
        assert result.structured_content == {
            "source_id": str(source_id),
            "subscriptions": [],
            "count": 0,
        }

    @pytest.mark.asyncio
    async def test_serializes_subscription_delivery_counts(self, context):
        from src.services.mcp_server.tools.events import list_event_subscriptions

        source_id = uuid4()
        subscription_id = uuid4()
        workflow_id = uuid4()
        mock_source = SimpleNamespace(id=source_id, name="Source", organization_id=None)
        mock_subscription = SimpleNamespace(
            id=subscription_id,
            workflow_id=workflow_id,
            workflow=SimpleNamespace(name="Workflow"),
            event_type="ticket.created",
            input_mapping={"ticket": "$.id"},
            is_active=True,
        )
        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=mock_source)
        mock_sub_repo = MagicMock()
        mock_sub_repo.get_by_source = AsyncMock(return_value=[mock_subscription])
        mock_delivery_repo = MagicMock()
        mock_delivery_repo.count_by_subscription = AsyncMock(side_effect=[5, 4, 1])

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ), patch(
                "src.repositories.events.EventSubscriptionRepository",
                return_value=mock_sub_repo,
            ), patch(
                "src.repositories.events.EventDeliveryRepository",
                return_value=mock_delivery_repo,
            ):
                result = await list_event_subscriptions(context, str(source_id))

        assert not is_error_result(result)
        assert result.structured_content["count"] == 1
        subscription = result.structured_content["subscriptions"][0]
        assert subscription["id"] == str(subscription_id)
        assert subscription["workflow_id"] == str(workflow_id)
        assert subscription["workflow_name"] == "Workflow"
        assert subscription["delivery_count"] == 5
        assert subscription["success_count"] == 4
        assert subscription["failed_count"] == 1


class TestCreateEventSubscription:
    """Tests for create_event_subscription tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_source_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import create_event_subscription

        result = await create_event_subscription(context, "", str(uuid4()))
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_workflow_id_empty(self, context):
        """Should return error when workflow_id is empty."""
        from src.services.mcp_server.tools.events import create_event_subscription

        result = await create_event_subscription(context, str(uuid4()), "")
        assert is_error_result(result)
        assert "workflow_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import create_event_subscription

        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                result = await create_event_subscription(
                    context, str(uuid4()), str(uuid4())
                )
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_creates_subscription_and_serializes_workflow_name(self, context):
        from src.services.mcp_server.tools.events import create_event_subscription

        source_id = uuid4()
        workflow_id = uuid4()
        subscription_id = uuid4()
        mock_source = SimpleNamespace(id=source_id, name="Source", organization_id=None)
        reloaded_subscription = SimpleNamespace(
            id=subscription_id,
            workflow=SimpleNamespace(name="Workflow"),
        )
        db = _EventDb(results=[_OneResult(reloaded_subscription)])
        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=mock_source)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                result = await create_event_subscription(
                    context,
                    str(source_id),
                    str(workflow_id),
                    event_type="ticket.created",
                    input_mapping={"id": "$.ticket.id"},
                )

        assert not is_error_result(result)
        assert len(db.added) == 1
        assert db.flushed == 1
        assert result.structured_content["id"] == str(subscription_id)
        assert result.structured_content["workflow_name"] == "Workflow"
        assert result.structured_content["event_type"] == "ticket.created"
        assert result.structured_content["input_mapping"] == {"id": "$.ticket.id"}


class TestUpdateEventSubscription:
    """Tests for update_event_subscription tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_ids_empty(self, context):
        """Should return error when source_id or subscription_id is empty."""
        from src.services.mcp_server.tools.events import update_event_subscription

        result = await update_event_subscription(context, "", str(uuid4()))
        assert is_error_result(result)
        assert "required" in result.structured_content["error"]

        result = await update_event_subscription(context, str(uuid4()), "")
        assert is_error_result(result)
        assert "required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_subscription_not_found(self, context):
        from src.services.mcp_server.tools.events import update_event_subscription

        source_id = uuid4()
        subscription_id = uuid4()
        mock_source = SimpleNamespace(id=source_id, organization_id=None)
        db = _EventDb(results=[_OneResult(None)])
        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=mock_source)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                result = await update_event_subscription(
                    context,
                    str(source_id),
                    str(subscription_id),
                )

        assert is_error_result(result)
        assert f"Subscription not found: {subscription_id}" == result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_updates_subscription_fields(self, context):
        from src.services.mcp_server.tools.events import update_event_subscription

        source_id = uuid4()
        subscription_id = uuid4()
        workflow_id = uuid4()
        mock_source = SimpleNamespace(id=source_id, organization_id=None)
        subscription = SimpleNamespace(
            id=subscription_id,
            workflow_id=workflow_id,
            workflow=None,
            event_type=None,
            input_mapping=None,
            is_active=True,
            updated_at=None,
        )
        db = _EventDb(results=[_OneResult(subscription)])
        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=mock_source)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                result = await update_event_subscription(
                    context,
                    str(source_id),
                    str(subscription_id),
                    event_type="ticket.updated",
                    input_mapping={"ticket": "$"},
                    is_active=False,
                )

        assert not is_error_result(result)
        assert db.flushed == 1
        assert subscription.event_type == "ticket.updated"
        assert subscription.input_mapping == {"ticket": "$"}
        assert subscription.is_active is False
        assert result.structured_content["workflow_id"] == str(workflow_id)


class TestDeleteEventSubscription:
    """Tests for delete_event_subscription tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_ids_empty(self, context):
        """Should return error when source_id or subscription_id is empty."""
        from src.services.mcp_server.tools.events import delete_event_subscription

        result = await delete_event_subscription(context, "", str(uuid4()))
        assert is_error_result(result)
        assert "required" in result.structured_content["error"]

        result = await delete_event_subscription(context, str(uuid4()), "")
        assert is_error_result(result)
        assert "required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_deletes_subscription_row(self, context):
        """Should permanently delete the event subscription."""
        from src.services.mcp_server.tools.events import delete_event_subscription

        source_id = str(uuid4())
        subscription_id = str(uuid4())
        mock_subscription = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_subscription

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await delete_event_subscription(
                context, source_id, subscription_id
            )

        assert not is_error_result(result)
        mock_session.execute.assert_awaited_once()
        mock_session.delete.assert_awaited_once_with(mock_subscription)
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scoped_admin_cannot_delete_subscription_for_another_org_source(self, context):
        """Subscription deletion must verify the parent event source org."""
        from src.services.mcp_server.tools.events import delete_event_subscription

        context.org_id = uuid4()
        mock_subscription = SimpleNamespace(
            id=uuid4(),
            event_source=SimpleNamespace(organization_id=uuid4()),
        )
        mock_result = _OneResult(mock_subscription)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await delete_event_subscription(
                context,
                str(uuid4()),
                str(mock_subscription.id),
            )

        assert is_error_result(result)
        assert "not authorized" in result.structured_content["error"].lower()
        mock_session.delete.assert_not_awaited()


# ==================== Webhook Adapter Tests ====================


class TestListWebhookAdapters:
    """Tests for list_webhook_adapters tool."""

    @pytest.mark.asyncio
    async def test_returns_adapter_list(self, context):
        """Should return list of adapters from registry."""
        from src.services.mcp_server.tools.events import list_webhook_adapters

        mock_registry = MagicMock()
        mock_registry.list_adapters.return_value = [
            {
                "name": "generic",
                "display_name": "Generic Webhook",
                "description": "Generic webhook adapter",
                "requires_integration": None,
                "supports_renewal": False,
            },
            {
                "name": "microsoft_graph",
                "display_name": "Microsoft Graph",
                "description": "Graph subscriptions",
                "requires_integration": "Microsoft",
                "supports_renewal": True,
            },
        ]

        with patch(
            "src.services.webhooks.registry.get_adapter_registry",
            return_value=mock_registry,
        ):
            result = await list_webhook_adapters(context)
            assert not is_error_result(result)
            assert result.structured_content["count"] == 2
            adapters = result.structured_content["adapters"]
            assert len(adapters) == 2
            assert adapters[0]["name"] == "generic"
            assert adapters[1]["name"] == "microsoft_graph"
            assert adapters[1]["requires_integration"] == "Microsoft"


# ==================== Registration Tests ====================


class TestEventToolsRegistration:
    """Tests for event tools registration."""

    def test_all_tools_have_matching_functions(self):
        """Every tool in TOOLS should have a corresponding function."""
        from src.services.mcp_server.tools.events import TOOLS

        import src.services.mcp_server.tools.events as events_module

        for tool_id, _name, _description in TOOLS:
            assert hasattr(events_module, tool_id), f"Missing function: {tool_id}"
            func = getattr(events_module, tool_id)
            assert callable(func), f"{tool_id} is not callable"

    def test_tool_count_is_ten(self):
        """Should have exactly 10 tools registered."""
        from src.services.mcp_server.tools.events import TOOLS

        assert len(TOOLS) == 10

    def test_all_tool_ids_unique(self):
        """All tool IDs should be unique."""
        from src.services.mcp_server.tools.events import TOOLS

        tool_ids = [t[0] for t in TOOLS]
        assert len(tool_ids) == len(set(tool_ids)), "Duplicate tool IDs found"

    def test_register_tools_calls_register_for_each(self):
        """register_tools should register all 10 tools."""
        from src.services.mcp_server.tools.events import register_tools

        mock_mcp = MagicMock()
        mock_get_context = MagicMock()

        with patch(
            "src.services.mcp_server.generators.fastmcp_generator.register_tool_with_context"
        ) as mock_register:
            register_tools(mock_mcp, mock_get_context)
            assert mock_register.call_count == 10
