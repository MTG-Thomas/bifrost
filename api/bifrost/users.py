"""
User management SDK for Bifrost.

Provides Python API for user operations from workflows.

All methods are async and must be awaited.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ._internal import get_context, require_admin

logger = logging.getLogger(__name__)


# Local dataclass for UserPublic (avoids src.* imports)
@dataclass
class UserPublic:
    """Public user model."""
    id: UUID
    email: str
    name: str
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False
    is_registered: bool = False
    user_type: str = "ORG"
    organization_id: UUID | None = None
    mfa_enabled: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class users:
    """
    User management operations.

    Uses direct database access (users are not cached in Redis).
    All write operations require admin privileges.

    All methods are async - await is required.
    """

    @staticmethod
    async def list(org_id: str | None = None) -> list[UserPublic]:
        """
        List users.

        Admin users can list all users across all organizations.
        Non-admin users can only list users in their own organization.

        Args:
            org_id: Optional organization ID to filter by (admin only)

        Returns:
            list[UserPublic]: List of user objects

        Raises:
            PermissionError: If non-admin tries to list users from other orgs
            RuntimeError: If no execution context

        Example:
            >>> from bifrost import users
            >>> all_users = await users.list()
            >>> org_users = await users.list(org_id="org-123")
        """
        from sqlalchemy import select
        from src.core.database import get_session_factory
        from src.models import User as UserORM
        from src.models.enums import UserType

        context = get_context()

        # Determine which org to filter by
        filter_org_id = org_id
        if not context.is_platform_admin:
            # Non-admins can only list their own org's users
            if org_id and org_id != context.org_id:
                raise PermissionError(
                    f"User {context.user_id} cannot list users from other organizations. "
                    "This operation requires platform admin privileges."
                )
            filter_org_id = context.org_id

        session_factory = get_session_factory()
        async with session_factory() as db:
            # Build query
            query = select(UserORM).where(
                UserORM.is_active == True,  # noqa: E712
                UserORM.user_type != UserType.SYSTEM,  # Exclude system users
            )

            # Apply org filter if specified
            if filter_org_id:
                from uuid import UUID
                query = query.where(UserORM.organization_id == UUID(filter_org_id))

            # Apply superuser filter for admin-only list
            if context.is_platform_admin and org_id is None:
                # No additional filter - return all users
                pass

            query = query.order_by(UserORM.email)

            result = await db.execute(query)
            users_list = result.scalars().all()

            return [UserPublic.model_validate(u) for u in users_list]

    @staticmethod
    async def get(user_id: str) -> UserPublic | None:
        """
        Get user by ID.

        Args:
            user_id: User ID (UUID or email)

        Returns:
            UserPublic | None: User object or None if not found

        Raises:
            RuntimeError: If no execution context

        Example:
            >>> from bifrost import users
            >>> user = await users.get("user-123")
            >>> if user:
            ...     print(user.email)
        """
        from sqlalchemy import select
        from uuid import UUID
        from src.core.database import get_session_factory
        from src.models import User as UserORM

        get_context()  # Validates user is authenticated

        session_factory = get_session_factory()
        async with session_factory() as db:
            # Try UUID first
            try:
                uuid_id = UUID(user_id)
                result = await db.execute(select(UserORM).where(UserORM.id == uuid_id))
            except ValueError:
                # Fall back to email lookup
                result = await db.execute(select(UserORM).where(UserORM.email == user_id))

            user = result.scalar_one_or_none()

            if not user:
                return None

            return UserPublic.model_validate(user)

    @staticmethod
    async def create(
        email: str,
        name: str,
        is_superuser: bool = False,
        org_id: str | None = None,
        is_active: bool = True,
    ) -> UserPublic:
        """
        Create a new user.

        Requires: Platform admin privileges

        Args:
            email: User email address
            name: User display name
            is_superuser: Whether user is a platform admin (default: False)
            org_id: Organization ID (required for non-superusers)
            is_active: Whether the user is active (default: True)

        Returns:
            UserPublic: Created user object

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If validation fails (e.g., org_id missing for non-superuser)
            RuntimeError: If no execution context

        Example:
            >>> from bifrost import users
            >>> user = await users.create(
            ...     email="john@example.com",
            ...     name="John Doe",
            ...     org_id="org-123"
            ... )
        """
        from datetime import datetime
        from uuid import UUID
        from src.core.database import get_session_factory
        from src.models import User as UserORM
        from src.models.enums import UserType

        context = require_admin()

        # Validate org_id requirement
        if not is_superuser and not org_id:
            raise ValueError("org_id is required when creating non-superuser users")

        session_factory = get_session_factory()
        async with session_factory() as db:
            # Determine user type
            user_type = UserType.PLATFORM if is_superuser else UserType.ORG

            # Create user
            now = datetime.utcnow()
            new_user = UserORM(
                email=email,
                name=name,
                hashed_password="",  # No password for admin-created users
                is_active=is_active,
                is_superuser=is_superuser,
                is_verified=True,  # Trusted since created by admin
                is_registered=False,  # User must complete registration
                user_type=user_type,
                organization_id=UUID(org_id) if org_id else None,
                created_at=now,
                updated_at=now,
            )

            db.add(new_user)
            await db.flush()
            await db.refresh(new_user)
            await db.commit()

            logger.info(
                f"Created user {new_user.email} (id: {new_user.id}) "
                f"by {context.user_id}"
            )

            return UserPublic.model_validate(new_user)

    @staticmethod
    async def update(user_id: str, **updates: Any) -> UserPublic:
        """
        Update a user.

        Requires: Platform admin privileges

        Args:
            user_id: User ID (UUID or email)
            **updates: Fields to update (email, name, is_active, is_superuser, etc.)

        Returns:
            UserPublic: Updated user object

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If user not found or validation fails
            RuntimeError: If no execution context

        Example:
            >>> from bifrost import users
            >>> user = await users.update("user-123", name="New Name", is_active=False)
        """
        from datetime import datetime
        from sqlalchemy import select
        from uuid import UUID
        from src.core.database import get_session_factory
        from src.models import User as UserORM
        from src.models.enums import UserType

        context = require_admin()

        session_factory = get_session_factory()
        async with session_factory() as db:
            # Try UUID first
            try:
                uuid_id = UUID(user_id)
                result = await db.execute(select(UserORM).where(UserORM.id == uuid_id))
            except ValueError:
                result = await db.execute(select(UserORM).where(UserORM.email == user_id))

            db_user = result.scalar_one_or_none()

            if not db_user:
                raise ValueError(f"User not found: {user_id}")

            # Apply updates
            if "email" in updates:
                db_user.email = updates["email"]
            if "name" in updates:
                db_user.name = updates["name"]
            if "is_active" in updates:
                db_user.is_active = updates["is_active"]
            if "is_superuser" in updates:
                db_user.is_superuser = updates["is_superuser"]
                if updates["is_superuser"]:
                    # Promoting to platform admin - remove org
                    db_user.organization_id = None
                    db_user.user_type = UserType.PLATFORM
                else:
                    db_user.user_type = UserType.ORG
            if "is_verified" in updates:
                db_user.is_verified = updates["is_verified"]
            if "mfa_enabled" in updates:
                db_user.mfa_enabled = updates["mfa_enabled"]
            if "organization_id" in updates:
                org_id_val = updates["organization_id"]
                db_user.organization_id = UUID(org_id_val) if org_id_val else None

            db_user.updated_at = datetime.utcnow()

            await db.flush()
            await db.refresh(db_user)
            await db.commit()

            logger.info(f"Updated user {user_id} by {context.user_id}")

            return UserPublic.model_validate(db_user)

    @staticmethod
    async def delete(user_id: str) -> bool:
        """
        Delete a user (soft delete - sets is_active to false).

        Requires: Platform admin privileges

        Args:
            user_id: User ID (UUID or email)

        Returns:
            bool: True if user was deleted

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If user not found or trying to delete self
            RuntimeError: If no execution context

        Example:
            >>> from bifrost import users
            >>> deleted = await users.delete("user-123")
        """
        from datetime import datetime
        from sqlalchemy import select
        from uuid import UUID
        from src.core.database import get_session_factory
        from src.models import User as UserORM

        context = require_admin()

        # Prevent self-deletion
        if user_id == context.user_id or user_id == context.email:
            raise ValueError("Cannot delete yourself")

        session_factory = get_session_factory()
        async with session_factory() as db:
            # Try UUID first
            try:
                uuid_id = UUID(user_id)
                result = await db.execute(select(UserORM).where(UserORM.id == uuid_id))
            except ValueError:
                result = await db.execute(select(UserORM).where(UserORM.email == user_id))

            db_user = result.scalar_one_or_none()

            if not db_user:
                raise ValueError(f"User not found: {user_id}")

            db_user.is_active = False
            db_user.updated_at = datetime.utcnow()

            await db.flush()
            await db.commit()

            logger.info(f"Deleted user {user_id} by {context.user_id}")

            return True
