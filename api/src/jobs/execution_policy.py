"""Declarative operational policy for every durable execution entry point.

This module describes *semantics*, not runner implementation. Numeric process
limits remain in ``PlatformJobPolicy`` and workflow settings; this registry is
the common operator-facing vocabulary used to validate retry, redelivery,
replay, cancellation, and authority boundaries across those implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterable, Mapping

if TYPE_CHECKING:
    from src.jobs.platform.base import PlatformJobDefinition


class ExecutionMechanism(StrEnum):
    RABBITMQ_QUEUE = "rabbitmq_queue"
    RABBITMQ_FANOUT = "rabbitmq_fanout"
    POSTGRES_LEASE = "postgres_lease"


class WorkloadClass(StrEnum):
    INTERACTIVE_WORKFLOW = "interactive_workflow"
    INTERACTIVE_AGENT = "interactive_agent"
    DERIVED_AI = "derived_ai"
    WORKER_MAINTENANCE = "worker_maintenance"
    PLATFORM_INTERACTIVE = "platform_interactive"
    PLATFORM_BATCH = "platform_batch"
    PLATFORM_MAINTENANCE = "platform_maintenance"


class CompletionBoundary(StrEnum):
    """Point after which the delivery or durable claim may be released."""

    CHILD_DISPATCHED = "child_dispatched"
    DOMAIN_OUTCOME_DURABLE = "domain_outcome_durable"
    LOCAL_WORKER_CONVERGED = "local_worker_converged"
    PLATFORM_JOB_TERMINAL_OR_WAITING = "platform_job_terminal_or_waiting"


class IdempotencyRequirement(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    LOCAL_WORKER_REQUIRED = "local_worker_required"


class WorkerLossBehavior(StrEnum):
    DURABLE_RECOVERY = "durable_recovery"
    RETRY = "retry"
    FAIL = "fail"
    RECONCILE = "reconcile"


class RetryProfile(StrEnum):
    NONE = "none"
    BROKER_STANDARD = "broker_standard"
    PLATFORM_RUNNER_LOSS = "platform_runner_loss"
    DOMAIN_CONTROLLED = "domain_controlled"


class CancellationMode(StrEnum):
    NOT_SUPPORTED = "not_supported"
    QUEUED_ONLY = "queued_only"
    QUEUED_AND_RUNNING = "queued_and_running"
    DOMAIN_CONTROLLED = "domain_controlled"


@dataclass(frozen=True)
class ExecutionOperationsPolicy:
    """Operator-visible semantics for one execution type or delivery channel."""

    identifier: str
    mechanism: ExecutionMechanism
    workload_class: WorkloadClass
    durable_authority: str
    completion_boundary: CompletionBoundary
    idempotency: IdempotencyRequirement
    worker_loss: WorkerLossBehavior
    retry_profile: RetryProfile
    cancellation: CancellationMode
    timeout_policy: str
    memory_policy: str
    replay_allowed: bool

    def __post_init__(self) -> None:
        if not self.identifier or not self.identifier.strip():
            raise ValueError("execution policy identifier must not be empty")
        if not self.durable_authority or not self.durable_authority.strip():
            raise ValueError(
                f"execution policy {self.identifier!r} requires a durable authority"
            )
        if not self.timeout_policy or not self.timeout_policy.strip():
            raise ValueError(
                f"execution policy {self.identifier!r} requires a timeout policy"
            )
        if not self.memory_policy or not self.memory_policy.strip():
            raise ValueError(
                f"execution policy {self.identifier!r} requires a memory policy"
            )
        if (
            self.mechanism == ExecutionMechanism.RABBITMQ_FANOUT
            and self.idempotency != IdempotencyRequirement.LOCAL_WORKER_REQUIRED
        ):
            raise ValueError(
                f"fanout policy {self.identifier!r} requires per-worker idempotency"
            )
        if self.replay_allowed and self.idempotency not in {
            IdempotencyRequirement.REQUIRED,
            IdempotencyRequirement.LOCAL_WORKER_REQUIRED,
        }:
            raise ValueError(
                f"replayable policy {self.identifier!r} requires idempotency"
            )
        if (
            self.retry_profile != RetryProfile.NONE
            or self.worker_loss
            in {
                WorkerLossBehavior.DURABLE_RECOVERY,
                WorkerLossBehavior.RETRY,
                WorkerLossBehavior.RECONCILE,
            }
        ) and self.idempotency == IdempotencyRequirement.NONE:
            raise ValueError(
                f"retryable policy {self.identifier!r} requires idempotency"
            )


def platform_job_operations_policy(
    job_type: str,
    *,
    workload_class: WorkloadClass,
    worker_loss: WorkerLossBehavior = WorkerLossBehavior.RETRY,
    cancellation: CancellationMode = CancellationMode.QUEUED_ONLY,
) -> ExecutionOperationsPolicy:
    """Build the semantic half of one explicit PlatformJob definition."""

    return ExecutionOperationsPolicy(
        identifier=job_type,
        mechanism=ExecutionMechanism.POSTGRES_LEASE,
        workload_class=workload_class,
        durable_authority="platform_jobs",
        completion_boundary=CompletionBoundary.PLATFORM_JOB_TERMINAL_OR_WAITING,
        idempotency=IdempotencyRequirement.REQUIRED,
        worker_loss=worker_loss,
        retry_profile=(
            RetryProfile.PLATFORM_RUNNER_LOSS
            if worker_loss == WorkerLossBehavior.RETRY
            else RetryProfile.NONE
        ),
        cancellation=cancellation,
        timeout_policy="platform_job_definition",
        memory_policy="platform_job_cgroup",
        replay_allowed=False,
    )


_BROKER_POLICIES: Mapping[str, ExecutionOperationsPolicy] = MappingProxyType(
    {
        "workflow-executions": ExecutionOperationsPolicy(
            identifier="workflow-executions",
            mechanism=ExecutionMechanism.RABBITMQ_QUEUE,
            workload_class=WorkloadClass.INTERACTIVE_WORKFLOW,
            durable_authority="executions",
            completion_boundary=CompletionBoundary.CHILD_DISPATCHED,
            idempotency=IdempotencyRequirement.REQUIRED,
            worker_loss=WorkerLossBehavior.DURABLE_RECOVERY,
            retry_profile=RetryProfile.BROKER_STANDARD,
            cancellation=CancellationMode.QUEUED_AND_RUNNING,
            timeout_policy="workflow_runtime_bound",
            memory_policy="workflow_process_pool_cgroup",
            replay_allowed=True,
        ),
        "agent-runs": ExecutionOperationsPolicy(
            identifier="agent-runs",
            mechanism=ExecutionMechanism.RABBITMQ_QUEUE,
            workload_class=WorkloadClass.INTERACTIVE_AGENT,
            durable_authority="agent_runs",
            completion_boundary=CompletionBoundary.DOMAIN_OUTCOME_DURABLE,
            idempotency=IdempotencyRequirement.REQUIRED,
            worker_loss=WorkerLossBehavior.DURABLE_RECOVERY,
            retry_profile=RetryProfile.BROKER_STANDARD,
            cancellation=CancellationMode.DOMAIN_CONTROLLED,
            timeout_policy="agent_run_budget_and_worker_timeout",
            memory_policy="worker_container",
            replay_allowed=True,
        ),
        "agent-summarization": ExecutionOperationsPolicy(
            identifier="agent-summarization",
            mechanism=ExecutionMechanism.RABBITMQ_QUEUE,
            workload_class=WorkloadClass.DERIVED_AI,
            durable_authority="agent_runs.summary_status",
            completion_boundary=CompletionBoundary.DOMAIN_OUTCOME_DURABLE,
            idempotency=IdempotencyRequirement.REQUIRED,
            worker_loss=WorkerLossBehavior.RETRY,
            retry_profile=RetryProfile.BROKER_STANDARD,
            cancellation=CancellationMode.NOT_SUPPORTED,
            timeout_policy="consumer_handler",
            memory_policy="worker_container",
            replay_allowed=True,
        ),
        "agent-summarization-backfill": ExecutionOperationsPolicy(
            identifier="agent-summarization-backfill",
            mechanism=ExecutionMechanism.RABBITMQ_QUEUE,
            workload_class=WorkloadClass.DERIVED_AI,
            durable_authority="platform_jobs_and_agent_runs.summary_status",
            completion_boundary=CompletionBoundary.DOMAIN_OUTCOME_DURABLE,
            idempotency=IdempotencyRequirement.REQUIRED,
            worker_loss=WorkerLossBehavior.RETRY,
            retry_profile=RetryProfile.BROKER_STANDARD,
            cancellation=CancellationMode.DOMAIN_CONTROLLED,
            timeout_policy="consumer_handler",
            memory_policy="worker_container",
            replay_allowed=True,
        ),
        "agent-tuning-chat": ExecutionOperationsPolicy(
            identifier="agent-tuning-chat",
            mechanism=ExecutionMechanism.RABBITMQ_QUEUE,
            workload_class=WorkloadClass.DERIVED_AI,
            durable_authority="agent_tuning_conversation",
            completion_boundary=CompletionBoundary.DOMAIN_OUTCOME_DURABLE,
            idempotency=IdempotencyRequirement.REQUIRED,
            worker_loss=WorkerLossBehavior.RETRY,
            retry_profile=RetryProfile.BROKER_STANDARD,
            cancellation=CancellationMode.NOT_SUPPORTED,
            timeout_policy="consumer_handler",
            memory_policy="worker_container",
            replay_allowed=True,
        ),
        "package-installations": ExecutionOperationsPolicy(
            identifier="package-installations",
            mechanism=ExecutionMechanism.RABBITMQ_FANOUT,
            workload_class=WorkloadClass.WORKER_MAINTENANCE,
            durable_authority="package_operation_progress",
            completion_boundary=CompletionBoundary.LOCAL_WORKER_CONVERGED,
            idempotency=IdempotencyRequirement.LOCAL_WORKER_REQUIRED,
            worker_loss=WorkerLossBehavior.RECONCILE,
            retry_profile=RetryProfile.DOMAIN_CONTROLLED,
            cancellation=CancellationMode.NOT_SUPPORTED,
            timeout_policy="package_operation",
            memory_policy="worker_container",
            replay_allowed=False,
        ),
    }
)


def broker_execution_policies() -> Mapping[str, ExecutionOperationsPolicy]:
    return _BROKER_POLICIES


def validate_platform_job_definition(definition: PlatformJobDefinition) -> None:
    """Reject drift between semantic and numeric PlatformJob policies."""

    operations = definition.operations_policy
    if operations.identifier != definition.job_type:
        raise ValueError(
            f"platform job {definition.job_type!r} operations policy uses "
            f"identifier {operations.identifier!r}"
        )
    if operations.mechanism != ExecutionMechanism.POSTGRES_LEASE:
        raise ValueError(
            f"platform job {definition.job_type!r} must use postgres_lease policy"
        )
    expects_runner_retry = operations.worker_loss == WorkerLossBehavior.RETRY
    if expects_runner_retry != definition.policy.retry_on_runner_loss:
        raise ValueError(
            f"platform job {definition.job_type!r} runner-loss policy disagrees "
            "with PlatformJobPolicy"
        )
    expects_running_cancel = (
        operations.cancellation == CancellationMode.QUEUED_AND_RUNNING
    )
    if expects_running_cancel != definition.policy.allow_running_cancellation:
        raise ValueError(
            f"platform job {definition.job_type!r} cancellation policy disagrees "
            "with PlatformJobPolicy"
        )


def all_execution_policies(
    platform_definitions: Iterable[PlatformJobDefinition],
) -> Mapping[str, ExecutionOperationsPolicy]:
    """Return one collision-free, validated view of all execution policies."""

    policies = dict(_BROKER_POLICIES)
    for definition in platform_definitions:
        validate_platform_job_definition(definition)
        identifier = definition.operations_policy.identifier
        if identifier in policies:
            raise ValueError(f"duplicate execution policy identifier {identifier!r}")
        policies[identifier] = definition.operations_policy
    return MappingProxyType(policies)


__all__ = [
    "CancellationMode",
    "CompletionBoundary",
    "ExecutionMechanism",
    "ExecutionOperationsPolicy",
    "IdempotencyRequirement",
    "RetryProfile",
    "WorkerLossBehavior",
    "WorkloadClass",
    "all_execution_policies",
    "broker_execution_policies",
    "platform_job_operations_policy",
    "validate_platform_job_definition",
]
