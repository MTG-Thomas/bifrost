from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from bifrost import cli


def test_env_helpers_upsert_gitignore_and_remove_values(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("export OLD=value\nBIFROST_API_URL=https://old\nNO_NEWLINE=value")
    (tmp_path / ".gitignore").write_text("dist")

    cli._upsert_env_vars({"OLD": "new", "ADDED": "yes"})
    assert (tmp_path / ".env").read_text() == (
        "OLD=new\nBIFROST_API_URL=https://old\nNO_NEWLINE=value\nADDED=yes\n"
    )

    cli._write_env_url("https://api.example")
    env_text = (tmp_path / ".env").read_text()
    assert "BIFROST_API_URL=https://api.example\n" in env_text
    assert (tmp_path / ".gitignore").read_text() == "dist\n.env\n"
    assert "Updated" in capsys.readouterr().out

    assert cli._remove_env_connection("https://api.example/")
    assert "BIFROST_API_URL" not in (tmp_path / ".env").read_text()
    assert cli._remove_env_keys({"OLD", "ADDED"})
    assert (tmp_path / ".env").read_text() == "NO_NEWLINE=value\n"
    assert not cli._remove_env_keys({"MISSING"})


def test_env_remove_deletes_blank_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("BIFROST_API_URL='https://api.example'\n\n")

    assert cli._remove_env_connection("https://api.example")
    assert not (tmp_path / ".env").exists()
    assert cli._read_env_file(tmp_path / "missing.env") == []


def test_warn_if_git_workspace_only_warns_inside_checkout(tmp_path, capsys):
    nested = tmp_path / "repo" / "nested"
    nested.mkdir(parents=True)
    (tmp_path / "repo" / ".git").mkdir()

    cli._warn_if_git_workspace(str(nested))
    assert "Warning: target path is inside a local git checkout" in capsys.readouterr().err

    cli._warn_if_git_workspace(str(tmp_path / "outside"))
    assert capsys.readouterr().err == ""


def test_sync_tui_and_push_watch_arg_parsing(monkeypatch, capsys):
    assert cli._sync_use_tui(force=False, is_tty=True)
    assert not cli._sync_use_tui(force=True, is_tty=True)
    assert not cli._sync_use_tui(force=False, is_tty=False)
    monkeypatch.setenv("BIFROST_NONINTERACTIVE", "1")
    assert not cli._sync_use_tui(force=False, is_tty=True)

    parsed = cli._parse_push_watch_args(["src", "--mirror", "--validate", "-y"])
    assert parsed == cli._PushWatchArgs(
        local_path="src",
        mirror=True,
        validate=True,
        force=True,
    )
    assert cli._parse_push_watch_args(["src", "extra"]) is None
    assert "Unexpected argument" in capsys.readouterr().err
    assert cli._parse_push_watch_args(["--bad"]) is None
    assert "Unknown option" in capsys.readouterr().err


def test_time_repo_path_and_skip_helpers(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("hello")

    assert cli._format_file_time(file_path) != "unknown"
    assert cli._format_file_time(tmp_path / "missing.txt") == "unknown"
    assert cli._format_server_time("2026-07-05T12:30:00+00:00") == "Jul 05 12:30"
    assert cli._format_server_time("bad") == "unknown"
    assert cli._strip_repo_prefix("apps/demo/main.py", "apps/demo") == "main.py"
    assert cli._strip_repo_prefix("apps/demo", "apps/demo") == ""
    assert cli._safe_server_rel_path("apps/demo/main.py", "apps/demo") == "main.py"
    with pytest.raises(ValueError, match="empty"):
        cli._safe_server_rel_path("apps/demo", "apps/demo")
    with pytest.raises(ValueError, match="unsafe"):
        cli._safe_server_rel_path("apps/demo/../secret.py", "apps/demo")

    spec = SimpleNamespace(match_file=lambda path: path in {"ignored.py", "repo/ignored.py"})
    assert cli._should_skip_path("ignored.py", spec)
    assert cli._should_skip_path("local.py", spec, "repo/ignored.py")
    assert not cli._should_skip_path("local.py", spec, "repo/local.py")


def test_collect_push_files_handles_single_file_and_binary_skips(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".gitignore").write_text("ignored.txt\n")
    (root / "keep.txt").write_text("line\r\n")
    (root / "ignored.txt").write_text("ignored")
    (root / "binary.bin").write_bytes(b"\x00\xff")
    monkeypatch.setattr(cli, "_is_bifrost_path", lambda _path: False)

    files, skipped = cli._collect_push_files(root, "prefix", single_file=str(root / "keep.txt"))
    assert skipped == 0
    assert base64.b64decode(files["prefix/keep.txt"]).replace(b"\r\n", b"\n") == b"line\n"

    files, skipped = cli._collect_push_files(root, "prefix", single_file=str(root / "ignored.txt"))
    assert files == {}
    assert skipped == 1

    files, skipped = cli._collect_push_files(root, "")
    assert "keep.txt" in files
    assert skipped == 0


def test_validate_api_endpoint_rejects_unsafe_inputs():
    assert cli._validate_api_endpoint("/api/workflows") is None
    assert "scheme-relative" in cli._validate_api_endpoint("//evil.example/api")
    assert "scheme or host" in cli._validate_api_endpoint("https://evil.example/api")
    assert "absolute API path" in cli._validate_api_endpoint("api/workflows")


@pytest.mark.asyncio
async def test_api_request_formats_json_text_http_errors_and_client_lookup(monkeypatch, capsys):
    class Client:
        def __init__(self, response=None, error=None):
            self.response = response
            self.error = error
            self.calls = []

        async def get(self, endpoint, **kwargs):
            self.calls.append((endpoint, kwargs))
            if self.error:
                raise self.error
            return self.response

        async def post(self, endpoint, **kwargs):
            self.calls.append((endpoint, kwargs))
            return self.response

    request = httpx.Request("GET", "https://api.example/api")
    ok = httpx.Response(200, json={"ok": True}, request=request)
    client = Client(ok)
    assert await cli._api_request("GET", "/api/demo", None, client=client) == 0
    assert '"ok": true' in capsys.readouterr().out
    assert client.calls == [("/api/demo", {})]

    failed = httpx.Response(404, text="missing", request=request)
    assert await cli._api_request("GET", "/api/demo", None, client=Client(failed)) == 1
    assert "missing" in capsys.readouterr().out

    posted = Client(httpx.Response(201, json={"created": True}, request=request))
    assert await cli._api_request("POST", "/api/demo", {"x": 1}, client=posted) == 0
    assert posted.calls == [("/api/demo", {"json": {"x": 1}})]

    assert await cli._api_request(
        "GET",
        "/api/demo",
        None,
        client=Client(error=httpx.ConnectError("offline")),
    ) == 1
    connect_error = capsys.readouterr().err
    assert "could not connect to Bifrost API" in connect_error

    monkeypatch.setattr(
        cli.BifrostClient,
        "get_instance",
        lambda require_auth: (_ for _ in ()).throw(RuntimeError("no creds")),
    )
    assert await cli._api_request("GET", "/api/demo", None, client=None) == 1
    assert "no creds" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_api_request_recovers_exact_execution_after_response_loss(capsys):
    class Client:
        def __init__(self):
            self.execution_id = None
            self.post_count = 0
            self.get_paths = []

        async def post(self, _endpoint, **kwargs):
            self.post_count += 1
            self.execution_id = kwargs["headers"][cli._EXECUTION_ID_HEADER]
            raise httpx.ReadError("")

        async def get(self, endpoint, **_kwargs):
            self.get_paths.append(endpoint)
            return httpx.Response(
                200,
                json={
                    "execution_id": self.execution_id,
                    "status": "Success",
                    "result": {"covered": True},
                },
                request=httpx.Request("GET", f"https://api.example{endpoint}"),
            )

    client = Client()
    result = await cli._api_request(
        "POST",
        "/api/workflows/execute",
        {"workflow_id": "reconcile", "input_data": {}},
        client=client,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["execution_id"] == client.execution_id
    assert payload["status"] == "Success"
    assert client.post_count == 1
    assert client.get_paths == [f"/api/executions/{client.execution_id}"]
    assert captured.err == ""


@pytest.mark.asyncio
async def test_api_request_fails_closed_before_missing_id_poll(capsys):
    class Client:
        def __init__(self):
            self.execution_id = None
            self.get_paths = []

        async def post(self, endpoint, **kwargs):
            self.execution_id = kwargs["headers"][cli._EXECUTION_ID_HEADER]
            return httpx.Response(
                200,
                json={"status": "Success"},
                request=httpx.Request("POST", f"https://api.example{endpoint}"),
            )

        async def get(self, endpoint, **_kwargs):
            self.get_paths.append(endpoint)
            return httpx.Response(
                404,
                text="Not Found",
                request=httpx.Request("GET", f"https://api.example{endpoint}"),
            )

    client = Client()
    result = await cli._api_request(
        "POST",
        "/api/workflows/execute",
        {"workflow_id": "reconcile", "input_data": {}},
        client=client,
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert client.get_paths == [f"/api/executions/{client.execution_id}"]
    assert "/api/executions/ did not" not in captured.err
    assert f"/api/executions/{client.execution_id}" in captured.err
    assert "ValueError" in captured.err


@pytest.mark.asyncio
async def test_api_request_diagnostics_keep_context_and_scrub_secrets(capsys):
    class Client:
        async def get(self, _endpoint, **_kwargs):
            raise RuntimeError(
                "Bearer bearer-value password=hunter2 "
                "https://api.example/api/demo?access_token=query-value"
            )

    result = await cli._api_request(
        "GET",
        "/api/demo?api_key=endpoint-value",
        None,
        client=Client(),
    )

    error = capsys.readouterr().err
    assert result == 1
    assert "RuntimeError" in error
    assert "GET /api/demo?<redacted>" in error
    assert "bearer-value" not in error
    assert "hunter2" not in error
    assert "query-value" not in error
    assert "endpoint-value" not in error


@pytest.mark.asyncio
async def test_api_request_rejects_non_finite_json(capsys):
    class Client:
        async def get(self, endpoint, **_kwargs):
            return httpx.Response(
                200,
                json={"value": float("nan")},
                request=httpx.Request("GET", f"https://api.example{endpoint}"),
            )

    assert await cli._api_request("GET", "/api/demo", None, client=Client()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ValueError" in captured.err
    assert "GET /api/demo" in captured.err
