from __future__ import annotations

import base64
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

    assert cli._remove_env_url_line("https://api.example/")
    assert "BIFROST_API_URL" not in (tmp_path / ".env").read_text()
    assert cli._remove_env_keys({"OLD", "ADDED"})
    assert (tmp_path / ".env").read_text() == "NO_NEWLINE=value\n"
    assert not cli._remove_env_keys({"MISSING"})


def test_env_remove_deletes_blank_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("BIFROST_API_URL='https://api.example'\n\n")

    assert cli._remove_env_url_line("https://api.example")
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
    assert base64.b64decode(files["prefix/keep.txt"]) == b"line\r\n"

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
    assert "could not connect" in capsys.readouterr().err

    monkeypatch.setattr(
        cli.BifrostClient,
        "get_instance",
        lambda require_auth: (_ for _ in ()).throw(RuntimeError("no creds")),
    )
    assert await cli._api_request("GET", "/api/demo", None, client=None) == 1
    assert "no creds" in capsys.readouterr().err
