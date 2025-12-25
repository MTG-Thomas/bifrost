"""
Unit tests for Bifrost Users SDK module.

Tests platform mode (inside workflows) only.
Uses mocked dependencies for fast, isolated testing.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from bifrost._context import set_execution_context, clear_execution_context


@pytest.fixture
def test_org_id():
    """Return a test organization ID."""
    return str(uuid4())


@pytest.fixture
def test_context(test_org_id):
    """Create execution context for platform mode testing."""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id=test_org_id, name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="test-user",
        email="test@example.com",
        name="Test User",
        scope=test_org_id,
        organization=org,
        is_platform_admin=False,
        is_function_key=False,
        execution_id="test-exec-123",
    )


@pytest.fixture
def admin_context(test_org_id):
    """Create platform admin execution context."""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id=test_org_id, name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="admin-user",
        email="admin@example.com",
        name="Admin User",
        scope=test_org_id,
        organization=org,
        is_platform_admin=True,
        is_function_key=False,
        execution_id="admin-exec-456",
    )


class TestUsersPlatformMode:
    """Test users SDK methods in platform mode (inside workflows)."""

    @pytest.fixture(autouse=True)
    def cleanup_context(self):
        """Ensure context is cleared after each test."""
        yield
        clear_execution_context()

    @pytest.mark.asyncio
    async def test_list_returns_users_for_admin(self, admin_context, test_org_id):
        """Test that users.list() returns all users for platform admins."""
        from bifrost import users
        from src.models import User as UserORM
        from src.models.enums import UserType

        set_execution_context(admin_context)

        # Mock database session and query results
        user1_id = uuid4()
        user2_id = uuid4()

        mock_user1 = UserORM(
            id=user1_id,
            email="user1@example.com",
            name="User One",
            hashed_password="hash1",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_registered=True,
            mfa_enabled=False,
            user_type=UserType.ORG,
            organization_id=uuid4(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        mock_user2 = UserORM(
            id=user2_id,
            email="user2@example.com",
            name="User Two",
            hashed_password="hash2",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_registered=True,
            mfa_enabled=False,
            user_type=UserType.ORG,
            organization_id=uuid4(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_user1, mock_user2]

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            result = await users.list()

        assert len(result) == 2
        assert result[0].email == "user1@example.com"
        assert result[1].email == "user2@example.com"

    @pytest.mark.asyncio
    async def test_list_filters_by_org_for_non_admin(self, test_context, test_org_id):
        """Test that users.list() filters by org for non-admin users."""
        from bifrost import users
        from src.models import User as UserORM
        from src.models.enums import UserType
        from uuid import UUID

        set_execution_context(test_context)

        # Mock user in same org
        mock_user = UserORM(
            id=uuid4(),
            email="colleague@example.com",
            name="Colleague",
            hashed_password="hash",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_registered=True,
            mfa_enabled=False,
            user_type=UserType.ORG,
            organization_id=UUID(test_org_id),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_user]

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            result = await users.list()

        assert len(result) == 1
        assert result[0].email == "colleague@example.com"

    @pytest.mark.asyncio
    async def test_list_raises_when_non_admin_tries_other_org(self, test_context):
        """Test that users.list() raises PermissionError for non-admin cross-org access."""
        from bifrost import users

        set_execution_context(test_context)

        other_org_id = str(uuid4())

        with pytest.raises(PermissionError, match="cannot list users from other organizations"):
            await users.list(org_id=other_org_id)

    @pytest.mark.asyncio
    async def test_get_returns_user_by_id(self, test_context):
        """Test that users.get() returns user by UUID."""
        from bifrost import users
        from src.models import User as UserORM
        from src.models.enums import UserType

        set_execution_context(test_context)

        user_id = uuid4()

        mock_user = UserORM(
            id=user_id,
            email="found@example.com",
            name="Found User",
            hashed_password="hash",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_registered=True,
            mfa_enabled=False,
            user_type=UserType.ORG,
            organization_id=uuid4(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            result = await users.get(str(user_id))

        assert result is not None
        assert result.email == "found@example.com"
        assert result.name == "Found User"

    @pytest.mark.asyncio
    async def test_get_returns_user_by_email(self, test_context):
        """Test that users.get() returns user by email."""
        from bifrost import users
        from src.models import User as UserORM
        from src.models.enums import UserType

        set_execution_context(test_context)

        mock_user = UserORM(
            id=uuid4(),
            email="email@example.com",
            name="Email User",
            hashed_password="hash",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_registered=True,
            mfa_enabled=False,
            user_type=UserType.ORG,
            organization_id=uuid4(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            result = await users.get("email@example.com")

        assert result is not None
        assert result.email == "email@example.com"

    @pytest.mark.asyncio
    async def test_get_returns_none_when_user_not_found(self, test_context):
        """Test that users.get() returns None when user not found."""
        from bifrost import users

        set_execution_context(test_context)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            result = await users.get("nonexistent@example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_requires_admin_privileges(self, test_context):
        """Test that users.create() requires admin privileges."""
        from bifrost import users

        set_execution_context(test_context)

        with pytest.raises(PermissionError, match="not a platform admin"):
            await users.create(
                email="new@example.com",
                name="New User",
                org_id=str(uuid4())
            )

    @pytest.mark.asyncio
    async def test_create_validates_org_id_for_non_superusers(self, admin_context):
        """Test that users.create() requires org_id for non-superuser users."""
        from bifrost import users

        set_execution_context(admin_context)

        with pytest.raises(ValueError, match="org_id is required"):
            await users.create(
                email="new@example.com",
                name="New User",
                is_superuser=False,
                org_id=None
            )

    @pytest.mark.asyncio
    async def test_create_creates_user_successfully(self, admin_context, test_org_id):
        """Test that users.create() creates user successfully."""
        from bifrost import users
        from src.models import User as UserORM
        from src.models.enums import UserType

        set_execution_context(admin_context)

        new_user_id = uuid4()

        mock_user = UserORM(
            id=new_user_id,
            email="created@example.com",
            name="Created User",
            hashed_password="",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_registered=False,
            mfa_enabled=False,
            user_type=UserType.ORG,
            organization_id=uuid4(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.commit = AsyncMock()

        # Mock refresh to set the user attributes
        async def mock_refresh_func(obj):
            for key, value in vars(mock_user).items():
                if not key.startswith("_"):
                    setattr(obj, key, value)

        mock_db.refresh = AsyncMock(side_effect=mock_refresh_func)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            result = await users.create(
                email="created@example.com",
                name="Created User",
                org_id=test_org_id
            )

        assert result.email == "created@example.com"
        assert result.name == "Created User"
        assert result.is_verified is True
        assert result.is_registered is False

    @pytest.mark.asyncio
    async def test_update_requires_admin_privileges(self, test_context):
        """Test that users.update() requires admin privileges."""
        from bifrost import users

        set_execution_context(test_context)

        with pytest.raises(PermissionError, match="not a platform admin"):
            await users.update("user-123", name="New Name")

    @pytest.mark.asyncio
    async def test_update_updates_user_successfully(self, admin_context):
        """Test that users.update() updates user successfully."""
        from bifrost import users
        from src.models import User as UserORM
        from src.models.enums import UserType

        set_execution_context(admin_context)

        user_id = uuid4()

        mock_user = UserORM(
            id=user_id,
            email="old@example.com",
            name="Old Name",
            hashed_password="hash",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_registered=True,
            mfa_enabled=False,
            user_type=UserType.ORG,
            organization_id=uuid4(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            result = await users.update(str(user_id), name="New Name")

        assert result.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_raises_when_user_not_found(self, admin_context):
        """Test that users.update() raises ValueError when user not found."""
        from bifrost import users

        set_execution_context(admin_context)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            with pytest.raises(ValueError, match="User not found"):
                await users.update("nonexistent@example.com", name="New Name")

    @pytest.mark.asyncio
    async def test_delete_requires_admin_privileges(self, test_context):
        """Test that users.delete() requires admin privileges."""
        from bifrost import users

        set_execution_context(test_context)

        with pytest.raises(PermissionError, match="not a platform admin"):
            await users.delete("user-123")

    @pytest.mark.asyncio
    async def test_delete_prevents_self_deletion(self, admin_context):
        """Test that users.delete() prevents deleting yourself."""
        from bifrost import users

        set_execution_context(admin_context)

        with pytest.raises(ValueError, match="Cannot delete yourself"):
            await users.delete(admin_context.user_id)

        with pytest.raises(ValueError, match="Cannot delete yourself"):
            await users.delete(admin_context.email)

    @pytest.mark.asyncio
    async def test_delete_soft_deletes_user_successfully(self, admin_context):
        """Test that users.delete() performs soft delete successfully."""
        from bifrost import users
        from src.models import User as UserORM
        from src.models.enums import UserType

        set_execution_context(admin_context)

        user_id = uuid4()

        mock_user = UserORM(
            id=user_id,
            email="todelete@example.com",
            name="To Delete",
            hashed_password="hash",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_registered=True,
            mfa_enabled=False,
            user_type=UserType.ORG,
            organization_id=uuid4(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            result = await users.delete(str(user_id))

        assert result is True
        assert mock_user.is_active is False

    @pytest.mark.asyncio
    async def test_delete_raises_when_user_not_found(self, admin_context):
        """Test that users.delete() raises ValueError when user not found."""
        from bifrost import users

        set_execution_context(admin_context)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_db

        with patch("bifrost.users.get_session_factory", return_value=mock_session_factory):
            with pytest.raises(ValueError, match="User not found"):
                await users.delete("nonexistent@example.com")

    @pytest.mark.asyncio
    async def test_all_methods_require_execution_context(self):
        """Test that all users methods require execution context."""
        from bifrost import users

        clear_execution_context()

        # Test methods that should fail without context
        with pytest.raises(RuntimeError, match="execution context"):
            await users.list()

        with pytest.raises(RuntimeError, match="execution context"):
            await users.get("user-123")

        with pytest.raises(RuntimeError, match="execution context"):
            await users.create(email="test@example.com", name="Test", org_id="org-123")

        with pytest.raises(RuntimeError, match="execution context"):
            await users.update("user-123", name="New Name")

        with pytest.raises(RuntimeError, match="execution context"):
            await users.delete("user-123")
