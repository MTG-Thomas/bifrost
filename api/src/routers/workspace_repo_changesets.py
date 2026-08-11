"""Thin HTTP surface for transactional workspace _repo changesets."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.contracts.workspace_repo_changesets import (
    WorkspaceRepoActivateRequest,
    WorkspaceRepoChangesetBegin,
    WorkspaceRepoChangesetDiffResponse,
    WorkspaceRepoChangesetResponse,
    WorkspaceRepoFileMutationRequest,
    WorkspaceRepoStateResponse,
    WorkspaceRepoValidationResponse,
)
from src.services.workspace_repo_changesets import (
    ChangesetConflict,
    ChangesetInvalid,
    OrganizationScopeRequired,
    WorkspaceRepoChangesetService,
    require_organization_id,
)

router = APIRouter(
    prefix="/api/workspace-repo-changesets",
    tags=["Workspace _repo compatibility changesets"],
)


async def _service(db: DbSession, org_id: UUID | None) -> WorkspaceRepoChangesetService:
    org_id = require_organization_id(org_id)
    from src.config import get_settings
    from src.services.github_config import get_github_config
    from src.services.platform_commit_writer import GitHubAppCommitWriter

    config = await get_github_config(db, org_id)
    settings = get_settings()
    writer = None
    if config and config.repo_url and settings.github_app_commit_writer_configured:
        private_key = settings.github_app_private_key
        assert private_key is not None
        assert settings.github_app_id is not None
        assert settings.github_app_installation_id is not None
        writer = GitHubAppCommitWriter(
            repo_url=config.repo_url,
            branch=config.branch,
            app_id=settings.github_app_id,
            installation_id=settings.github_app_installation_id,
            private_key=private_key.get_secret_value(),
        )
    return WorkspaceRepoChangesetService(db, org_id, commit_writer=writer)


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, ChangesetConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail)
    if isinstance(exc, ChangesetInvalid):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    if isinstance(exc, OrganizationScopeRequired):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workspace _repo changeset not found",
        )
    raise exc


@router.get("/state", response_model=WorkspaceRepoStateResponse)
async def workspace_repo_state(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    scope: str = Query(..., min_length=1),
):
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


@router.post(
    "",
    response_model=WorkspaceRepoChangesetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def begin_workspace_repo_changeset(
    request: WorkspaceRepoChangesetBegin,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    try:
        return await (await _service(db, ctx.org_id)).begin(request, user.user_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/recoverable-git-closures",
    response_model=list[WorkspaceRepoChangesetResponse],
)
async def list_recoverable_workspace_repo_git_closures(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    scope: str | None = None,
):
    try:
        return await (await _service(db, ctx.org_id)).recoverable_git_closures(
            scope=scope
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/{changeset_id}", response_model=WorkspaceRepoChangesetResponse)
async def show_workspace_repo_changeset(
    changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser
):
    try:
        return await (await _service(db, ctx.org_id)).get(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/files", response_model=WorkspaceRepoChangesetResponse)
async def stage_workspace_repo_file(
    changeset_id: UUID,
    request: WorkspaceRepoFileMutationRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    try:
        return await (await _service(db, ctx.org_id)).stage(changeset_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/{changeset_id}/diff", response_model=WorkspaceRepoChangesetDiffResponse)
async def workspace_repo_changeset_diff(
    changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser
):
    try:
        return await (await _service(db, ctx.org_id)).diff(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/validate", response_model=WorkspaceRepoValidationResponse)
async def validate_workspace_repo_changeset(
    changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser
):
    try:
        return await (await _service(db, ctx.org_id)).validate(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/activate", response_model=WorkspaceRepoChangesetResponse)
async def activate_workspace_repo_changeset(
    changeset_id: UUID,
    request: WorkspaceRepoActivateRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    try:
        return await (await _service(db, ctx.org_id)).activate(
            changeset_id, request, user.email
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/{changeset_id}/retry-git-closure",
    response_model=WorkspaceRepoChangesetResponse,
)
async def retry_workspace_repo_git_closure(
    changeset_id: UUID,
    request: WorkspaceRepoActivateRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    try:
        return await (await _service(db, ctx.org_id)).retry_git_closure(
            changeset_id, request, user.email
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/abort", response_model=WorkspaceRepoChangesetResponse)
async def abort_workspace_repo_changeset(
    changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser
):
    try:
        return await (await _service(db, ctx.org_id)).abort(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc
