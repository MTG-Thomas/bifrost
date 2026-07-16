"""Thin HTTP surface for transactional workspace changesets."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.contracts.workspace_changesets import (
    WorkspaceActivateRequest,
    WorkspaceChangesetBegin,
    WorkspaceChangesetDiffResponse,
    WorkspaceChangesetResponse,
    WorkspaceFileMutationRequest,
    WorkspaceStateResponse,
    WorkspaceValidationResponse,
)
from src.services.workspace_changesets import ChangesetConflict, ChangesetInvalid, WorkspaceChangesetService

router = APIRouter(prefix="/api/workspace-changesets", tags=["Workspace changesets"])


async def _service(db: DbSession, org_id) -> WorkspaceChangesetService:
    from src.services.github_config import get_github_config
    from src.services.github_sync import GitHubSyncService

    config = await get_github_config(db, org_id)
    callback = None
    if config and config.repo_url:
        git = GitHubSyncService(db, config.repo_url, config.branch)

        async def commit(message: str, push: bool) -> str | None:
            return await git.commit_workspace_changes(message, push=push)

        callback = commit
    return WorkspaceChangesetService(db, commit_callback=callback)


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, ChangesetConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail)
    if isinstance(exc, ChangesetInvalid):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace changeset not found")
    raise exc


@router.get("/state", response_model=WorkspaceStateResponse)
async def workspace_state(ctx: Context, db: DbSession, user: CurrentSuperuser, scope: str = Query(..., min_length=1)):
    try:
        from src.core.repo_dirty import get_repo_dirty_since
        from src.services.github_config import get_github_config

        config = await get_github_config(db, ctx.org_id)
        dirty_since = await get_repo_dirty_since()
        git_status = {
            "configured": bool(config and config.repo_url),
            "branch": config.branch if config and config.repo_url else None,
            "dirty_since": dirty_since,
        }
        return await (await _service(db, ctx.org_id)).state(
            scope, git_status=git_status, workspace_dirty=dirty_since is not None
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("", response_model=WorkspaceChangesetResponse, status_code=status.HTTP_201_CREATED)
async def begin_changeset(request: WorkspaceChangesetBegin, ctx: Context, db: DbSession, user: CurrentSuperuser):
    try:
        return await (await _service(db, ctx.org_id)).begin(request, user.user_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/{changeset_id}", response_model=WorkspaceChangesetResponse)
async def show_changeset(changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser):
    try:
        return await (await _service(db, ctx.org_id)).get(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/files", response_model=WorkspaceChangesetResponse)
async def stage_file(changeset_id: UUID, request: WorkspaceFileMutationRequest, ctx: Context, db: DbSession, user: CurrentSuperuser):
    try:
        return await (await _service(db, ctx.org_id)).stage(changeset_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/{changeset_id}/diff", response_model=WorkspaceChangesetDiffResponse)
async def changeset_diff(changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser):
    try:
        return await (await _service(db, ctx.org_id)).diff(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/validate", response_model=WorkspaceValidationResponse)
async def validate_changeset(changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser):
    try:
        return await (await _service(db, ctx.org_id)).validate(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/activate", response_model=WorkspaceChangesetResponse)
async def activate_changeset(changeset_id: UUID, request: WorkspaceActivateRequest, ctx: Context, db: DbSession, user: CurrentSuperuser):
    try:
        return await (await _service(db, ctx.org_id)).activate(changeset_id, request, user.email)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/abort", response_model=WorkspaceChangesetResponse)
async def abort_changeset(changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser):
    try:
        return await (await _service(db, ctx.org_id)).abort(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc
