"""Agent run contract models."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.contracts.executions import AIUsagePublicSimple, AIUsageTotalsSimple


class AgentRunStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    step_number: int
    type: str
    content: dict | None = None
    tokens_used: int | None = None
    duration_ms: int | None = None
    created_at: datetime


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    agent_name: str | None = None
    trigger_type: str
    trigger_source: str | None = None
    conversation_id: UUID | None = None
    event_delivery_id: UUID | None = None
    input: dict | None = None
    output: dict | None = None
    status: str
    error: str | None = None
    org_id: UUID | None = None
    caller_user_id: str | None = None
    caller_email: str | None = None
    caller_name: str | None = None
    iterations_used: int
    tokens_used: int
    budget_max_iterations: int | None = None
    budget_max_tokens: int | None = None
    duration_ms: int | None = None
    llm_model: str | None = None
    asked: str | None = None
    did: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    confidence: float | None = None
    confidence_reason: str | None = None
    verdict: str | None = None
    verdict_note: str | None = None
    verdict_set_at: datetime | None = None
    verdict_set_by: UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    parent_run_id: UUID | None = None


class AgentRunDetailResponse(AgentRunResponse):
    steps: list[AgentRunStepResponse] = Field(default_factory=list)
    child_run_ids: list[UUID] = Field(default_factory=list)
    ai_usage: list[AIUsagePublicSimple] | None = None
    ai_totals: AIUsageTotalsSimple | None = None


class AgentRunListResponse(BaseModel):
    items: list[AgentRunResponse]
    total: int
    next_cursor: str | None = None


class AgentRunCreateRequest(BaseModel):
    agent_name: str
    input: dict | None = None
    output_schema: dict | None = None
    timeout: int = 1800


class AgentRunRerunResponse(BaseModel):
    run_id: UUID


class VerdictRequest(BaseModel):
    verdict: Literal["up", "down"]
    note: str | None = Field(default=None, max_length=2000)


class VerdictResponse(BaseModel):
    run_id: UUID
    verdict: str | None = None
    verdict_note: str | None = None
    verdict_set_at: datetime | None = None
    verdict_set_by: UUID | None = None
