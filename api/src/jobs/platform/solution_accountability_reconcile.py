"""Durable recovery of Solution deployment accountability evidence."""

from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.execution_policy import WorkloadClass, platform_job_operations_policy
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.services.solutions.accountability_recovery import (
    AccountabilityRecoveryConflict,
    recover_solution_deploy_accountability,
)


class SolutionAccountabilityReconcilePayload(BaseModel):
    solution_id: UUID
    deploy_job_id: UUID


async def run_solution_accountability_reconcile(
    context: PlatformJobContext,
    payload: SolutionAccountabilityReconcilePayload,
) -> dict:
    await context.report("Verifying installed Solution deployment evidence", percent=5)
    try:
        async with get_db_context() as db:
            result = await recover_solution_deploy_accountability(
                db,
                solution_id=payload.solution_id,
                deploy_job_id=payload.deploy_job_id,
            )
    except (AccountabilityRecoveryConflict, KeyError) as exc:
        raise PlatformJobFailure("solution_accountability_conflict", str(exc)) from exc
    await context.report("Solution deployment accountability reconciled", percent=100)
    return result


SOLUTION_ACCOUNTABILITY_RECONCILE_DEFINITION = PlatformJobDefinition(
    job_type="solution.deploy.reconcile",
    payload_version=1,
    payload_model=SolutionAccountabilityReconcilePayload,
    handler=run_solution_accountability_reconcile,
    policy=PlatformJobPolicy(
        timeout_seconds=20 * 60, max_attempts=2, min_memory_headroom_mb=512
    ),
    operations_policy=platform_job_operations_policy(
        "solution.deploy.reconcile",
        workload_class=WorkloadClass.PLATFORM_INTERACTIVE,
    ),
)
