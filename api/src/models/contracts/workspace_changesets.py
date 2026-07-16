"""REST contracts for transactional workspace changesets."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

ChangesetStatus = Literal["open", "staged", "validated", "activating", "activated", "committed", "aborted", "conflicted", "failed"]


class WorkspaceStateResponse(BaseModel):
    scope: str
    revision: str
    file_count: int
    dirty: bool
    open_changesets: int
    git_status: dict | None = None


class WorkspaceChangesetBegin(BaseModel):
    scope: str = Field(min_length=1, max_length=1000)
    base_revision: str | None = Field(default=None, min_length=64, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    worker_id: str | None = Field(default=None, max_length=255)


class WorkspaceFileMutationRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
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


class WorkspaceMutation(BaseModel):
    path: str
    operation: Literal["write", "delete"]
    content_base64: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    force_deactivation: bool = False


class WorkspaceChangesetResponse(BaseModel):
    id: UUID
    scope: str
    base_revision: str
    status: ChangesetStatus
    title: str | None = None
    worker_id: str | None = None
    mutations: list[WorkspaceMutation] = Field(default_factory=list)
    validation: dict | None = None
    activated_revision: str | None = None
    commit_sha: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceFileDiff(BaseModel):
    path: str
    operation: Literal["write", "delete"]
    before_hash: str | None = None
    after_hash: str | None = None
    unified_diff: str | None = None


class WorkspaceChangesetDiffResponse(BaseModel):
    changeset_id: UUID
    files: list[WorkspaceFileDiff]


class WorkspaceValidationResponse(BaseModel):
    valid: bool
    diagnostics: list[dict] = Field(default_factory=list)
    pending_deactivations: list[dict] = Field(default_factory=list)
    validated_revision: str


class WorkspaceActivateRequest(BaseModel):
    commit_message: str | None = Field(default=None, max_length=500)
    push: bool = False
