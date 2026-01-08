"""
Entity Resolution Service for File Storage.

Handles resolving workflow and agent references by ID or name.
"""

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID as UUID_type

if TYPE_CHECKING:
    from src.models import Workflow, Agent

logger = logging.getLogger(__name__)


class EntityResolutionService:
    """Service for resolving workflow and agent entity references."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_workflow_by_id(self, workflow_id: str) -> "Workflow | None":
        """
        Get a workflow by its ID (string UUID).

        Args:
            workflow_id: UUID string of the workflow

        Returns:
            Workflow if found, None otherwise
        """
        from src.models import Workflow

        try:
            workflow_uuid = UUID_type(workflow_id)
        except ValueError:
            return None

        result = await self.db.execute(
            select(Workflow).where(Workflow.id == workflow_uuid)
        )
        return result.scalar_one_or_none()

    async def get_agent_by_id(self, agent_id: str) -> "Agent | None":
        """
        Get an agent by its ID (string UUID).

        Args:
            agent_id: UUID string of the agent

        Returns:
            Agent if found, None otherwise
        """
        from src.models import Agent

        try:
            agent_uuid = UUID_type(agent_id)
        except ValueError:
            return None

        result = await self.db.execute(
            select(Agent).where(Agent.id == agent_uuid)
        )
        return result.scalar_one_or_none()

    async def find_workflow_match(self, stale_id: str) -> "Workflow | None":
        """
        Try to find a workflow that matches a stale/invalid ID.

        Strategy:
        1. Check if stale_id is actually a workflow name (legacy format)

        Args:
            stale_id: The ID or name that could not be resolved

        Returns:
            Matching Workflow if found, None otherwise
        """
        from src.models import Workflow

        # 1. Check if stale_id is a workflow name
        result = await self.db.execute(
            select(Workflow).where(
                Workflow.name == stale_id,
                Workflow.is_active == True,  # noqa: E712
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow:
            return workflow

        return None

    async def find_agent_match(self, stale_id: str) -> "Agent | None":
        """
        Try to find an agent that matches a stale/invalid ID.

        Strategy:
        1. Check if stale_id is actually an agent name (legacy format)

        Args:
            stale_id: The ID or name that could not be resolved

        Returns:
            Matching Agent if found, None otherwise
        """
        from src.models import Agent

        # 1. Check if stale_id is an agent name
        result = await self.db.execute(
            select(Agent).where(
                Agent.name == stale_id,
                Agent.is_active == True,  # noqa: E712
            )
        )
        agent = result.scalar_one_or_none()
        if agent:
            return agent

        return None
