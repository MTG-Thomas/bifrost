from __future__ import annotations

import json
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import click
import pytest
import yaml
from click.testing import CliRunner

from bifrost.commands.solution import (
    _AmbiguousInstall,
    _collect_agents,
    _collect_apps,
    _collect_claims,
    _collect_config_schemas,
    _collect_connection_schemas,
    _collect_python_files,
    _collect_events,
    _collect_file_locations,
    _collect_forms,
    _collect_readme,
    _collect_tables,
    _collect_workflows,
    _entities_in_manifest,
    _poll_deploy_job,
    _resolve_install_org,
    _resolve_target_install,
    _v2_scaffold_files,
    resolve_install_id_for_workspace,
    solution_group,
    summarize_bundle,
)


def _write(path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_collect_tables_preserves_schema_and_optional_policies(tmp_path):
    _write(
        tmp_path / ".bifrost" / "tables.yaml",
        "tables:\n"
        "  tickets:\n"
        "    name: Tickets\n"
        "    description: Helpdesk tickets\n"
        "    schema:\n"
        "      columns:\n"
        "        - name: ticket_id\n"
        "          type: string\n"
        "    policies:\n"
        "      read: role:Support\n",
    )

    assert _collect_tables(tmp_path) == [
        {
            "id": "tickets",
            "name": "Tickets",
            "description": "Helpdesk tickets",
            "schema": {"columns": [{"name": "ticket_id", "type": "string"}]},
            "policies": {"read": "role:Support"},
        }
    ]


def test_collect_config_schemas_defaults_and_coerces_manifest_fields(tmp_path):
    _write(
        tmp_path / ".bifrost" / "configs.yaml",
        "configs:\n"
        "  API_KEY:\n"
        "    description: API token\n"
        "    required: true\n"
        "    position: '7'\n"
        "  MODE:\n"
        "    key: RUN_MODE\n"
        "    type: enum\n"
        "    default: dry-run\n",
    )

    assert _collect_config_schemas(tmp_path) == [
        {
            "id": "API_KEY",
            "key": "API_KEY",
            "type": "string",
            "required": True,
            "description": "API token",
            "default": None,
            "position": 7,
        },
        {
            "id": "MODE",
            "key": "RUN_MODE",
            "type": "enum",
            "required": False,
            "description": None,
            "default": "dry-run",
            "position": 0,
        },
    ]


def test_collect_file_locations_validates_manifest_list(tmp_path):
    _write(
        tmp_path / ".bifrost" / "files.yaml",
        "locations:\n"
        "  - attachments\n"
        "  - ticket-exports\n",
    )

    assert _collect_file_locations(tmp_path) == ["attachments", "ticket-exports"]

    _write(tmp_path / ".bifrost" / "files.yaml", "locations: not-a-list\n")
    with pytest.raises(ValueError, match="locations must be a list"):
        _collect_file_locations(tmp_path)


def test_collect_connection_schemas_keeps_template_and_position(tmp_path):
    _write(
        tmp_path / ".bifrost" / "connections.yaml",
        "connections:\n"
        "  halo:\n"
        "    integration_name: Halo PSA\n"
        "    position: '3'\n"
        "    template:\n"
        "      base_url: https://example.test\n"
        "  ninja: {}\n",
    )

    assert _collect_connection_schemas(tmp_path) == [
        {
            "integration_name": "Halo PSA",
            "template": {"base_url": "https://example.test"},
            "position": 3,
        },
        {"integration_name": "ninja", "template": {}, "position": 0},
    ]


def test_collect_readme_returns_markdown_or_none(tmp_path):
    assert _collect_readme(tmp_path) is None

    _write(tmp_path / "README.md", "# Solution\n")

    assert _collect_readme(tmp_path) == "# Solution\n"


def test_collect_claims_requires_query_and_defaults_type(tmp_path):
    _write(
        tmp_path / ".bifrost" / "claims.yaml",
        "claims:\n"
        "  campus_ids:\n"
        "    name: campus_ids\n"
        "    description: Campus scope\n"
        "    query:\n"
        "      table: memberships\n"
        "      select: campus_id\n",
    )

    assert _collect_claims(tmp_path) == [
        {
            "id": "campus_ids",
            "name": "campus_ids",
            "description": "Campus scope",
            "type": "list",
            "query": {"table": "memberships", "select": "campus_id"},
        }
    ]


def test_collect_inline_manifest_entities_for_forms_agents_and_events(tmp_path):
    _write(
        tmp_path / ".bifrost" / "forms.yaml",
        "forms:\n"
        "  intake:\n"
        "    title: Intake\n"
        "    fields:\n"
        "      - name: subject\n",
    )
    _write(
        tmp_path / ".bifrost" / "agents.yaml",
        "agents:\n"
        "  helper:\n"
        "    name: Helper\n"
        "    system_prompt: Help the operator\n",
    )
    _write(
        tmp_path / ".bifrost" / "events.yaml",
        "events:\n"
        "  ticket-created:\n"
        "    name: Ticket Created\n"
        "    source_type: webhook\n",
    )

    assert _collect_forms(tmp_path) == [
        {"id": "intake", "title": "Intake", "fields": [{"name": "subject"}]}
    ]
    assert _collect_agents(tmp_path) == [
        {"id": "helper", "name": "Helper", "system_prompt": "Help the operator"}
    ]
    assert _collect_events(tmp_path) == [
        {"id": "ticket-created", "name": "Ticket Created", "source_type": "webhook"}
    ]


def test_solution_init_writes_descriptor_binds_remote_and_refuses_overwrite(
    tmp_path, monkeypatch
):
    runner = CliRunner()

    async def fake_resolve_install_org(client, org, is_global):
        assert client is fake_client
        assert org is None
        assert is_global is False
        return None

    class Response:
        status_code = 202
        text = ""

        def json(self):
            return {"deploy_job_id": "job-1"}

    class Client:
        async def post(self, path, json=None, **kwargs):
            assert path == "/api/solutions"
            assert json["slug"] == "desk"
            assert json["global_repo_access"] is True
            assert kwargs == {}
            return Response()

    fake_client = Client()
    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(lambda **kwargs: fake_client),
    )
    monkeypatch.setattr(
        "bifrost.commands.solution._resolve_install_org",
        fake_resolve_install_org,
    )
    monkeypatch.setattr(
        "bifrost.commands.solution._poll_deploy_job",
        AsyncMock(return_value=0),
    )

    result = runner.invoke(
        solution_group,
        [
            "init",
            str(tmp_path),
            "--slug",
            "desk",
            "--name",
            "Desk",
            "--version",
            "1.2.3",
            "--global-repo-access",
        ],
    )

    assert result.exit_code == 0, result.output
    descriptor = tmp_path / "bifrost.solution.yaml"
    assert yaml.safe_load(descriptor.read_text()) == {
        "slug": "desk",
        "name": "Desk",
        "version": "1.2.3",
        "global_repo_access": True,
    }
    assert "BIFROST_SOLUTION_ID=sol-1" in (tmp_path / ".env").read_text()

    duplicate = runner.invoke(solution_group, ["init", str(tmp_path), "--slug", "desk"])
    assert duplicate.exit_code != 0
    assert "already exists" in duplicate.output


