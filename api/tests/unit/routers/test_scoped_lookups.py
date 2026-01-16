"""
Unit tests for scoped entity lookups.

Tests the prioritized lookup pattern (org-specific > global) for:
- TableRepository.get_by_name()
- ConfigRepository.get_config()
- WorkflowRepository.get_by_name()
- DataProviderRepository.get_by_name()

These tests verify that when the same name/key exists in both org scope
and global scope, the org-specific entity is returned (not MultipleResultsFound).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.models.orm.tables import Table
from src.models.orm.config import Config
from src.models.orm.workflows import Workflow
from src.models.enums import ConfigType


def make_table(name: str, org_id=None) -> Table:
    """Create a Table instance for testing."""
    return Table(
        id=uuid4(),
        name=name,
        organization_id=org_id,
        application_id=None,
        schema=None,
        description=None,
    )


def make_config(key: str, org_id=None, value: str = "test") -> Config:
    """Create a Config instance for testing."""
    return Config(
        id=uuid4(),
        key=key,
        organization_id=org_id,
        value={"value": value},
        config_type=ConfigType.STRING,
        description=None,
        updated_by="test@example.com",
    )


def make_workflow(name: str, org_id=None, workflow_type: str = "workflow") -> Workflow:
    """Create a Workflow instance for testing."""
    return Workflow(
        id=uuid4(),
        name=name,
        organization_id=org_id,
        type=workflow_type,
        is_active=True,
        function_name=name.lower().replace(" ", "_"),
        path=f"/workflows/{name.lower().replace(' ', '_')}.py",
    )


class TestTableRepositoryScopedLookup:
    """Tests for TableRepository.get_by_name() prioritized lookup."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def org_id(self):
        """Create a test organization ID."""
        return uuid4()

    async def test_same_name_in_org_and_global_returns_org_specific(
        self, mock_session, org_id
    ):
        """When same name exists in org AND global, return org-specific."""
        from src.routers.tables import TableRepository

        org_table = make_table("shared_table", org_id)

        # First query (org-specific) returns the org table
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org_table
        mock_session.execute.return_value = mock_result_org

        repo = TableRepository(mock_session, org_id)
        result = await repo.get_by_name("shared_table")

        assert result is not None
        assert result.id == org_table.id
        assert result.organization_id == org_id
        # Should only execute one query (org-specific found, no fallback needed)
        assert mock_session.execute.call_count == 1

    async def test_name_only_in_global_returns_global(self, mock_session, org_id):
        """When name only exists in global scope, return global."""
        from src.routers.tables import TableRepository

        global_table = make_table("shared_table", None)

        # First query (org-specific) returns None
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = None

        # Second query (global) returns the global table
        mock_result_global = MagicMock()
        mock_result_global.scalar_one_or_none.return_value = global_table

        mock_session.execute.side_effect = [mock_result_org, mock_result_global]

        repo = TableRepository(mock_session, org_id)
        result = await repo.get_by_name("shared_table")

        assert result is not None
        assert result.id == global_table.id
        assert result.organization_id is None
        # Should execute two queries (org-specific not found, then global)
        assert mock_session.execute.call_count == 2

    async def test_name_only_in_org_returns_org_specific(self, mock_session, org_id):
        """When name only exists in org scope, return org-specific."""
        from src.routers.tables import TableRepository

        org_table = make_table("shared_table", org_id)

        # First query (org-specific) returns the org table
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org_table
        mock_session.execute.return_value = mock_result_org

        repo = TableRepository(mock_session, org_id)
        result = await repo.get_by_name("shared_table")

        assert result is not None
        assert result.id == org_table.id
        assert result.organization_id == org_id
        # Should only execute one query (org-specific found)
        assert mock_session.execute.call_count == 1

    async def test_no_org_id_only_checks_global(self, mock_session):
        """When no org_id, only check global scope."""
        from src.routers.tables import TableRepository

        global_table = make_table("shared_table", None)

        # Only global query should be executed
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = global_table
        mock_session.execute.return_value = mock_result

        repo = TableRepository(mock_session, None)  # No org_id
        result = await repo.get_by_name("shared_table")

        assert result is not None
        assert result.id == global_table.id
        # Should only execute one query (global only)
        assert mock_session.execute.call_count == 1

    async def test_name_not_found_returns_none(self, mock_session, org_id):
        """When name doesn't exist anywhere, return None."""
        from src.routers.tables import TableRepository

        # Both queries return None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        repo = TableRepository(mock_session, org_id)
        result = await repo.get_by_name("nonexistent")

        assert result is None


