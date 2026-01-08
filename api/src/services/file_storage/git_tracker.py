"""
Git status tracking utilities for workspace files.

Provides methods to update git status metadata for workspace files
in the database.
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import GitStatus
from src.models import WorkspaceFile


class GitStatusTracker:
    """Track git status for workspace files."""

    def __init__(self, db: AsyncSession):
        """
        Initialize git status tracker.

        Args:
            db: Database session
        """
        self.db = db

    async def update_git_status(
        self,
        path: str,
        status: GitStatus,
        commit_hash: str | None = None,
    ) -> None:
        """
        Update git status for a file.

        Args:
            path: File path
            status: New git status
            commit_hash: Git commit hash (for synced files)
        """
        values = {
            "git_status": status,
            "updated_at": datetime.utcnow(),
        }
        if commit_hash:
            values["last_git_commit_hash"] = commit_hash

        stmt = update(WorkspaceFile).where(
            WorkspaceFile.path == path,
        ).values(**values)

        await self.db.execute(stmt)

    async def bulk_update_git_status(
        self,
        status: GitStatus,
        commit_hash: str | None = None,
        paths: list[str] | None = None,
    ) -> int:
        """
        Bulk update git status for files.

        Args:
            status: New git status
            commit_hash: Git commit hash
            paths: List of paths to update (all if None)

        Returns:
            Number of files updated
        """
        values = {
            "git_status": status,
            "updated_at": datetime.utcnow(),
        }
        if commit_hash:
            values["last_git_commit_hash"] = commit_hash

        stmt = update(WorkspaceFile).values(**values)

        if paths:
            stmt = stmt.where(WorkspaceFile.path.in_(paths))

        cursor = await self.db.execute(stmt)

        # rowcount may be -1 for some database drivers
        row_count = getattr(cursor, "rowcount", 0)
        return row_count if row_count >= 0 else 0