def test_solution_scaffold_app_creates_app_manifest_and_sample_workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "bifrost.solution.yaml", "slug: desk\nname: Desk\n")

    result = CliRunner().invoke(
        solution_group,
        ["scaffold-app", "portal", "--api-url", "https://bifrost.example"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "apps" / "portal" / "package.json").is_file()
    package = json.loads((tmp_path / "apps" / "portal" / "package.json").read_text())
    assert package["dependencies"]["bifrost"] == "https://bifrost.example/api/sdk/download"

    apps_yaml = yaml.safe_load((tmp_path / ".bifrost" / "apps.yaml").read_text())
    app_entry = next(iter(apps_yaml["apps"].values()))
    assert app_entry["slug"] == "portal"
    assert app_entry["path"] == "apps/portal"
    assert app_entry["app_model"] == "standalone_v2"

    workflows_yaml = yaml.safe_load((tmp_path / ".bifrost" / "workflows.yaml").read_text())
    workflow_entry = next(iter(workflows_yaml["workflows"].values()))
    assert workflow_entry["name"] == "hello"
    assert workflow_entry["path"] == "functions/hello.py"
    assert workflow_entry["function_name"] == "main"
    assert (tmp_path / "functions" / "hello.py").is_file()


def test_solution_scaffold_app_requires_solution_root_and_empty_target(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    outside = runner.invoke(solution_group, ["scaffold-app", "portal"])
    assert outside.exit_code != 0
    assert "Not inside a solution workspace" in outside.output

    _write(tmp_path / "bifrost.solution.yaml", "slug: desk\nname: Desk\n")
    _write(tmp_path / "apps" / "portal" / "existing.txt", "keep\n")

    occupied = runner.invoke(solution_group, ["scaffold-app", "portal"])
    assert occupied.exit_code != 0
    assert "already exists and is not empty" in occupied.output


def test_solution_pull_writes_only_manifest_files_and_acks_entities(tmp_path, monkeypatch):
    _write(tmp_path / "bifrost.solution.yaml", "slug: desk\nname: Desk\n")

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(".bifrost/tables.yaml", "tables:\n  table-1:\n    name: Tickets\n")
        zf.writestr(".bifrost/configs.yaml", "configs:\n  API_KEY:\n    type: secret\n")
        zf.writestr("functions/ignored.py", "raise RuntimeError('do not write')\n")

    class Response:
        def __init__(self, status_code, body=None, text="", content=b""):
            self.status_code = status_code
            self._body = body or {}
            self.text = text
            self.content = content

        def json(self):
            return self._body

    class Client:
        def __init__(self):
            self.posts: list[tuple[str, dict | None]] = []

        async def post(self, path, json=None):
            self.posts.append((path, json))
            if path.endswith("/export?mode=shareable"):
                return Response(200, content=archive.getvalue())
            if path.endswith("/pull/ack"):
                return Response(200, {"cleared": 2})
            raise AssertionError(path)

    client = Client()
    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(lambda **kwargs: client),
    )

    result = CliRunner().invoke(solution_group, ["pull", str(tmp_path), "--solution", "sol-1"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".bifrost" / "tables.yaml").is_file()
    assert (tmp_path / ".bifrost" / "configs.yaml").is_file()
    assert not (tmp_path / "functions" / "ignored.py").exists()
    assert client.posts[0] == ("/api/solutions/sol-1/export?mode=shareable", None)
    assert client.posts[1] == (
        "/api/solutions/sol-1/pull/ack",
        {
            "entities": [
                {"entity_type": "table", "entity_id": "table-1"},
                {"entity_type": "config", "entity_id": "API_KEY"},
            ]
        },
    )
    assert "2 manifest file(s)" in result.output
    assert "2 capture(s) cleared" in result.output


def test_solution_pull_reports_export_and_ack_failures(tmp_path, monkeypatch):
    _write(tmp_path / "bifrost.solution.yaml", "slug: desk\nname: Desk\n")

    class Response:
        def __init__(self, status_code, body=None, text="", content=b""):
            self.status_code = status_code
            self._body = body or {}
            self.text = text
            self.content = content

        def json(self):
            return self._body

    class ExportFailureClient:
        async def post(self, path, json=None):
            return Response(500, text="boom")

    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(lambda **kwargs: ExportFailureClient()),
    )
    failed_export = CliRunner().invoke(
        solution_group, ["pull", str(tmp_path), "--solution", "sol-1"]
    )
    assert failed_export.exit_code != 0
    assert "Pull failed: 500 boom" in failed_export.output

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(".bifrost/tables.yaml", "tables:\n  table-1: {}\n")

    class AckFailureClient:
        async def post(self, path, json=None):
            if path.endswith("/export?mode=shareable"):
                return Response(200, content=archive.getvalue())
            return Response(503, text="queue down")

    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(lambda **kwargs: AckFailureClient()),
    )
    failed_ack = CliRunner().invoke(
        solution_group, ["pull", str(tmp_path), "--solution", "sol-1"]
    )
    assert failed_ack.exit_code != 0
    assert "failed to clear the capture queue" in failed_ack.output


