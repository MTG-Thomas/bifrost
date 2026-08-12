"""Durable deletion of recursive paths beneath the authoritative ``_repo`` root."""

from __future__ import annotations

import asyncio
import logging
from pathlib import PurePosixPath
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.workspace_writer import (
    WORKSPACE_WRITER_RESOURCE_LOCK,
    checkpoint_workspace_writer_lease,
    lock_workspace_writer_gate,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.file_storage import FileStorageService
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)


def normalize_workspace_delete_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized or ".." in PurePosixPath(normalized).parts:
        raise ValueError("path must be workspace-relative and cannot contain '..'")
    return normalized


async def delete_workspace_path_recursively(
    db: AsyncSession,
    path: str,
    *,
    repo: RepoStorage | None = None,
    report_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> int:
    """Drain one prefix while repeatedly checkpointing the durable writer lease."""
    normalized = normalize_workspace_delete_path(path)
    prefix = f"{normalized}/"
    repo_storage = repo or RepoStorage()
    file_storage = FileStorageService(db)
    deleted = 0

    for attempt in range(5):
        await checkpoint_workspace_writer_lease(db)
        children = sorted(set(await repo_storage.list(prefix)))
        remaining = len(children)
        for child_path in children:
            await checkpoint_workspace_writer_lease(db)
            if await _delete_workspace_child(
                db, repo_storage, file_storage, child_path
            ):
                deleted += 1
            remaining -= 1
            if report_progress is not None:
                await report_progress(deleted, deleted + remaining)

        for marker in (normalized, prefix):
            await checkpoint_workspace_writer_lease(db)
            try:
                await repo_storage.delete(marker)
            except FileNotFoundError:
                # Retried drains may have removed folder markers already.
                pass

        await checkpoint_workspace_writer_lease(db)
        if not await repo_storage.list(prefix):
            return deleted
        if attempt < 4:
            await asyncio.sleep(0.1)

    raise RuntimeError(f"workspace path deletion did not drain prefix {prefix!r}")


async def _delete_workspace_child(
    db: AsyncSession,
    repo_storage: RepoStorage,
    file_storage: FileStorageService,
    child_path: str,
) -> bool:
    """Delete one child, returning whether this attempt performed the deletion."""
    try:
        if child_path.endswith("/"):
            await repo_storage.delete(child_path)
        else:
            await file_storage.delete_file(child_path, skip_dirty_flag=True)
            await db.commit()
    except FileNotFoundError:
        # A retried durable drain treats an already-deleted child as converged.
        return False
    return True


async def enqueue_workspace_path_deletion(
    db: AsyncSession,
    path: str,
    *,
    organization_id: UUID | None,
    requested_by_user_id: UUID,
    requested_by_email: str,
    requested_by_name: str,
) -> tuple[PlatformJob, bool]:
    """Enqueue one recursive deletion behind the global workspace writer."""
    from src.jobs.platform.workspace_path_deletion import (
        WORKSPACE_PATH_DELETION_DEFINITION,
        WorkspacePathDeletionPayload,
    )
    from src.services.platform_jobs import (
        enqueue_platform_job,
        ensure_platform_job_notification,
        publish_platform_job_update,
    )

    normalized = normalize_workspace_delete_path(path)
    await lock_workspace_writer_gate(db)
    job, reused = await enqueue_platform_job(
        db,
        WORKSPACE_PATH_DELETION_DEFINITION,
        WorkspacePathDeletionPayload(path=normalized),
        dedupe_key=normalized,
        resource_lock_key=WORKSPACE_WRITER_RESOURCE_LOCK,
        priority=900,
        organization_id=organization_id,
        requested_by_user_id=requested_by_user_id,
        requested_by_email=requested_by_email,
        requested_by_name=requested_by_name,
        resource_type="workspace_path",
        resource_id=normalized,
        title=f"Deleting workspace path {normalized}",
        action_url="/diagnostics",
    )
    if job.notification_id is None:
        try:
            async with db.begin_nested():
                await ensure_platform_job_notification(db, job)
        except Exception:
            logger.warning(
                "Workspace deletion queued without a progress notification",
                extra={"platform_job_id": str(job.id)},
                exc_info=True,
            )
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    return job, reused
