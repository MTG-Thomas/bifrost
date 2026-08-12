"""Fenced ownership for authoritative ``_repo`` mutations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.platform_jobs import PlatformJob

WORKSPACE_WRITER_RESOURCE_LOCK = "workspace:authoritative-writer"
_ACTIVE_WRITER_STATUSES = ("queued", "running", "waiting", "cancel_requested")


class WorkspaceWriterBusy(RuntimeError):
    def __init__(self, job_id: UUID | None, phase: str | None = None) -> None:
        self.job_id = job_id
        self.phase = phase
        detail = f" by platform job {job_id}" if job_id else ""
        if phase:
            detail += f" ({phase})"
        super().__init__(f"the authoritative workspace is owned{detail}")


class WorkspaceWriterLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspaceWriterIdentity:
    job_id: UUID
    lease_token: UUID
    label: str


_identity: ContextVar[WorkspaceWriterIdentity | None] = ContextVar(
    "workspace_writer_identity", default=None
)


@contextmanager
def workspace_writer_identity(
    job_id: UUID, lease_token: UUID, *, label: str
) -> Iterator[None]:
    token = _identity.set(WorkspaceWriterIdentity(job_id, lease_token, label))
    try:
        yield
    finally:
        _identity.reset(token)


def current_workspace_writer_label(default: str | None = None) -> str | None:
    identity = _identity.get()
    return identity.label if identity else default


async def lock_workspace_writer_gate(db: AsyncSession) -> None:
    """Serialize short direct writes with durable writer activation/queueing."""
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtext('bifrost:workspace-authoritative-writer'))"
        )
    )


async def assert_workspace_writer_access(db: AsyncSession) -> None:
    """Reject mutations outside the current durable workspace-writer job.

    Direct request-scoped writes retain the transaction advisory lock through
    their short object-store mutation.  A platform writer takes the same gate
    while it is enqueued, then its durable job row is the observable lease.
    """
    await lock_workspace_writer_gate(db)
    current = (
        await db.execute(
            select(PlatformJob)
            .where(
                PlatformJob.resource_lock_key == WORKSPACE_WRITER_RESOURCE_LOCK,
                PlatformJob.status.in_(_ACTIVE_WRITER_STATUSES),
            )
            .order_by(PlatformJob.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    identity = _identity.get()
    if current is None:
        if identity is not None:
            raise WorkspaceWriterLeaseLost(
                f"platform job {identity.job_id} no longer owns the workspace writer lease"
            )
        return

    if identity is None or current.id != identity.job_id:
        raise WorkspaceWriterBusy(current.id, current.phase)
    if (
        current.status != "running"
        or current.lease_token != identity.lease_token
        or current.lease_expires_at is None
        or current.lease_expires_at <= datetime.now(timezone.utc)
    ):
        raise WorkspaceWriterLeaseLost(
            f"platform job {identity.job_id} no longer owns the workspace writer lease"
        )


async def checkpoint_workspace_writer_lease(db: AsyncSession) -> None:
    """Fence a durable writer phase and close the short check transaction.

    Direct request writers retain the advisory lock from
    :func:`assert_workspace_writer_access` through their one mutation. Durable
    jobs own the global resource through their leased job row; phase
    checkpoints verify that fencing token without carrying a DB transaction
    into Git or object-store network work.
    """
    if _identity.get() is None:
        return
    await assert_workspace_writer_access(db)
    await db.commit()
