from __future__ import annotations

import pytest

from bifrost.workspace_impact import (
    WorkspaceImpactError,
    analyze_workspace_impact,
    reverse_edges,
    transitive_distances,
)


def test_analysis_combines_import_and_registry_reverse_edges() -> None:
    workflow_id = "11111111-2222-3333-4444-555555555555"
    files = {
        "helpers/shared.py": b"VALUE = 1\n",
        "features/vendor/child.py": (
            b"from helpers.shared import VALUE\n"
            + f'@workflow(id="{workflow_id}", name="Vendor: Child")\n'.encode()
            + b"async def child(): return VALUE\n"
        ),
        "features/vendor/parent.py": (
            f'CHILD_WORKFLOW_ID = "{workflow_id}"\n'.encode()
            + b"async def parent(): return {'workflow_id': CHILD_WORKFLOW_ID}\n"
        ),
    }

    graph = analyze_workspace_impact(files)

    assert graph.edges["features/vendor/child.py"] == frozenset(
        {"helpers/shared.py"}
    )
    assert graph.edges["features/vendor/parent.py"] == frozenset(
        {"features/vendor/child.py"}
    )
    reverse = reverse_edges(graph.edges)
    assert transitive_distances("helpers/shared.py", reverse) == {
        "helpers/shared.py": 0,
        "features/vendor/child.py": 1,
        "features/vendor/parent.py": 2,
    }


def test_analysis_reports_unresolved_and_computed_imports() -> None:
    graph = analyze_workspace_impact(
        {
            "features/vendor/report.py": (
                b"from helpers.missing import VALUE\n"
                b"import importlib\n"
                b"name = input()\n"
                b"importlib.import_module(name)\n"
            )
        }
    )

    assert graph.unresolved_imports == {
        "features/vendor/report.py": ("helpers.missing",)
    }
    assert graph.dynamic_importers == frozenset({"features/vendor/report.py"})


def test_analysis_tracks_dunder_import_and_workflow_execution_reference() -> None:
    workflow_id = "89f4f152-1eb7-4c82-b90a-7b6da2686113"
    graph = analyze_workspace_impact(
        {
            "helpers/shared.py": b"VALUE = 1\n",
            "workflows/child.py": (
                b"from bifrost import workflow\n"
                + f"@workflow(id='{workflow_id}', name='Child')\n".encode()
                + b"def child():\n    return 1\n"
            ),
            "workflows/parent.py": (
                b"from bifrost import workflows\n"
                b"shared = __import__('helpers.shared')\n"
                b"async def parent():\n"
                + f"    return await workflows.execute('{workflow_id}', {{}})\n".encode()
            ),
        }
    )

    assert graph.edges["workflows/parent.py"] == frozenset(
        {"helpers/shared.py", "workflows/child.py"}
    )
    assert graph.dynamic_importers == frozenset()


def test_analysis_fails_on_ambiguous_module_identity() -> None:
    with pytest.raises(WorkspaceImpactError, match="ambiguous Python module"):
        analyze_workspace_impact(
            {
                "helpers/example.py": b"VALUE = 1\n",
                "helpers/example/__init__.py": b"VALUE = 2\n",
            }
        )


def test_analysis_fails_on_ambiguous_referenced_workflow_identity() -> None:
    with pytest.raises(
        WorkspaceImpactError, match="ambiguous referenced workflow identities: Child"
    ):
        analyze_workspace_impact(
            {
                "workflows/one.py": (
                    b"@workflow(name='Child')\ndef one():\n    return 1\n"
                ),
                "workflows/two.py": (
                    b"@workflow(name='Child')\ndef two():\n    return 2\n"
                ),
                "workflows/parent.py": (
                    b"async def parent():\n"
                    b"    return {'workflow_name': 'Child'}\n"
                ),
            }
        )
