from __future__ import annotations

import sys

import pytest

from bifrost.workspace_impact import (
    WorkspaceImpactError,
    analyze_workspace_impact,
    index_workspace_impact,
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

    assert graph.edges["features/vendor/child.py"] == frozenset({"helpers/shared.py"})
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
    assert graph.dynamic_reference_importers == frozenset()


def test_analysis_reports_computed_workflow_references() -> None:
    graph = analyze_workspace_impact(
        {
            "workflows/parent.py": (
                b"from bifrost import workflows\n"
                b"async def parent(workflow_id):\n"
                b"    return await workflows.execute(workflow_id, {})\n"
            )
        }
    )

    assert graph.dynamic_reference_importers == frozenset({"workflows/parent.py"})


@pytest.mark.parametrize(
    "function_source",
    [
        (
            b"async def parent(workflow_id):\n"
            b"    return await workflows.execute(workflow_id=workflow_id, inputs={})\n"
        ),
        (
            b"async def parent(function_name):\n"
            b"    return await workflows.execute_workflow(\n"
            b"        function_name=function_name, inputs={}\n"
            b"    )\n"
        ),
        (b"async def parent(kwargs):\n    return await workflows.execute(**kwargs)\n"),
    ],
)
def test_analysis_reports_computed_workflow_reference_keyword(
    function_source: bytes,
) -> None:
    graph = analyze_workspace_impact(
        {"workflows/parent.py": (b"from bifrost import workflows\n" + function_source)}
    )

    assert graph.dynamic_reference_importers == frozenset({"workflows/parent.py"})


def test_analysis_does_not_treat_dynamic_result_fields_as_workflow_dispatch() -> None:
    graph = analyze_workspace_impact(
        {
            "workflows/report.py": (
                b"async def report(item):\n"
                b"    return {'workflow_id': item.id, 'workflow_name': item.name}\n"
            )
        }
    )

    assert graph.registry_edges == frozenset()
    assert graph.dynamic_reference_importers == frozenset()


def test_analysis_does_not_treat_generic_call_keywords_as_workflow_dispatch() -> None:
    graph = analyze_workspace_impact(
        {
            "workflows/child.py": (
                b"@workflow(name='Child')\ndef child(): return None\n"
            ),
            "workflows/report.py": (
                b"def render(**kwargs): return kwargs\n"
                b"def report(item):\n"
                b"    return (\n"
                b"        render(workflow_name=item.name),\n"
                b"        render(workflow_name='Child'),\n"
                b"        render(workflow_id=item.id),\n"
                b"        render(function_name=item.function_name),\n"
                b"    )\n"
            ),
        }
    )

    assert graph.registry_edges == frozenset()
    assert graph.dynamic_reference_importers == frozenset()


def test_incremental_existing_file_overlay_matches_full_reanalysis() -> None:
    base = {
        "helpers/one.py": b"VALUE = 1\n",
        "helpers/two.py": b"VALUE = 2\n",
        "workflows/report.py": (
            b"from helpers.one import VALUE\n"
            b"@workflow(name='Before')\n"
            b"def report(): return VALUE\n"
        ),
        "workflows/consumer.py": (
            b"def consumer(): return {'workflow_name': 'After'}\n"
        ),
    }
    proposed = (
        b"from helpers.two import VALUE\n"
        b"@workflow(name='After')\n"
        b"def report(): return VALUE\n"
    )

    incremental = index_workspace_impact(base).overlay(
        "workflows/report.py",
        proposed,
    )
    complete = analyze_workspace_impact({**base, "workflows/report.py": proposed})

    assert incremental == complete


def test_analysis_indexes_direct_aliased_relative_and_qualified_symbol_use() -> None:
    graph = analyze_workspace_impact(
        {
            "features/vendor/_shared.py": b"class Client: pass\nVALUE = 1\n",
            "features/vendor/direct.py": (
                b"from ._shared import Client\ndef direct(): return Client()\n"
            ),
            "features/vendor/aliased.py": (
                b"import features.vendor._shared as shared\n"
                b"def aliased(): return shared.Client()\n"
            ),
            "features/vendor/qualified.py": (
                b"import features.vendor._shared\n"
                b"def qualified(): return features.vendor._shared.VALUE\n"
            ),
        }
    )

    target = "features/vendor/_shared.py"
    assert graph.symbol_imports[("features/vendor/direct.py", target)] == ("Client",)
    assert graph.symbol_imports[("features/vendor/aliased.py", target)] == ("Client",)
    assert graph.symbol_imports[("features/vendor/qualified.py", target)] == ("VALUE",)


def test_analysis_respects_lexical_shadowing_of_module_aliases() -> None:
    target = "modules/vendor.py"
    graph = analyze_workspace_impact(
        {
            target: b"def legacy(): return 1\n",
            "workflows/report.py": (
                b"import modules.vendor as vendor\n"
                b"def shadowed(vendor): return vendor.legacy()\n"
                b"def actual(): return vendor.legacy()\n"
            ),
        }
    )

    assert graph.symbol_imports[("workflows/report.py", target)] == ("legacy",)


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="PEP 695 type-parameter syntax requires Python 3.12+",
)
def test_analysis_respects_generic_type_parameter_shadowing() -> None:
    target = "modules/vendor.py"
    graph = analyze_workspace_impact(
        {
            target: b"def legacy(): return 1\n",
            "workflows/report.py": (
                b"import modules.vendor as vendor\n"
                b"def generic[vendor](): return vendor.legacy\n"
            ),
        }
    )

    assert ("workflows/report.py", target) not in graph.symbol_imports


