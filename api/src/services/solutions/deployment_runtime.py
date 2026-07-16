"""Execution-time resolution against an immutable Solution deployment closure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow
from src.models.orm.solutions import Solution
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import validate_runtime_closure


class DeploymentRuntimeError(RuntimeError):
    """A Solution execution could not prove its immutable runtime closure."""


@dataclass(frozen=True)
class PinnedWorkflowRuntime:
    workflow_id: UUID
    solution_id: UUID
    deployment_id: UUID
    bundle_hash: str
    compiled_manifest_hash: str
    git_commit_sha: str | None
    runtime_storage_prefix: str
    portable_ref: str
    name: str
    function_name: str
    path: str
    source_hash: str
    timeout_seconds: int
    time_saved: int
    value: float
    execution_mode: str
    workflow_type: str
    cache_ttl_seconds: int
    organization_id: str | None
    can_access_global_repo: bool

    def queue_evidence(self) -> dict[str, Any]:
        return {
            "solution_id": str(self.solution_id),
            "solution_deployment_id": str(self.deployment_id),
            "bundle_hash": self.bundle_hash,
            "compiled_manifest_hash": self.compiled_manifest_hash,
            "git_commit_sha": self.git_commit_sha,
            "runtime_storage_prefix": self.runtime_storage_prefix,
            "workflow_portable_ref": self.portable_ref,
            "workflow_name": self.name,
            "workflow_function_name": self.function_name,
            "workflow_path": self.path,
            "workflow_source_hash": self.source_hash,
            "workflow_timeout_seconds": self.timeout_seconds,
            "workflow_time_saved": self.time_saved,
            "workflow_value": self.value,
            "workflow_execution_mode": self.execution_mode,
            "workflow_type": self.workflow_type,
            "workflow_cache_ttl_seconds": self.cache_ttl_seconds,
            "workflow_organization_id": self.organization_id,
            "solution_global_repo_access": self.can_access_global_repo,
        }


def workflow_data_from_evidence(
    deployment_id: str, evidence: dict[str, Any] | None
) -> dict[str, Any]:
    """Validate trusted queue evidence and expose the legacy consumer shape."""
    if not isinstance(evidence, dict):
        raise DeploymentRuntimeError("pinned execution is missing runtime evidence")
    if evidence.get("solution_deployment_id") != deployment_id:
        raise DeploymentRuntimeError("pinned execution deployment evidence mismatch")
    required = (
        "workflow_name", "workflow_function_name", "workflow_path",
        "workflow_source_hash", "solution_id", "runtime_storage_prefix",
    )
    if any(not evidence.get(key) for key in required):
        raise DeploymentRuntimeError("pinned execution runtime evidence is incomplete")
    return {
        "name": evidence["workflow_name"],
        "function_name": evidence["workflow_function_name"],
        "path": evidence["workflow_path"],
        "content_hash": evidence["workflow_source_hash"],
        "type": evidence.get("workflow_type", "workflow"),
        "cache_ttl_seconds": evidence.get("workflow_cache_ttl_seconds", 0),
        "timeout_seconds": evidence.get("workflow_timeout_seconds", 1800),
        "time_saved": evidence.get("workflow_time_saved", 0),
        "value": evidence.get("workflow_value", 0),
        "solution_id": evidence["solution_id"],
        "organization_id": evidence.get("workflow_organization_id"),
        "can_access_global_repo": evidence.get("solution_global_repo_access", False),
        "runtime_storage_prefix": evidence["runtime_storage_prefix"],
    }


def _required(definition: dict[str, Any], key: str) -> Any:
    value = definition.get(key)
    if value is None or value == "":
        raise DeploymentRuntimeError(f"compiled workflow definition missing {key}")
    return value


def _number(definition: dict[str, Any], key: str, default: int | float) -> int | float:
    value = definition.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DeploymentRuntimeError(f"compiled workflow definition has invalid {key}")
    return value


async def pin_workflow_runtime(
    session: AsyncSession, workflow_id: UUID
) -> PinnedWorkflowRuntime | None:
    """Return immutable evidence for a Solution workflow; None means `_repo`."""
    row = (
        await session.execute(
            select(Workflow, Solution)
            .outerjoin(Solution, Workflow.solution_id == Solution.id)
            .where(Workflow.id == workflow_id, Workflow.is_active.is_(True))
        )
    ).one_or_none()
    if row is None:
        raise DeploymentRuntimeError(f"workflow {workflow_id} is not executable")
    workflow, solution = row
    if workflow.solution_id is None:
        return None
    if solution is None or solution.status != "active":
        raise DeploymentRuntimeError("Solution is not active")
    if solution.active_deployment_id is None:
        raise DeploymentRuntimeError(
            "Solution has no active deployment; capture an initial deployment before execution"
        )

    deployment = await SolutionDeploymentRepository(session).get_runtime_closure(
        solution.active_deployment_id, solution.organization_id
    )
    if deployment is None or deployment.solution_id != solution.id:
        raise DeploymentRuntimeError("active deployment is missing or out of scope")
    if deployment.state not in {"active", "committed_unpushed"}:
        raise DeploymentRuntimeError("active deployment is not executable")
    _, resolution = validate_runtime_closure(
        deployment.compiled_manifest,
        deployment.resolution_map,
        deployment.dependencies,
        expected_manifest_hash=deployment.compiled_manifest_hash,
        expected_resolution_hash=deployment.resolution_map_hash,
    )
    try:
        entity = resolution.resolve_workflow_id(workflow.id)
    except KeyError as exc:
        raise DeploymentRuntimeError(
            f"workflow {workflow.id} is absent from active deployment"
        ) from exc
    definition = entity.definition
    source_ref = entity.source_ref or str(_required(definition, "path"))
    try:
        source = resolution.resolve_source(source_ref)
    except KeyError as exc:
        raise DeploymentRuntimeError(f"source {source_ref} is unresolved") from exc
    runtime_prefix = deployment.runtime_storage_prefix.rstrip("/") + "/"
    workflow_path = str(_required(definition, "path")).lstrip("/")
    if source.object_key != f"{runtime_prefix}{workflow_path}":
        raise DeploymentRuntimeError(
            "workflow source is outside deployment runtime prefix"
        )
    return PinnedWorkflowRuntime(
        workflow_id=workflow.id,
        solution_id=solution.id,
        deployment_id=deployment.id,
        bundle_hash=deployment.bundle_hash,
        compiled_manifest_hash=deployment.compiled_manifest_hash,
        git_commit_sha=deployment.git_commit_sha,
        runtime_storage_prefix=runtime_prefix,
        portable_ref=entity.portable_ref,
        name=str(_required(definition, "name")),
        function_name=str(_required(definition, "function_name")),
        path=workflow_path,
        source_hash=source.content_hash,
        timeout_seconds=int(_number(definition, "timeout_seconds", 1800)),
        time_saved=int(_number(definition, "time_saved", 0)),
        value=float(_number(definition, "value", 0)),
        execution_mode=str(definition.get("execution_mode") or "async"),
        workflow_type=str(definition.get("type") or "workflow"),
        cache_ttl_seconds=int(_number(definition, "cache_ttl_seconds", 0)),
        organization_id=(
            str(definition["organization_id"])
            if definition.get("organization_id")
            else None
        ),
        can_access_global_repo=bool(solution.global_repo_access),
    )
