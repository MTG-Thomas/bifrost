from __future__ import annotations

import pytest

from src.models.contracts.workspace_file_impact import WorkspaceFileImpactRequest
from src.services.workspace_file_impact import WorkspaceFileImpactService


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
