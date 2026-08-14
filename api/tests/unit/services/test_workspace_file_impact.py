from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from src.models.contracts.workspace_file_impact import WorkspaceFileImpactRequest
from src.services.workspace_file_impact import (
    WorkspaceFileImpactConflict,
    WorkspaceFileImpactInvalid,
    WorkspaceFileImpactService,
)


class _Repo:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    async def list(self):
        return list(self.files)

    async def read(self, path: str):
        return self.files[path]


@pytest.mark.asyncio
async def test_preview_binds_proposed_bytes_and_transitive_reverse_closure() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "helpers/shared.py": b"VALUE = 1\n",
                "features/vendor/report.py": (
                    b"from helpers.shared import VALUE\n\ndef report(): return VALUE\n"
                ),
                "features/vendor/daily.py": (
                    b"from features.vendor.report import report\n"
                    b"\ndef daily(): return report()\n"
                ),
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="helpers/shared.py",
            content="VALUE = 2\n",
        )
    )

    assert result.ready_to_write is True
    assert result.changed is True
    assert [item.path for item in result.reverse_dependencies] == [
        "features/vendor/report.py",
        "features/vendor/daily.py",
    ]
    assert [item.depth for item in result.reverse_dependencies] == [1, 2]
    assert result.impacted_paths == [
        "features/vendor/daily.py",
        "features/vendor/report.py",
        "helpers/shared.py",
    ]
    assert result.candidate_id.startswith("sha256:")


@pytest.mark.asyncio
async def test_preview_ignores_unrelated_legacy_python_module_collisions() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "api/bifrost.py": b"VALUE = 'legacy module'\n",
                "api/bifrost/__init__.py": b"VALUE = 'legacy package'\n",
                "helpers/shared.py": b"VALUE = 1\n",
                "features/vendor/report.py": b"from helpers.shared import VALUE\n",
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="helpers/shared.py",
            content="VALUE = 2\n",
        )
    )

    assert result.ready_to_write is True
    assert [item.path for item in result.reverse_dependencies] == [
        "features/vendor/report.py"
    ]


@pytest.mark.asyncio
async def test_preview_reports_proposed_syntax_before_unrelated_legacy_paths() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "api/bifrost.py": b"VALUE = 'legacy module'\n",
                "api/bifrost/__init__.py": b"VALUE = 'legacy package'\n",
                "helpers/shared.py": b"VALUE = 1\n",
            }
        )
    )

    request = WorkspaceFileImpactRequest(
        path="helpers/shared.py",
        content="def broken(:\n",
    )

    with pytest.raises(WorkspaceFileImpactInvalid, match="cannot parse helpers/shared.py"):
        await service.preview(request)


@pytest.mark.asyncio
async def test_preview_rejects_non_authored_python_root() -> None:
    service = WorkspaceFileImpactService(
        _Repo({"api/bifrost.py": b"VALUE = 'legacy module'\n"})
    )

    request = WorkspaceFileImpactRequest(path="api/bifrost.py")

    with pytest.raises(WorkspaceFileImpactInvalid, match="authored roots only"):
        await service.preview(request)


@pytest.mark.asyncio
async def test_preview_blocks_unresolved_import_in_affected_consumer() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "helpers/shared.py": b"VALUE = 1\n",
                "features/vendor/report.py": (
                    b"from helpers.shared import VALUE\n"
                    b"from modules.missing import OTHER\n"
                ),
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="helpers/shared.py",
            content="VALUE = 2\n",
        )
    )

    assert result.ready_to_write is False
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("unresolved_repo_import", "features/vendor/report.py"),
        ("reverse_dependents_present", "helpers/shared.py"),
    ]


@pytest.mark.asyncio
async def test_candidate_changes_when_unrelated_workspace_snapshot_changes() -> None:
    repo = _Repo(
        {
            "helpers/shared.py": b"VALUE = 1\n",
            "features/other.py": b"VALUE = 1\n",
        }
    )
    service = WorkspaceFileImpactService(repo)
    request = WorkspaceFileImpactRequest(
        path="helpers/shared.py",
        content="VALUE = 2\n",
    )

    first = await service.preview(request)
    repo.files["features/other.py"] = b"VALUE = 2\n"
    second = await service.preview(request)

    assert first.candidate_id != second.candidate_id
    assert first.snapshot_id != second.snapshot_id


@pytest.mark.asyncio
async def test_preview_blocks_computed_workflow_reference() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "workflows/report.py": (
                    b"from bifrost import workflows\n"
                    b"async def report(workflow_id):\n"
                    b"    return await workflows.execute(workflow_id, {})\n"
                )
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="workflows/report.py",
            content=(
                "from bifrost import workflows\n"
                "async def report(workflow_id):\n"
                "    return await workflows.execute(workflow_id, {})\n"
            ),
        )
    )

    assert result.ready_to_write is False
    assert [item.code for item in result.diagnostics] == [
        "dynamic_workflow_reference_unresolved",
        "proposed_bytes_unchanged",
    ]


@pytest.mark.asyncio
async def test_guarded_write_rejects_stale_candidate_before_write(
    monkeypatch,
) -> None:
    calls: list[object] = []

    @asynccontextmanager
    async def barrier(**kwargs):
        calls.append(kwargs)
        yield

    async def write() -> None:
        calls.append("write")

    monkeypatch.setattr("src.core.module_cache.workspace_source_update", barrier)
    service = WorkspaceFileImpactService(_Repo({"workflows/report.py": b"VALUE = 1\n"}))
    request = WorkspaceFileImpactRequest(
        path="workflows/report.py",
        content="VALUE = 2\n",
    )

    with pytest.raises(WorkspaceFileImpactConflict) as exc_info:
        await service.guarded_write(
            request,
            "sha256:" + "0" * 64,
            write,
        )

    assert exc_info.value.path == "workflows/report.py"
    assert calls == [
        {
            "reason": "checked_workspace_file_write",
            "changed_paths": ["workflows/report.py"],
        }
    ]
