"""
Unit tests for MCP workflow tool name normalization and mapping.

Tests the functions that convert workflow names to MCP-compatible tool names,
handle duplicate detection, and manage tool name <-> workflow ID mappings.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest


class TestNormalizeToolName:
    """Tests for _normalize_tool_name()."""

    def test_basic_lowercase(self):
        """Should convert to lowercase."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("ReviewTickets") == "reviewtickets"
        assert _normalize_tool_name("UPPER_CASE") == "upper_case"

    def test_spaces_to_underscores(self):
        """Should convert spaces to underscores."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("Review Tickets") == "review_tickets"
        assert _normalize_tool_name("get user data") == "get_user_data"

    def test_hyphens_to_underscores(self):
        """Should convert hyphens to underscores."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("get-user-data") == "get_user_data"
        assert _normalize_tool_name("review-support-tickets") == "review_support_tickets"

    def test_removes_special_characters(self):
        """Should remove non-alphanumeric characters except underscores."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("user@email.com") == "useremailcom"
        assert _normalize_tool_name("price$100") == "price100"
        assert _normalize_tool_name("data[0]") == "data0"

    def test_collapses_multiple_underscores(self):
        """Should collapse multiple underscores into one."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("get___data") == "get_data"
        assert _normalize_tool_name("hello _ _ world") == "hello_world"

    def test_strips_leading_trailing_underscores(self):
        """Should remove leading and trailing underscores."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("_private") == "private"
        assert _normalize_tool_name("data_") == "data"
        assert _normalize_tool_name("_both_") == "both"

    def test_handles_empty_string(self):
        """Should handle empty string gracefully."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("") == ""
        assert _normalize_tool_name("   ") == ""

    def test_handles_only_special_chars(self):
        """Should handle strings with only special characters."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("@#$%") == ""
        assert _normalize_tool_name("---") == ""

    def test_preserves_numbers(self):
        """Should preserve numbers in the name."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("process123") == "process123"
        assert _normalize_tool_name("v2_api") == "v2_api"


class TestWorkflowIdentitySuffix:
    """Tests for deterministic workflow identity suffixes."""

    def test_uses_complete_uuid_without_replica_local_randomness(self):
        from src.services.mcp_server.server import _workflow_identity_suffix

        workflow_id = UUID("11111111-2222-3333-4444-555555555555")

        assert _workflow_identity_suffix(workflow_id) == workflow_id.hex
        assert _workflow_identity_suffix(str(workflow_id)) == workflow_id.hex


def _workflow_tool(
    workflow_id: str,
    *,
    name: str,
    description: str = "A workflow tool",
    parameters_schema=None,
):
    from src.services.tool_registry import RegisteredTool

    return RegisteredTool(
        id=UUID(workflow_id),
        name=name,
        description=description,
        category="General",
        parameters_schema=parameters_schema or [],
        file_path="workflows/tool.py",
        function_name="run",
    )