def test_solution_install_posts_zip_config_and_replace_flags(tmp_path, monkeypatch):
    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(b"zip-bytes")

    async def fake_resolve_install_org(client, org, is_global):
        assert client is fake_client
        assert org is None
        assert is_global is False
        return "org-1"

    class Response:
        status_code = 201
        text = ""

        def json(self):
            return {"id": "sol-1", "slug": "desk"}

    class Client:
        def __init__(self):
            self.posts: list[tuple[str, dict, dict]] = []

        async def post(self, path, files=None, data=None, **kwargs):
            self.posts.append((path, files, data))
            return Response()

    fake_client = Client()
    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(lambda **kwargs: fake_client),
    )
    monkeypatch.setattr(
        "bifrost.commands.solution._resolve_install_org",
        fake_resolve_install_org,
    )

    result = CliRunner().invoke(
        solution_group,
        [
            "install",
            str(zip_path),
            "--set",
            "API_KEY=secret",
            "--set",
            "MODE=dry-run",
            "--password",
            "pw",
            "--replace-secrets",
            "--replace-data",
        ],
    )

    assert result.exit_code == 0, result.output
    path, files, data = fake_client.posts[0]
    assert path == "/api/solutions/install"
    assert files == {"file": ("bundle.zip", b"zip-bytes", "application/zip")}
    assert json.loads(data["config_values"]) == {
        "API_KEY": "secret",
        "MODE": "dry-run",
    }
    assert data["organization_id"] == "org-1"
    assert data["password"] == "pw"
    assert data["replace_secrets"] == "true"
    assert data["replace_data"] == "true"
    assert "Installing solution (job job-1)..." in result.output


