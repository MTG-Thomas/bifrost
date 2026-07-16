"""Persistence for immutable Solution deployment runtime closures."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm.solution_deployments import SolutionDeployment
from src.repositories.base import BaseRepository


class SolutionDeploymentRepository(BaseRepository[SolutionDeployment]):  # type: ignore[type-var]
    model = SolutionDeployment

    def __init__(self, session: AsyncSession):
        super().__init__(session)

    async def get_runtime_closure(
        self, deployment_id: UUID, organization_id: UUID
    ) -> SolutionDeployment | None:
        """Read one tenant-scoped deployment together with exact dependency edges."""
        result = await self.session.execute(
            select(SolutionDeployment)
            .options(selectinload(SolutionDeployment.dependencies))
            .where(
                SolutionDeployment.id == deployment_id,
                SolutionDeployment.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()
