from src.models.contracts.base import ExecutionRetryPolicy
from src.services.execution.retry_policy import (
    normalize_retry_policy,
    should_retry_execution,
    snapshot_retry_policy,
)


def test_retry_policy_is_disabled_by_default() -> None:
    policy = ExecutionRetryPolicy()

    assert not policy.enabled
    assert policy.retry_on == []
    assert not should_retry_execution(policy.model_dump(), "worker_lost", 1, 10)


def test_retry_requires_workflow_permission_and_respects_both_limits() -> None:
    policy = {
        "version": "execution-retry/v1",
        "enabled": True,
        "max_attempts": 3,
        "retry_on": ["worker_lost"],
    }

    assert should_retry_execution(policy, "worker_lost", 1, 3)
    assert not should_retry_execution(policy, "subprocess_crash", 1, 3)
    assert not should_retry_execution(policy, "worker_lost", 2, 2)
    assert not should_retry_execution(policy, "worker_lost", 3, 10)


def test_invalid_stored_policy_fails_closed() -> None:
    policy = normalize_retry_policy(
        {
            "version": "execution-retry/v1",
            "enabled": True,
            "max_attempts": 50,
            "retry_on": ["worker_lost"],
        }
    )

    assert policy == ExecutionRetryPolicy()
    assert snapshot_retry_policy({"enabled": "garbage"}) == {
        "version": "execution-retry/v1",
        "enabled": False,
        "max_attempts": 2,
        "retry_on": [],
    }