def test_solution_install_validates_set_pairs_before_http(tmp_path, monkeypatch):
    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(b"zip-bytes")
    called = False

    def fail_get_instance(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("HTTP should not be reached")

    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(fail_get_instance),
    )

    result = CliRunner().invoke(
        solution_group,
        ["install", str(zip_path), "--set", "MISSING_EQUALS"],
    )

    assert result.exit_code != 0
    assert "--set expects KEY=VALUE" in result.output
    assert called is False


def test_solution_install_reports_collision_rejection_and_server_errors(tmp_path, monkeypatch):
    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(b"zip-bytes")

    async def fake_resolve_install_org(client, org, is_global):
        return None

    class Response:
        def __init__(self, status_code, body=None, text=""):
            self.status_code = status_code
            self._body = body or {}
            self.text = text

        def json(self):
            return self._body

    class Client:
        def __init__(self, response):
            self.response = response

        async def post(self, path, files=None, data=None, **kwargs):
            return self.response

    monkeypatch.setattr(
        "bifrost.commands.solution._resolve_install_org",
        fake_resolve_install_org,
    )

    for response, expected in [
        (
            Response(409, {"detail": "API_KEY already set"}, "conflict"),
            "Install conflict: API_KEY already set",
        ),
        (
            Response(422, {"detail": "wrong password"}, "bad"),
            "Install rejected: wrong password",
        ),
        (
            Response(500, text="server down"),
            "Install failed: 500 server down",
        ),
    ]:
        def get_client(**kwargs):
            return Client(response)

        monkeypatch.setattr(
            "bifrost.commands.solution.BifrostClient.get_instance",
            staticmethod(get_client),
        )
        result = CliRunner().invoke(solution_group, ["install", str(zip_path)])
        assert result.exit_code != 0
        assert expected in result.output


def test_swap_slugs_resolves_slugs_preserves_uuid_refs_and_prints_result(monkeypatch):
    uuid_ref = "11111111-1111-1111-1111-111111111111"

    class Response:
        def __init__(self, status_code, body=None, text=""):
            self.status_code = status_code
            self._body = body or {}
            self.text = text

        def json(self):
            return self._body

    class Client:
        def __init__(self):
            self.gets: list[str] = []
            self.posts: list[tuple[str, dict]] = []

        async def get(self, path):
            self.gets.append(path)
            return Response(200, {"id": "resolved-app"})

        async def post(self, path, json):
            self.posts.append((path, json))
            return Response(
                200,
                {
                    "applications": [
                        {"name": "V1", "slug": "old"},
                        {"name": "V2", "slug": "live"},
                    ]
                },
            )

    client = Client()
    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(lambda **kwargs: client),
    )

    result = CliRunner().invoke(solution_group, ["swap-slugs", "portal", uuid_ref])

    assert result.exit_code == 0, result.output
    assert client.gets == ["/api/applications/portal"]
    assert client.posts == [
        (
            "/api/applications/swap-slugs",
            {"app_a": "resolved-app", "app_b": uuid_ref},
        )
    ]
    assert "V1" in result.output
    assert "Slug swap complete" in result.output