def test_analysis_respects_control_flow_and_comprehension_shadowing() -> None:
    target = "modules/vendor.py"
    graph = analyze_workspace_impact(
        {
            target: b"def shadow(): pass\ndef actual(): pass\n",
            "workflows/loop.py": (
                b"import modules.vendor as vendor\n"
                b"for vendor in values:\n    vendor.shadow()\n"
            ),
            "workflows/with_alias.py": (
                b"import modules.vendor as vendor\n"
                b"with manager() as vendor:\n    vendor.shadow()\n"
            ),
            "workflows/walrus.py": (
                b"import modules.vendor as vendor\n"
                b"if vendor := local:\n    vendor.shadow()\n"
            ),
            "workflows/comprehension.py": (
                b"import modules.vendor as vendor\n"
                b"items = [vendor.shadow() for vendor in values]\n"
                b"result = vendor.actual()\n"
            ),
        }
    )

    assert ("workflows/loop.py", target) not in graph.symbol_imports
    assert ("workflows/with_alias.py", target) not in graph.symbol_imports
    assert ("workflows/walrus.py", target) not in graph.symbol_imports
    assert graph.symbol_imports[("workflows/comprehension.py", target)] == ("actual",)


def test_analysis_resolves_sibling_and_comprehension_lambdas_positionally() -> None:
    target = "modules/vendor.py"
    graph = analyze_workspace_impact(
        {
            target: b"def one(): pass\ndef two(): pass\n",
            "workflows/siblings.py": (
                b"import modules.vendor as vendor\n"
                b"handlers = (lambda: vendor.one(), lambda: vendor.two())\n"
            ),
            "workflows/generator.py": (
                b"import modules.vendor as vendor\n"
                b"values = tuple((lambda: vendor.one())() for _ in items)\n"
            ),
        }
    )

    assert graph.symbol_imports[("workflows/siblings.py", target)] == (
        "one",
        "two",
    )
    assert graph.symbol_imports[("workflows/generator.py", target)] == ("one",)


def test_analysis_indexes_conditional_walrus_with_and_dynamic_exports() -> None:
    graph = analyze_workspace_impact(
        {
            "modules/vendor.py": (
                b"if enabled := True:\n"
                b"    with manager() as managed:\n"
                b"        conditional = 1\n"
                b"def __getattr__(name): return dynamic[name]\n"
            )
        }
    )

    symbols = graph.module_symbols["modules/vendor.py"]
    assert {"enabled", "managed", "conditional", "__getattr__"} <= symbols
    assert graph.dynamic_exporters == frozenset({"modules/vendor.py"})
    assert graph.dynamic_export_contracts["modules/vendor.py"]


def test_analysis_indexes_match_captures_and_assigned_dynamic_export() -> None:
    graph = analyze_workspace_impact(
        {
            "modules/vendor.py": (
                b"match payload:\n"
                b"    case {'kind': kind, **rest}:\n"
                b"        captured = kind\n"
                b"__getattr__ = load_dynamic\n"
            )
        }
    )

    symbols = graph.module_symbols["modules/vendor.py"]
    assert {"kind", "rest", "captured", "__getattr__"} <= symbols
    assert graph.dynamic_exporters == frozenset({"modules/vendor.py"})


def test_analysis_ignores_non_reference_workflow_constants() -> None:
    graph = analyze_workspace_impact(
        {
            "workflows/one.py": (
                b"@workflow(name='strict')\ndef one():\n    return 1\n"
            ),
            "workflows/two.py": (
                b"@workflow(name='strict')\ndef two():\n    return 2\n"
            ),
            "workflows/parent.py": b"WORKFLOW_RETRY_MODE = 'strict'\n",
        }
    )

    assert graph.registry_edges == frozenset()


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


def test_analysis_retains_all_edges_for_ambiguous_workflow_reference() -> None:
    graph = analyze_workspace_impact(
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

    assert graph.edges["workflows/parent.py"] == frozenset(
        {"workflows/one.py", "workflows/two.py"}
    )
    assert graph.ambiguous_references == {"workflows/parent.py": ("Child",)}
