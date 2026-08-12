"""Local Solution planning and deploy's fail-closed preflight."""

from __future__ import annotations

import json
import pathlib
from unittest import mock

import yaml
from click.testing import CliRunner

from bifrost.commands.solution import solution_group
from bifrost.solution_descriptor import DESCRIPTOR_FILENAME


def _workspace(tmp_path: pathlib.Path, *, register: bool) -> pathlib.Path:
    workspace = tmp_path / "solution"
    workspace.mkdir()
    (workspace / DESCRIPTOR_FILENAME).write_text(
        yaml.safe_dump(
            {
                "slug": "demo",
                "name": "Demo",
                "version": "0.1.0",
                "global_repo_access": False,
            }
        )
    )
    functions = workspace / "functions"
    functions.mkdir()
    (functions / "hello.py").write_text(
        "from bifrost import workflow\n\n"
        "@workflow(name='Hello')\n"
        "async def hello():\n"
        "    return {'hello': 'world'}\n"
    )
    if register:
        manifest_dir = workspace / ".bifrost"
        manifest_dir.mkdir()
        (manifest_dir / "workflows.yaml").write_text(
            "workflows:\n"
            "  11111111-1111-1111-1111-111111111111:\n"
            "    name: Hello\n"
            "    path: functions/hello.py\n"
            "    function_name: hello\n"
        )
    return workspace


def test_solution_plan_json_reports_missing_manifest_row(tmp_path) -> None:
    workspace = _workspace(tmp_path, register=False)

    result = CliRunner().invoke(
        solution_group,
        ["plan", str(workspace), "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["schema_version"] == 1
    assert document["mode"] == "solution"
    assert document["valid"] is False
    assert document["diagnostics"][0]["ref"] == "functions/hello.py::hello"


def test_solution_plan_human_output_passes_registered_workspace(tmp_path) -> None:
    workspace = _workspace(tmp_path, register=True)

    result = CliRunner().invoke(
        solution_group,
        ["plan", str(workspace)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Solution plan: valid" in result.output
    assert "No blocking diagnostics." in result.output


def test_solution_deploy_stops_before_auth_or_upload_when_row_is_missing(
    tmp_path,
) -> None:
    workspace = _workspace(tmp_path, register=False)

    with mock.patch("bifrost.client.BifrostClient.get_instance") as get_client:
        result = CliRunner().invoke(
            solution_group,
            ["deploy", str(workspace)],
        )

    assert result.exit_code == 1
    assert "Solution preflight failed; no files were uploaded" in result.output
    assert "solution.workflow_manifest_row_missing" in result.output
    assert "functions/hello.py::hello" in result.output
    assert "function_name: hello" in result.output
    get_client.assert_not_called()


def test_solution_add_workflow_mints_stable_local_identity_and_fixes_plan(
    tmp_path,
) -> None:
    workspace = _workspace(tmp_path, register=False)
    runner = CliRunner()

    added = runner.invoke(
        solution_group,
        [
            "add-workflow",
            "functions/hello.py::hello",
            "--path",
            str(workspace),
            "--json",
        ],
        catch_exceptions=False,
    )
    repeated = runner.invoke(
        solution_group,
        [
            "add-workflow",
            "functions/hello.py::hello",
            "--path",
            str(workspace),
            "--json",
        ],
        catch_exceptions=False,
    )
    planned = runner.invoke(
        solution_group,
        ["plan", str(workspace), "--json"],
        catch_exceptions=False,
    )

    assert added.exit_code == 0
    assert repeated.exit_code == 0
    assert planned.exit_code == 0
    added_document = json.loads(added.output)
    repeated_document = json.loads(repeated.output)
    assert added_document["action"] == "created"
    assert repeated_document["action"] == "preserved"
    assert repeated_document["id"] == added_document["id"]
    assert json.loads(planned.output)["valid"] is True


def test_solution_add_workflow_rejects_undecorated_function(tmp_path) -> None:
    workspace = _workspace(tmp_path, register=False)
    source = workspace / "functions" / "helper.py"
    source.write_text("def helper():\n    return True\n")

    result = CliRunner().invoke(
        solution_group,
        [
            "add-workflow",
            "functions/helper.py::helper",
            "--path",
            str(workspace),
        ],
    )

    assert result.exit_code == 1
    assert "No top-level @workflow, @tool, or @data_provider" in result.output
