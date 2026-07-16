"""Application service behind the minimal SolutionDeployment HTTP API."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solution_deployments import SolutionDeploymentCreate
from src.models.orm.solution_deployments import (
    SolutionDeployment,
    SolutionDeploymentDependency,
)
from src.models.orm.solutions import Solution
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import canonical_json, sha256_digest
from src.services.solutions.deployment_storage import (
    DeploymentArtifactIntegrityError,
    SolutionDeploymentStorage,
    deployment_source_artifact_key,
    deployment_runtime_prefix,
)


class DeploymentRegistrationConflict(ValueError):
    """A deployment identifier already names a different immutable closure."""


class SolutionDeploymentAPIService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = SolutionDeploymentRepository(session)

    async def create_ready_draft(
        self, solution_id: UUID, created_by: UUID, body: SolutionDeploymentCreate
    ) -> SolutionDeployment:
        solution = await self.session.scalar(
            select(Solution).where(Solution.id == solution_id)
        )
        if solution is None:
            raise LookupError("Solution not found")
        manifest = body.compiled_manifest
        if manifest.solution_id != solution_id:
            raise ValueError("manifest solution_id does not match route")
        expected_source = deployment_source_artifact_key(
            solution_id, manifest.deployment_id
        )
        expected_runtime = deployment_runtime_prefix(
            solution_id, manifest.deployment_id
        )
        if manifest.source.artifact_key != expected_source:
            raise ValueError("source artifact must use the canonical deployment key")
        if manifest.source.runtime_prefix != expected_runtime:
            raise ValueError("runtime prefix must use the canonical deployment key")

        resolution_hash = sha256_digest(canonical_json(body.resolution_map))
        manifest_hash = manifest.content_hash()
        existing = await self.session.get(SolutionDeployment, manifest.deployment_id)
        if existing is not None:
            if (
                existing.solution_id == solution_id
                and existing.compiled_manifest_hash == manifest_hash
                and existing.resolution_map_hash == resolution_hash
            ):
                return existing
            raise DeploymentRegistrationConflict(
                "deployment id already names a different immutable closure"
            )
        dependencies = [
            SolutionDeploymentDependency(
                deployment_id=manifest.deployment_id,
                dependency_solution_id=edge.solution_id,
                dependency_deployment_id=edge.deployment_id,
                declared_constraint=edge.declared_constraint,
                resolved_bundle_hash=edge.bundle_hash,
            )
            for edge in manifest.dependencies.values()
        ]
        deployment = SolutionDeployment(
            id=manifest.deployment_id,
            organization_id=solution.organization_id,
            solution_id=solution_id,
            parent_deployment_id=body.parent_deployment_id,
            base_deployment_id=body.base_deployment_id,
            # The body is a complete, hash-validated immutable draft. `ready`
            # means it may enter the existing CAS activation seam.
            state="ready",
            declared_version=body.declared_version,
            bundle_hash=manifest.bundle_hash,
            compiled_manifest=manifest.model_dump(mode="json", exclude_none=True),
            compiled_manifest_hash=manifest_hash,
            resolution_map=body.resolution_map.model_dump(
                mode="json", exclude_none=True
            ),
            resolution_map_hash=resolution_hash,
            source_artifact_key=expected_source,
            runtime_storage_prefix=expected_runtime,
            git_repository=body.git_repository,
            git_ref=body.git_ref,
            git_commit_sha=body.git_commit_sha,
            created_by=created_by,
            codex_worker_id=body.codex_worker_id,
            dependencies=dependencies,
        )
        await self.repository.create(deployment)
        # The source/runtime objects are reference-only inputs. The canonical
        # manifest itself is finalized server-side through create-only storage.
        try:
            await SolutionDeploymentStorage(
                solution_id, manifest.deployment_id
            ).write_compiled_manifest(manifest.canonical_bytes())
        except DeploymentArtifactIntegrityError as exc:
            raise DeploymentRegistrationConflict(
                "manifest storage already exists; inspect the deployment before retrying"
            ) from exc
        return deployment