class TestWorkflowCatalog:
    """Tests for replica-stable workflow catalog construction."""

    def test_catalog_is_stable_across_database_row_order(self):
        from src.services.mcp_server.server import (
            _build_workflow_catalog,
            _normalized_workflow_catalog,
            _workflow_catalog_digest,
        )

        first = _workflow_tool(
            "11111111-1111-1111-1111-111111111111",
            name="Review Tickets",
            parameters_schema=[
                {
                    "name": "priority",
                    "type": "string",
                    "options": [{"label": "High", "value": "high"}],
                    "default_value": "high",
                }
            ],
        )
        second = _workflow_tool(
            "22222222-2222-2222-2222-222222222222",
            name="review-tickets",
        )

        catalog_a = _build_workflow_catalog([second, first], frozenset())
        catalog_b = _build_workflow_catalog([first, second], frozenset())

        definitions_a = [entry.externally_visible_definition() for entry in catalog_a]
        definitions_b = [entry.externally_visible_definition() for entry in catalog_b]
        assert definitions_a == definitions_b
        assert _normalized_workflow_catalog(
            catalog_a
        ) == _normalized_workflow_catalog(catalog_b)
        assert _workflow_catalog_digest(catalog_a) == _workflow_catalog_digest(
            catalog_b
        )
        assert [entry.name for entry in catalog_a] == [
            f"review_tickets__{first.id.hex}",
            f"review_tickets__{second.id.hex}",
        ]

    def test_native_and_workflow_name_collisions_are_stable_and_unique(self):
        from src.services.mcp_server.server import _build_workflow_catalog

        native_collision = _workflow_tool(
            "11111111-1111-1111-1111-111111111111",
            name="Execute Workflow",
        )
        normalized_collision = _workflow_tool(
            "22222222-2222-2222-2222-222222222222",
            name="Execute Workflow Workflow",
        )
        empty_name = _workflow_tool(
            "33333333-3333-3333-3333-333333333333",
            name="!!!",
        )

        catalog = _build_workflow_catalog(
            [empty_name, normalized_collision, native_collision],
            {"execute_workflow"},
        )
        names_by_id = {entry.workflow_id: entry.name for entry in catalog}

        assert len(set(names_by_id.values())) == 3
        assert names_by_id[str(native_collision.id)] == (
            f"execute_workflow__{native_collision.id.hex}"
        )
        assert names_by_id[str(normalized_collision.id)] == (
            f"execute_workflow_workflow__{normalized_collision.id.hex}"
        )
        assert names_by_id[str(empty_name.id)] == f"workflow__{empty_name.id.hex}"

    def test_identity_does_not_change_when_hidden_collision_appears(self):
        from src.services.mcp_server.server import _build_workflow_catalog

        visible = _workflow_tool(
            "11111111-1111-1111-1111-111111111111",
            name="Private Sync",
        )
        hidden_other_tenant = _workflow_tool(
            "22222222-2222-2222-2222-222222222222",
            name="private-sync",
        )

        before = _build_workflow_catalog([visible], frozenset())
        after = _build_workflow_catalog(
            [visible, hidden_other_tenant],
            frozenset(),
        )

        assert before[0].name == f"private_sync__{visible.id.hex}"
        assert next(
            entry.name for entry in after if entry.workflow_id == str(visible.id)
        ) == before[0].name

    def test_digest_covers_description_and_complete_nested_input_schema(self):
        from src.services.mcp_server.server import (
            _build_workflow_catalog,
            _workflow_catalog_digest,
        )

        workflow_id = "11111111-1111-1111-1111-111111111111"
        schema = {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["open", "closed"]}
                        },
                    },
                }
            },
        }
        baseline = _build_workflow_catalog(
            [
                _workflow_tool(
                    workflow_id,
                    name="Search Tickets",
                    description="Search current tickets",
                    parameters_schema=schema,
                )
            ],
            frozenset(),
        )
        changed_description = _build_workflow_catalog(
            [
                _workflow_tool(
                    workflow_id,
                    name="Search Tickets",
                    description="Search archived tickets",
                    parameters_schema=schema,
                )
            ],
            frozenset(),
        )
        changed_schema = _build_workflow_catalog(
            [
                _workflow_tool(
                    workflow_id,
                    name="Search Tickets",
                    description="Search current tickets",
                    parameters_schema={
                        **schema,
                        "required": ["filters"],
                    },
                )
            ],
            frozenset(),
        )

        baseline_digest = _workflow_catalog_digest(baseline)
        assert baseline[0].input_schema == schema
        assert baseline_digest != _workflow_catalog_digest(changed_description)
        assert baseline_digest != _workflow_catalog_digest(changed_schema)


