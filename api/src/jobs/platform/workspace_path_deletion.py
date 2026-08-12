"""Durable recursive deletion beneath the authoritative workspace root."""

from pydantic import BaseModel, Field

from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)


class WorkspacePathDeletionPayload(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


async def run_workspace_path_deletion(
    context: PlatformJobContext,
    payload: WorkspacePathDeletionPayload,
) -> dict:
    from src.core.database import get_db_context
    from src.core.repo_dirty import mark_repo_dirty
    from src.core.workspace_writer import (
        checkpoint_workspace_writer_lease,
        workspace_writer_identity,
    )
    from src.services.workspace_path_deletion import delete_workspace_path_recursively

    await context.report("Acquiring authoritative workspace writer", percent=2)
    try:
        with workspace_writer_identity(
            context.job_id,
            context.lease_token,
            label=f"delete:{payload.path}",
        ):
            async with get_db_context() as db:
                await checkpoint_workspace_writer_lease(db)
                await mark_repo_dirty(writer=f"delete:{payload.path}")

                async def report(current: int, total: int) -> None:
                    percent = 10 + (80 * current / total) if total else 90
                    await context.report(
                        "Deleting authoritative workspace path",
                        current=current,
                        total=total,
                        percent=percent,
                    )

                deleted = await delete_workspace_path_recursively(
                    db,
                    payload.path,
                    report_progress=report,
                )
    except PlatformJobCancelled:
        raise
    except Exception as exc:
        raise PlatformJobFailure(
            "workspace_path_deletion_failed",
            str(exc),
            retryable=True,
        ) from exc

    await context.report("Workspace path deletion complete", percent=100)
    return {"path": payload.path, "deleted_entries": deleted}


WORKSPACE_PATH_DELETION_DEFINITION = PlatformJobDefinition(
    job_type="workspace.delete-path",
    payload_version=1,
    payload_model=WorkspacePathDeletionPayload,
    handler=run_workspace_path_deletion,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=3,
        max_concurrency=1,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=256,
    ),
)
