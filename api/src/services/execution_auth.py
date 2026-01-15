"""
Execution Authorization Service

Unified permission checking for workflow and data provider execution.
Determines if a user can execute a workflow based on:
1. Platform admin status
2. API key access
3. Integration-based access (data providers tied to integrations)
4. Direct workflow access via workflow.access_level + workflow_roles

All workflow ID checks also cover data providers, since data providers
are now stored in the workflows table with type='data_provider'.
"""

import logging
from uuid import UUID

from sqlalchemy import exists, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.integrations import Integration
from src.models.orm.users import UserRole
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow

logger = logging.getLogger(__name__)


class ExecutionAuthService:
    """
    Service for checking workflow/data provider execution permissions.

    Permission is granted if ANY of:
    1. User is platform admin (superuser)
    2. Request is via API key
    3. Workflow is tied to an integration (any authenticated user)
    4. User has direct access via workflow.access_level + workflow_roles

    Authorization is based on the workflow's access_level and workflow_roles table,
    which are populated when forms/apps are created/updated.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def can_execute_workflow(
        self,
        workflow_id: str,
        user_id: UUID | None,
        user_org_id: UUID | None,
        is_superuser: bool,
        is_api_key: bool = False,
    ) -> bool:
        """
        Check if user can execute a workflow.

        Args:
            workflow_id: UUID string of the workflow (or data provider)
            user_id: User UUID (None for API key requests)
            user_org_id: User's organization UUID
            is_superuser: Whether user is platform admin
            is_api_key: Whether request is via API key

        Returns:
            True if execution is allowed
        """
        # 1. Platform admin - always allowed
        if is_superuser:
            logger.debug("Execution allowed: user is platform admin")
            return True

        # 2. API key - always allowed (key validation happens in router)
        if is_api_key:
            logger.debug("Execution allowed: API key access")
            return True

        # Need user_id for access checks
        if not user_id:
            logger.debug("Execution denied: no user_id for non-admin/non-api-key request")
            return False

        # 3. Check integration-based access (data providers tied to integrations)
        # Any authenticated user can access data providers that are linked to integrations
        if await self._has_integration_access(workflow_id):
            logger.debug(f"Execution allowed: workflow {workflow_id} is tied to an integration")
            return True

        # 4. Check direct workflow access via workflow.access_level + workflow_roles
        # This checks if the workflow itself grants access (authenticated or role-based)
        if await self._has_direct_workflow_access(workflow_id, user_id, user_org_id):
            logger.debug(f"Execution allowed: user has direct access to workflow {workflow_id}")
            return True

        logger.debug(f"Execution denied: no access found for workflow {workflow_id}")
        return False

    async def _has_direct_workflow_access(
        self,
        workflow_id: str,
        user_id: UUID,
        user_org_id: UUID | None,
    ) -> bool:
        """
        Check if user has direct access to a workflow via its access_level and workflow_roles.

        This is a new authorization path that checks:
        1. Workflow org scoping (user's org or global)
        2. Workflow access_level: 'authenticated' (any authenticated user) or 'role_based'
        3. For role_based, checks if user has a matching role via workflow_roles

        Args:
            workflow_id: UUID string of the workflow
            user_id: User UUID
            user_org_id: User's organization UUID

        Returns:
            True if user has direct access to the workflow
        """
        try:
            workflow_uuid = UUID(workflow_id)
        except ValueError:
            logger.warning(f"Invalid workflow_id format: {workflow_id}")
            return False

        # Get the workflow with org scoping
        query = select(Workflow).where(
            Workflow.id == workflow_uuid,
            Workflow.is_active.is_(True),
        )

        # Apply org scoping: user's org or global (NULL)
        if user_org_id:
            query = query.where(
                or_(
                    Workflow.organization_id == user_org_id,
                    Workflow.organization_id.is_(None),
                )
            )
        else:
            query = query.where(Workflow.organization_id.is_(None))

        result = await self.db.execute(query)
        workflow = result.scalar_one_or_none()

        if not workflow:
            return False

        # Check access_level
        access_level = workflow.access_level or "role_based"

        if access_level == "authenticated":
            # Any authenticated user can access
            return True

        if access_level == "role_based":
            # Check if user has a role assigned to this workflow
            user_roles_subq = select(UserRole.role_id).where(UserRole.user_id == user_id)

            role_check_query = select(
                exists(
                    select(literal(1))
                    .select_from(WorkflowRole)
                    .where(
                        WorkflowRole.workflow_id == workflow_uuid,
                        WorkflowRole.role_id.in_(user_roles_subq),
                    )
                )
            )

            role_result = await self.db.execute(role_check_query)
            return role_result.scalar() or False

        return False

    async def _has_integration_access(self, workflow_id: str) -> bool:
        """
        Check if workflow is tied to an integration.

        Data providers linked to integrations are accessible to any authenticated user.
        This is a special case for integration entity providers (e.g., list_entities_data_provider_id).

        Returns:
            True if workflow is linked to an active integration
        """
        try:
            workflow_uuid = UUID(workflow_id)
        except ValueError:
            return False

        # Check if this workflow/data provider is tied to an integration
        query = select(
            exists(
                select(Integration.id).where(
                    Integration.list_entities_data_provider_id == workflow_uuid,
                    Integration.is_deleted.is_(False),
                )
            )
        )

        result = await self.db.execute(query)
        return result.scalar() or False


async def check_workflow_execution_access(
    db: AsyncSession,
    workflow_id: str,
    user_id: UUID | None,
    user_org_id: UUID | None,
    is_superuser: bool,
    is_api_key: bool = False,
) -> bool:
    """
    Convenience function to check workflow execution access.

    This is a thin wrapper around ExecutionAuthService for simple use cases.
    """
    service = ExecutionAuthService(db)
    return await service.can_execute_workflow(
        workflow_id=workflow_id,
        user_id=user_id,
        user_org_id=user_org_id,
        is_superuser=is_superuser,
        is_api_key=is_api_key,
    )