class TestConfigRepositoryScopedLookup:
    """Tests for ConfigRepository.get_config() prioritized lookup."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def org_id(self):
        """Create a test organization ID."""
        return uuid4()

    async def test_same_key_in_org_and_global_returns_org_specific(
        self, mock_session, org_id
    ):
        """When same key exists in org AND global, return org-specific."""
        from src.routers.config import ConfigRepository

        org_config = make_config("shared_key", org_id, "org_value")

        # First query (org-specific) returns the org config
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org_config
        mock_session.execute.return_value = mock_result_org

        repo = ConfigRepository(mock_session, org_id)
        result = await repo.get_config("shared_key")

        assert result is not None
        assert result.id == org_config.id
        assert result.organization_id == org_id
        # Should only execute one query (org-specific found)
        assert mock_session.execute.call_count == 1

    async def test_key_only_in_global_returns_global(self, mock_session, org_id):
        """When key only exists in global scope, return global."""
        from src.routers.config import ConfigRepository

        global_config = make_config("shared_key", None, "global_value")

        # First query (org-specific) returns None
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = None

        # Second query (global) returns the global config
        mock_result_global = MagicMock()
        mock_result_global.scalar_one_or_none.return_value = global_config

        mock_session.execute.side_effect = [mock_result_org, mock_result_global]

        repo = ConfigRepository(mock_session, org_id)
        result = await repo.get_config("shared_key")

        assert result is not None
        assert result.id == global_config.id
        assert result.organization_id is None
        # Should execute two queries (org-specific not found, then global)
        assert mock_session.execute.call_count == 2

    async def test_key_only_in_org_returns_org_specific(self, mock_session, org_id):
        """When key only exists in org scope, return org-specific."""
        from src.routers.config import ConfigRepository

        org_config = make_config("shared_key", org_id, "org_value")

        # First query (org-specific) returns the org config
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org_config
        mock_session.execute.return_value = mock_result_org

        repo = ConfigRepository(mock_session, org_id)
        result = await repo.get_config("shared_key")

        assert result is not None
        assert result.id == org_config.id
        assert result.organization_id == org_id


class TestWorkflowRepositoryScopedLookup:
    """
    Tests for WorkflowRepository.get_by_name() prioritized lookup.

    Tests regular user (non-superuser) cascade scoping behavior.
    """

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def org_id(self):
        """Create a test organization ID."""
        return uuid4()

    @pytest.fixture
    def user_id(self):
        """Create a test user ID."""
        return uuid4()

    async def test_same_name_in_org_and_global_returns_org_specific(
        self, mock_session, org_id, user_id
    ):
        """When same name exists in org AND global, return org-specific."""
        from src.repositories.workflows import WorkflowRepository

        org_workflow = make_workflow("shared_workflow", org_id)
        org_workflow.access_level = "authenticated"

        # First query (org-specific) returns the org workflow
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org_workflow
        mock_session.execute.return_value = mock_result_org

        # Regular user - tests cascade scoping
        repo = WorkflowRepository(
            mock_session, org_id=org_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get_by_name("shared_workflow")

        assert result is not None
        assert result.id == org_workflow.id
        assert result.organization_id == org_id
        # Should only execute one query (org-specific found)
        assert mock_session.execute.call_count == 1

    async def test_name_only_in_global_returns_global(
        self, mock_session, org_id, user_id
    ):
        """When name only exists in global scope, return global."""
        from src.repositories.workflows import WorkflowRepository

        global_workflow = make_workflow("shared_workflow", None)
        global_workflow.access_level = "authenticated"

        # First query (org-specific) returns None
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = None

        # Second query (global) returns the global workflow
        mock_result_global = MagicMock()
        mock_result_global.scalar_one_or_none.return_value = global_workflow

        mock_session.execute.side_effect = [mock_result_org, mock_result_global]

        # Regular user - tests cascade scoping (org first, then global)
        repo = WorkflowRepository(
            mock_session, org_id=org_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get_by_name("shared_workflow")

        assert result is not None
        assert result.id == global_workflow.id
        assert result.organization_id is None
        # Should execute two queries (org-specific not found, then global)
        assert mock_session.execute.call_count == 2

    async def test_name_only_in_org_returns_org_specific(
        self, mock_session, org_id, user_id
    ):
        """When name only exists in org scope, return org-specific."""
        from src.repositories.workflows import WorkflowRepository

        org_workflow = make_workflow("shared_workflow", org_id)
        org_workflow.access_level = "authenticated"

        # First query (org-specific) returns the org workflow
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org_workflow
        mock_session.execute.return_value = mock_result_org

        # Regular user - tests cascade scoping
        repo = WorkflowRepository(
            mock_session, org_id=org_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get_by_name("shared_workflow")

        assert result is not None
        assert result.id == org_workflow.id
        assert result.organization_id == org_id

    async def test_no_org_id_only_checks_global(self, mock_session, user_id):
        """When no org_id provided, only check global scope."""
        from src.repositories.workflows import WorkflowRepository

        global_workflow = make_workflow("shared_workflow", None)
        global_workflow.access_level = "authenticated"

        # Only global query should be executed
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = global_workflow
        mock_session.execute.return_value = mock_result

        # Regular user with no org - only global scope
        repo = WorkflowRepository(
            mock_session, org_id=None, user_id=user_id, is_superuser=False
        )
        result = await repo.get_by_name("shared_workflow")

        assert result is not None
        assert result.id == global_workflow.id
        # Should only execute one query (global only)
        assert mock_session.execute.call_count == 1


class TestDataProviderRepositoryScopedLookup:
    """
    Tests for DataProviderRepository.get_by_name() prioritized lookup.

    Tests regular user (non-superuser) cascade scoping behavior.
    """

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def org_id(self):
        """Create a test organization ID."""
        return uuid4()

    @pytest.fixture
    def user_id(self):
        """Create a test user ID."""
        return uuid4()

    async def test_same_name_in_org_and_global_returns_org_specific(
        self, mock_session, org_id, user_id
    ):
        """When same name exists in org AND global, return org-specific."""
        from src.repositories.data_providers import DataProviderRepository

        org_provider = make_workflow("shared_provider", org_id, "data_provider")
        org_provider.access_level = "authenticated"

        # First query (org-specific) returns the org provider
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org_provider
        mock_session.execute.return_value = mock_result_org

        # Regular user - tests cascade scoping
        repo = DataProviderRepository(
            mock_session, org_id=org_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get_by_name("shared_provider")

        assert result is not None
        assert result.id == org_provider.id
        assert result.organization_id == org_id
        # Should only execute one query (org-specific found)
        assert mock_session.execute.call_count == 1

    async def test_name_only_in_global_returns_global(
        self, mock_session, org_id, user_id
    ):
        """When name only exists in global scope, return global."""
        from src.repositories.data_providers import DataProviderRepository

        global_provider = make_workflow("shared_provider", None, "data_provider")
        global_provider.access_level = "authenticated"

        # First query (org-specific) returns None
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = None

        # Second query (global) returns the global provider
        mock_result_global = MagicMock()
        mock_result_global.scalar_one_or_none.return_value = global_provider

        mock_session.execute.side_effect = [mock_result_org, mock_result_global]

        # Regular user - tests cascade scoping (org first, then global)
        repo = DataProviderRepository(
            mock_session, org_id=org_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get_by_name("shared_provider")

        assert result is not None
        assert result.id == global_provider.id
        assert result.organization_id is None
        # Should execute two queries (org-specific not found, then global)
        assert mock_session.execute.call_count == 2

    async def test_name_only_in_org_returns_org_specific(
        self, mock_session, org_id, user_id
    ):
        """When name only exists in org scope, return org-specific."""
        from src.repositories.data_providers import DataProviderRepository

        org_provider = make_workflow("shared_provider", org_id, "data_provider")
        org_provider.access_level = "authenticated"

        # First query (org-specific) returns the org provider
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org_provider
        mock_session.execute.return_value = mock_result_org

        # Regular user - tests cascade scoping
        repo = DataProviderRepository(
            mock_session, org_id=org_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get_by_name("shared_provider")

        assert result is not None
        assert result.id == org_provider.id
        assert result.organization_id == org_id

    async def test_no_org_id_only_checks_global(self, mock_session, user_id):
        """When no org_id provided, only check global scope."""
        from src.repositories.data_providers import DataProviderRepository

        global_provider = make_workflow("shared_provider", None, "data_provider")
        global_provider.access_level = "authenticated"

        # Only global query should be executed
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = global_provider
        mock_session.execute.return_value = mock_result

        # Regular user with no org - only global scope
        repo = DataProviderRepository(
            mock_session, org_id=None, user_id=user_id, is_superuser=False
        )
        result = await repo.get_by_name("shared_provider")

        assert result is not None
        assert result.id == global_provider.id
        # Should only execute one query (global only)
        assert mock_session.execute.call_count == 1


# =============================================================================
# OrgScopedRepository.get() Superuser Bypass Tests
# =============================================================================


class TestOrgScopedRepositorySuperuserBypass:
    """
    Tests superuser access behavior in OrgScopedRepository.get().

    The key behaviors being tested:
    - ID lookups: Superusers can access ANY entity by ID (no cascade needed)
    - Name lookups: Superusers use cascade scoping (org-specific first, then global)
      to ensure correct entity resolution when names exist in multiple orgs
    - Superusers bypass role checks but not cascade scoping for name lookups
    """

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def org1_id(self):
        """Create a test organization ID for org1."""
        return uuid4()

    @pytest.fixture
    def org2_id(self):
        """Create a test organization ID for org2."""
        return uuid4()

    async def test_superuser_no_org_accesses_any_org_entity(
        self, mock_session, org1_id
    ):
        """Superuser with org_id=None can access org-scoped workflows."""
        from src.repositories.workflows import WorkflowRepository

        org1_workflow = make_workflow("org1_workflow", org1_id)

        # Query returns the org1 workflow
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = org1_workflow
        mock_session.execute.return_value = mock_result

        # Superuser with org_id=None (platform admin without org context)
        repo = WorkflowRepository(mock_session, org_id=None, is_superuser=True)
        result = await repo.get(id=org1_workflow.id)

        assert result is not None
        assert result.id == org1_workflow.id
        assert result.organization_id == org1_id
        # Superuser bypasses org scoping - single query without org filter
        assert mock_session.execute.call_count == 1

    async def test_superuser_with_org_accesses_same_org_entity(
        self, mock_session, org1_id
    ):
        """Superuser with org_id=org1 can access org1-scoped workflows."""
        from src.repositories.workflows import WorkflowRepository

        org1_workflow = make_workflow("org1_workflow", org1_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = org1_workflow
        mock_session.execute.return_value = mock_result

        # Superuser with org_id=org1
        repo = WorkflowRepository(mock_session, org_id=org1_id, is_superuser=True)
        result = await repo.get(id=org1_workflow.id)

        assert result is not None
        assert result.id == org1_workflow.id
        assert result.organization_id == org1_id
        # Single query - superuser bypass
        assert mock_session.execute.call_count == 1

    async def test_superuser_with_org_accesses_other_org_entity(
        self, mock_session, org1_id, org2_id
    ):
        """Superuser with org_id=org1 can access org2-scoped workflows (cross-org)."""
        from src.repositories.workflows import WorkflowRepository

        org2_workflow = make_workflow("org2_workflow", org2_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = org2_workflow
        mock_session.execute.return_value = mock_result

        # Superuser with org_id=org1, but accessing org2's workflow
        repo = WorkflowRepository(mock_session, org_id=org1_id, is_superuser=True)
        result = await repo.get(id=org2_workflow.id)

        assert result is not None
        assert result.id == org2_workflow.id
        assert result.organization_id == org2_id
        # Superuser bypasses org scoping - can access cross-org
        assert mock_session.execute.call_count == 1

    async def test_superuser_with_org_accesses_global_entity(
        self, mock_session, org1_id
    ):
        """Superuser with org_id=org1 can access global workflows."""
        from src.repositories.workflows import WorkflowRepository

        global_workflow = make_workflow("global_workflow", None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = global_workflow
        mock_session.execute.return_value = mock_result

        # Superuser with org_id=org1
        repo = WorkflowRepository(mock_session, org_id=org1_id, is_superuser=True)
        result = await repo.get(id=global_workflow.id)

        assert result is not None
        assert result.id == global_workflow.id
        assert result.organization_id is None
        # Single query - superuser bypass
        assert mock_session.execute.call_count == 1

    async def test_superuser_name_lookup_uses_cascade_scoping(
        self, mock_session, org2_id
    ):
        """
        Superuser name lookup uses cascade scoping (org-specific first).

        This is critical for SDK data isolation - when org2 looks up table
        "test_table" by name, it should find org2's table, not org1's.
        Even superusers must use cascade scoping for name lookups.
        """
        from src.repositories.workflows import WorkflowRepository

        # Org2 has a workflow with the same name
        org2_workflow = make_workflow("shared_workflow", org2_id)
        org2_workflow.access_level = "authenticated"

        # First query (org-specific) returns the org2 workflow
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = org2_workflow
        mock_session.execute.return_value = mock_result_org

        # Superuser with org_id=org2 looking up by NAME (not ID)
        repo = WorkflowRepository(mock_session, org_id=org2_id, is_superuser=True)
        result = await repo.get(name="shared_workflow")

        assert result is not None
        assert result.id == org2_workflow.id
        # CRITICAL: Should find org2's workflow, not org1's
        assert result.organization_id == org2_id
        # Should use cascade scoping - first query is org-specific
        assert mock_session.execute.call_count == 1

    async def test_superuser_name_lookup_falls_back_to_global(
        self, mock_session, org1_id
    ):
        """
        Superuser name lookup falls back to global if not found in org.

        When org-specific lookup returns None, should fall back to global scope.
        """
        from src.repositories.workflows import WorkflowRepository

        global_workflow = make_workflow("global_only_workflow", None)
        global_workflow.access_level = "authenticated"

        # First query (org-specific) returns None
        mock_result_org = MagicMock()
        mock_result_org.scalar_one_or_none.return_value = None

        # Second query (global) returns the global workflow
        mock_result_global = MagicMock()
        mock_result_global.scalar_one_or_none.return_value = global_workflow

        mock_session.execute.side_effect = [mock_result_org, mock_result_global]

        # Superuser with org_id looking up by NAME
        repo = WorkflowRepository(mock_session, org_id=org1_id, is_superuser=True)
        result = await repo.get(name="global_only_workflow")

        assert result is not None
        assert result.id == global_workflow.id
        assert result.organization_id is None
        # Two queries: org-specific (not found), then global (found)
        assert mock_session.execute.call_count == 2


class TestOrgScopedRepositoryRegularUserAccess:
    """
    Tests that regular users have cascade scoping + role checks.

    The key behaviors being tested:
    - Regular users only see their org + global (cascade scoping)
    - Regular users must pass role checks for role-based entities
    - Regular users cannot access other orgs' workflows
    """

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def org1_id(self):
        """Create a test organization ID for org1."""
        return uuid4()

    @pytest.fixture
    def org2_id(self):
        """Create a test organization ID for org2."""
        return uuid4()

    @pytest.fixture
    def user_id(self):
        """Create a test user ID."""
        return uuid4()

    async def test_regular_user_accesses_own_org_authenticated_workflow(
        self, mock_session, org1_id, user_id
    ):
        """Regular user can access org1 authenticated workflow."""
        from src.repositories.workflows import WorkflowRepository

        org1_workflow = make_workflow("org1_workflow", org1_id)
        # Set access_level to authenticated (any user in scope can access)
        org1_workflow.access_level = "authenticated"

        # First query (org-specific) returns the workflow
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = org1_workflow
        mock_session.execute.return_value = mock_result

        # Regular user in org1
        repo = WorkflowRepository(
            mock_session, org_id=org1_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get(id=org1_workflow.id)

        assert result is not None
        assert result.id == org1_workflow.id
        assert result.organization_id == org1_id

    async def test_regular_user_accesses_global_authenticated_workflow(
        self, mock_session, org1_id, user_id
    ):
        """Regular user can access global authenticated workflow by ID."""
        from src.repositories.workflows import WorkflowRepository

        global_workflow = make_workflow("global_workflow", None)
        global_workflow.access_level = "authenticated"

        # ID lookup: single query returns the global workflow directly
        # (no cascade for ID lookups - IDs are globally unique)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = global_workflow
        mock_session.execute.return_value = mock_result

        # Regular user in org1
        repo = WorkflowRepository(
            mock_session, org_id=org1_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get(id=global_workflow.id)

        assert result is not None
        assert result.id == global_workflow.id
        assert result.organization_id is None
        # ID lookup: single query, no cascade
        assert mock_session.execute.call_count == 1

    async def test_regular_user_cannot_access_other_org_workflow(
        self, mock_session, org1_id, org2_id, user_id
    ):
        """Regular user in org1 cannot access org2-scoped workflow by ID."""
        from src.repositories.workflows import WorkflowRepository

        org2_workflow = make_workflow("org2_workflow", org2_id)
        org2_workflow.access_level = "authenticated"

        # ID lookup: single query finds the workflow
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = org2_workflow
        mock_session.execute.return_value = mock_result

        # Regular user in org1 trying to access org2's workflow by ID
        repo = WorkflowRepository(
            mock_session, org_id=org1_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get(id=org2_workflow.id)

        # Should NOT access the workflow (not in user's scope)
        # ID lookup finds it, but org scope check blocks access
        assert result is None
        # Single query for ID lookup
        assert mock_session.execute.call_count == 1

    async def test_regular_user_with_role_accesses_role_based_workflow(
        self, mock_session, org1_id, user_id
    ):
        """Regular user with matching role can access role-based workflow."""
        from src.repositories.workflows import WorkflowRepository

        org1_workflow = make_workflow("org1_workflow", org1_id)
        org1_workflow.access_level = "role_based"

        role_id = uuid4()

        # First query returns the workflow
        mock_result_workflow = MagicMock()
        mock_result_workflow.scalar_one_or_none.return_value = org1_workflow

        # Second query (user roles) returns the user's roles
        mock_result_user_roles = MagicMock()
        mock_result_user_roles.scalars.return_value.all.return_value = [role_id]

        # Third query (workflow roles) returns the workflow's roles
        mock_result_workflow_roles = MagicMock()
        mock_result_workflow_roles.scalars.return_value.all.return_value = [role_id]

        mock_session.execute.side_effect = [
            mock_result_workflow,
            mock_result_user_roles,
            mock_result_workflow_roles,
        ]

        # Regular user with role
        repo = WorkflowRepository(
            mock_session, org_id=org1_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get(id=org1_workflow.id)

        assert result is not None
        assert result.id == org1_workflow.id

    async def test_regular_user_without_role_cannot_access_role_based_workflow(
        self, mock_session, org1_id, user_id
    ):
        """Regular user without matching role cannot access role-based workflow."""
        from src.repositories.workflows import WorkflowRepository

        org1_workflow = make_workflow("org1_workflow", org1_id)
        org1_workflow.access_level = "role_based"

        workflow_role_id = uuid4()
        user_role_id = uuid4()  # Different role

        # First query returns the workflow
        mock_result_workflow = MagicMock()
        mock_result_workflow.scalar_one_or_none.return_value = org1_workflow

        # Second query (user roles) returns the user's roles (different from workflow)
        mock_result_user_roles = MagicMock()
        mock_result_user_roles.scalars.return_value.all.return_value = [user_role_id]

        # Third query (workflow roles) returns the workflow's roles
        mock_result_workflow_roles = MagicMock()
        mock_result_workflow_roles.scalars.return_value.all.return_value = [
            workflow_role_id
        ]

        mock_session.execute.side_effect = [
            mock_result_workflow,
            mock_result_user_roles,
            mock_result_workflow_roles,
        ]

        # Regular user without matching role
        repo = WorkflowRepository(
            mock_session, org_id=org1_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get(id=org1_workflow.id)

        # Should NOT find the workflow (role check fails)
        assert result is None

    async def test_regular_user_without_any_roles_cannot_access_role_based_workflow(
        self, mock_session, org1_id, user_id
    ):
        """Regular user with no roles cannot access role-based workflow."""
        from src.repositories.workflows import WorkflowRepository

        org1_workflow = make_workflow("org1_workflow", org1_id)
        org1_workflow.access_level = "role_based"

        # First query returns the workflow
        mock_result_workflow = MagicMock()
        mock_result_workflow.scalar_one_or_none.return_value = org1_workflow

        # Second query (user roles) returns empty list (user has no roles)
        mock_result_user_roles = MagicMock()
        mock_result_user_roles.scalars.return_value.all.return_value = []

        mock_session.execute.side_effect = [
            mock_result_workflow,
            mock_result_user_roles,
        ]

        # Regular user without any roles
        repo = WorkflowRepository(
            mock_session, org_id=org1_id, user_id=user_id, is_superuser=False
        )
        result = await repo.get(id=org1_workflow.id)

        # Should NOT find the workflow (no roles to check)
        assert result is None
