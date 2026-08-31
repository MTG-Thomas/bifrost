"""Deterministic failure matrix for execution infrastructure boundaries."""

import asyncio
from pathlib import Path

import pytest

from src.services.execution.fault_injection import (
    FailurePoint,
    InjectedExecutionFailure,
    execution_failure_checkpoint,
    inject_execution_failures,
)


def test_failure_occurs_on_exact_configured_hit_and_then_stops() -> None:
    with inject_execution_failures({FailurePoint.RETRY_PUBLISH: 2}) as plan:
        execution_failure_checkpoint(FailurePoint.RETRY_PUBLISH)
        with pytest.raises(InjectedExecutionFailure, match="retry_publish"):
            execution_failure_checkpoint(FailurePoint.RETRY_PUBLISH)
        execution_failure_checkpoint(FailurePoint.RETRY_PUBLISH)
        assert plan.hits[FailurePoint.RETRY_PUBLISH] == 3

    execution_failure_checkpoint(FailurePoint.RETRY_PUBLISH)


@pytest.mark.asyncio
async def test_failure_plans_are_task_local() -> None:
    async def armed() -> str:
        with inject_execution_failures({FailurePoint.CHILD_FORK: 1}):
            await asyncio.sleep(0)
            with pytest.raises(InjectedExecutionFailure):
                execution_failure_checkpoint(FailurePoint.CHILD_FORK)
        return "armed"

    async def unarmed() -> str:
        await asyncio.sleep(0)
        execution_failure_checkpoint(FailurePoint.CHILD_FORK)
        return "unarmed"

    assert await asyncio.gather(armed(), unarmed()) == ["armed", "unarmed"]


def test_invalid_zero_hit_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        with inject_execution_failures({FailurePoint.PLATFORM_CLAIM: 0}):
            pass


def test_every_failure_point_is_wired_to_a_runtime_boundary() -> None:
    root = Path(__file__).parents[3] / "src"
    runtime = "\n".join(path.read_text() for path in root.rglob("*.py"))
    for point in FailurePoint:
        assert runtime.count(f"FailurePoint.{point.name}") >= 1
