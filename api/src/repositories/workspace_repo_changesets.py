"""Persistence boundary for workspace _repo changesets."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.workspace_repo_changesets import WorkspaceRepoChangeset


class WorkspaceRepoChangesetRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, row: WorkspaceRepoChangeset) -> WorkspaceRepoChangeset:
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(
        self, changeset_id: UUID, organization_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRepoChangeset | None:
        stmt = select(WorkspaceRepoChangeset).where(
            WorkspaceRepoChangeset.id == changeset_id,
            WorkspaceRepoChangeset.organization_id == organization_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_open(self, scope: str, organization_id: UUID) -> int:
        statuses = ("open", "staged", "validated", "activating")
        stmt = (
            select(func.count())
            .select_from(WorkspaceRepoChangeset)
            .where(
                WorkspaceRepoChangeset.scope == scope,
                WorkspaceRepoChangeset.organization_id == organization_id,
                WorkspaceRepoChangeset.status.in_(statuses),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())
