"""Contracts for immutable Workspace release artifact previews."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class PromotionValidationTarget(BaseModel):
    """One executable entity that must import from the immutable candidate tree."""

    path: str = Field(min_length=1, max_length=1000)
    function: str = Field(min_length=1, max_length=255)
    entity_type: Literal["workflow", "tool", "data_provider"]
    relation: Literal["selected_entry", "affected_executable"]


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
    governed_paths: list[str] = Field(
        description=(
            "Sorted cumulative paths whose reads and mutations are governed by Live."
        )
    )
    governed_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_registration_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_registrations: dict[str, dict]
    snapshot_id: str
    risk_class: Literal["R0", "R1", "R2"]
    policy_version: str
    closure: list[PromotionClosureMember]
    validation_targets: list[PromotionValidationTarget]
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
    governed_paths: list[str] = Field(
        description=(
            "Sorted cumulative paths whose reads and mutations are governed by Live."
        )
    )
    governed_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_registration_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_registrations: dict[str, dict]
    entry: PromotionEntry
    closure: list[PromotionClosureMember]
    validation_targets: list[PromotionValidationTarget]
    risk_class: Literal["R0", "R1", "R2"]
    policy_version: str
    registration: PromotionRegistrationEvidence
    protected_source: PromotionSourceEvidence
    declared_effects: list[str]
    static_effects: list[str]
    computed_effects: list[str]
    bounds: dict[str, int]
    requested_bounds: dict[str, int]
    local_run: PromotionRunEvidence | None = None
    diagnostics: list[PromotionDiagnostic] = Field(default_factory=list)
    lifecycle_status: PromotionArtifactLifecycle
    supersedes_candidate_id: str | None = None
    source_artifact_key: str
    expires_at: datetime
    created_at: datetime


class WorkspacePromotionCanaryRequest(BaseModel):
    """Parameters for an isolated canary of one reviewed artifact."""

    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkspacePromotionCanaryAccepted(BaseModel):
    schema_version: Literal["bifrost.workspace-reviewed-canary/v1"] = (
        "bifrost.workspace-reviewed-canary/v1"
    )
    execution_id: UUID
    artifact_id: UUID
    runtime_mode: Literal["workspace-canary-v1"] = "workspace-canary-v1"
    status: Literal["Pending"] = "Pending"


class WorkspaceReleaseActivationChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["bifrost.workspace-release-activation-challenge/v1"]
    artifact_id: UUID
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prepared_evidence_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    governed_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effective_registration_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    risk_class: Literal["R0", "R1", "R2"]
    computed_effects: list[str] = Field(min_length=1)
    computed_effects_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1)
    protected_source: PromotionSourceEvidence
    required_authorization: Literal["reviewed_canary", "risk_acknowledgement"]
    challenge_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class WorkspaceReviewedCanaryAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["reviewed_canary"]
    challenge_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    canary_execution_id: UUID


class WorkspaceReleaseRiskAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["bifrost.workspace-release-risk-acknowledgement/v1"]
    challenge_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prepared_evidence_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    risk_class: Literal["R1", "R2"]
    computed_effects: list[str] = Field(min_length=1)
    computed_effects_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protected_source: PromotionSourceEvidence
    decision: Literal["activate_without_canary_or_effect_execution"]
    acknowledgement_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class WorkspaceRiskAcknowledgementAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["risk_acknowledgement"]
    acknowledgement: WorkspaceReleaseRiskAcknowledgement


WorkspaceReleaseAuthorization = Annotated[
    WorkspaceReviewedCanaryAuthorization | WorkspaceRiskAcknowledgementAuthorization,
    Field(discriminator="kind"),
]


class WorkspaceReleaseActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_base_release_id: str = Field(pattern=r"^(?:sha256|repo-v1):[0-9a-f]{64}$")
    expected_active_release_id: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    prepared_evidence_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authorization: WorkspaceReleaseAuthorization


class WorkspaceReleaseRuntimeStatus(BaseModel):
    state: Literal["coherent", "prepared", "not_prepared"]
    immutable_release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prepared_evidence_id: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    activation_authorization: WorkspaceReleaseActivationChallenge | None = None
    authorization_kind: Literal["reviewed_canary", "risk_acknowledgement"] | None = None
    authorization_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    canary_execution_id: UUID | None = None
    risk_acknowledgement_id: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class WorkspaceReleaseHistoryStatus(BaseModel):
    state: Literal[
        "pending", "locked", "attention_required", "superseded", "not_queued"
    ]
    lock_state: str
    job_id: UUID | None = None
    attention_deadline: datetime | None = None
    overdue: bool = False
    runtime_history_verified: bool = False


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


WorkspaceSourceDisposition = Literal[
    "pending", "attention_required", "released", "deferred", "non_production"
]


class WorkspaceSourceReleaseDeclareRequest(BaseModel):
    """Exact reviewed source state declared by the trusted merge producer."""

    model_config = ConfigDict(extra="forbid")

    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    paths: dict[str, str | None] = Field(default_factory=dict, max_length=4000)
    disposition: Literal["pending", "attention_required", "non_production"]
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    due_at: datetime | None = None

    @model_validator(mode="after")
    def validate_disposition(self):
        if self.disposition == "pending" and not self.paths:
            raise ValueError("pending source release requires exact path hashes")
        if self.disposition == "non_production" and not self.reason:
            raise ValueError("non-production disposition requires a reason")
        if self.disposition == "attention_required" and not self.reason:
            raise ValueError("attention-required disposition requires a reason")
        if self.due_at is not None and self.due_at.utcoffset() is None:
            raise ValueError("source release deadline must include a timezone")
        invalid_hashes = [
            path
            for path, digest in self.paths.items()
            if not path
            or len(path) > 1000
            or (digest is not None and not _is_sha256(digest))
        ]
        if invalid_hashes:
            raise ValueError("source release paths require normalized SHA-256 values")
        return self


class WorkspaceSourceReleaseDispositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: Literal["deferred", "non_production"]
    reason: str = Field(min_length=1, max_length=2000)


class WorkspaceSourceReleaseResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-source-release/v1"] = (
        "bifrost.workspace-source-release/v1"
    )
    id: UUID
    organization_id: UUID
    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    paths: dict[str, str | None]
    disposition: WorkspaceSourceDisposition
    reason: str | None = None
    release_row_id: UUID | None = None
    completion_evidence: dict[str, Any] | None = None
    due_at: datetime | None = None
    overdue: bool
    requires_attention: bool
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceSourceReleaseListResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-source-release-list/v1"] = (
        "bifrost.workspace-source-release-list/v1"
    )
    records: list[WorkspaceSourceReleaseResponse]
    total: int
    pending: int
    attention_required: int
    overdue: int
    tracking_state: Literal["not_configured", "active"]
    last_observed_source_commit_sha: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$"
    )
    last_observed_at: datetime | None = None
    producer_contract: str


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
