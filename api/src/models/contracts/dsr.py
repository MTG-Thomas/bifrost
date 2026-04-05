"""Contract models for desired state resources."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ResourceCreate(BaseModel):
    kind: str = Field(..., min_length=1, max_length=255)
    engine: str = Field(default="tofu", pattern="^(tofu|terraform|python)$")
    spec: dict[str, Any] = Field(default_factory=dict)


class ResourceUpdate(BaseModel):
    kind: str | None = Field(default=None, min_length=1, max_length=255)
    engine: str | None = Field(default=None, pattern="^(tofu|terraform|python)$")
    spec: dict[str, Any] | None = None


class ResourceResponse(BaseModel):
    id: UUID
    kind: str
    engine: str
    spec: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class PlanCreateRequest(BaseModel):
    auto_approve_low_risk: bool = True


class PlanResponse(BaseModel):
    id: UUID
    resource_id: UUID
    engine: str
    status: str
    plan_path: str
    plan_json_path: str
    summary: str
    summary_json: dict[str, Any]
    risk_level: str
    requires_approval: bool
    approved_at: datetime | None
    approved_by: str | None
    created_at: datetime


class PlanApprovalRequest(BaseModel):
    approved: bool = True


class ApplyResponse(BaseModel):
    id: UUID
    plan_id: UUID
    status: str
    logs_path: str
    outputs_path: str
    result_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
