"""Admin API for immutable Solution deployment registration and pointer movement."""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.solution_deployments import (
    DeploymentActivationPublic,
    DeploymentPointerRequest,
    SolutionDeploymentCreate,
    SolutionDeploymentCapabilities,
    SolutionDeploymentPublic,
)
from src.models.orm.solutions import Solution
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_activation import (
    ActivationResult,
    SolutionDeploymentActivationService,
)
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.deployment_api import DeploymentRegistrationConflict
from src.services.solutions.write_lock import (
    SolutionWriteLockHeld,
    SolutionWriteLockLost,
    solution_write_lock,
)

router = APIRouter(
    prefix="/api/solutions/{solution_id}/deployments", tags=["Solution Deployments"]
)


@router.get(
    "/capabilities",
    response_model=SolutionDeploymentCapabilities,
    responses={404: {"description": "Solution not found"}},
)
async def deployment_capabilities(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
):
    del user
    await _scope(ctx, solution_id)
    return SolutionDeploymentCapabilities()


async def _scope(ctx: Context, solution_id: UUID) -> UUID | None:
    solution = await ctx.db.get(Solution, solution_id)
    if solution is None:
        raise HTTPException(status_code=404, detail="Solution not found")
    return solution.organization_id


def get_activation_service(ctx: Context) -> SolutionDeploymentActivationService:
    """Dependency seam overridden by the projection/artifact adapter and tests."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Solution deployment activation hooks are not configured",
    )


async def _run_pointer_move(
    ctx: Context,
    solution_id: UUID,
    operation: Callable[[UUID | None], Awaitable[ActivationResult]],
) -> ActivationResult:
    """Serialize one pointer mutation and normalize its transactional errors."""
    organization_id = await _scope(ctx, solution_id)
    try:
        async with solution_write_lock(solution_id):
            result = await operation(organization_id)
    except SolutionWriteLockHeld as exc:
        raise HTTPException(
            status_code=409, detail={"code": "solution_write_lock_held"}
        ) from exc
    except SolutionWriteLockLost as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"code": "solution_write_lock_lost", "retryable": True},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await ctx.db.commit()
    if result.state == "conflicted":
        raise HTTPException(status_code=409, detail=result.conflict)
    return result


@router.post(
    "",
    response_model=SolutionDeploymentPublic,
    status_code=201,
    responses={
        404: {"description": "Solution not found"},
        409: {"description": "Deployment registration conflict"},
        422: {"description": "Invalid deployment closure"},
    },
)
async def create_deployment(
    solution_id: UUID,
    body: SolutionDeploymentCreate,
    ctx: Context,
    user: CurrentSuperuser,
):
    try:
        row = await SolutionDeploymentAPIService(ctx.db).create_ready_draft(
            solution_id, user.user_id, body
        )
        await ctx.db.commit()
        return row
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DeploymentRegistrationConflict as exc:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "deployment_registration_conflict",
                "message": str(exc),
                "reconcile": f"GET /api/solutions/{solution_id}/deployments/{body.compiled_manifest.deployment_id}",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/{deployment_id}",
    response_model=SolutionDeploymentPublic,
    responses={404: {"description": "Solution or deployment not found"}},
)
async def inspect_deployment(
    solution_id: UUID, deployment_id: UUID, ctx: Context, user: CurrentSuperuser
):
    del user
    organization_id = await _scope(ctx, solution_id)
    row = await SolutionDeploymentRepository(ctx.db).get_runtime_closure(
        deployment_id, organization_id
    )
    if row is None or row.solution_id != solution_id:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return row


@router.post(
    "/{deployment_id}/activate",
    response_model=DeploymentActivationPublic,
    responses={
        404: {"description": "Solution or deployment not found"},
        409: {"description": "Activation conflict or write lock held"},
        422: {"description": "Invalid activation request"},
        503: {"description": "Activation unavailable or write lock lost"},
    },
)
async def activate_deployment(
    solution_id: UUID,
    deployment_id: UUID,
    body: DeploymentPointerRequest,
    ctx: Context,
    user: CurrentSuperuser,
    service: Annotated[
        SolutionDeploymentActivationService, Depends(get_activation_service)
    ],
):
    del user
    return await _run_pointer_move(
        ctx,
        solution_id,
        partial(
            service.activate,
            deployment_id,
            solution_id=solution_id,
            expected_active_deployment_id=body.expected_active_deployment_id,
        ),
    )


@router.post(
    "/{deployment_id}/rollback",
    response_model=DeploymentActivationPublic,
    responses={
        404: {"description": "Solution or deployment not found"},
        409: {"description": "Rollback conflict or write lock held"},
        422: {"description": "Invalid rollback request"},
        503: {"description": "Rollback unavailable or write lock lost"},
    },
)
async def rollback_deployment(
    solution_id: UUID,
    deployment_id: UUID,
    body: DeploymentPointerRequest,
    ctx: Context,
    user: CurrentSuperuser,
    service: Annotated[
        SolutionDeploymentActivationService, Depends(get_activation_service)
    ],
):
    del user
    if body.expected_active_deployment_id is None:
        raise HTTPException(
            status_code=422, detail="rollback requires expected active deployment"
        )
    return await _run_pointer_move(
        ctx,
        solution_id,
        partial(
            service.rollback,
            deployment_id,
            solution_id=solution_id,
            expected_active_deployment_id=body.expected_active_deployment_id,
        ),
    )
