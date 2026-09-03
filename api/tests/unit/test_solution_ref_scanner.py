from __future__ import annotations

from textwrap import dedent

from src.services.solutions.ref_scanner import (
    scan_config_refs,
    scan_integration_refs,
    scan_table_refs,
    scan_workflow_refs,
)


def test_scan_table_refs_python_and_tsx() -> None:
    py = 'rows = await sdk.tables.get("orders")\nx = await tables.get("customers", "id-1")'
    tsx = 'const t = useTable("inventory");'
    assert scan_table_refs(py) == {"orders", "customers"}
    assert scan_table_refs(tsx) == {"inventory"}


def test_scan_config_refs() -> None:
    src = 'k = await config.get("API_KEY")\nz = await sdk.config.get("TIMEOUT", default=5)'
    assert scan_config_refs(src) == {"API_KEY", "TIMEOUT"}


def test_scan_workflow_refs_tsx() -> None:
    tsx = 'const { run } = useWorkflow("workflows/main.py::handler");'
    assert scan_workflow_refs(tsx) == {"workflows/main.py::handler"}


def test_scan_workflow_refs_query_and_mutation_hooks() -> None:
    # useWorkflowQuery / useWorkflowMutation take a bare name OR a path::fn ref.
    src = (
        'const a = useWorkflowQuery("get_clients");\n'
        'const b = useWorkflowMutation("workflows/m.py::save");'
    )
    assert scan_workflow_refs(src) == {"get_clients", "workflows/m.py::save"}


def test_scan_workflow_refs_python_execute() -> None:
    src = dedent('''
        async def run():
            results = workflows.execute("workflows/main.py::handler")
            follow = await workflows.execute('workflows/child.py::run')
            await sdk.workflows.execute('workflows/sdk.py::main')
            return results, follow
        ''')
    assert scan_workflow_refs(src) == {
        "workflows/main.py::handler",
        "workflows/child.py::run",
        "workflows/sdk.py::main",
    }


def test_scan_workflow_refs_python_execute_with_whitespace_and_dynamic_arg() -> None:
    src = (
        'workflows.execute ( "workflows/with_space.py::spaced" , timeout=5 )\n'
        "workflow_ref = 'workflows/dynamic.py::ignore'\n"
        "x = workflows.execute(workflow_ref)\n"
    )
    assert scan_workflow_refs(src) == {"workflows/with_space.py::spaced"}


def test_scan_workflow_refs_python_execute_ignores_comments_and_docstrings() -> None:
    src = dedent('''
        """workflows.execute("workflows/docstring.py::ignored")"""
        # workflows.execute("workflows/comment.py::ignored")
        async def run():
            return await workflows.execute("workflows/real.py::run")
        ''')
    assert scan_workflow_refs(src) == {"workflows/real.py::run"}


def test_scan_workflow_refs_non_python_keeps_text_matching() -> None:
    tsx = 'const result = workflows.execute("workflows/ui.py::run");'
    assert scan_workflow_refs(tsx) == {"workflows/ui.py::run"}


def test_scan_workflow_refs_python_execute_rejects_prefixed_call() -> None:
    src = (
        'foo.workflows.execute("workflows/rejected.py::run")\n'
        'bar.sdk.workflows.execute("workflows/ignored.py::run")\n'
        'workflows.execute("workflows/accepted.py::run")\n'
    )
    assert scan_workflow_refs(src) == {"workflows/accepted.py::run"}


def test_scan_workflow_refs_python_execute_unrelated_and_malformed() -> None:
    src = (
        'other.execute("workflows/noop.py::run")\n'
        "workflows.schedule('workflows/nope.py::run')\n"
        "workflows.execute()\n"
        'workflows.execute("workflows/bad.py::broken\n'
    )
    assert scan_workflow_refs(src) == set()


def test_scanners_ignore_unrelated_strings() -> None:
    # A bare string or a similarly-named symbol must NOT be picked up.
    src = 'name = "orders"\nimport environment\nconfigure("x")'
    assert scan_table_refs(src) == set()
    assert scan_config_refs(src) == set()
    assert scan_workflow_refs(src) == set()


def test_scan_integration_refs_matches_get_calls() -> None:
    src = '''
    a = await integrations.get("HaloPSA")
    b = await sdk.integrations.get('Microsoft Partner')
    c = await integrations.get(name)  # dynamic — invisible
    '''
    assert scan_integration_refs(src) == {"HaloPSA", "Microsoft Partner"}


def test_scan_handles_single_and_double_quotes() -> None:
    assert scan_table_refs("tables.get('q')") == {"q"}
    assert scan_config_refs('config.get("q")') == {"q"}
