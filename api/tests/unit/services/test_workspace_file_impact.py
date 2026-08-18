from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.models.contracts.workspace_file_impact import WorkspaceFileImpactRequest
from src.services.workspace_file_impact import (
    LARGE_IMPACT_GRAPH_FILES,
    _WorkspaceSnapshotCache,
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


class _CountingRepo(_Repo):
    def __init__(self, files: dict[str, bytes]):
        super().__init__(files)
        self.read_count = 0

    async def read(self, path: str):
        self.read_count += 1
        await asyncio.sleep(0)
        return await super().read(path)


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
    assert result.traversal_complete is True
    assert result.analyzed_path_count == 3
    assert result.blocking_diagnostic_count == 0


@pytest.mark.asyncio
async def test_preview_new_file_falls_back_to_complete_analysis() -> None:
    service = WorkspaceFileImpactService(_Repo({"helpers/shared.py": b"VALUE = 1\n"}))

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="workflows/new_report.py",
            content="from helpers.shared import VALUE\n",
        )
    )

    assert result.ready_to_write is True
    assert result.current_sha256 is None
    assert [item.path for item in result.forward_dependencies] == ["helpers/shared.py"]


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
async def test_preview_ignores_unrelated_ambiguous_workflow_reference() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "helpers/shared.py": b"VALUE = 1\n",
                "workflows/one.py": (
                    b"@workflow(name='Child')\ndef one():\n    return 1\n"
                ),
                "workflows/two.py": (
                    b"@workflow(name='Child')\ndef two():\n    return 2\n"
                ),
                "workflows/parent.py": (
                    b"async def parent():\n    return {'workflow_name': 'Child'}\n"
                ),
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(path="helpers/shared.py", content="VALUE = 2\n")
    )

    assert result.ready_to_write is True
    assert result.diagnostics == []


@pytest.mark.asyncio
async def test_preview_blocks_relevant_ambiguous_workflow_reference() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "workflows/one.py": (
                    b"@workflow(name='Child')\ndef one():\n    return 1\n"
                ),
                "workflows/two.py": (
                    b"@workflow(name='Child')\ndef two():\n    return 2\n"
                ),
                "workflows/parent.py": (
                    b"async def parent():\n    return {'workflow_name': 'Child'}\n"
                ),
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="workflows/one.py",
            content="@workflow(name='Child')\ndef one():\n    return 3\n",
        )
    )

    assert result.ready_to_write is False
    assert [item.path for item in result.reverse_dependencies] == [
        "workflows/parent.py"
    ]
    assert [
        (item.code, item.severity, item.path) for item in result.diagnostics
    ] == [
        ("ambiguous_workflow_reference", "blocker", "workflows/parent.py"),
        ("reverse_dependents_present", "info", "workflows/one.py"),
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

    with pytest.raises(
        WorkspaceFileImpactInvalid, match="cannot parse helpers/shared.py"
    ):
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
async def test_preview_reports_preexisting_unrelated_consumer_issue_without_blocking() -> (
    None
):
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

    assert result.ready_to_write is True
    assert [(item.code, item.severity, item.path) for item in result.diagnostics] == [
        (
            "unresolved_repo_import",
            "warning",
            "features/vendor/report.py",
        ),
        ("reverse_dependents_present", "info", "helpers/shared.py"),
    ]
    assert result.diagnostics[0].message.startswith(
        "pre-existing connected-file issue:"
    )


@pytest.mark.asyncio
async def test_preview_blocks_new_unresolved_import_in_changed_source() -> None:
    service = WorkspaceFileImpactService(_Repo({"helpers/shared.py": b"VALUE = 1\n"}))

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="helpers/shared.py",
            content="from modules.missing import OTHER\nVALUE = OTHER\n",
        )
    )

    assert result.ready_to_write is False
    assert [(item.code, item.severity, item.path) for item in result.diagnostics] == [
        ("unresolved_repo_import", "blocker", "helpers/shared.py")
    ]


