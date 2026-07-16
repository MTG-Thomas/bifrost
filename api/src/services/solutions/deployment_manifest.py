"""Canonical immutable runtime-closure contracts for Solution deployments."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def canonical_json(value: BaseModel | dict[str, Any]) -> bytes:
    """Serialize a deployment document deterministically for hashing/storage."""
    payload = (
        value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, BaseModel)
        else value
    )
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def sha256_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class ImmutableContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeploymentSource(ImmutableContract):
    artifact_key: str
    runtime_prefix: str


class RuntimeEntityDefinition(ImmutableContract):
    portable_ref: str
    resolved_id: UUID
    definition: dict[str, Any]
    source_ref: str | None = None
    source_hash: str | None = None
    dependency_solution_id: UUID | None = None


class RuntimeSourceResolution(ImmutableContract):
    object_key: str
    content_hash: str


class DependencyResolution(ImmutableContract):
    solution_id: UUID
    deployment_id: UUID
    declared_constraint: str | None = None
    bundle_hash: str


class DeploymentGitProvenance(ImmutableContract):
    repository: str | None = None
    resolved_ref: str | None = None
    commit_sha: str | None = None


class CompiledDeploymentManifest(ImmutableContract):
    schema_version: Literal[1] = 1
    solution_id: UUID
    deployment_id: UUID
    bundle_hash: str
    source: DeploymentSource
    workflows: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    agents: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    forms: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    events: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    applications: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    tables: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    file_locations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    connections: dict[str, dict[str, Any]] = Field(default_factory=dict)
    config_requirements: dict[str, dict[str, Any]] = Field(default_factory=dict)
    dependencies: dict[str, DependencyResolution] = Field(default_factory=dict)
    git: DeploymentGitProvenance = Field(default_factory=DeploymentGitProvenance)

    def canonical_bytes(self) -> bytes:
        return canonical_json(self)

    def content_hash(self) -> str:
        return sha256_digest(self.canonical_bytes())


class DeploymentResolutionMap(ImmutableContract):
    """Deployment-local lookup document consumed by the future runtime adapter."""

    schema_version: Literal[1] = 1
    workflows: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    agents: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    forms: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    events: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    applications: dict[str, RuntimeEntityDefinition] = Field(default_factory=dict)
    dependencies: dict[str, DependencyResolution] = Field(default_factory=dict)
    sources: dict[str, RuntimeSourceResolution] = Field(default_factory=dict)

    def resolve_workflow(self, portable_ref: str) -> RuntimeEntityDefinition:
        return self.workflows[portable_ref]

    def resolve_agent(self, portable_ref: str) -> RuntimeEntityDefinition:
        return self.agents[portable_ref]

    def resolve_form(self, portable_ref: str) -> RuntimeEntityDefinition:
        return self.forms[portable_ref]

    def resolve_event(self, portable_ref: str) -> RuntimeEntityDefinition:
        return self.events[portable_ref]

    def resolve_application(self, portable_ref: str) -> RuntimeEntityDefinition:
        return self.applications[portable_ref]

    def resolve_dependency(self, solution_ref: str) -> UUID:
        return self.dependencies[solution_ref].deployment_id

    def resolve_source(self, source_ref: str) -> RuntimeSourceResolution:
        return self.sources[source_ref]
