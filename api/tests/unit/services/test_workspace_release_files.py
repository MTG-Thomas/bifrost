from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.workspace_release_files import (
    WorkspaceReleaseFileView,
    WorkspaceReleasePathGoverned,
    active_workspace_release_file_view,
    global_active_workspace_release_descriptor,
    reject_release_governed_paths,
)
from src.services.workspace_release_runtime import WorkspaceReleaseRuntimeError


class _Storage:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    async def read(self, path: str) -> bytes:
        return self.files[path]

    async def read_many(self, paths: list[str], *, concurrency: int = 32):
        del concurrency
        return {path: self.files[path] for path in paths}


def _view(files: dict[str, bytes]) -> WorkspaceReleaseFileView:
    release = SimpleNamespace(
        release_id="sha256:" + "a" * 64,
        source_hashes={
            path: hashlib.sha256(content).hexdigest() for path, content in files.items()
        },
        runtime_storage_prefix="immutable/",
    )
    return WorkspaceReleaseFileView.from_release(
        release,
        storage=_Storage(files),
    )


@pytest.mark.asyncio
async def test_global_workspace_has_no_organization_release_overlay() -> None:
    assert await active_workspace_release_file_view(SimpleNamespace(), None) is None


@pytest.mark.asyncio
async def test_immutable_view_ignores_repo_history_and_reads_release_bytes() -> None:
    live = b"VALUE = 'immutable-live'\n"
    view = _view({"modules/vendor.py": live})

    # A stale `_repo` or production-live history blob is deliberately not an
    # input to this view. The active immutable manifest remains authoritative.
    assert await view.read("modules/vendor.py") == live
    assert await view.list("modules/") == ["modules/vendor.py"]


@pytest.mark.asyncio
async def test_immutable_view_fails_closed_when_object_does_not_match_manifest() -> (
    None
):
    view = _view({"modules/vendor.py": b"VALUE = 'reviewed'\n"})
    view.storage.files["modules/vendor.py"] = b"VALUE = 'corrupt'\n"

    with pytest.raises(WorkspaceReleaseRuntimeError, match="do not match"):
        await view.read("modules/vendor.py")


@pytest.mark.asyncio
async def test_legacy_mutation_guard_names_promote_for_governed_path(
    monkeypatch,
) -> None:
    view = _view({"modules/vendor.py": b"VALUE = 1\n"})

    async def global_release(_session):
        return view.release

    monkeypatch.setattr(
        "src.services.workspace_release_files.global_active_workspace_release_descriptor",
        global_release,
    )
    acquire_lock = AsyncMock()
    monkeypatch.setattr(
        "src.services.workspace_release_projection.acquire_workspace_release_lock",
        acquire_lock,
    )

    with pytest.raises(WorkspaceReleasePathGoverned, match="use `bifrost promote`"):
        await reject_release_governed_paths(
            SimpleNamespace(),
            # The guard is platform-global: a mutation attempted from a
            # different org still cannot overwrite a governed shared path.
            uuid4(),
            ["modules/vendor.py"],
        )

    await reject_release_governed_paths(
        SimpleNamespace(),
        SimpleNamespace(),
        ["workflows/unpromoted.py"],
    )
    assert acquire_lock.await_count == 2


@pytest.mark.asyncio
async def test_global_live_descriptor_rejects_multiple_live_rows() -> None:
    class Result:
        def all(self):
            row = (SimpleNamespace(), SimpleNamespace())
            return [row, row]

    class Session:
        async def execute(self, _statement):
            return Result()

    with pytest.raises(
        WorkspaceReleaseRuntimeError,
        match="more than one global Live Workspace release",
    ):
        await global_active_workspace_release_descriptor(Session())
