"""Criterion 15 (offline dev loop): `bifrost run` resolves solution-local
imports against the Solution root even when invoked from a subdirectory.

The workflow executes locally (no network); solution-local modules
(`from modules.x import y`) resolve because the Solution root — detected via
``bifrost.solution.yaml`` above the file — is put on ``sys.path``. The
data-plane half (tables/integrations hitting a live instance) is the SDK's
job and is covered by the table SDK tests; here we prove the import root.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from types import SimpleNamespace

import pytest

from bifrost import cli
from bifrost.cli import handle_run


@pytest.fixture
def _restore_sys_path():
    before = list(sys.path)
    before_mods = set(sys.modules)
    yield
    sys.path[:] = before
    for name in set(sys.modules) - before_mods:
        sys.modules.pop(name, None)


def test_bifrost_run_resolves_solution_local_import(
    tmp_path: pathlib.Path, capsys, monkeypatch, _restore_sys_path
) -> None:
    # A Solution workspace: descriptor at the root, a local module, and a
    # workflow in workflows/ that imports the local module.
    (tmp_path / "bifrost.solution.yaml").write_text("slug: mna\nname: MNA\nscope: org\n")
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "x.py").write_text("VAL = 42\n")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "w.py").write_text(
        "from bifrost import workflow\n"
        "from modules.x import VAL\n"
        "@workflow\n"
        "async def run():\n"
        "    return VAL\n"
    )

    # Invoke from a DIFFERENT cwd (not the solution root) so resolution can only
    # succeed via the descriptor-detected solution root, not cwd.
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    wf_file = str(tmp_path / "workflows" / "w.py")
    rc = handle_run([wf_file, "-w", "run"])
    assert rc == 0, capsys.readouterr()

    out = capsys.readouterr().out.strip()
    assert out == "42"


def test_bifrost_run_non_solution_file_still_works(
    tmp_path: pathlib.Path, capsys, monkeypatch, _restore_sys_path
) -> None:
    # No descriptor → not a solution; a self-contained workflow still runs
    # (regression: find_solution_root returns None, no sys.path surprise).
    (tmp_path / "w.py").write_text(
        "from bifrost import workflow\n"
        "@workflow\n"
        "async def run():\n"
        "    return 7\n"
    )
    monkeypatch.chdir(tmp_path)
    rc = handle_run([str(tmp_path / "w.py"), "-w", "run"])
    assert rc == 0, capsys.readouterr()
    assert capsys.readouterr().out.strip() == "7"


def test_extract_workflow_parameters_maps_annotations_and_defaults():
    async def sample(
        ticket_id: int,
        confidence: float = 0.5,
        active: bool = True,
        tags: list[str] | None = None,
        metadata: dict | None = None,
        *args,
        **kwargs,
    ):
        return None

    assert cli._extract_workflow_parameters(sample) == [
        {
            "name": "ticket_id",
            "type": "int",
            "required": True,
            "label": "Ticket Id",
            "default_value": None,
        },
        {
            "name": "confidence",
            "type": "float",
            "required": False,
            "label": "Confidence",
            "default_value": 0.5,
        },
        {
            "name": "active",
            "type": "bool",
            "required": False,
            "label": "Active",
            "default_value": True,
        },
        {
            "name": "tags",
            "type": "list",
            "required": False,
            "label": "Tags",
            "default_value": None,
        },
        {
            "name": "metadata",
            "type": "dict",
            "required": False,
            "label": "Metadata",
            "default_value": None,
        },
    ]


def test_run_direct_executes_workflow_and_reports_errors(monkeypatch, capsys):
    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            raise RuntimeError("offline")

    async def ok(name="Ada"):
        return {"hello": name}

    async def boom():
        raise RuntimeError("bad workflow")

    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)

    assert cli._run_direct("ok", {"ok": ok}, {"name": "Grace"}, verbose=False) == 0
    assert '"hello": "Grace"' in capsys.readouterr().out

    assert cli._run_direct("ok", {"ok": ok}, {}, organization_id="org-1") == 1
    assert "--org requires authentication" in capsys.readouterr().err

    assert cli._run_direct("boom", {"boom": boom}, {}) == 1
    assert "bad workflow" in capsys.readouterr().err


def test_run_direct_fetches_org_context_and_sets_solution_context(monkeypatch, capsys, tmp_path):
    contexts = []

    class Response:
        status_code = 200

        def json(self):
            return {"organization": {"id": "org-2"}}

    class Client:
        user = {
            "id": "user-1",
            "email": "dev@example.test",
            "name": "Dev",
            "is_superuser": True,
        }
        organization = {"id": "org-1", "name": "Org", "is_active": True}

        def __init__(self):
            self._sync_http = SimpleNamespace(get=lambda path, params=None: Response())

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            return Client()

    async def ok():
        return "done"

    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)
    monkeypatch.setattr(
        "bifrost._context.set_execution_context",
        lambda ctx: contexts.append(ctx),
    )
    monkeypatch.setattr(
        "bifrost.commands.solution.resolve_install_id_for_workspace",
        lambda client, root: "solution-1",
    )

    rc = cli._run_direct(
        "ok",
        {"ok": ok},
        {},
        verbose=True,
        organization_id="org-2",
        solution_root=tmp_path,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "Resolved Solution install id: solution-1" in out
    assert "Running in standalone mode" in out
    assert contexts[0].solution_id == "solution-1"
    assert contexts[0].workflow_name == "ok"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, "--org requires superuser privileges"),
        (404, "not found or inactive"),
        (500, "Error fetching org context"),
    ],
)
def test_run_direct_reports_org_context_http_errors(monkeypatch, capsys, status, expected):
    class Response:
        status_code = status
        text = "error"

    class Client:
        _sync_http = SimpleNamespace(get=lambda path, params=None: Response())

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            return Client()

    async def ok():
        return "done"

    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)

    assert cli._run_direct("ok", {"ok": ok}, {}, organization_id="org-2") == 1
    assert expected in capsys.readouterr().err


def test_handle_run_argument_and_file_validation(tmp_path, capsys):
    assert handle_run([]) == 0
    assert "Usage: bifrost run" in capsys.readouterr().out

    assert handle_run(["missing.py"]) == 1
    assert "File not found" in capsys.readouterr().err

    workflow = tmp_path / "workflow.py"
    workflow.write_text("raise RuntimeError('import failed')\n", encoding="utf-8")
    assert handle_run([str(workflow), "-w", "run"]) == 1
    assert "Error loading workflow file" in capsys.readouterr().err

    plain = tmp_path / "plain.py"
    plain.write_text("async def run():\n    return 1\n", encoding="utf-8")
    assert handle_run([str(plain)]) == 1
    assert "No @workflow decorated functions" in capsys.readouterr().err

    assert handle_run([str(plain), "--params", "{bad"]) == 1
    assert "Invalid JSON" in capsys.readouterr().err

    assert handle_run([str(plain), "--workflow"]) == 1
    assert "--workflow requires a value" in capsys.readouterr().err

    assert handle_run([str(plain), "--unknown"]) == 1
    assert "Unknown option" in capsys.readouterr().err


class _SessionResponse:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class _SessionClient:
    api_url = "https://bifrost.example"

    def __init__(self, pending_responses):
        self.pending_responses = list(pending_responses)
        self.posts = []
        self.gets = []

    async def post(self, path, **kwargs):
        self.posts.append((path, kwargs))
        if path.endswith("/result"):
            return _SessionResponse(200, {})
        if path.endswith("/heartbeat"):
            return _SessionResponse(204, {})
        return _SessionResponse(201, {"ok": True})

    async def get(self, path, **kwargs):
        self.gets.append((path, kwargs))
        if not self.pending_responses:
            raise AssertionError("test exhausted pending responses")
        return self.pending_responses.pop(0)


def test_run_session_flow_executes_pending_workflow(monkeypatch, capsys):
    async def sleep_now(_seconds):
        return None

    async def selected(name):
        return {"hello": name}

    client = _SessionClient(
        [
            _SessionResponse(
                200,
                {
                    "execution_id": "exec-1",
                    "workflow_name": "selected",
                    "params": {"name": "Ada"},
                },
            )
        ]
    )
    monkeypatch.setattr(cli.asyncio, "sleep", sleep_now)
    monkeypatch.setattr(cli.time, "time", lambda: 100.0)
    monkeypatch.setattr(cli.webbrowser, "open", lambda _url: True)

    rc = asyncio.run(
        cli._run_session_flow(
            client=client,
            session_id="session-1",
            file_path="/tmp/workflow.py",
            workflow_infos=[{"name": "selected"}],
            workflows={"selected": selected},
            selected_workflow="selected",
            no_browser=False,
        )
    )

    assert rc == 0
    assert client.posts[0] == (
        "/api/sdk/sessions",
        {
            "json": {
                "session_id": "session-1",
                "file_path": "/tmp/workflow.py",
                "workflows": [{"name": "selected"}],
                "selected_workflow": "selected",
            }
        },
    )
    result_post = client.posts[-1]
    assert result_post[0] == "/api/sdk/sessions/session-1/executions/exec-1/result"
    assert result_post[1]["json"]["status"] == "Success"
    assert result_post[1]["json"]["result"] == {"hello": "Ada"}
    assert "Executing workflow: selected" in capsys.readouterr().out


def test_run_session_flow_posts_missing_workflow_failure(monkeypatch, capsys):
    async def sleep_now(_seconds):
        return None

    client = _SessionClient(
        [
            _SessionResponse(
                200,
                {
                    "execution_id": "exec-2",
                    "workflow_name": "missing",
                    "params": {},
                },
            )
        ]
    )
    monkeypatch.setattr(cli.asyncio, "sleep", sleep_now)
    monkeypatch.setattr(cli.time, "time", lambda: 100.0)

    rc = asyncio.run(
        cli._run_session_flow(
            client=client,
            session_id="session-2",
            file_path="/tmp/workflow.py",
            workflow_infos=[],
            workflows={},
            selected_workflow=None,
            no_browser=True,
        )
    )

    assert rc == 1
    result_post = client.posts[-1]
    assert result_post[1]["json"]["status"] == "Failed"
    assert "not found in loaded module" in result_post[1]["json"]["error_message"]
    assert "Workflow 'missing' not found" in capsys.readouterr().err


def test_run_session_flow_reports_registration_and_poll_errors(capsys):
    class RegisterFailureClient(_SessionClient):
        async def post(self, path, **kwargs):
            self.posts.append((path, kwargs))
            return _SessionResponse(500, text="server down")

    rc = asyncio.run(
        cli._run_session_flow(
            client=RegisterFailureClient([]),
            session_id="session-3",
            file_path="/tmp/workflow.py",
            workflow_infos=[],
            workflows={},
            selected_workflow=None,
            no_browser=True,
        )
    )
    assert rc == 1
    assert "Error registering session: 500 - server down" in capsys.readouterr().err

    async def never_called():
        return None

    poll_404 = _SessionClient([_SessionResponse(404, {})])
    rc = asyncio.run(
        cli._run_session_flow(
            client=poll_404,
            session_id="session-4",
            file_path="/tmp/workflow.py",
            workflow_infos=[],
            workflows={"never_called": never_called},
            selected_workflow=None,
            no_browser=True,
        )
    )
    assert rc == 1
    assert "Session expired or deleted" in capsys.readouterr().err


def test_post_result_warns_but_does_not_raise(capsys):
    class Client:
        async def post(self, path, **kwargs):
            raise RuntimeError("offline")

    asyncio.run(
        cli._post_result(
            Client(),
            "session-5",
            "exec-5",
            "Success",
            {"ok": True},
            None,
            12,
        )
    )

    assert "Failed to post result: offline" in capsys.readouterr().err


def test_handle_run_requires_workflow_and_validates_selected_workflow(tmp_path, capsys):
    workflow = tmp_path / "workflow.py"
    workflow.write_text(
        "from bifrost import workflow\n"
        "@workflow\n"
        "async def run():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    assert handle_run([str(workflow)]) == 1
    assert "--workflow is required" in capsys.readouterr().err

    assert handle_run([str(workflow), "-w", "missing"]) == 1
    assert "Workflow 'missing' not found" in capsys.readouterr().err
