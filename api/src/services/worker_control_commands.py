"""Atomic persistence and fencing for audited worker controls."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.worker_control_commands import WorkerControlCommand

ALLOWED_ACTIONS = {"recycle_process", "recycle_all"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_worker_control_command(
    db: AsyncSession,
    *,
    worker_id: str,
    action: str,
    requested_by_user_id: UUID,
    reason: str,
    process_id: int | None = None,
) -> WorkerControlCommand:
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported worker control action: {action}")
    command = WorkerControlCommand(
        worker_id=worker_id[:255],
        action=action,
        process_id=process_id,
        requested_by_user_id=requested_by_user_id,
        reason=(reason.strip() or "operator request")[:2000],
    )
    db.add(command)
    await db.flush()
    return command


async def claim_worker_control_command(
    db: AsyncSession,
    *,
    command_id: UUID,
    worker_id: str,
) -> WorkerControlCommand | None:
    command = (
        await db.execute(
            select(WorkerControlCommand)
            .where(
                WorkerControlCommand.id == command_id,
                WorkerControlCommand.worker_id == worker_id,
                or_(
                    WorkerControlCommand.status == "pending",
                    (
                        (WorkerControlCommand.status == "running")
                        & (
                            WorkerControlCommand.claimed_at
                            < _now() - timedelta(minutes=15)
                        )
                    ),
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is None:
        return None
    command.status = "running"
    command.claimed_at = _now()
    await db.flush()
    return command


async def finish_worker_control_command(
    db: AsyncSession,
    *,
    command_id: UUID,
    worker_id: str,
    succeeded: bool,
    failure_message: str | None = None,
) -> WorkerControlCommand | None:
    command = (
        await db.execute(
            select(WorkerControlCommand)
            .where(
                WorkerControlCommand.id == command_id,
                WorkerControlCommand.worker_id == worker_id,
                WorkerControlCommand.status == "running",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is None:
        return None
    command.status = "succeeded" if succeeded else "failed"
    command.failure_message = (
        failure_message.strip()[:4000] if failure_message else None
    )
    command.completed_at = _now()
    await db.flush()
    return command


async def get_pending_worker_control_command(
    db: AsyncSession,
    *,
    worker_id: str,
) -> WorkerControlCommand | None:
    """Find the oldest desired command for polling after a lost pub/sub hint."""

    return (
        await db.execute(
            select(WorkerControlCommand)
            .where(
                WorkerControlCommand.worker_id == worker_id,
                or_(
                    WorkerControlCommand.status == "pending",
                    (
                        (WorkerControlCommand.status == "running")
                        & (
                            WorkerControlCommand.claimed_at
                            < _now() - timedelta(minutes=15)
                        )
                    ),
                ),
            )
            .order_by(WorkerControlCommand.requested_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
