from pydantic import ValidationError
import pytest

from src.models.contracts.base import (
    ExecutionRetryFailure,
    ExecutionRetryPolicy,
)
from src.models.contracts.workflows import WorkflowUpdateRequest


def test_execution_retry_policy_defaults_off() -> None:
    policy = ExecutionRetryPolicy()

    assert policy.model_dump(mode="json") == {
        "version": "execution-retry/v1",
        "enabled": False,
        "max_attempts": 2,
        "retry_on": [],
    }


def test_execution_retry_policy_accepts_supported_failure_kinds() -> None:
    request = WorkflowUpdateRequest(
        retry_policy={
            "version": "execution-retry/v1",
            "enabled": True,
            "max_attempts": 3,
            "retry_on": ["worker_lost", "subprocess_crash"],
        }
    )

    assert request.retry_policy is not None
    assert request.retry_policy.retry_on == [
        ExecutionRetryFailure.WORKER_LOST,
        ExecutionRetryFailure.SUBPROCESS_CRASH,
    ]


@pytest.mark.parametrize(
    "override",
    [
        {"version": "execution-retry/v2"},
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"retry_on": ["python_exception"]},
    ],
)
def test_execution_retry_policy_rejects_unknown_or_unbounded_values(
    override: dict,
) -> None:
    with pytest.raises(ValidationError):
        ExecutionRetryPolicy(**override)
