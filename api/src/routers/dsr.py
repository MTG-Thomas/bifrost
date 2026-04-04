"""Desired state resource API routes."""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.config import get_settings
from src.core.auth import Context, CurrentActiveUser
from src.core.database import DbSession
from src.models.contracts.dsr import (
    ApplyResponse,
    PlanApprovalRequest,
    PlanCreateRequest,
    PlanResponse,
    ResourceCreate,
    ResourceResponse,
    ResourceUpdate,
)
from src.models.orm.dsr import DesiredStatePlan, DesiredStateResource, DesiredStateRun
from src.services.dsr_engines import classify_risk, get_engine_adapter

router = APIRouter(prefix="/api", tags=["Desired State Resources"])


def _artifact_path(path: str) -> str:
    settings = get_settings()
    bucket = settings.s3_bucket or "local"
    return f"s3://{bucket}/iac/{path}"


def _resource_fingerprint(resource: DesiredStateResource) -> str:
    content = f"{resource.id}:{resource.kind}:{resource.engine}:{resource.spec}:{resource.updated_at}"
    return hashlib.sha256(content.encode()).hexdigest()


def _resource_response(resource: DesiredStateResource) -> ResourceResponse:
    return ResourceResponse(
        id=resource.id,
        kind=resource.kind,
        engine=resource.engine,
        spec=resource.spec,
        status=resource.status,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )


def _plan_response(plan: DesiredStatePlan) -> PlanResponse:
    return PlanResponse(
        id=plan.id,
        resource_id=plan.resource_id,
        engine=plan.engine,
        status=plan.status,
        plan_path=plan.plan_path,
        plan_json_path=plan.plan_json_path,
        summary=plan.summary,
        summary_json=plan.summary_json,
        risk_level=plan.risk_level,
        requires_approval=plan.requires_approval,
        approved_at=plan.approved_at,
        approved_by=plan.approved_by,
        created_at=plan.created_at,
    )


