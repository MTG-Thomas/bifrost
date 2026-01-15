"""
Authorization Service

Provides permission checking and access control for the Bifrost platform.
This service handles three layers of access control:

1. Organization Scoping (OrgScopedRepository)
   - Tenant isolation via organization_id filtering
   - PlatformAdmins can access any org via X-Organization-Id header

2. Role-Based Access Control
   - Forms can be restricted to specific roles
   - accessLevel: "authenticated" (any logged-in user) or "role_based" (role membership required)

3. User-Level Ownership
   - Executions are filtered by the user who triggered them
   - PlatformAdmins can view all executions in scope
"""

import logging
from typing import Literal
from uuid import UUID

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import ExecutionContext
from src.models import Form, UserRole, Execution
from src.models.orm.applications import Application
from src.models.orm.app_roles import AppRole
from src.models.orm.agents import Agent, AgentRole
from src.models.orm.forms import FormRole
from src.models.orm.workflows import Workflow
from src.models.orm.workflow_roles import WorkflowRole

logger = logging.getLogger(__name__)


class AuthorizationService:
    """
    Service for checking user permissions and access control.

    This service is stateless and operates on the provided ExecutionContext.
    """

    def __init__(self, db: AsyncSession, context: ExecutionContext):
        """
        Initialize authorization service.

        Args:
            db: Database session
            context: Execution context with user and org scope
        """
        self.db = db
        self.context = context

    # =========================================================================
    # Role Queries
    # =========================================================================

    async def get_user_role_ids(self, user_id: UUID | None = None) -> list[str]:
        """
        Get all role IDs (UUIDs) assigned to a user.

        Args:
            user_id: User ID (defaults to current user from context)

        Returns:
            List of role UUID strings
        """
        uid = user_id or self.context.user.user_id

        query = select(UserRole.role_id).where(UserRole.user_id == uid)
        result = await self.db.execute(query)
        return [str(row) for row in result.scalars().all()]

    async def get_form_role_ids(self, form_id: UUID) -> list[str]:
        """
        Get all role IDs (UUIDs) that can access a form.

        Args:
            form_id: Form ID (UUID)

        Returns:
            List of role UUID strings
        """
        # Query role assignments from the FormRole join table
        query = select(FormRole.role_id).where(FormRole.form_id == form_id)
        result = await self.db.execute(query)
        return [str(role_id) for role_id in result.scalars().all()]

    # =========================================================================
    # Form Access Control
    # =========================================================================

    async def can_user_view_form(self, form_id: UUID) -> bool:
        """
        Check if user can view a form.

        Rules:
        - Platform admins: can view all forms (active and inactive)
        - Regular users:
            - Must be active form
            - accessLevel="authenticated" -> any authenticated user
            - accessLevel="role_based" (or None) -> user must have assigned role

        Args:
            form_id: Form ID (UUID)

        Returns:
            True if user can view form, False otherwise
        """
        # Get form (with org scoping - includes global forms)
        form = await self._get_form_with_scoping(form_id)

        if not form:
            return False

        # Platform admins can view all forms (including inactive)
        if self.context.is_platform_admin:
            return True

        # Regular users can only view active forms
        if not form.is_active:
            return False

        # Check access level (default to role_based if not set)
        access_level = form.access_level or "role_based"

        if access_level == "authenticated":
            # Any authenticated user can access
            return True
        elif access_level == "role_based":
            # Check role membership
            user_roles = await self.get_user_role_ids()
            form_roles = await self.get_form_role_ids(form_id)
            return any(role in form_roles for role in user_roles)

        return False

    async def can_user_execute_form(self, form_id: UUID) -> bool:
        """
        Check if user can execute a form.

        Same rules as can_user_view_form (if you can view it, you can execute it).

        Args:
            form_id: Form ID (UUID)

        Returns:
            True if user can execute form, False otherwise
        """
        return await self.can_user_view_form(form_id)

    async def get_user_visible_forms(self, active_only: bool = True) -> list[Form]:
        """
        Get all forms visible to the user (filtered by permissions).

        Rules:
        - Platform admins: see all forms (active and inactive) in context.org_id scope
        - Regular users: see active forms they have access to based on accessLevel:
            - authenticated: all active forms with this access level
            - role_based: only forms where user has an assigned role

        Args:
            active_only: If True, only return active forms (ignored for platform admins)

        Returns:
            List of Form objects
        """
        # Base query with org scoping (org + global)
        query = select(Form)
        if self.context.org_id:
            query = query.where(
                or_(
                    Form.organization_id == self.context.org_id,
                    Form.organization_id.is_(None),
                )
            )
        else:
            query = query.where(Form.organization_id.is_(None))

        # Platform admin sees all forms (including inactive) in their current scope
        if self.context.is_platform_admin:
            if active_only:
                query = query.where(Form.is_active)
            result = await self.db.execute(query)
            return list(result.scalars().all())

        # Regular user: filter to active only
        query = query.where(Form.is_active)
        result = await self.db.execute(query)
        all_forms = list(result.scalars().all())

        # Get user's role IDs once for efficiency
        user_role_ids = await self.get_user_role_ids()

        # Filter forms by access level
        visible_forms = []
        for form in all_forms:
            access_level = form.access_level or "role_based"

            if access_level == "authenticated":
                # Any authenticated user can see this form
                visible_forms.append(form)
            elif access_level == "role_based":
                # Check if user has any of the roles assigned to this form
                form_role_ids = await self.get_form_role_ids(form.id)
                if any(role_id in form_role_ids for role_id in user_role_ids):
                    visible_forms.append(form)

        return visible_forms

    # =========================================================================
    # Execution Access Control
    # =========================================================================

    def can_user_view_execution(self, execution: Execution) -> bool:
        """
        Check if user can view an execution.

        Rules:
        - Platform admins: can view all executions
        - Regular users: can only view THEIR executions

        Args:
            execution: Execution entity

        Returns:
            True if user can view execution, False otherwise
        """
        # Platform admins can view all
        if self.context.is_platform_admin:
            return True

        # Regular users can only view their own executions
        return execution.executed_by == self.context.user.user_id

    async def get_user_executions(self, limit: int = 50) -> list[Execution]:
        """
        Get executions visible to the user.

        Rules:
        - Platform admins: all executions in context.org_id scope
        - Regular users: only THEIR executions

        Args:
            limit: Maximum number of executions to return

        Returns:
            List of Execution objects
        """
        query = select(Execution)

        # Org scoping (strict - no global fallback for executions)
        if self.context.org_id:
            query = query.where(Execution.organization_id == self.context.org_id)
        else:
            query = query.where(Execution.organization_id.is_(None))

        # User-level filtering for non-admins
        if not self.context.is_platform_admin:
            query = query.where(Execution.executed_by == self.context.user.user_id)

        # Order by most recent and limit
        query = query.order_by(Execution.started_at.desc()).limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    # =========================================================================
    # Private Helpers
    # =========================================================================

    async def _get_form_with_scoping(self, form_id: UUID) -> Form | None:
        """
        Get form by ID with org scoping (includes global forms).

        Args:
            form_id: Form ID

        Returns:
            Form or None if not found/not accessible
        """
        query = select(Form).where(Form.id == form_id)

        # Apply org scoping (cascade pattern - org + global)
        if self.context.org_id:
            query = query.where(
                or_(
                    Form.organization_id == self.context.org_id,
                    Form.organization_id.is_(None),
                )
            )
        else:
            query = query.where(Form.organization_id.is_(None))

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    # =========================================================================
    # Unified Entity Access Control
    # =========================================================================

    async def _check_role_access(
        self,
        entity_id: UUID,
        entity_type: Literal["form", "app", "agent", "workflow"],
    ) -> bool:
        """
        Check if user has a role granting access to this entity.

        Args:
            entity_id: Entity ID (UUID)
            entity_type: Type of entity ("form", "app", "agent", or "workflow")

        Returns:
            True if user has a role that grants access, False otherwise
        """
        user_roles = await self.get_user_role_ids()
        if not user_roles:
            return False

        # Map entity type to role table and ID column
        role_configs = {
            "form": (FormRole, FormRole.form_id),
            "app": (AppRole, AppRole.app_id),
            "agent": (AgentRole, AgentRole.agent_id),
            "workflow": (WorkflowRole, WorkflowRole.workflow_id),
        }
        role_table, id_column = role_configs[entity_type]

        # Query roles assigned to this entity
        query = select(role_table.role_id).where(id_column == entity_id)
        result = await self.db.execute(query)
        entity_roles = [str(r) for r in result.scalars().all()]

        # Check if user has any of the entity's roles
        return any(role in entity_roles for role in user_roles)

    async def can_access_entity(
        self,
        entity: Form | Application | Agent | Workflow,
        entity_type: Literal["form", "app", "agent", "workflow"],
    ) -> bool:
        """
        Unified access check for forms, apps, agents, and workflows.

        Rules:
        1. Platform admins can access anything
        2. Entity org must match user org (or entity must be global)
        3. access_level="authenticated" -> any authenticated user in scope
        4. access_level="role_based" -> user must have a matching role

        Args:
            entity: The entity to check access for
            entity_type: Type of entity ("form", "app", "agent", or "workflow")

        Returns:
            True if user can access the entity, False otherwise
        """
        # Platform admins can access anything
        if self.context.is_platform_admin:
            return True

        # Check org scoping - entity org must match user org, or be global
        entity_org = getattr(entity, "organization_id", None)
        if entity_org is not None and entity_org != self.context.org_id:
            return False

        # If user has no org and entity is org-scoped, deny
        if entity_org is not None and self.context.org_id is None:
            return False

        # Check access level (default to "authenticated" if not set)
        raw_access_level = getattr(entity, "access_level", None)
        if raw_access_level is None:
            access_level_str = "authenticated"
        elif hasattr(raw_access_level, "value"):
            # It's an enum, get the string value
            access_level_str = raw_access_level.value
        else:
            # It's already a string
            access_level_str = str(raw_access_level)

        if access_level_str == "authenticated":
            return True

        if access_level_str == "role_based":
            return await self._check_role_access(entity.id, entity_type)

        return False

    # =========================================================================
    # Entity-Specific Convenience Wrappers
    # =========================================================================

    async def can_access_form(self, form: Form) -> bool:
        """
        Check if user can access a form.

        Args:
            form: Form entity

        Returns:
            True if user can access the form, False otherwise
        """
        return await self.can_access_entity(form, "form")

    async def can_access_app(self, app: Application) -> bool:
        """
        Check if user can access an application.

        Args:
            app: Application entity

        Returns:
            True if user can access the app, False otherwise
        """
        return await self.can_access_entity(app, "app")

    async def can_access_agent(self, agent: Agent) -> bool:
        """
        Check if user can access an agent.

        Args:
            agent: Agent entity

        Returns:
            True if user can access the agent, False otherwise
        """
        return await self.can_access_entity(agent, "agent")

    async def can_access_workflow(self, workflow: Workflow) -> bool:
        """
        Check if user can access a workflow.

        Args:
            workflow: Workflow entity

        Returns:
            True if user can access the workflow, False otherwise
        """
        return await self.can_access_entity(workflow, "workflow")


# Convenience function for creating service from context
def get_authorization_service(context: ExecutionContext) -> AuthorizationService:
    """
    Create authorization service from execution context.

    Args:
        context: Execution context (contains db session)

    Returns:
        AuthorizationService instance
    """
    return AuthorizationService(context.db, context)