@pytest.mark.asyncio
async def test_checked_write_traverses_large_graph_without_truncating_or_blocking(
    monkeypatch,
) -> None:
    consumer_count = LARGE_IMPACT_GRAPH_FILES + 50
    files = {"helpers/shared.py": b"VALUE = 1\n"}
    files.update(
        {
            f"workflows/consumer_{index:03d}.py": (
                b"from helpers.shared import VALUE\n"
                + f"def consumer_{index:03d}(): return VALUE\n".encode()
            )
            for index in range(consumer_count)
        }
    )
    service = WorkspaceFileImpactService(_Repo(files))
    wrote = False

    @asynccontextmanager
    async def barrier(**kwargs):
        assert kwargs == {
            "reason": "checked_workspace_file_write",
            "changed_paths": ["helpers/shared.py"],
        }
        yield

    async def write() -> None:
        nonlocal wrote
        wrote = True

    monkeypatch.setattr("src.core.module_cache.workspace_source_update", barrier)

    request = WorkspaceFileImpactRequest(
        path="helpers/shared.py",
        content="VALUE = 2\n",
    )
    result = await service.preview(request)

    assert result.traversal_complete is True
    assert result.analyzed_path_count == consumer_count + 1
    assert len(result.reverse_dependencies) == consumer_count
    assert len(result.edges) == consumer_count
    assert result.blocking_diagnostic_count == 0
    assert result.ready_to_write is True
    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("large_impact_graph", "info"),
        ("reverse_dependents_present", "info"),
    ]
    consumed = await service.guarded_write(request, result.candidate_id, write)
    assert consumed.candidate_id == result.candidate_id
    assert wrote is True


@pytest.mark.asyncio
async def test_preview_blocks_removing_symbol_used_by_reverse_dependent() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "modules/vendor.py": (
                    b"class Client:\n    pass\n\ndef retained(): return 1\n"
                ),
                "workflows/direct.py": (
                    b"from modules.vendor import Client\n"
                    b"def direct(): return Client()\n"
                ),
                "workflows/alias.py": (
                    b"import modules.vendor as vendor_module\n"
                    b"def alias(): return vendor_module.Client()\n"
                ),
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="modules/vendor.py",
            content="def retained(): return 1\n",
        )
    )

    assert result.ready_to_write is False
    assert result.blocking_diagnostic_count == 2
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("reverse_dependents_present", "modules/vendor.py"),
        ("removed_imported_symbol", "workflows/alias.py"),
        ("removed_imported_symbol", "workflows/direct.py"),
    ]


@pytest.mark.asyncio
async def test_preview_checks_relative_imports_and_conditional_module_exports() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "features/vendor/_shared.py": (
                    b"try:\n"
                    b"    def available(): return 1\n"
                    b"except Exception:\n"
                    b"    available = None\n"
                ),
                "features/vendor/report.py": (
                    b"from ._shared import available\n"
                    b"def report(): return available()\n"
                ),
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="features/vendor/_shared.py",
            content="VALUE = 1\n",
        )
    )

    assert result.ready_to_write is False
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("reverse_dependents_present", "features/vendor/_shared.py"),
        ("removed_imported_symbol", "features/vendor/report.py"),
    ]


@pytest.mark.asyncio
async def test_preview_blocks_star_import_when_module_surface_shrinks() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "helpers/shared.py": b"ONE = 1\nTWO = 2\n",
                "workflows/report.py": (
                    b"from helpers.shared import *\ndef report(): return ONE + TWO\n"
                ),
            }
        )
    )

    result = await service.preview(
        WorkspaceFileImpactRequest(
            path="helpers/shared.py",
            content="ONE = 1\n",
        )
    )

    assert result.ready_to_write is False
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("reverse_dependents_present", "helpers/shared.py"),
        ("star_import_contract_unresolved", "workflows/report.py"),
    ]


