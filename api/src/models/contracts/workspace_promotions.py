"""Contracts for immutable Workspace release artifact previews."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PromotionEntry(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    function: str = Field(min_length=1, max_length=255)


class PromotionFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_base64: str | None = Field(
        default=None,
        max_length=44_739_244,
        description=(
            "Draft-only source bytes; reviewed production reads from protected Git."
        ),
    )


class PromotionSnapshot(BaseModel):
    snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    files: dict[str, str] = Field(min_length=1, max_length=4000)
    closure: list[PromotionFile] = Field(min_length=1, max_length=200)


class PromotionRunEvidence(BaseModel):
    succeeded: bool
    closure_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence_id: str | None = Field(default=None, max_length=255)
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    observed_effects: list[str] = Field(default_factory=list, max_length=100)


class PromotionClientContract(BaseModel):
    cli_version: str = Field(min_length=1, max_length=100)
    sdk_version: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(min_length=1, max_length=100)


class PromotionProtectedSource(BaseModel):
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class WorkspacePromotionPreviewRequest(BaseModel):
    schema_version: Literal["bifrost.workspace-promotion-bundle/v2"]
    target: Literal["production"] = "production"
    entry: PromotionEntry
    snapshot: PromotionSnapshot
    expected_base_release_id: str | None = Field(
        default=None, pattern=r"^(?:sha256|repo-v1):[0-9a-f]{64}$"
    )
    protected_source: PromotionProtectedSource
    supersedes_candidate_id: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    local_run: PromotionRunEvidence | None = None
    client: PromotionClientContract


class WorkspacePromotionDraftRequest(BaseModel):
    """Local-only source upload that can never authorize a Live release."""

    schema_version: Literal["bifrost.workspace-draft-upload/v1"]
    target: Literal["draft"] = "draft"
    entry: PromotionEntry
    snapshot: PromotionSnapshot
    local_run: PromotionRunEvidence | None = None
    client: PromotionClientContract


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


class PromotionRegistrationEvidence(BaseModel):
    intent: list[dict] = Field(default_factory=list)
    intent_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: dict | None = None
    state_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class PromotionSourceEvidence(BaseModel):
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


PromotionArtifactLifecycle = Literal[
    "review_required", "eligible", "invalid", "expired", "superseded"
]


class WorkspacePromotionDraftResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-draft-artifact/v1"] = (
        "bifrost.workspace-draft-artifact/v1"
    )
    artifact_id: UUID
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    closure_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authority: Literal["local_only"] = "local_only"
    activatable: Literal[False] = False
    entry: PromotionEntry
    snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    closure: list[PromotionClosureMember]
    declared_effects: list[str]
    computed_effects: list[str]
    bounds: dict[str, int]
    lifecycle_status: Literal["previewed", "expired"]
    source_artifact_key: str
    expires_at: datetime
    created_at: datetime


class WorkspaceReleasePrepareRequest(BaseModel):
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class WorkspacePromotionPreviewResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-promotion-preview/v2"] = (
        "bifrost.workspace-promotion-preview/v2"
    )
    preview_only: Literal[True] = True
    ready_to_activate: bool = False
    disposition: Literal["eligible", "review_required", "invalid"]
    artifact_id: UUID | None = None
    candidate_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    content_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    closure_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    release_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    base_release_id: str
    base_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_files: dict[str, str] = Field(
        description=(
            "Complete executable authored-Python path-to-SHA-256 tree for release v1."
        )
    )
    effective_registration_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_registrations: dict[str, dict]
    snapshot_id: str
    risk_class: Literal["R0", "R1", "R2"]
    policy_version: str
    closure: list[PromotionClosureMember]
    declared_effects: list[str]
    static_effects: list[str]
    computed_effects: list[str]
    bounds: dict[str, int]
    requested_bounds: dict[str, int]
    registration: PromotionRegistrationEvidence
    protected_source: PromotionSourceEvidence
    lifecycle_status: PromotionArtifactLifecycle
    supersedes_candidate_id: str | None = None
    source_artifact_key: str | None = None
    diagnostics: list[PromotionDiagnostic]
    expires_at: datetime | None = None


class WorkspacePromotionArtifactResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-release-artifact/v1"] = (
        "bifrost.workspace-release-artifact/v1"
    )
    artifact_id: UUID
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    closure_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_release_id: str = Field(pattern=r"^(?:sha256|repo-v1):[0-9a-f]{64}$")
    base_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_files: dict[str, str] = Field(
        description=(
            "Complete executable authored-Python path-to-SHA-256 tree for release v1."
        )
    )
    effective_registration_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_registrations: dict[str, dict]
    entry: PromotionEntry
    closure: list[PromotionClosureMember]
    registration: PromotionRegistrationEvidence
    protected_source: PromotionSourceEvidence
    declared_effects: list[str]
    computed_effects: list[str]
    bounds: dict[str, int]
    local_run: PromotionRunEvidence | None = None
    diagnostics: list[PromotionDiagnostic] = Field(default_factory=list)
    lifecycle_status: PromotionArtifactLifecycle
    supersedes_candidate_id: str | None = None
    source_artifact_key: str
    expires_at: datetime
    created_at: datetime


class WorkspaceDraftCanaryRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkspaceDraftCanaryAccepted(BaseModel):
    schema_version: Literal["bifrost.workspace-draft-canary/v1"] = (
        "bifrost.workspace-draft-canary/v1"
    )
    execution_id: UUID
    artifact_id: UUID
    runtime_mode: Literal["workspace-draft-v1"] = "workspace-draft-v1"
    status: Literal["Pending"] = "Pending"
class WorkspaceReleaseActivateRequest(BaseModel):
    artifact_id: UUID
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_base_release_id: str = Field(
        pattern=r"^(?:sha256|repo-v1):[0-9a-f]{64}$"
    )
    expected_active_release_id: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    prepared_evidence_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    canary_execution_id: UUID


class WorkspaceReleaseRuntimeStatus(BaseModel):
    state: Literal["coherent", "prepared", "not_prepared"]
    immutable_release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prepared_evidence_id: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    canary_execution_id: UUID | None = None


class WorkspaceReleaseHistoryStatus(BaseModel):
    state: Literal[
        "pending", "locked", "attention_required", "superseded", "not_queued"
    ]
    lock_state: str
    job_id: UUID | None = None


class WorkspaceReleaseStatusResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-release-status/v1"] = (
        "bifrost.workspace-release-status/v1"
    )
    release_row_id: UUID
    artifact_id: UUID
    organization_id: UUID
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_release_id: str = Field(pattern=r"^(?:sha256|repo-v1):[0-9a-f]{64}$")
    activation_state: str
    is_live: bool
    previous_release_row_id: UUID | None = None
    runtime: WorkspaceReleaseRuntimeStatus
    history: WorkspaceReleaseHistoryStatus
    activated_at: datetime | None = None


class WorkspaceLiveStatusResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-live-status/v1"] = (
        "bifrost.workspace-live-status/v1"
    )
    organization_id: UUID
    active_release: WorkspaceReleaseStatusResponse | None = None