class TestToolNameMappings:
    """Tests for workflow UUID to registered-name lookups."""

    def test_lookup_before_registration(self):
        """Should return None when tool is not registered."""
        from src.services.mcp_server.server import get_registered_tool_name

        assert get_registered_tool_name("nonexistent-id") is None

    def test_registered_name_lookup(self):
        """A registered workflow ID resolves to its FastMCP name."""
        from src.services.mcp_server import server

        original_id_to_name = server._WORKFLOW_ID_TO_TOOL_NAME.copy()

        try:
            server._WORKFLOW_ID_TO_TOOL_NAME["test-uuid-123"] = "test_workflow"

            assert server.get_registered_tool_name("test-uuid-123") == "test_workflow"
        finally:
            server._WORKFLOW_ID_TO_TOOL_NAME.clear()
            server._WORKFLOW_ID_TO_TOOL_NAME.update(original_id_to_name)

    def test_complete_replacement_matches_fresh_provider_order(self, monkeypatch):
        """Warm and fresh providers expose the same canonical order."""
        from src.services.mcp_server import server

        class WorkflowTool:
            def __init__(self, **kwargs):
                self.name = kwargs["name"]

        class Provider:
            def __init__(self):
                self.tools = {}

            def remove_tool(self, name):
                del self.tools[name]

        class MCP:
            def __init__(self):
                self.local_provider = Provider()

            def add_tool(self, tool):
                self.local_provider.tools[tool.name] = tool

        first = _workflow_tool(
            "11111111-1111-1111-1111-111111111111",
            name="Alpha",
        )
        second = _workflow_tool(
            "22222222-2222-2222-2222-222222222222",
            name="Zulu",
        )
        full_catalog = server._build_workflow_catalog(
            [second, first],
            frozenset(),
        )
        monkeypatch.setattr(server, "_WorkflowTool", WorkflowTool)

        warm = MCP()
        initial = server._build_workflow_catalog([second], frozenset())
        monkeypatch.setattr(server, "_WORKFLOW_ID_TO_TOOL_NAME", {})
        initial_entries = server._replace_workflow_catalog(warm, initial)
        server._WORKFLOW_ID_TO_TOOL_NAME = {
            entry.workflow_id: entry.name for entry in initial_entries
        }
        warm_entries = server._replace_workflow_catalog(warm, full_catalog)

        fresh = MCP()
        server._WORKFLOW_ID_TO_TOOL_NAME = {}
        fresh_entries = server._replace_workflow_catalog(fresh, full_catalog)

        assert [entry.name for entry in warm_entries] == [
            entry.name for entry in fresh_entries
        ]
        assert list(warm.local_provider.tools) == list(fresh.local_provider.tools)

    def test_partial_replacement_unwinds_every_prepared_identity(self, monkeypatch):
        from src.services.mcp_server import server

        class WorkflowTool:
            def __init__(self, **kwargs):
                self.name = kwargs["name"]

        class Provider:
            def __init__(self):
                self.tools = {"old_tool": object()}

            def remove_tool(self, name):
                del self.tools[name]

        class MCP:
            def __init__(self):
                self.local_provider = Provider()
                self.add_count = 0

            def add_tool(self, tool):
                self.add_count += 1
                self.local_provider.tools[tool.name] = tool
                if self.add_count == 2:
                    raise ValueError("invalid schema")

        catalog = server._build_workflow_catalog(
            [
                _workflow_tool(
                    "11111111-1111-1111-1111-111111111111",
                    name="Alpha",
                ),
                _workflow_tool(
                    "22222222-2222-2222-2222-222222222222",
                    name="Zulu",
                ),
            ],
            frozenset(),
        )
        monkeypatch.setattr(server, "_WorkflowTool", WorkflowTool)
        monkeypatch.setattr(
            server,
            "_WORKFLOW_ID_TO_TOOL_NAME",
            {"old-id": "old_tool"},
        )
        mcp = MCP()

        with pytest.raises(ValueError, match="invalid schema"):
            server._replace_workflow_catalog(mcp, catalog)

        assert mcp.local_provider.tools == {}

    @pytest.mark.asyncio
    async def test_refresh_retries_when_revision_moves_during_snapshot(
        self,
        monkeypatch,
    ):
        from src.services.mcp_server import catalog_sync, server

        mcp = SimpleNamespace(
            local_provider=SimpleNamespace(remove_tool=MagicMock()),
        )
        register = AsyncMock(return_value=1)
        revisions = AsyncMock(side_effect=[3, 4, 4, 4])
        monkeypatch.setattr(server, "_fastmcp_instance", mcp)
        monkeypatch.setattr(server, "_WORKFLOW_CATALOG_REVISION", 2)
        monkeypatch.setattr(server, "_register_workflow_tools", register)
        monkeypatch.setattr(
            catalog_sync,
            "get_workflow_catalog_revision",
            revisions,
        )

        assert await server.refresh_workflow_tools(target_revision=3) == 1

        assert register.await_count == 2
        assert server._WORKFLOW_CATALOG_REVISION == 4

    @pytest.mark.asyncio
    async def test_refresh_failure_does_not_advance_local_revision(
        self,
        monkeypatch,
    ):
        from src.services.mcp_server import catalog_sync, server

        mcp = SimpleNamespace(
            local_provider=SimpleNamespace(remove_tool=MagicMock()),
        )
        monkeypatch.setattr(server, "_fastmcp_instance", mcp)
        monkeypatch.setattr(server, "_WORKFLOW_CATALOG_REVISION", 2)
        monkeypatch.setattr(
            server,
            "_register_workflow_tools",
            AsyncMock(side_effect=RuntimeError("database unavailable")),
        )
        monkeypatch.setattr(
            catalog_sync,
            "get_workflow_catalog_revision",
            AsyncMock(return_value=3),
        )

        with pytest.raises(RuntimeError, match="database unavailable"):
            await server.refresh_workflow_tools(target_revision=3)

        assert server._WORKFLOW_CATALOG_REVISION == 2

    @pytest.mark.asyncio
    async def test_refresh_is_bounded_without_marking_a_racing_snapshot_current(
        self,
        monkeypatch,
    ):
        from src.services.mcp_server import catalog_sync, server

        mcp = SimpleNamespace(
            local_provider=SimpleNamespace(remove_tool=MagicMock()),
        )
        register = AsyncMock(return_value=1)
        revisions = AsyncMock(side_effect=[3, 4, 4, 5, 5, 6])
        monkeypatch.setattr(server, "_fastmcp_instance", mcp)
        monkeypatch.setattr(server, "_WORKFLOW_CATALOG_REVISION", 2)
        monkeypatch.setattr(server, "_register_workflow_tools", register)
        monkeypatch.setattr(
            catalog_sync,
            "get_workflow_catalog_revision",
            revisions,
        )

        with pytest.raises(RuntimeError, match="kept changing"):
            await server.refresh_workflow_tools(target_revision=3)

        assert register.await_count == server._WORKFLOW_CATALOG_REFRESH_ATTEMPTS
        assert server._WORKFLOW_CATALOG_REVISION == 2

    @pytest.mark.asyncio
    async def test_refresh_reconciles_when_shared_revision_moves_backwards(
        self,
        monkeypatch,
    ):
        """A restored or replaced Redis must not leave a replica stale."""
        from src.services.mcp_server import catalog_sync, server

        mcp = SimpleNamespace(
            local_provider=SimpleNamespace(remove_tool=MagicMock()),
        )
        register = AsyncMock(return_value=1)
        monkeypatch.setattr(server, "_fastmcp_instance", mcp)
        monkeypatch.setattr(server, "_WORKFLOW_CATALOG_REVISION", 8)
        monkeypatch.setattr(
            server,
            "_WORKFLOW_ID_TO_TOOL_NAME",
            {"current-id": "current_tool"},
        )
        monkeypatch.setattr(server, "_register_workflow_tools", register)
        monkeypatch.setattr(
            catalog_sync,
            "get_workflow_catalog_revision",
            AsyncMock(return_value=1),
        )

        assert await server.refresh_workflow_tools(target_revision=1) == 1

        register.assert_awaited_once_with(mcp)
        assert server._WORKFLOW_CATALOG_REVISION == 1

    @pytest.mark.asyncio
    async def test_refresh_ignores_delayed_pubsub_revision(
        self,
        monkeypatch,
    ):
        from src.services.mcp_server import catalog_sync, server

        mcp = SimpleNamespace(
            local_provider=SimpleNamespace(remove_tool=MagicMock()),
        )
        register = AsyncMock(return_value=1)
        monkeypatch.setattr(server, "_fastmcp_instance", mcp)
        monkeypatch.setattr(server, "_WORKFLOW_CATALOG_REVISION", 8)
        monkeypatch.setattr(
            server,
            "_WORKFLOW_ID_TO_TOOL_NAME",
            {"current-id": "current_tool"},
        )
        monkeypatch.setattr(server, "_register_workflow_tools", register)
        monkeypatch.setattr(
            catalog_sync,
            "get_workflow_catalog_revision",
            AsyncMock(return_value=8),
        )

        assert await server.refresh_workflow_tools(target_revision=3) == 1

        register.assert_not_awaited()
        assert server._WORKFLOW_CATALOG_REVISION == 8


