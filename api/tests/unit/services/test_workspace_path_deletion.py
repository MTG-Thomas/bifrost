from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.services import workspace_path_deletion


@pytest.mark.asyncio
async def test_recursive_deletion_checkpoints_between_storage_mutations(
    monkeypatch,
) -> None:
    db = AsyncMock()
    repo = AsyncMock()
    repo.list = AsyncMock(
        side_effect=[
            ["apps/example/a.ts", "apps/example/nested/"],
            [],
        ]
    )
    storage = AsyncMock()
    monkeypatch.setattr(
        workspace_path_deletion, "FileStorageService", lambda _db: storage
    )
    checkpoint = AsyncMock()
    monkeypatch.setattr(
        workspace_path_deletion, "checkpoint_workspace_writer_lease", checkpoint
    )
    report = AsyncMock()

    deleted = await workspace_path_deletion.delete_workspace_path_recursively(
        db,
        "apps/example",
        repo=repo,
        report_progress=report,
    )

    assert deleted == 2
    storage.delete_file.assert_awaited_once_with(
        "apps/example/a.ts", skip_dirty_flag=True
    )
    assert repo.delete.await_args_list[0].args == ("apps/example/nested/",)
    assert repo.delete.await_args_list[-2].args == ("apps/example",)
    assert repo.delete.await_args_list[-1].args == ("apps/example/",)
    assert checkpoint.await_count == 6
    report.assert_any_await(2, 2)
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("path", ["", "/", "../secret", "apps/../../secret"])
def test_recursive_deletion_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError):
        workspace_path_deletion.normalize_workspace_delete_path(path)


@pytest.mark.asyncio
async def test_recursive_deletion_fails_when_prefix_never_drains(monkeypatch) -> None:
    db = AsyncMock()
    repo = AsyncMock()
    repo.list = AsyncMock(return_value=["apps/example/a.ts"])
    storage = AsyncMock()
    monkeypatch.setattr(
        workspace_path_deletion, "FileStorageService", lambda _db: storage
    )
    monkeypatch.setattr(
        workspace_path_deletion, "checkpoint_workspace_writer_lease", AsyncMock()
    )
    sleep = AsyncMock()
    monkeypatch.setattr(workspace_path_deletion.asyncio, "sleep", sleep)

    with pytest.raises(RuntimeError, match="did not drain prefix"):
        await workspace_path_deletion.delete_workspace_path_recursively(
            db, "apps/example", repo=repo
        )

    assert repo.list.await_count == 10
    assert sleep.await_count == 4


@pytest.mark.asyncio
async def test_enqueue_deletion_locks_deduplicates_and_survives_notification_failure(
    monkeypatch,
) -> None:
    db = AsyncMock()

    @asynccontextmanager
    async def nested():
        yield

    db.begin_nested = Mock(side_effect=nested)
    job = SimpleNamespace(id=uuid4(), notification_id=None)
    lock = AsyncMock()
    enqueue = AsyncMock(return_value=(job, False))
    notification = AsyncMock(side_effect=RuntimeError("notifications down"))
    publish = AsyncMock()
    monkeypatch.setattr(workspace_path_deletion, "lock_workspace_writer_gate", lock)
    monkeypatch.setattr("src.services.platform_jobs.enqueue_platform_job", enqueue)
    monkeypatch.setattr(
        "src.services.platform_jobs.ensure_platform_job_notification", notification
    )
    monkeypatch.setattr("src.services.platform_jobs.publish_platform_job_update", publish)

    result = await workspace_path_deletion.enqueue_workspace_path_deletion(
        db,
        "/apps/example/",
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="admin@example.test",
        requested_by_name="Admin",
    )

    assert result == (job, False)
    lock.assert_awaited_once_with(db)
    assert enqueue.await_args.kwargs["dedupe_key"] == "apps/example"
    assert enqueue.await_args.kwargs["resource_id"] == "apps/example"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(job)
    publish.assert_awaited_once_with(job)
