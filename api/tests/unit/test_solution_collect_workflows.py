"""CLI `_collect_workflows` — reads .bifrost/workflows.yaml (keyed by UUID) into
the deploy bundle. The deployer's `_upsert_workflows` consumes the full metadata
set (endpoint_enabled, public_endpoint, timeout_seconds, category, tags), so the
CLI collector must pass them through — otherwise a disconnected redeploy silently
resets an exported workflow's endpoint/timeout to defaults (Codex P2-e)."""
from __future__ import annotations

import pathlib
import sys

import click
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.solution import _collect_workflows  # noqa: E402


def _ws(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "workflows.yaml").write_text(body)
    return tmp_path


def test_collect_workflows_preserves_full_metadata(tmp_path) -> None:
    ws = _ws(
        tmp_path,
        "workflows:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    id: 11111111-1111-1111-1111-111111111111\n"
        "    name: Sync Tickets\n"
        "    function_name: sync_tickets\n"
        "    path: workflows/sync.py\n"
        "    type: workflow\n"
        "    description: Pulls tickets\n"
        "    access_level: organization\n"
        "    endpoint_enabled: true\n"
        "    allowed_methods: [GET, POST]\n"
        "    public_endpoint: true\n"
        "    timeout_seconds: 600\n"
        "    category: Tickets\n"
        "    tags: [psa, sync]\n",
    )
    wfs = _collect_workflows(ws)
    assert len(wfs) == 1
    w = wfs[0]
    assert w["id"] == "11111111-1111-1111-1111-111111111111"
    assert w["name"] == "Sync Tickets"
    assert w["function_name"] == "sync_tickets"
    assert w["path"] == "workflows/sync.py"
    assert w["type"] == "workflow"
    assert w["description"] == "Pulls tickets"
    assert w["access_level"] == "organization"
    # These five are what a narrowed collector silently dropped (P2-e).
    assert w["endpoint_enabled"] is True
    assert w["allowed_methods"] == ["GET", "POST"]
    assert w["public_endpoint"] is True
    assert w["timeout_seconds"] == 600
    assert w["category"] == "Tickets"
    assert w["tags"] == ["psa", "sync"]


def test_collect_workflows_empty_when_no_manifest(tmp_path) -> None:
    assert _collect_workflows(tmp_path) == []


@pytest.mark.parametrize("wf_path", ["../outside.py", "/tmp/outside.py"])
def test_collect_workflows_rejects_manifest_path_outside_workspace(
    tmp_path: pathlib.Path,
    wf_path: str,
) -> None:
    ws = _ws(
        tmp_path,
        "workflows:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    name: Escape\n"
        "    function_name: run\n"
        f"    path: {wf_path}\n",
    )

    with pytest.raises(click.ClickException, match="escapes the workspace"):
        _collect_workflows(ws)


def test_compile_solution_plan_flags_each_decorated_function_without_exact_manifest_row(
    tmp_path,
):
    from bifrost.commands.solution import compile_solution_plan

    python_files = {
        "functions/registered.py": "from bifrost import workflow\n\n@workflow\nasync def main():\n    return 1\n",
        "functions/loose.py": "from bifrost import workflow\n\n@workflow(name='Loose')\nasync def main():\n    return 2\n",
        "modules/helper.py": "def util():\n    return 3\n",
    }
    workflows = [{"path": "functions/registered.py", "function_name": "main", "name": "reg"}]

    plan = compile_solution_plan(
        tmp_path,
        python_files=python_files,
        workflows=workflows,
    )

    assert plan.valid is False
    assert plan.counts == {
        "python_files": 3,
        "workflow_manifest_rows": 1,
        "decorated_workflows": 2,
        "errors": 1,
    }
    assert [finding.ref for finding in plan.diagnostics] == [
        "functions/loose.py::main"
    ]
    finding = plan.diagnostics[0]
    assert finding.code == "solution.workflow_manifest_row_missing"
    assert finding.path == "functions/loose.py"
    assert finding.function_name == "main"
    assert "live references would return 404" in finding.message
    assert "function_name: main" in (finding.remediation or "")


