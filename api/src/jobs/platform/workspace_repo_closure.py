"""Durable single-writer activation and Git closure for ``_repo`` changesets."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)


class WorkspaceRepoClosurePayload(BaseModel):
    changeset_id: UUID
    organization_id: UUID
    operation: Literal["activate", "retry"]
    commit_message: str | None = Field(default=None, max_length=500)
    push: bool = False
    updated_by: str
    plan_id: str | None = None
    protected_main_source_sha: str | None = None


async def run_workspace_repo_closure(
    context: PlatformJobContext,
    payload: WorkspaceRepoClosurePayload,
) -> dict:
    from src.core.database import get_db_context
    from src.core.workspace_writer import workspace_writer_identity
    from src.models.contracts.workspace_repo_changesets import (
        WorkspaceRepoActivateRequest,
    )
    from src.services.workspace_repo_changesets import (
        ChangesetConflict,
        ChangesetInvalid,
        build_workspace_repo_changeset_service,
    )

    await context.report("Acquiring authoritative workspace writer", percent=2)
    try:
        with workspace_writer_identity(
            context.job_id,
            context.lease_token,
            label=f"changeset:{payload.changeset_id}",
        ):
            async with get_db_context() as db:
                service = await build_workspace_repo_changeset_service(
                    db, payload.organization_id
                )
                request = WorkspaceRepoActivateRequest(
                    commit_message=payload.commit_message,
                    push=payload.push,
                    plan_id=payload.plan_id,
                    protected_main_source_sha=payload.protected_main_source_sha,
                )
                await context.report(
                    "Activating authoritative workspace"
                    if payload.operation == "activate"
                    else "Retrying Git closure",
                    percent=10,
                )
                if payload.operation == "activate":
                    result = await service.activate(
                        payload.changeset_id,
                        request,
                        payload.updated_by,
                        writer_job_id=context.job_id,
                    )
                else:
                    result = await service.retry_git_closure(
                        payload.changeset_id,
                        request,
                        payload.updated_by,
                        writer_job_id=context.job_id,
                    )
    except (ChangesetConflict, ChangesetInvalid) as exc:
        raise PlatformJobFailure(
            "workspace_changeset_conflict",
            str(exc),
            retryable=False,
        ) from exc
    except Exception as exc:
        raise PlatformJobFailure(
            "workspace_repo_closure_failed",
            str(exc),
            retryable=False,
        ) from exc

    if result.failure_detail and result.failure_detail.get("state") == "failed":
        raise PlatformJobFailure(
            "workspace_git_closure_failed",
            result.error or "Workspace Git closure failed",
            retryable=True,
        )
    await context.report("Workspace closure complete", percent=100)
    return {"changeset": result.model_dump(mode="json")}


WORKSPACE_REPO_CLOSURE_DEFINITION = PlatformJobDefinition(
    job_type="workspace.repo-closure",
    payload_version=1,
    payload_model=WorkspaceRepoClosurePayload,
    handler=run_workspace_repo_closure,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=1,
        max_concurrency=1,
        retry_on_runner_loss=False,
        min_memory_headroom_mb=512,
    ),
)