def test_swap_slugs_reports_resolution_and_swap_failures(monkeypatch):
    class Response:
        def __init__(self, status_code, body=None, text=""):
            self.status_code = status_code
            self._body = body or {}
            self.text = text

        def json(self):
            return self._body

    class MissingClient:
        async def get(self, path):
            return Response(404, text="missing")

    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(lambda **kwargs: MissingClient()),
    )
    missing = CliRunner().invoke(solution_group, ["swap-slugs", "missing", "other"])
    assert missing.exit_code != 0
    assert "No application 'missing'" in missing.output

    class SwapFailureClient:
        async def get(self, path):
            return Response(200, {"id": path.rsplit("/", 1)[-1]})

        async def post(self, path, json):
            return Response(409, text="slug locked")

    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        staticmethod(lambda **kwargs: SwapFailureClient()),
    )
    failed = CliRunner().invoke(solution_group, ["swap-slugs", "a", "b"])
    assert failed.exit_code != 0
    assert "Slug swap failed (409): slug locked" in failed.output


def test_collect_python_files_skips_app_generated_and_manifest_dirs(tmp_path):
    _write(
        tmp_path / ".bifrost" / "apps.yaml",
        "apps:\n"
        "  app-1:\n"
        "    path: apps/dash\n",
    )
    _write(tmp_path / "functions" / "hello.py", "def main():\n    return 'ok'\n")
    _write(tmp_path / "apps" / "dash" / "server.py", "def app_only(): pass\n")
    _write(tmp_path / "node_modules" / "pkg" / "ignored.py", "def ignored(): pass\n")
    _write(tmp_path / ".bifrost" / "ignored.py", "def ignored(): pass\n")
    _write(tmp_path / "lib" / "__pycache__" / "ignored.py", "def ignored(): pass\n")

    assert _collect_python_files(tmp_path) == {
        "functions/hello.py": "def main():\n    return 'ok'\n"
    }


def test_collect_workflows_carries_source_and_rejects_escaping_paths(tmp_path):
    _write(tmp_path / "functions" / "hello.py", "from bifrost import workflow\n")
    _write(
        tmp_path / ".bifrost" / "workflows.yaml",
        "workflows:\n"
        "  wf-1:\n"
        "    name: hello\n"
        "    path: functions/hello.py\n"
        "    function_name: main\n"
        "    endpoint_enabled: true\n"
        "    timeout_seconds: 0\n",
    )

    assert _collect_workflows(tmp_path) == [
        {
            "id": "wf-1",
            "name": "hello",
            "path": "functions/hello.py",
            "function_name": "main",
            "endpoint_enabled": True,
            "timeout_seconds": 0,
            "source": "from bifrost import workflow\n",
        }
    ]

    _write(
        tmp_path / ".bifrost" / "workflows.yaml",
        "workflows:\n"
        "  bad:\n"
        "    path: ../outside.py\n"
        "    function_name: main\n",
    )

    with pytest.raises(click.ClickException, match="escapes the workspace"):
        _collect_workflows(tmp_path)


def test_collect_apps_bundles_text_binary_logo_and_skips_local_artifacts(tmp_path):
    _write(
        tmp_path / ".bifrost" / "apps.yaml",
        "apps:\n"
        "  app-1:\n"
        "    slug: dash\n"
        "    name: Dashboard\n"
        "    description: Operator view\n"
        "    path: apps/dash\n"
        "    app_model: standalone_v2\n"
        "    dependencies:\n"
        "      left-pad: 1.3.0\n"
        "    access_level: authenticated\n"
        "    roles:\n"
        "      - role-1\n"
        "    role_names:\n"
        "      - Operators\n"
        "    logo: public/logo.svg\n",
    )
    _write(tmp_path / "apps" / "dash" / "src" / "App.tsx", "export default function App() {}\n")
    _write(tmp_path / "apps" / "dash" / "public" / "logo.svg", "<svg />\n")
    (tmp_path / "apps" / "dash" / "public" / "font.woff2").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "apps" / "dash" / "public" / "font.woff2").write_bytes(b"\x00\x01font")
    _write(tmp_path / "apps" / "dash" / ".env.local", "BIFROST_ACCESS_TOKEN=secret\n")
    _write(tmp_path / "apps" / "dash" / "dist" / "bundle.js", "generated\n")

    apps = _collect_apps(tmp_path)

    assert len(apps) == 1
    app = apps[0]
    assert app["id"] == "app-1"
    assert app["slug"] == "dash"
    assert app["description"] == "Operator view"
    assert app["app_model"] == "standalone_v2"
    assert app["dependencies"] == {"left-pad": "1.3.0"}
    assert app["access_level"] == "authenticated"
    assert app["roles"] == ["role-1"]
    assert app["role_names"] == ["Operators"]
    assert app["src_files"] == {
        "src/App.tsx": "export default function App() {}\n",
        "public/logo.svg": "<svg />\n",
    }
    assert app["bin_files"] == {"public/font.woff2": "AAFmb250"}
    assert app["logo_b64"] == "PHN2ZyAvPgo="
    assert app["logo_content_type"] == "image/svg+xml"
    assert ".env.local" not in app["src_files"]
    assert "dist/bundle.js" not in app["src_files"]