@pytest.mark.asyncio
async def test_preview_blocks_changed_dynamic_export_contract_in_use() -> None:
    service = WorkspaceFileImpactService(
        _Repo(
            {
                "modules/vendor.py": (
                    b"VALUE = 1\ndef __getattr__(name): return {'dynamic': 1}[name]\n"
                ),
                "workflows/report.py": (
                    b"import modules.vendor as vendor\n"
                    b"def report(): return vendor.dynamic\n"
                ),
            }
        )
    )

    unrelated = await service.preview(
        WorkspaceFileImpactRequest(
            path="modules/vendor.py",
            content=("VALUE = 2\ndef __getattr__(name): return {'dynamic': 1}[name]\n"),
        )
    )
    changed_contract = await service.preview(
        WorkspaceFileImpactRequest(
            path="modules/vendor.py",
            content=(
                "VALUE = 1\ndef __getattr__(name): return {'replacement': 1}[name]\n"
            ),
        )
    )

    assert unrelated.ready_to_write is True
    assert changed_contract.ready_to_write is False
    assert [(item.code, item.path) for item in changed_contract.diagnostics] == [
        ("reverse_dependents_present", "modules/vendor.py"),
        ("dynamic_module_export_contract", "workflows/report.py"),
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
async def test_snapshot_cache_single_flights_and_rotates_with_workspace_generation(
    monkeypatch,
) -> None:
    generation = "generation-one"

    async def current_generation() -> str:
        return generation

    monkeypatch.setattr(
        "src.core.module_cache.get_workspace_generation",
        current_generation,
    )
    repo = _CountingRepo({"helpers/shared.py": b"VALUE = 1\n"})
    service = WorkspaceFileImpactService(
        repo,
        snapshot_cache=_WorkspaceSnapshotCache(),
    )
    request = WorkspaceFileImpactRequest(path="helpers/shared.py")

    first, second = await asyncio.gather(
        service.preview(request),
        service.preview(request),
    )

    assert repo.read_count == 1
    assert first.snapshot_id == second.snapshot_id

    repo.files["helpers/shared.py"] = b"VALUE = 2\n"
    generation = "generation-two"
    third = await service.preview(request)

    assert repo.read_count == 2
    assert third.snapshot_id != first.snapshot_id


@pytest.mark.asyncio
async def test_snapshot_cache_rejects_updating_workspace_generation(
    monkeypatch,
) -> None:
    async def updating_generation() -> str:
        return "updating:lease"

    monkeypatch.setattr(
        "src.core.module_cache.get_workspace_generation",
        updating_generation,
    )
    repo = _CountingRepo({"helpers/shared.py": b"VALUE = 1\n"})
    service = WorkspaceFileImpactService(
        repo,
        snapshot_cache=_WorkspaceSnapshotCache(),
    )

    with pytest.raises(WorkspaceFileImpactInvalid, match="update is in progress"):
        await service.preview(WorkspaceFileImpactRequest(path="helpers/shared.py"))

    assert repo.read_count == 0


@pytest.mark.asyncio
async def test_snapshot_cache_rejects_generation_change_during_load(
    monkeypatch,
) -> None:
    calls = 0

    async def changing_generation() -> str:
        nonlocal calls
        calls += 1
        return "before" if calls == 1 else "after"

    monkeypatch.setattr(
        "src.core.module_cache.get_workspace_generation",
        changing_generation,
    )
    repo = _CountingRepo({"helpers/shared.py": b"VALUE = 1\n"})
    service = WorkspaceFileImpactService(
        repo,
        snapshot_cache=_WorkspaceSnapshotCache(),
    )

    with pytest.raises(WorkspaceFileImpactInvalid, match="changed during"):
        await service.preview(WorkspaceFileImpactRequest(path="helpers/shared.py"))

    assert repo.read_count == 1


@pytest.mark.asyncio
async def test_guarded_write_recomputes_without_cache_inside_update_barrier(
    monkeypatch,
) -> None:
    generation = "stable"
    wrote = False

    async def current_generation() -> str:
        return generation

    @asynccontextmanager
    async def barrier(**kwargs):
        nonlocal generation
        generation = "updating:lease"
        yield
        generation = "rotated"

    async def write() -> None:
        nonlocal wrote
        wrote = True

    monkeypatch.setattr(
        "src.core.module_cache.get_workspace_generation",
        current_generation,
    )
    monkeypatch.setattr("src.core.module_cache.workspace_source_update", barrier)
    repo = _CountingRepo({"helpers/shared.py": b"VALUE = 1\n"})
    service = WorkspaceFileImpactService(
        repo,
        snapshot_cache=_WorkspaceSnapshotCache(),
    )
    request = WorkspaceFileImpactRequest(
        path="helpers/shared.py",
        content="VALUE = 2\n",
    )
    preview = await service.preview(request)

    result = await service.guarded_write(request, preview.candidate_id, write)

    assert result.candidate_id == preview.candidate_id
    assert wrote is True
    assert repo.read_count == 2


@pytest.mark.asyncio
async def test_preview_warns_on_unchanged_computed_workflow_reference() -> None:
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

    assert result.ready_to_write is True
    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("dynamic_workflow_reference_unresolved", "warning"),
        ("proposed_bytes_unchanged", "warning"),
    ]


@pytest.mark.asyncio
async def test_preview_blocks_new_computed_workflow_reference() -> None:
    service = WorkspaceFileImpactService(
        _Repo({"workflows/report.py": b"async def report(): return None\n"})
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
    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("dynamic_workflow_reference_unresolved", "blocker"),
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
