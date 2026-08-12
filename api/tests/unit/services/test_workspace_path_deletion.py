from unittest.mock import AsyncMock

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
