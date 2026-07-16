"""Narrow, scope-safe persistence for immutable deployment closures."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.services.solutions.deployment_manifest import validate_runtime_closure


class InvalidDeploymentTransition(ValueError):
    pass


class _Unset:
    pass


_UNSET = _Unset()


_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"building", "aborted"}),
    "building": frozenset({"validated", "failed", "aborted"}),
    "validated": frozenset({"ready", "aborted"}),
    "ready": frozenset({"activating", "aborted"}),
    "activating": frozenset({"active", "conflicted", "failed", "recovery_required"}),
    "active": frozenset({"superseded", "committed_unpushed", "recovery_required"}),
    "committed_unpushed": frozenset({"active", "superseded", "recovery_required"}),
}


class SolutionDeploymentRepository:
    """Create and transition deployments without exposing generic CRUD escape hatches."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _scope_clause(organization_id: UUID | None):
        column = SolutionDeployment.organization_id
        return column.is_(None) if organization_id is None else column == organization_id

    async def create(self, deployment: SolutionDeployment) -> SolutionDeployment:
        solution_org = await self.session.scalar(
            select(Solution.organization_id).where(Solution.id == deployment.solution_id)
        )
        if solution_org != deployment.organization_id:
            raise ValueError("deployment scope must exactly match its Solution install")
        validate_runtime_closure(
            deployment.compiled_manifest,
            deployment.resolution_map,
            deployment.dependencies,
            expected_manifest_hash=deployment.compiled_manifest_hash,
            expected_resolution_hash=deployment.resolution_map_hash,
        )
        self.session.add(deployment)
        await self.session.flush()
        return deployment

    async def get_runtime_closure(
        self, deployment_id: UUID, organization_id: UUID | None
    ) -> SolutionDeployment | None:
        result = await self.session.execute(
            select(SolutionDeployment)
            .options(selectinload(SolutionDeployment.dependencies))
            .where(
                SolutionDeployment.id == deployment_id,
                self._scope_clause(organization_id),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_runtime(
        self, deployment_id: UUID
    ) -> SolutionDeployment | None:
        """Internal immutable-runtime lookup; authorization happened at pin creation."""
        result = await self.session.execute(
            select(SolutionDeployment)
            .options(selectinload(SolutionDeployment.dependencies))
            .where(SolutionDeployment.id == deployment_id)
        )
        return result.scalar_one_or_none()

    async def transition(
        self,
        deployment_id: UUID,
        organization_id: UUID | None,
        *,
        expected_state: str,
        new_state: str,
        validated_at: datetime | None | _Unset = _UNSET,
        activated_at: datetime | None | _Unset = _UNSET,
        superseded_at: datetime | None | _Unset = _UNSET,
        validation_result: dict | None | _Unset = _UNSET,
        failure_detail: dict | None | _Unset = _UNSET,
        git_push_state: str | None | _Unset = _UNSET,
    ) -> SolutionDeployment:
        if new_state not in _TRANSITIONS.get(expected_state, frozenset()):
            raise InvalidDeploymentTransition(f"{expected_state} -> {new_state}")
        values: dict[str, object] = {"state": new_state}
        optional_values = {
            "validated_at": validated_at,
            "activated_at": activated_at,
            "superseded_at": superseded_at,
            "validation_result": validation_result,
            "failure_detail": failure_detail,
            "git_push_state": git_push_state,
        }
        values.update(
            {key: value for key, value in optional_values.items() if value is not _UNSET}
        )
        result = await self.session.execute(
            update(SolutionDeployment)
            .where(
                SolutionDeployment.id == deployment_id,
                self._scope_clause(organization_id),
                SolutionDeployment.state == expected_state,
            )
            .values(**values)
            .returning(SolutionDeployment)
        )
        deployment = result.scalar_one_or_none()
        if deployment is None:
            raise InvalidDeploymentTransition("deployment missing, out of scope, or state changed")
        return deployment
