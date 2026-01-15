"""
Data Provider Repository

Database operations for data provider registry.

NOTE: Data providers are now stored in the workflows table with type='data_provider'.
This repository queries the workflows table with type filter for backward compatibility.
New code should use WorkflowRepository.get_data_providers() instead.
"""

from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select

from src.models import Workflow
from src.repositories.base import BaseRepository


class DataProviderRepository(BaseRepository[Workflow]):
    """Repository for data provider registry operations.

    NOTE: This repository now queries the workflows table with type='data_provider'
    filter. Data providers were consolidated into the workflows table in migration
    20260103_000000.

    For new code, prefer using WorkflowRepository.get_data_providers() directly.
    """

    model = Workflow

    async def get_by_name(
        self, name: str, org_id: UUID | None = None
    ) -> Workflow | None:
        """Get data provider by name with priority: org-specific > global.

        This uses prioritized lookup to avoid MultipleResultsFound when
        the same name exists in both org scope and global scope.

        Args:
            name: Data provider name to look up
            org_id: If provided, check org-specific first, then global fallback.
                   If None, only check global data providers.
        """
        # First try org-specific (if we have an org)
        if org_id:
            result = await self.session.execute(
                select(Workflow).where(
                    Workflow.name == name,
                    Workflow.type == "data_provider",
                    Workflow.is_active.is_(True),
                    Workflow.organization_id == org_id,
                )
            )
            entity = result.scalar_one_or_none()
            if entity:
                return entity

        # Fall back to global (or global-only if no org_id)
        result = await self.session.execute(
            select(Workflow).where(
                Workflow.name == name,
                Workflow.type == "data_provider",
                Workflow.is_active.is_(True),
                Workflow.organization_id.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_all_active(self) -> Sequence[Workflow]:
        """Get all active data providers."""
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.type == "data_provider")
            .where(Workflow.is_active.is_(True))
            .order_by(Workflow.name)
        )
        return result.scalars().all()

    async def count_active(self) -> int:
        """Count all active data providers."""
        result = await self.session.execute(
            select(func.count(Workflow.id))
            .where(Workflow.type == "data_provider")
            .where(Workflow.is_active.is_(True))
        )
        return result.scalar() or 0

    async def search(
        self,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Workflow]:
        """Search data providers with filters."""
        stmt = (
            select(Workflow)
            .where(Workflow.type == "data_provider")
            .where(Workflow.is_active.is_(True))
        )

        if query:
            stmt = stmt.where(
                Workflow.name.ilike(f"%{query}%") |
                Workflow.description.ilike(f"%{query}%")
            )

        stmt = stmt.order_by(Workflow.name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()
