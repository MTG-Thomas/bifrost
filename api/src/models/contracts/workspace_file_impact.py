"""Contracts for guarded Workspace file impact analysis."""

from typing import Literal

from pydantic import BaseModel, Field


class WorkspaceFileImpactRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    content: str | None = Field(
        default=None,
        description="Optional proposed UTF-8 source; omitted to inspect live bytes.",
    )
    direction: Literal["forward", "reverse", "both"] = "both"


class WorkspaceFileImpactMember(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    depth: int = Field(ge=1)


class WorkspaceFileImpactEdge(BaseModel):
    importer: str
    dependency: str
    kind: Literal["import", "registry"]


class WorkspaceFileImpactDiagnostic(BaseModel):
    code: str
    severity: Literal["info", "warning", "blocker"]
    message: str
    path: str | None = None


class WorkspaceFileImpactResponse(BaseModel):
    schema_version: Literal["bifrost.workspace-file-impact/v1"] = (
        "bifrost.workspace-file-impact/v1"
    )
    candidate_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    path: str
    direction: Literal["forward", "reverse", "both"]
    proposed: bool
    changed: bool
    current_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    proposed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    forward_dependencies: list[WorkspaceFileImpactMember] = Field(default_factory=list)
    reverse_dependencies: list[WorkspaceFileImpactMember] = Field(default_factory=list)
    impacted_paths: list[str] = Field(default_factory=list)
    edges: list[WorkspaceFileImpactEdge] = Field(default_factory=list)
    diagnostics: list[WorkspaceFileImpactDiagnostic] = Field(default_factory=list)
    ready_to_write: bool
