"""Contracts for preview-only rapid Workspace promotion."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PromotionEntry(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    function: str = Field(min_length=1, max_length=255)


class PromotionFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_base64: str = Field(min_length=1)


class PromotionSnapshot(BaseModel):
    snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    files: dict[str, str] = Field(min_length=1, max_length=4000)
    closure: list[PromotionFile] = Field(min_length=1, max_length=200)


class PromotionRunEvidence(BaseModel):
    succeeded: bool
    snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence_id: str | None = Field(default=None, max_length=255)
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    observed_effects: list[str] = Field(default_factory=list, max_length=100)


class PromotionClientContract(BaseModel):
    cli_version: str = Field(min_length=1, max_length=100)
    sdk_version: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(min_length=1, max_length=100)


class WorkspacePromotionPreviewRequest(BaseModel):
    schema_version: Literal["bifrost.workspace-promotion-bundle/v1"]
    target: Literal["production"] = "production"
    entry: PromotionEntry
    snapshot: PromotionSnapshot
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    local_run: PromotionRunEvidence | None = None
    client: PromotionClientContract

    @model_validator(mode="after")
    def bind_run_to_snapshot(self):
        if self.local_run and self.local_run.snapshot_id != self.snapshot.snapshot_id:
            raise ValueError("local run evidence is for a different snapshot")
        return self


class PromotionClosureMember(BaseModel):
    path: str
    sha256: str
    size: int
    relation: Literal["selected", "dependency"]


class PromotionDiagnostic(BaseModel):
    code: str
    severity: Literal["info", "warning", "blocker"]
    message: str
    path: str | None = None


class WorkspacePromotionPreviewResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-promotion-preview/v1"] = (
        "bifrost.workspace-promotion-preview/v1"
    )
    preview_only: Literal[True] = True
    ready_to_activate: Literal[False] = False
    disposition: Literal["review_required", "invalid"]
    artifact_id: UUID | None = None
    candidate_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    snapshot_id: str
    risk_class: Literal["R0", "R1", "R2"]
    policy_version: str
    closure: list[PromotionClosureMember]
    declared_effects: list[str]
    static_effects: list[str]
    computed_effects: list[str]
    bounds: dict[str, int]
    requested_bounds: dict[str, int]
    registration: dict
    diagnostics: list[PromotionDiagnostic]
    expires_at: datetime | None = None
