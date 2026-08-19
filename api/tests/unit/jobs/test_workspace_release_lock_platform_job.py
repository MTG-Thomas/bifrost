"""Registration, policy, and enqueue contract for Workspace release lock-in."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.workspace_release_lock import (
    WORKSPACE_RELEASE_LOCK_DEFINITION,
    enqueue_workspace_release_lock,
)


def test_workspace_release_lock_is_a_bounded_durable_job() -> None:
    definition = get_platform_job_definition("workspace.release.lock")

    assert definition is WORKSPACE_RELEASE_LOCK_DEFINITION
    assert definition.payload_version == 1
    assert definition.policy.max_attempts == 2
    assert definition.policy.max_concurrency == 1
    assert definition.policy.retry_on_runner_loss is True
    assert definition.policy.timeout_seconds == 15 * 60


@pytest.mark.asyncio
async def test_enqueue_uses_global_resource_fence_and_binds_release_digest(
    monkeypatch,
) -> None:
    release = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        activation_state="live",
        lock_state="not_queued",
        lock_in_job_id=None,
    )
    artifact = SimpleNamespace()
    job = SimpleNamespace(id=uuid4())
    enqueue = AsyncMock(return_value=(job, False))
    lock = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_lock.acquire_workspace_release_lock",
        lock,
    )
    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_lock.enqueue_platform_job", enqueue
    )
    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_lock.WorkspaceReleaseDescriptor.from_rows",
        lambda _release, _artifact: SimpleNamespace(release_id="sha256:" + "a" * 64),
    )

    class Database:
        async def flush(self):
            return None

    observed, reused = await enqueue_workspace_release_lock(
        Database(),
        release=release,
        artifact=artifact,
        requested_by_user_id=uuid4(),
        requested_by_email="operator@example.com",
        requested_by_name="Operator",
    )

    assert observed is job
    assert reused is False
    lock.assert_awaited_once_with(ANY, release.organization_id)
    kwargs = enqueue.await_args.kwargs
    assert kwargs["resource_lock_key"] == "workspace-release"
    assert kwargs["dedupe_key"] == f"{release.id}:sha256:{'a' * 64}"
    assert release.lock_state == "queued"
    assert release.lock_in_job_id == job.id
