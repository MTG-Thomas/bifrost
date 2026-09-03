"""Policy decisions for retries after execution-engine failures."""

import os
from typing import Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.base import ExecutionRetryPolicy

RetryFailureKind = Literal["worker_lost", "subprocess_crash"]


def operator_max_attempts() -> int:
    """Return the deployment-wide safety ceiling, never retry permission."""
    try:
        return max(
            1,
            min(
                10,
                int(os.environ.get("BIFROST_WORKFLOW_EXECUTION_MAX_ATTEMPTS", "1")),
            ),
        )
    except ValueError:
        return 1


def disabled_retry_policy() -> dict:
    return ExecutionRetryPolicy().model_dump(mode="json")


def normalize_retry_policy(value: object) -> ExecutionRetryPolicy:
    """Validate stored policy, failing closed for missing or invalid values."""
    if not isinstance(value, dict):
        return ExecutionRetryPolicy()
    try:
        return ExecutionRetryPolicy.model_validate(value)
    except ValidationError:
        return ExecutionRetryPolicy()


def snapshot_retry_policy(value: object) -> dict:
    snapshot = normalize_retry_policy(value).model_dump(mode="json")
    snapshot["retry_on"] = sorted(set(snapshot["retry_on"]))
    return snapshot


async def workflow_retry_policy_snapshot(
    session: AsyncSession, workflow_id: UUID | None
) -> dict:
    if workflow_id is None:
        return disabled_retry_policy()
    from src.models.orm.workflows import Workflow

    policy = await session.scalar(
        select(Workflow.retry_policy).where(Workflow.id == workflow_id)
    )
    return snapshot_retry_policy(policy)


def should_retry_execution(
    policy_snapshot: object,
    failure_kind: RetryFailureKind,
    attempt_count: int,
    operator_max_attempts: int,
) -> bool:
    """Apply workflow permission and the operator's absolute attempt ceiling."""
    policy = normalize_retry_policy(policy_snapshot)
    maximum = min(policy.max_attempts, max(1, operator_max_attempts))
    return (
        policy.enabled
        and failure_kind in policy.retry_on
        and attempt_count < maximum
    )
