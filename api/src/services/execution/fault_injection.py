"""Explicit, context-local failure checkpoints for deterministic tests.

There is deliberately no environment-variable or HTTP activation path. Tests
must install a plan in their own context, so production defaults are inert.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterator, Mapping


class FailurePoint(StrEnum):
    BROKER_HANDLER_START = "broker_handler_start"
    RETRY_PUBLISH = "retry_publish"
    POISON_PUBLISH = "poison_publish"
    WORKFLOW_ADMISSION = "workflow_admission"
    CHILD_FORK = "child_fork"
    PLATFORM_CLAIM = "platform_claim"
    SCHEDULE_PUBLISH = "schedule_publish"


class InjectedExecutionFailure(RuntimeError):
    pass


@dataclass
class FailurePlan:
    fail_on_hit: Mapping[FailurePoint, int]
    hits: dict[FailurePoint, int] = field(default_factory=dict)


_active_plan: ContextVar[FailurePlan | None] = ContextVar(
    "bifrost_execution_failure_plan", default=None
)


def execution_failure_checkpoint(point: FailurePoint) -> None:
    plan = _active_plan.get()
    if plan is None:
        return
    hit = plan.hits.get(point, 0) + 1
    plan.hits[point] = hit
    if hit == plan.fail_on_hit.get(point):
        raise InjectedExecutionFailure(f"injected execution failure at {point.value}")


@contextmanager
def inject_execution_failures(
    fail_on_hit: Mapping[FailurePoint, int],
) -> Iterator[FailurePlan]:
    if any(hit < 1 for hit in fail_on_hit.values()):
        raise ValueError("failure hit numbers must be positive")
    plan = FailurePlan(dict(fail_on_hit))
    token = _active_plan.set(plan)
    try:
        yield plan
    finally:
        _active_plan.reset(token)
