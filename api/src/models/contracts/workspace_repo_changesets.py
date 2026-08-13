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
    operation: Literal["write", "delete", "verify"]
    content_base64: str | None = None
    expected_hash: str | None = Field(default=None, min_length=64, max_length=64)
    force_deactivation: bool = False

    @model_validator(mode="after")
    def require_content_for_write(self):
        if self.operation == "write" and self.content_base64 is None:
            raise ValueError("content_base64 is required for write operations")
        if self.operation != "write" and self.content_base64 is not None:
            raise ValueError("content_base64 is only allowed for write operations")
        if self.operation == "verify" and self.expected_hash is None:
            raise ValueError("expected_hash is required for verify operations")
        return self


class WorkspaceRepoMutation(BaseModel):
    path: str
    operation: Literal["write", "delete", "verify"]
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
    created_at: datetime
    updated_at: datetime


class WorkspaceRepoFileDiff(BaseModel):
    path: str
    operation: Literal["write", "delete", "verify"]
    before_hash: str | None = None
    after_hash: str | None = None
    unified_diff: str | None = None


class WorkspaceRepoChangesetDiffResponse(BaseModel):
    changeset_id: UUID
    files: list[WorkspaceRepoFileDiff]


class WorkspaceRepoValidationResponse(BaseModel):
    valid: bool
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    diagnostics: list[dict] = Field(default_factory=list)
    pending_deactivations: list[dict] = Field(default_factory=list)
    registration_actions: list[dict] = Field(default_factory=list)
    validated_revision: str


class WorkspaceRepoActivateRequest(BaseModel):
    commit_message: str | None = Field(default=None, max_length=500)
    push: bool = False
    plan_id: str | None = Field(default=None, max_length=255)
    candidate_id: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
        description="Exact immutable candidate returned by the latest validation.",
    )
    protected_main_source_sha: str | None = Field(
        default=None,
        pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$",
    )


class WorkspaceRepoGitConvergencePreviewRequest(BaseModel):
    changeset_ids: list[UUID] = Field(min_length=1, max_length=25)
    protected_main_source_sha: str = Field(
        pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$"
    )

    @model_validator(mode="after")
    def require_unique_changesets(self):
        if len(set(self.changeset_ids)) != len(self.changeset_ids):
            raise ValueError("changeset_ids must be unique")
        return self


class WorkspaceRepoGitConvergenceApplyRequest(
    WorkspaceRepoGitConvergencePreviewRequest
):
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    commit_message: str = Field(min_length=1, max_length=500)


class WorkspaceRepoGitConvergencePath(BaseModel):
    path: str
    desired_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    live_sha256: str | None = None
    reviewed_sha256: str | None = None
    history_sha256: str | None = None
    requires_write: bool
    source_changeset_id: UUID | None = None


class WorkspaceRepoGitConvergenceChangeset(BaseModel):
    changeset_id: UUID
    disposition: Literal["reconciled", "partially_superseded", "superseded"]
    reconciled_paths: list[str] = Field(default_factory=list)
    superseded_paths: list[str] = Field(default_factory=list)


class WorkspaceRepoGitConvergenceResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-history-convergence/v1"] = (
        "bifrost.workspace-history-convergence/v1"
    )
    ready_to_apply: bool
    applied: bool = False
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    protected_main_source_sha: str
    protected_main_tree_sha: str
    history_head_sha: str
    history_tree_sha: str
    commit_sha: str | None = None
    signature_state: str | None = None
    diagnostics: list[dict] = Field(default_factory=list)
    paths: list[WorkspaceRepoGitConvergencePath] = Field(default_factory=list)
    changesets: list[WorkspaceRepoGitConvergenceChangeset] = Field(
        default_factory=list
    )
