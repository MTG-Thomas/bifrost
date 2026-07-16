"""Compare-and-swap activation and rollback for immutable Solution deployments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from src.models.orm.solution_deployments import SolutionDeployment
from src.repositories.solution_deployments import SolutionDeploymentRepository


class DeploymentActivationHooks(Protocol):
    """External artifact/projection work required before pointer movement."""

    async def verify_finalized(self, deployment: SolutionDeployment) -> None: ...

    async def rebuild_projections(self, deployment: SolutionDeployment) -> None: ...


@dataclass(frozen=True)
class ActivationResult:
    deployment_id: UUID
    solution_id: UUID
    state: str
    previous_active_deployment_id: UUID | None
    active_deployment_id: UUID | None
    conflict: dict | None = None
    recovery: dict | None = None


class SolutionDeploymentActivationService:
    """Own state transitions and active-pointer CAS without touching execution paths."""

    def __init__(
        self,
        repository: SolutionDeploymentRepository,
        hooks: DeploymentActivationHooks,
    ):
        self.repository = repository
        self.hooks = hooks

    async def activate(
        self,
        deployment_id: UUID,
        organization_id: UUID | None,
        solution_id: UUID,
        *,
        expected_active_deployment_id: UUID | None,
    ) -> ActivationResult:
        deployment = await self._require_deployment(
            deployment_id, organization_id, solution_id
        )
        if deployment.base_deployment_id != expected_active_deployment_id:
            raise ValueError(
                "expected active deployment must match the draft base deployment"
            )
        return await self._move_pointer(
            deployment,
            organization_id,
            expected_active_deployment_id=expected_active_deployment_id,
            expected_target_state="ready",
            operation="activate",
        )

    async def rollback(
        self,
        target_deployment_id: UUID,
        organization_id: UUID | None,
        solution_id: UUID,
        *,
        expected_active_deployment_id: UUID,
    ) -> ActivationResult:
        deployment = await self._require_deployment(
            target_deployment_id, organization_id, solution_id
        )
        if deployment.id == expected_active_deployment_id:
            raise ValueError("rollback target is already active")
        return await self._move_pointer(
            deployment,
            organization_id,
            expected_active_deployment_id=expected_active_deployment_id,
            expected_target_state="superseded",
            operation="rollback",
        )

    async def _move_pointer(
        self,
        deployment: SolutionDeployment,
        organization_id: UUID | None,
        *,
        expected_active_deployment_id: UUID | None,
        expected_target_state: str,
        operation: str,
    ) -> ActivationResult:
        now = datetime.now(timezone.utc)
        deployment = await self.repository.transition(
            deployment.id,
            organization_id,
            expected_state=expected_target_state,
            new_state="activating",
        )

        try:
            await self.hooks.verify_finalized(deployment)
        except Exception as exc:  # noqa: BLE001 - adapters expose backend-specific failures
            recovery = {
                "operation": operation,
                "stage": "artifact_finalization",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "expected_active_deployment_id": str(expected_active_deployment_id)
                if expected_active_deployment_id
                else None,
                "target_deployment_id": str(deployment.id),
                "next_action": "verify immutable artifacts before retry",
            }
            await self.repository.transition(
                deployment.id,
                organization_id,
                expected_state="activating",
                new_state="recovery_required",
                failure_detail=recovery,
            )
            current = await self.repository.get_solution_active_deployment(
                deployment.solution_id, organization_id
            )
            return ActivationResult(
                deployment_id=deployment.id,
                solution_id=deployment.solution_id,
                state="recovery_required",
                previous_active_deployment_id=expected_active_deployment_id,
                active_deployment_id=current,
                recovery=recovery,
            )

        moved = await self.repository.compare_and_set_active_deployment(
            deployment.solution_id,
            organization_id,
            expected_active_deployment_id=expected_active_deployment_id,
            new_active_deployment_id=deployment.id,
        )
        if not moved:
            current = await self.repository.get_solution_active_deployment(
                deployment.solution_id, organization_id
            )
            conflict = {
                "operation": operation,
                "solution_id": str(deployment.solution_id),
                "target_deployment_id": str(deployment.id),
                "expected_active_deployment_id": str(expected_active_deployment_id)
                if expected_active_deployment_id
                else None,
                "actual_active_deployment_id": str(current) if current else None,
            }
            await self.repository.transition(
                deployment.id,
                organization_id,
                expected_state="activating",
                new_state="conflicted",
                failure_detail=conflict,
            )
            return ActivationResult(
                deployment_id=deployment.id,
                solution_id=deployment.solution_id,
                state="conflicted",
                previous_active_deployment_id=expected_active_deployment_id,
                active_deployment_id=current,
                conflict=conflict,
            )

        try:
            async with self.repository.projection_savepoint():
                await self.hooks.rebuild_projections(deployment)
            if expected_active_deployment_id is not None:
                previous = await self.repository.get_runtime_closure(
                    expected_active_deployment_id, organization_id
                )
                if previous is not None and previous.state in {
                    "active",
                    "committed_unpushed",
                }:
                    await self.repository.transition(
                        previous.id,
                        organization_id,
                        expected_state=previous.state,
                        new_state="superseded",
                        superseded_at=now,
                    )
            await self.repository.transition(
                deployment.id,
                organization_id,
                expected_state="activating",
                new_state="active",
                activated_at=now,
            )
        except Exception as exc:  # noqa: BLE001 - persist cross-system recovery evidence
            recovery = {
                "operation": operation,
                "stage": "projection_or_pointer_finalization",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "expected_active_deployment_id": str(expected_active_deployment_id)
                if expected_active_deployment_id
                else None,
                "target_deployment_id": str(deployment.id),
                "pointer_moved": True,
                "next_action": "repair projections and deployment states before enabling execution",
            }
            await self.repository.transition(
                deployment.id,
                organization_id,
                expected_state="activating",
                new_state="recovery_required",
                failure_detail=recovery,
            )
            return ActivationResult(
                deployment_id=deployment.id,
                solution_id=deployment.solution_id,
                state="recovery_required",
                previous_active_deployment_id=expected_active_deployment_id,
                active_deployment_id=deployment.id,
                recovery=recovery,
            )
        return ActivationResult(
            deployment_id=deployment.id,
            solution_id=deployment.solution_id,
            state="active",
            previous_active_deployment_id=expected_active_deployment_id,
            active_deployment_id=deployment.id,
        )

    async def _require_deployment(
        self, deployment_id: UUID, organization_id: UUID | None, solution_id: UUID
    ) -> SolutionDeployment:
        deployment = await self.repository.get_runtime_closure(
            deployment_id, organization_id, solution_id
        )
        if deployment is None:
            raise ValueError("deployment missing or out of scope")
        return deployment
