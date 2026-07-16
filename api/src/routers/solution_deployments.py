"""Admin API for immutable Solution deployment registration and pointer movement."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.solution_deployments import (
    DeploymentActivationPublic,
    DeploymentPointerRequest,
    SolutionDeploymentCreate,
    SolutionDeploymentPublic,
)
from src.models.orm.solutions import Solution
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_activation import SolutionDeploymentActivationService
from src.services.solutions.deployment_api import SolutionDeploymentAPIService

router = APIRouter(prefix="/api/solutions/{solution_id}/deployments", tags=["Solution Deployments"])


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


@router.post("", response_model=SolutionDeploymentPublic, status_code=201)
async def create_deployment(
    solution_id: UUID, body: SolutionDeploymentCreate, ctx: Context, user: CurrentSuperuser
):
    try:
        row = await SolutionDeploymentAPIService(ctx.db).create_ready_draft(
            solution_id, user.user_id, body
        )
        await ctx.db.commit()
        return row
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{deployment_id}", response_model=SolutionDeploymentPublic)
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


@router.post("/{deployment_id}/activate", response_model=DeploymentActivationPublic)
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
    organization_id = await _scope(ctx, solution_id)
    try:
        result = await service.activate(
            deployment_id,
            organization_id,
            expected_active_deployment_id=body.expected_active_deployment_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await ctx.db.commit()
    if result.state == "conflicted":
        raise HTTPException(status_code=409, detail=result.conflict)
    return result


@router.post("/{deployment_id}/rollback", response_model=DeploymentActivationPublic)
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
        raise HTTPException(status_code=422, detail="rollback requires expected active deployment")
    organization_id = await _scope(ctx, solution_id)
    try:
        result = await service.rollback(
            deployment_id,
            organization_id,
            expected_active_deployment_id=body.expected_active_deployment_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await ctx.db.commit()
    if result.state == "conflicted":
        raise HTTPException(status_code=409, detail=result.conflict)
    return result