def test_collect_apps_rejects_escaping_app_and_logo_paths(tmp_path):
    _write(
        tmp_path / ".bifrost" / "apps.yaml",
        "apps:\n"
        "  bad:\n"
        "    path: ../outside\n",
    )
    with pytest.raises(click.ClickException, match="escapes the workspace"):
        _collect_apps(tmp_path)

    _write(
        tmp_path / ".bifrost" / "apps.yaml",
        "apps:\n"
        "  bad-logo:\n"
        "    path: apps/dash\n"
        "    logo: ../logo.svg\n",
    )
    _write(tmp_path / "apps" / "dash" / "App.tsx", "export {}\n")
    with pytest.raises(click.ClickException, match="escapes the app dir"):
        _collect_apps(tmp_path)

    _write(
        tmp_path / ".bifrost" / "apps.yaml",
        "apps:\n"
        "  missing-logo:\n"
        "    path: apps/dash\n"
        "    logo: missing.svg\n",
    )
    with pytest.raises(click.ClickException, match="logo file not found"):
        _collect_apps(tmp_path)


def test_resolve_target_install_matches_scope_and_refuses_ambiguous_matches():
    installs = [
        {"id": "global-1", "slug": "desk", "organization_id": None},
        {"id": "org-a-1", "slug": "desk", "organization_id": "org-a"},
        {"id": "org-b-1", "slug": "desk", "organization_id": "org-b"},
        {"id": "other", "slug": "other", "organization_id": "org-a"},
    ]

    assert _resolve_target_install(installs, "desk", None) == "global-1"
    assert _resolve_target_install(installs, "desk", "org-a") == "org-a-1"
    assert _resolve_target_install(installs, "missing", "org-a") is None

    with pytest.raises(_AmbiguousInstall, match="2 installs of 'desk' exist for org org-a"):
        _resolve_target_install(
            installs + [{"id": "org-a-2", "slug": "desk", "organization_id": "org-a"}],
            "desk",
            "org-a",
        )


def test_entities_in_manifest_reports_pull_ack_entities(tmp_path):
    _write(
        tmp_path / ".bifrost" / "tables.yaml",
        "tables:\n"
        "  table-1:\n"
        "    name: Tickets\n",
    )
    _write(
        tmp_path / ".bifrost" / "configs.yaml",
        "configs:\n"
        "  API_KEY:\n"
        "    type: secret\n",
    )
    _write(
        tmp_path / ".bifrost" / "agents.yaml",
        "agents:\n"
        "  agent-1:\n"
        "    name: Helper\n",
    )

    assert _entities_in_manifest(tmp_path) == [
        {"entity_type": "table", "entity_id": "table-1"},
        {"entity_type": "agent", "entity_id": "agent-1"},
        {"entity_type": "config", "entity_id": "API_KEY"},
    ]


