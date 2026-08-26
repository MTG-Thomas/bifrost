"""Contracts for the unified execution operations policy registry."""

from dataclasses import replace

import pytest

from src.jobs.execution_policy import (
    CancellationMode,
    CompletionBoundary,
    ExecutionMechanism,
    ExecutionOperationsPolicy,
    IdempotencyRequirement,
    RetryProfile,
    WorkerLossBehavior,
    WorkloadClass,
    broker_execution_policies,
    validate_platform_job_definition,
)
from src.jobs.platform.registry import (
    get_execution_operations_policy,
    list_execution_operations_policies,
    list_platform_job_definitions,
)


EXPECTED_BROKER_CHANNELS = {
    "workflow-executions",
    "agent-runs",
    "agent-summarization",
    "agent-summarization-backfill",
    "agent-tuning-chat",
    "package-installations",
}


def test_registry_covers_broker_channels_and_platform_jobs() -> None:
    platform_job_types = {
        definition.job_type for definition in list_platform_job_definitions()
    }
    policies = list_execution_operations_policies()

    assert set(broker_execution_policies()) == EXPECTED_BROKER_CHANNELS
    assert {policy.identifier for policy in policies} == (
        EXPECTED_BROKER_CHANNELS | platform_job_types
    )
    assert [policy.identifier for policy in policies] == sorted(
        policy.identifier for policy in policies
    )


def test_workflow_policy_records_the_existing_dispatch_boundary() -> None:
    policy = get_execution_operations_policy("workflow-executions")

    assert policy is not None
    assert policy.mechanism == ExecutionMechanism.RABBITMQ_QUEUE
    assert policy.workload_class == WorkloadClass.INTERACTIVE_WORKFLOW
    assert policy.completion_boundary == CompletionBoundary.CHILD_DISPATCHED
    assert policy.idempotency == IdempotencyRequirement.REQUIRED
    assert policy.worker_loss == WorkerLossBehavior.DURABLE_RECOVERY
    assert policy.cancellation == CancellationMode.QUEUED_AND_RUNNING


def test_package_fanout_requires_per_worker_idempotency() -> None:
    policy = get_execution_operations_policy("package-installations")

    assert policy is not None
    assert policy.mechanism == ExecutionMechanism.RABBITMQ_FANOUT
    assert policy.idempotency == IdempotencyRequirement.LOCAL_WORKER_REQUIRED
    assert policy.worker_loss == WorkerLossBehavior.RECONCILE
    assert policy.replay_allowed is False


def test_retryable_policy_requires_idempotency() -> None:
    with pytest.raises(ValueError, match="requires idempotency"):
        ExecutionOperationsPolicy(
            identifier="unsafe",
            mechanism=ExecutionMechanism.RABBITMQ_QUEUE,
            workload_class=WorkloadClass.PLATFORM_BATCH,
            durable_authority="unsafe_jobs",
            completion_boundary=CompletionBoundary.DOMAIN_OUTCOME_DURABLE,
            idempotency=IdempotencyRequirement.NONE,
            worker_loss=WorkerLossBehavior.RETRY,
            retry_profile=RetryProfile.BROKER_STANDARD,
            cancellation=CancellationMode.NOT_SUPPORTED,
            timeout_policy="handler",
            memory_policy="container",
            replay_allowed=False,
        )


def test_platform_semantics_agree_with_runner_policy() -> None:
    for definition in list_platform_job_definitions():
        validate_platform_job_definition(definition)
        operations = definition.operations_policy

        assert operations.mechanism == ExecutionMechanism.POSTGRES_LEASE
        assert (operations.worker_loss == WorkerLossBehavior.RETRY) == (
            definition.policy.retry_on_runner_loss
        )
        assert (
            operations.cancellation == CancellationMode.QUEUED_AND_RUNNING
        ) == definition.policy.allow_running_cancellation


def test_platform_validation_rejects_runner_loss_drift() -> None:
    definition = list_platform_job_definitions()[0]
    with pytest.raises(ValueError, match="runner-loss policy disagrees"):
        replace(
            definition,
            operations_policy=replace(
                definition.operations_policy,
                worker_loss=(
                    WorkerLossBehavior.FAIL
                    if definition.policy.retry_on_runner_loss
                    else WorkerLossBehavior.RETRY
                ),
            ),
        )
