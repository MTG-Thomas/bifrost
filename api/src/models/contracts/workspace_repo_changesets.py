"""REST contracts for transactional workspace _repo changesets."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

ChangesetStatus = Literal[
    "open",
    "staged",
    "validated",
    "activating",
    "activated",
    "committed",
    "committed_unpushed",
    "aborted",
    "conflicted",
    "failed",
    "recovery_required",
]


class WorkspaceRepoStateResponse(BaseModel):
    storage_root: Literal["_repo"] = "_repo"
    scope: str
    revision: str
    file_count: int
    file_hashes: dict[str, str] = Field(default_factory=dict)
    dirty: bool
    open_changesets: int
    git_status: dict | None = None


class WorkspaceRepoChangesetBegin(BaseModel):
    scope: str = Field(
        min_length=1,
        max_length=1000,
        description="Path prefix relative to the global _repo compatibility root.",
    )
    base_revision: str | None = Field(default=None, min_length=64, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    worker_id: str | None = Field(default=None, max_length=255)


class WorkspaceRepoFileMutationRequest(BaseModel):
    path: str = Field(
        min_length=1,
        max_length=1000,
        description="File path relative to _repo and contained by the changeset scope.",
    )
    operation: Literal["write", "delete"]
    content_base64: str | None = None
    expected_hash: str | None = Field(default=None, min_length=64, max_length=64)
    force_deactivation: bool = False

    @model_validator(mode="after")
    def require_content_for_write(self):
        if self.operation == "write" and self.content_base64 is None:
            raise ValueError("content_base64 is required for write operations")
        if self.operation == "delete" and self.content_base64 is not None:
            raise ValueError("content_base64 is not allowed for delete operations")
        return self


class WorkspaceRepoMutation(BaseModel):
    path: str
    operation: Literal["write", "delete"]
    content_base64: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    force_deactivation: bool = False


class WorkspaceRepoChangesetResponse(BaseModel):
    id: UUID
    scope: str
    base_revision: str
    status: ChangesetStatus
    title: str | None = None
    worker_id: str | None = None
    mutations: list[WorkspaceRepoMutation] = Field(default_factory=list)
    validation: dict | None = None
    activated_revision: str | None = None
    commit_sha: str | None = None
    error: str | None = None
    failure_detail: dict | None = None
    created_by: UUID
    writer_job_id: UUID | None = None
    dirty_generation: str | None = None
    authoritative_revision: str | None = None
    remote_sha: str | None = None
    commit_message: str | None = None
    push_requested: bool = False
    closure_started_at: datetime | None = None
    closure_completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceRepoChangesetListResponse(BaseModel):
    changesets: list[WorkspaceRepoChangesetResponse]


class WorkspaceWriterStatus(BaseModel):
    job_id: UUID
    changeset_id: UUID | None = None
    status: str
    phase: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    lease_expired: bool = False
    started_at: datetime | None = None


class WorkspaceDirtyStatus(BaseModel):
    dirty: bool
    generation: str | None = None
    dirty_since: str | None = None
    updated_at: str | None = None
    writer: str | None = None
    legacy: bool = False


class WorkspaceAuthoritativeConvergenceResponse(BaseModel):
    configured: bool
    branch: str | None = None
    generated_checkout_clean: bool | None = None
    authoritative_converged: bool | None = None
    authoritative_revision: str | None = None
    authoritative_root_revisions: dict[str, str] = Field(default_factory=dict)
    remote_sha: str | None = None
    mismatch_count: int = 0
    mismatch_paths: list[str] = Field(default_factory=list)
    error: str | None = None


class WorkspaceRepoOperationalStatusResponse(BaseModel):
    dirty: WorkspaceDirtyStatus
    active_writer: WorkspaceWriterStatus | None = None
    active_changesets: list[WorkspaceRepoChangesetResponse] = Field(
        default_factory=list
    )
    recoverable_closures: list[WorkspaceRepoChangesetResponse] = Field(
        default_factory=list
    )
    closure_ledger: list[WorkspaceRepoChangesetResponse] = Field(default_factory=list)
    convergence: WorkspaceAuthoritativeConvergenceResponse


class WorkspaceRepoFileDiff(BaseModel):
    path: str
    operation: Literal["write", "delete"]
    before_hash: str | None = None
    after_hash: str | None = None
    unified_diff: str | None = None


class WorkspaceRepoChangesetDiffResponse(BaseModel):
    changeset_id: UUID
    files: list[WorkspaceRepoFileDiff]


class WorkspaceRepoValidationResponse(BaseModel):
    valid: bool
    diagnostics: list[dict] = Field(default_factory=list)
    pending_deactivations: list[dict] = Field(default_factory=list)
    validated_revision: str


class WorkspaceRepoActivateRequest(BaseModel):
    commit_message: str | None = Field(default=None, max_length=500)
    push: bool = False
    plan_id: str | None = Field(default=None, max_length=255)
    protected_main_source_sha: str | None = Field(
        default=None,
        pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$",
    )
