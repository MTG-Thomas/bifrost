"""Canonical immutable runtime-closure contracts for Solution deployments."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


def canonical_json(value: BaseModel | dict[str, Any]) -> bytes:
    """Serialize a deployment document deterministically for hashing/storage."""
    payload = (
        value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, BaseModel)
        else value
    )
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


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
    definition: dict[str, JsonValue]
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
    resolution_map_hash: str
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

    def resolve_workflow_id(self, resolved_id: UUID) -> RuntimeEntityDefinition:
        return self._resolve_id(self.workflows, resolved_id)

    def resolve_agent_id(self, resolved_id: UUID) -> RuntimeEntityDefinition:
        return self._resolve_id(self.agents, resolved_id)

    @staticmethod
    def _resolve_id(
        entities: dict[str, RuntimeEntityDefinition], resolved_id: UUID
    ) -> RuntimeEntityDefinition:
        matches = [entity for entity in entities.values() if entity.resolved_id == resolved_id]
        if len(matches) != 1:
            raise KeyError(resolved_id)
        return matches[0]


def validate_runtime_closure(
    manifest_value: CompiledDeploymentManifest | dict[str, Any],
    resolution_value: DeploymentResolutionMap | dict[str, Any],
    dependency_edges: list[Any],
    *,
    expected_manifest_hash: str,
    expected_resolution_hash: str,
) -> tuple[CompiledDeploymentManifest, DeploymentResolutionMap]:
    """Prove that all duplicated closure evidence describes one immutable runtime."""
    manifest = (
        manifest_value
        if isinstance(manifest_value, CompiledDeploymentManifest)
        else CompiledDeploymentManifest.model_validate(manifest_value)
    )
    resolution = (
        resolution_value
        if isinstance(resolution_value, DeploymentResolutionMap)
        else DeploymentResolutionMap.model_validate(resolution_value)
    )
    resolution_hash = sha256_digest(canonical_json(resolution))
    if resolution_hash != expected_resolution_hash:
        raise ValueError("resolution map hash mismatch")
    if manifest.resolution_map_hash != resolution_hash:
        raise ValueError("manifest does not anchor the resolution map")
    if manifest.content_hash() != expected_manifest_hash:
        raise ValueError("compiled manifest hash mismatch")

    _validate_manifest_resolution_agreement(manifest, resolution)
    _validate_entity_sources(resolution)
    _validate_dependency_edges(manifest, dependency_edges)
    return manifest, resolution


def _validate_manifest_resolution_agreement(
    manifest: CompiledDeploymentManifest,
    resolution: DeploymentResolutionMap,
) -> None:
    for kind in ("workflows", "agents", "forms", "events", "applications"):
        if getattr(manifest, kind) != getattr(resolution, kind):
            raise ValueError(f"manifest/resolution mismatch for {kind}")
    if manifest.dependencies != resolution.dependencies:
        raise ValueError("manifest/resolution dependency mismatch")


def _validate_entity_sources(resolution: DeploymentResolutionMap) -> None:
    for entities in (
        resolution.workflows,
        resolution.agents,
        resolution.forms,
        resolution.events,
        resolution.applications,
    ):
        for portable_ref, entity in entities.items():
            if portable_ref != entity.portable_ref:
                raise ValueError(f"portable reference key mismatch: {portable_ref}")
            if entity.source_ref is not None:
                source = resolution.sources.get(entity.source_ref)
                if source is None or source.content_hash != entity.source_hash:
                    raise ValueError(f"source resolution mismatch: {entity.source_ref}")


def _validate_dependency_edges(
    manifest: CompiledDeploymentManifest,
    dependency_edges: list[Any],
) -> None:
    expected_edges = {
        (edge.dependency_solution_id, edge.dependency_deployment_id, edge.resolved_bundle_hash)
        for edge in dependency_edges
    }
    manifest_edges = {
        (item.solution_id, item.deployment_id, item.bundle_hash)
        for item in manifest.dependencies.values()
    }
    if expected_edges != manifest_edges:
        raise ValueError("relational dependency edges do not match the manifest")
