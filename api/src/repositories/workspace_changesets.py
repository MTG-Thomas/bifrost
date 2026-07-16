"""Persistence boundary for workspace changesets."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.workspace_changesets import WorkspaceChangeset


class WorkspaceChangesetRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, row: WorkspaceChangeset) -> WorkspaceChangeset:
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, changeset_id: UUID, organization_id: UUID, *, for_update: bool = False) -> WorkspaceChangeset | None:
        stmt = select(WorkspaceChangeset).where(
            WorkspaceChangeset.id == changeset_id,
            WorkspaceChangeset.organization_id == organization_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_open(self, scope: str, organization_id: UUID) -> int:
        statuses = ("open", "staged", "validated", "activating")
        stmt = select(func.count()).select_from(WorkspaceChangeset).where(
            WorkspaceChangeset.scope == scope,
            WorkspaceChangeset.organization_id == organization_id,
            WorkspaceChangeset.status.in_(statuses),
        )
        return int((await self.db.execute(stmt)).scalar_one())