class TestMCPContext:
    """Tests for MCPContext claim normalization."""

    def test_normalizes_string_uuid_claims(self):
        from src.services.mcp_server.server import MCPContext

        user_id = str(uuid4())
        org_id = str(uuid4())

        context = MCPContext(
            user_id=user_id,
            org_id=org_id,
            is_platform_admin=False,
            is_external=True,
            user_email="user@example.com",
            user_name="User",
            accessible_namespaces=["docs"],
            session=object(),
        )

        assert context.user_id == UUID(user_id)
        assert context.org_id == UUID(org_id)
        assert context.is_external is True
        assert context.accessible_namespaces == ["docs"]
        assert context.session is not None

    def test_preserves_uuid_and_empty_optional_claims(self):
        from src.services.mcp_server.server import MCPContext

        user_id = uuid4()

        context = MCPContext(user_id=user_id, org_id=None)

        assert context.user_id == user_id
        assert context.org_id is None
        assert context.accessible_namespaces == []


class TestDuplicateDetection:
    """Tests for duplicate workflow name detection logic."""

    def test_normalize_detects_case_duplicates(self):
        """Names differing only by case should normalize to same value."""
        from src.services.mcp_server.server import _normalize_tool_name

        # These should all normalize to the same thing
        name1 = _normalize_tool_name("ReviewTickets")
        name2 = _normalize_tool_name("reviewtickets")
        name3 = _normalize_tool_name("REVIEWTICKETS")

        assert name1 == name2 == name3

    def test_normalize_detects_separator_duplicates(self):
        """Names differing only by separators should normalize to same value."""
        from src.services.mcp_server.server import _normalize_tool_name

        name1 = _normalize_tool_name("get_user_data")
        name2 = _normalize_tool_name("get-user-data")
        name3 = _normalize_tool_name("get user data")

        assert name1 == name2 == name3


class TestEdgeCases:
    """Tests for edge cases in tool name handling."""

    def test_unicode_characters_removed(self):
        """Unicode characters should be removed during normalization."""
        from src.services.mcp_server.server import _normalize_tool_name

        # Emoji and unicode should be stripped
        assert _normalize_tool_name("hello🔥world") == "helloworld"
        assert _normalize_tool_name("café") == "caf"  # é is removed

    def test_very_long_names(self):
        """Should handle very long workflow names."""
        from src.services.mcp_server.server import _normalize_tool_name

        long_name = "this_is_a_very_long_workflow_name_" * 10
        result = _normalize_tool_name(long_name)
        # Should still work, just be long
        assert len(result) > 100
        assert "_" in result

    def test_numeric_only_names(self):
        """Should handle names that are only numbers."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("12345") == "12345"
        assert _normalize_tool_name("123_456") == "123_456"
