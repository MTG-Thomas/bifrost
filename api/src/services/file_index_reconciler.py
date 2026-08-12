"""
File Index Reconciler — heals drift between S3 _repo/ and file_index DB.

Runs on API startup and can be triggered manually.
Lists all files in S3 _repo/, compares against file_index,
adds missing entries, removes orphaned entries, updates stale content.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repo_dirty import mark_repo_dirty
from src.core.workspace_writer import (
    WorkspaceWriterBusy,
    WorkspaceWriterLeaseLost,
    assert_workspace_writer_access,
    current_workspace_writer_label,
)
from src.models.orm.file_index import FileIndex
from src.services.file_index_service import _is_text_file
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)


async def reconcile_file_index(
    db: AsyncSession,
    repo_storage: RepoStorage | None = None,
) -> dict[str, int]:
    """
    Reconcile file_index with S3 _repo/ contents.

    Returns stats dict with counts of added, removed, updated entries.
    """
    repo = repo_storage or RepoStorage()
    stats = {"added": 0, "removed": 0, "updated": 0, "unchanged": 0, "reverse_synced": 0}

    # Get all files from S3
    s3_paths = set(await repo.list())
    # Filter to text files only
    s3_text_paths = {p for p in s3_paths if _is_text_file(p)}

    # Get all paths from file_index
    result = await db.execute(select(FileIndex.path))
    db_paths = {row[0] for row in result.all()}

    # Files in S3 but not in DB -> add
    to_add = s3_text_paths - db_paths
    for path in to_add:
        try:
            content = await repo.read(path)
            content_str = content.decode("utf-8")
            content_hash = hashlib.sha256(content).hexdigest()

            stmt = insert(FileIndex).values(
                path=path,
                content=content_str,
                content_hash=content_hash,
            ).on_conflict_do_nothing()
            await db.execute(stmt)
            stats["added"] += 1
        except Exception as e:
            logger.warning(f"Failed to index {path}: {e}")

    # Close the index-maintenance transaction before taking the authoritative
    # writer gate. Reverse-syncs below are individually serialized so a scan
    # never owns the workspace while it is only reading object storage.
    await db.commit()

    # Files in DB but not in S3 -> reverse-sync (write DB content to S3)
    # This handles the case where the pre-migration backfill populated
    # file_index but S3 was unavailable at the time.
    to_reverse_sync = db_paths - s3_paths
    for path in to_reverse_sync:
        try:
            # Direct writes and durable closure activation take the same
            # advisory gate. Recheck existence while holding it so a stale
            # scan cannot overwrite a file another writer just created.
            await assert_workspace_writer_access(db)
            if await repo.exists(path):
                await db.commit()
                continue

            fi_result = await db.execute(
                select(FileIndex.content).where(FileIndex.path == path)
            )
            content_str = fi_result.scalar_one_or_none()
            if content_str is not None:
                await mark_repo_dirty(
                    writer=current_workspace_writer_label(
                        "file-index-reconciliation"
                    )
                    or "file-index-reconciliation"
                )
                await repo.write(path, content_str.encode("utf-8"))
                stats["reverse_synced"] += 1
            else:
                # No content in DB either — orphaned row, remove it
                await db.execute(
                    delete(FileIndex).where(FileIndex.path == path)
                )
                stats["removed"] += 1
            await db.commit()
        except Exception as e:
            await db.rollback()
            if isinstance(e, (WorkspaceWriterBusy, WorkspaceWriterLeaseLost)):
                raise
            logger.warning(f"Failed to reverse-sync {path}: {e}")

    logger.info(
        f"Reconciliation complete: {stats['added']} added, "
        f"{stats['removed']} removed, {stats['updated']} updated, "
        f"{stats['reverse_synced']} reverse-synced"
    )

    return stats
