"""
File Index Reconciler — rebuilds the searchable index from durable _repo/ bytes.

Runs on API startup and can be triggered manually.
Object storage is the sole source of truth. This job adds missing index entries,
updates stale indexed content, and removes index rows whose durable object no
longer exists. It must never write index content back to object storage.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

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
    stats = {"added": 0, "removed": 0, "updated": 0, "unchanged": 0}

    # Get all files from S3
    s3_paths = set(await repo.list())
    # Filter to text files only
    s3_text_paths = {p for p in s3_paths if _is_text_file(p)}

    # Get all paths from file_index
    result = await db.execute(select(FileIndex.path, FileIndex.content_hash))
    indexed_hashes = {row[0]: row[1] for row in result.all()}
    db_paths = set(indexed_hashes)

    # Rebuild missing or stale searchable content from durable storage.
    for path in sorted(s3_text_paths):
        try:
            content = await repo.read(path)
            content_str = content.decode("utf-8")
            content_hash = hashlib.sha256(content).hexdigest()
            existing_hash = indexed_hashes.get(path)
            if existing_hash == content_hash:
                stats["unchanged"] += 1
                continue

            stmt = insert(FileIndex).values(
                path=path,
                content=content_str,
                content_hash=content_hash,
            ).on_conflict_do_update(
                index_elements=[FileIndex.path],
                set_={
                    "content": content_str,
                    "content_hash": content_hash,
                },
            )
            await db.execute(stmt)
            stats["added" if existing_hash is None else "updated"] += 1
        except Exception as e:
            logger.warning(f"Failed to index {path}: {e}")

    # Index-only rows are stale metadata, never recoverable source code.
    for path in sorted(db_paths - s3_paths):
        try:
            await db.execute(delete(FileIndex).where(FileIndex.path == path))
            stats["removed"] += 1
        except Exception as e:
            logger.warning(f"Failed to remove stale index row {path}: {e}")

    await db.commit()

    logger.info(
        f"Reconciliation complete: {stats['added']} added, "
        f"{stats['removed']} removed, {stats['updated']} updated, "
        f"{stats['unchanged']} unchanged"
    )

    return stats