def _run_response(run: DesiredStateRun) -> ApplyResponse:
    return ApplyResponse(
        id=run.id,
        plan_id=run.plan_id,
        status=run.status,
        logs_path=run.logs_path,
        outputs_path=run.outputs_path,
        result_json=run.result_json,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.post("/resources", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_resource(
    payload: ResourceCreate,
    ctx: Context,
    user: CurrentActiveUser,
    db: DbSession,
) -> ResourceResponse:
    resource = DesiredStateResource(
        kind=payload.kind,
        engine=payload.engine,
        spec=payload.spec,
        organization_id=ctx.org_id,
        created_by=user.email,
        updated_by=user.email,
    )
    db.add(resource)
    await db.commit()
    await db.refresh(resource)
    return _resource_response(resource)


@router.get("/resources/{resource_id}", response_model=ResourceResponse)
async def get_resource(resource_id: UUID, ctx: Context, user: CurrentActiveUser, db: DbSession) -> ResourceResponse:
    query = select(DesiredStateResource).where(DesiredStateResource.id == resource_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    return _resource_response(resource)


@router.patch("/resources/{resource_id}", response_model=ResourceResponse)
async def update_resource(
    resource_id: UUID,
    payload: ResourceUpdate,
    ctx: Context,
    user: CurrentActiveUser,
    db: DbSession,
) -> ResourceResponse:
    query = select(DesiredStateResource).where(DesiredStateResource.id == resource_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(resource, key, value)
    resource.status = "pending"
    resource.updated_by = user.email

    await db.commit()
    await db.refresh(resource)
    return _resource_response(resource)


@router.delete("/resources/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resource(resource_id: UUID, ctx: Context, user: CurrentActiveUser, db: DbSession) -> None:
    query = select(DesiredStateResource).where(DesiredStateResource.id == resource_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    await db.delete(resource)
    await db.commit()


@router.post("/resources/{resource_id}/plan", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    resource_id: UUID,
    payload: PlanCreateRequest,
    ctx: Context,
    user: CurrentActiveUser,
    db: DbSession,
) -> PlanResponse:
    query = select(DesiredStateResource).where(DesiredStateResource.id == resource_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    adapter = get_engine_adapter(resource.engine)
    plan_result = await adapter.plan(resource.kind, resource.spec)
    risk_level, requires_approval = classify_risk(plan_result.summary_json)
    if risk_level == "low" and payload.auto_approve_low_risk:
        status_value = "approved"
        approved_at = resource.updated_at
        approved_by = user.email
    else:
        status_value = "pending"
        approved_at = None
        approved_by = None

    plan = DesiredStatePlan(
        resource_id=resource.id,
        engine=resource.engine,
        status=status_value,
        plan_path=_artifact_path(f"plans/{resource.id}/plan.bin"),
        plan_json_path=_artifact_path(f"plans/{resource.id}/plan.json"),
        plan_fingerprint=_resource_fingerprint(resource),
        summary=plan_result.summary,
        summary_json=plan_result.summary_json,
        risk_level=risk_level,
        requires_approval=requires_approval,
        approved_at=approved_at,
        approved_by=approved_by,
        created_by=user.email,
    )
    resource.status = "planned"
    resource.updated_by = user.email

    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return _plan_response(plan)


@router.get("/plans/{plan_id}", response_model=PlanResponse)
async def get_plan(plan_id: UUID, ctx: Context, user: CurrentActiveUser, db: DbSession) -> PlanResponse:
    query = select(DesiredStatePlan).join(DesiredStateResource).where(DesiredStatePlan.id == plan_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _plan_response(plan)


@router.get("/plans/{plan_id}/json")
async def get_plan_json(plan_id: UUID, ctx: Context, user: CurrentActiveUser, db: DbSession) -> dict:
    query = select(DesiredStatePlan).join(DesiredStateResource).where(DesiredStatePlan.id == plan_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan.summary_json


@router.post("/plans/{plan_id}/approve", response_model=PlanResponse)
async def approve_plan(
    plan_id: UUID,
    payload: PlanApprovalRequest,
    ctx: Context,
    user: CurrentActiveUser,
    db: DbSession,
) -> PlanResponse:
    query = select(DesiredStatePlan).join(DesiredStateResource).where(DesiredStatePlan.id == plan_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if not payload.approved:
        raise HTTPException(status_code=400, detail="Plan approval was not granted")

    plan.status = "approved"
    plan.approved_by = user.email
    plan.approved_at = plan.approved_at or plan.created_at
    await db.commit()
    await db.refresh(plan)
    return _plan_response(plan)


@router.post("/plans/{plan_id}/apply", response_model=ApplyResponse, status_code=status.HTTP_201_CREATED)
async def apply_plan(plan_id: UUID, ctx: Context, user: CurrentActiveUser, db: DbSession) -> ApplyResponse:
    query = select(DesiredStatePlan).join(DesiredStateResource).where(DesiredStatePlan.id == plan_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if plan.requires_approval and plan.status != "approved":
        raise HTTPException(status_code=400, detail="Plan must be approved before apply")

    resource = await db.get(DesiredStateResource, plan.resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    if _resource_fingerprint(resource) != plan.plan_fingerprint:
        raise HTTPException(status_code=409, detail="Resource changed since plan creation")

    run = DesiredStateRun(
        plan_id=plan.id,
        status="running",
        logs_path=_artifact_path(f"runs/{plan.id}/logs.txt"),
        outputs_path=_artifact_path(f"runs/{plan.id}/outputs.json"),
        result_json={},
        created_by=user.email,
    )
    db.add(run)
    await db.flush()

    try:
        adapter = get_engine_adapter(plan.engine)
        apply_result = await adapter.apply(resource.kind, resource.spec, plan.summary_json)
        run.status = "completed"
        run.result_json = apply_result.result_json
        resource.status = "applied"
        plan.status = "applied"
    except Exception as exc:
        run.status = "failed"
        run.result_json = {"error": str(exc)}
        resource.status = "failed"
        plan.status = "failed"

    resource.updated_by = user.email
    await db.commit()
    await db.refresh(run)
    return _run_response(run)


@router.get("/runs/{run_id}", response_model=ApplyResponse)
async def get_run(run_id: UUID, ctx: Context, user: CurrentActiveUser, db: DbSession) -> ApplyResponse:
    query = select(DesiredStateRun).join(DesiredStatePlan).join(DesiredStateResource).where(DesiredStateRun.id == run_id)
    if not user.is_superuser:
        query = query.where(DesiredStateResource.organization_id == ctx.org_id)
    result = await db.execute(query)
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_response(run)