def test_compile_solution_plan_matches_path_and_function_not_counts(tmp_path):
    from bifrost.commands.solution import compile_solution_plan

    src = (
        "from bifrost import workflow\n\n"
        "@workflow\nasync def a():\n    return 1\n\n"
        "@workflow(name='B')\nasync def b():\n    return 2\n"
    )
    python_files = {"functions/two.py": src}

    one_entry = [{"path": "functions/two.py", "function_name": "a", "name": "a"}]
    plan = compile_solution_plan(
        tmp_path,
        python_files=python_files,
        workflows=one_entry,
    )
    assert [finding.ref for finding in plan.diagnostics] == ["functions/two.py::b"]

    both_entries = one_entry + [{"path": "functions/two.py", "function_name": "b", "name": "B"}]
    plan = compile_solution_plan(
        tmp_path,
        python_files=python_files,
        workflows=both_entries,
    )
    assert plan.valid is True
    assert plan.diagnostics == ()


def test_compile_solution_plan_ast_ignores_comments_and_supports_qualified_decorator(
    tmp_path,
):
    from bifrost.commands.solution import compile_solution_plan

    python_files = {
        "functions/qualified.py": (
            "import bifrost\n\n"
            "# @workflow async def fake(): pass\n"
            "@bifrost.workflow(name='Real')\n"
            "async def real():\n"
            "    return 1\n"
        ),
    }
    plan = compile_solution_plan(
        tmp_path,
        python_files=python_files,
        workflows=[],
    )

    assert [finding.ref for finding in plan.diagnostics] == [
        "functions/qualified.py::real"
    ]


def test_solution_plan_json_contract_is_stable(tmp_path):
    from bifrost.commands.solution import compile_solution_plan

    plan = compile_solution_plan(
        tmp_path,
        python_files={
            "functions/hello.py": "from bifrost import workflow\n@workflow\ndef hello(): pass\n"
        },
        workflows=[],
    )

    document = plan.to_dict()
    assert document["schema_version"] == 1
    assert document["mode"] == "solution"
    assert document["root"] == str(tmp_path.resolve())
    assert document["valid"] is False
    assert document["entities"] == {
        "decorated_workflows": ["functions/hello.py::hello"],
        "registered_workflows": [],
    }
    assert document["diagnostics"][0]["code"] == (
        "solution.workflow_manifest_row_missing"
    )


def test_compile_solution_plan_rejects_manifest_row_without_decorated_source(
    tmp_path,
):
    from bifrost.commands.solution import compile_solution_plan

    plan = compile_solution_plan(
        tmp_path,
        python_files={"functions/tasks.py": "def task():\n    return True\n"},
        workflows=[
            {"path": "functions/tasks.py", "function_name": "task", "name": "Task"}
        ],
    )

    assert plan.valid is False
    assert plan.diagnostics[0].code == (
        "solution.workflow_source_missing_or_undecorated"
    )


def test_compile_solution_plan_understands_aliased_bifrost_decorator(tmp_path):
    from bifrost.commands.solution import compile_solution_plan

    plan = compile_solution_plan(
        tmp_path,
        python_files={
            "functions/tasks.py": (
                "from bifrost import workflow as bifrost_workflow\n\n"
                "@bifrost_workflow\n"
                "async def task():\n"
                "    return True\n"
            )
        },
        workflows=[
            {"path": "functions/tasks.py", "function_name": "task", "name": "Task"}
        ],
    )

    assert plan.valid is True


def test_compile_solution_plan_reports_decorated_source_syntax_error(tmp_path):
    from bifrost.commands.solution import compile_solution_plan

    plan = compile_solution_plan(
        tmp_path,
        python_files={
            "functions/broken.py": "from bifrost import workflow\n@workflow\ndef broken(:\n"
        },
        workflows=[],
    )

    assert plan.valid is False
    assert [item.code for item in plan.diagnostics] == [
        "solution.workflow_source_invalid"
    ]


def test_compile_solution_plan_ignores_unrelated_helper_syntax_error(tmp_path):
    from bifrost.commands.solution import compile_solution_plan

    plan = compile_solution_plan(
        tmp_path,
        python_files={"helpers/broken.py": "def broken(:\n"},
        workflows=[],
    )

    assert plan.diagnostics == ()