def test_v2_scaffold_files_are_wired_for_instance_sdk_and_runtime_config():
    files = _v2_scaffold_files("desk", "https://bifrost.example/")

    assert {
        "package.json",
        "index.html",
        "src/main.tsx",
        "src/App.tsx",
        "src/index.css",
        "tsconfig.json",
        "vite.config.ts",
    }.issubset(files)
    package = json.loads(files["package.json"])
    assert package["name"] == "desk"
    assert package["dependencies"]["bifrost"] == "https://bifrost.example/api/sdk/download"
    assert package["dependencies"]["lucide-react"]
    assert "window.__BIFROST_APP__" in files["src/main.tsx"]
    assert "BIFROST_ACCESS_TOKEN" in files["vite.config.ts"]
    assert "functions/hello.py::main" in files["src/App.tsx"]


def test_summarize_bundle_counts_text_files_and_flags_large_vendored_trees():
    normal = summarize_bundle(
        {"functions/a.py": "print('a')\n"},
        [{"src_files": {"App.tsx": "export {}\n"}, "bin_files": {"logo.png": "AA=="}}],
        vendored_count=2,
    )
    assert normal.file_count == 3
    assert normal.size_mb == 0.0
    assert normal.warn is False
    assert normal.message == "Bundle: 3 files, 0.0 MB."

    warned = summarize_bundle({"modules/vendor.py": "x"}, [], vendored_count=201)
    assert warned.warn is True
    assert "201 vendored files" in warned.message


@pytest.mark.asyncio
async def test_poll_deploy_job_reports_success_failure_and_read_errors(capsys):
    class Response:
        def __init__(self, status_code, body=None, text=""):
            self.status_code = status_code
            self._body = body or {}
            self.text = text

        def json(self):
            return self._body

    class Client:
        def __init__(self, responses):
            self.responses = list(responses)
            self.paths: list[str] = []

        async def get(self, path):
            self.paths.append(path)
            return self.responses.pop(0)

    success = Client([
        Response(200, {"status": "running"}),
        Response(200, {"status": "succeeded"}),
    ])
    assert await _poll_deploy_job(success, "job-1", interval=0) == 0
    assert success.paths == [
        "/api/solutions/deploy-jobs/job-1",
        "/api/solutions/deploy-jobs/job-1",
    ]
    assert "Still deploying" in capsys.readouterr().out

    failed = Client([Response(200, {"status": "failed", "error": "bundle older than installed"})])
    assert await _poll_deploy_job(failed, "job-2", interval=0) == 1
    assert "Re-run with --force" in capsys.readouterr().err

    unreadable = Client([Response(503, text="maintenance")])
    assert await _poll_deploy_job(unreadable, "job-3", interval=0) == 1
    assert "Failed to read deploy status (503)" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_resolve_install_org_maps_home_global_and_explicit_org(monkeypatch):
    async def fake_resolve_org_target(org_ref, is_global, resolver):
        assert resolver is not None
        if is_global:
            return SimpleNamespace(is_set=True, organization_id=None)
        if org_ref:
            return SimpleNamespace(is_set=True, organization_id=f"resolved-{org_ref}")
        return SimpleNamespace(is_set=False, organization_id=None)

    monkeypatch.setattr(
        "bifrost.commands.solution.resolve_org_target",
        fake_resolve_org_target,
    )
    client = SimpleNamespace(organization={"id": "home-org"})

    assert await _resolve_install_org(client, None, False) == "home-org"
    assert await _resolve_install_org(client, None, True) is None
    assert await _resolve_install_org(client, "Support", False) == "resolved-Support"


def test_resolve_install_id_for_workspace_prefers_own_then_global_and_fails_closed(tmp_path):
    _write(tmp_path / "bifrost.solution.yaml", "slug: desk\nname: Desk\n")

    class Response:
        status_code = 200

        def json(self):
            return {
                "solutions": [
                    {"id": "global", "slug": "desk", "organization_id": None},
                    {"id": "own", "slug": "desk", "organization_id": "org-1"},
                ]
            }

    client = SimpleNamespace(
        organization={"id": "org-1"},
        _sync_http=SimpleNamespace(get=lambda path: Response()),
    )
    assert resolve_install_id_for_workspace(client, tmp_path) == "own"

    client.organization = {}
    assert resolve_install_id_for_workspace(client, tmp_path) == "global"

    class Forbidden:
        status_code = 403

    client._sync_http = SimpleNamespace(get=lambda path: Forbidden())
    assert resolve_install_id_for_workspace(client, tmp_path) is None
    assert resolve_install_id_for_workspace(client, tmp_path / "missing") is None
