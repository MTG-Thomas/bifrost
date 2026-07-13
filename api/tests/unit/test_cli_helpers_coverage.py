import base64
import os
import sys
from types import SimpleNamespace

import httpx
import pytest

from bifrost import cli


def test_hash_helpers_normalize_text_but_not_binary_bytes():
    assert cli._normalize_line_endings(b"a\r\nb\r\n") == b"a\nb\n"
    binary = b"a\x00\r\nb"
    assert cli._normalize_line_endings(binary) == binary

    assert cli._etag_md5(b"abc") == "900150983cd24fb0d6963f7d28e17f72"
    assert cli._hash_for_cache(b"a\r\nb\r\n") == cli._etag_md5(b"a\nb\n")


def test_path_and_tty_helpers(monkeypatch, tmp_path):
    assert cli._is_bifrost_path("apps/demo/.bifrost/workflows.yaml")
    assert cli._is_bifrost_path(r"apps\demo\.bifrost\workflows.yaml")
    assert not cli._is_bifrost_path("apps/demo/workflows.py")

    monkeypatch.setattr(cli, "_is_tty", True)
    assert cli._resolve_is_tty() is True
    monkeypatch.setattr(cli, "_is_tty", False)
    assert cli._resolve_is_tty() is False
    monkeypatch.setattr(cli, "_is_tty", None)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    assert cli._resolve_is_tty() is False

    monkeypatch.chdir(tmp_path)
    child = tmp_path / "apps" / "demo"
    child.mkdir(parents=True)
    assert cli._workspace_root_for(child) == tmp_path.resolve()
    assert cli._workspace_root_for(tmp_path.parent) == tmp_path.parent


def test_sync_use_tui_respects_force_tty_and_environment(monkeypatch):
    monkeypatch.delenv("BIFROST_NONINTERACTIVE", raising=False)
    assert cli._sync_use_tui(force=False, is_tty=True) is True
    assert cli._sync_use_tui(force=True, is_tty=True) is False
    assert cli._sync_use_tui(force=False, is_tty=False) is False

    monkeypatch.setenv("BIFROST_NONINTERACTIVE", "1")
    assert cli._sync_use_tui(force=False, is_tty=True) is False


def test_parse_push_watch_args_accepts_aliases_and_rejects_unknowns(capsys):
    parsed = cli._parse_push_watch_args(
        ["apps/demo", "--mirror", "--validate", "--yes"]
    )

    assert parsed == cli._PushWatchArgs(
        local_path="apps/demo",
        mirror=True,
        validate=True,
        force=True,
    )

    assert cli._parse_push_watch_args(["one", "two"]) is None
    assert "Unexpected argument" in capsys.readouterr().err

    assert cli._parse_push_watch_args(["--bad"]) is None
    assert "Unknown option" in capsys.readouterr().err


def test_repo_prefix_and_server_path_helpers(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    app = root / "apps" / "demo"
    app.mkdir(parents=True)
    monkeypatch.chdir(root)

    assert cli._detect_repo_prefix(app) == "apps/demo"
    assert cli._detect_repo_prefix(root) == ""
    assert cli._detect_repo_prefix(tmp_path.parent) == ""

    assert cli._strip_repo_prefix("apps/demo/main.py", "apps/demo") == "main.py"
    assert cli._strip_repo_prefix("apps/demo", "apps/demo") == ""
    assert cli._strip_repo_prefix("other/main.py", "apps/demo") == "other/main.py"

    assert cli._safe_server_rel_path("apps/demo/main.py", "apps/demo") == "main.py"
    for unsafe in ("apps/demo/../secret.py", "/absolute.py", r"apps/demo\bad.py"):
        with pytest.raises(ValueError):
            cli._safe_server_rel_path(unsafe, "apps/demo")


def test_collect_push_files_applies_prefix_ignore_and_line_normalization(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("ignored", encoding="utf-8")
    (root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (root / "main.py").write_bytes(b"print('ok')\r\n")
    (root / "skip.tmp").write_text("ignored", encoding="utf-8")

    files, skipped = cli._collect_push_files(root, repo_prefix="apps/demo")

    assert skipped == 0
    assert files == {
        "apps/demo/.gitignore": base64.b64encode(b"*.tmp\n").decode("ascii"),
        "apps/demo/main.py": base64.b64encode(b"print('ok')\n").decode("ascii")
    }

    one_file, skipped = cli._collect_push_files(
        root,
        repo_prefix="apps/demo",
        single_file=str(root / "main.py"),
    )
    assert skipped == 0
    assert one_file == {
        "apps/demo/main.py": base64.b64encode(b"print('ok')\n").decode("ascii")
    }


def test_warn_if_git_workspace_only_warns_inside_repo(tmp_path, capsys):
    repo = tmp_path / "repo"
    nested = repo / "apps"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()

    cli._warn_if_git_workspace(str(nested))
    assert "inside a local git checkout" in capsys.readouterr().err

    cli._warn_if_git_workspace(str(tmp_path / "elsewhere"))
    assert capsys.readouterr().err == ""


def test_watch_state_drains_requeues_and_tracks_hashes(tmp_path):
    state = cli._WatchState(tmp_path)

    with state.lock:
        state.pending_changes.add("main.py")
        state.pending_deletes.add("old.py")

    assert state.drain() == ({"main.py"}, {"old.py"})
    assert state.drain() == (set(), set())

    state.requeue({"retry.py"}, {"gone.py"})
    assert state.drain() == ({"retry.py"}, {"gone.py"})

    state.queue_incoming_files(["remote.py"], "Alice")
    state.queue_incoming_deletes(["deleted.py"], "Bob")
    assert state.drain_incoming() == (
        [(["remote.py"], "Alice")],
        [(["deleted.py"], "Bob")],
    )

    state.set_known_hash("main.py", "abc")
    assert state.get_known_hash("main.py") == "abc"
    state.seed_known_hashes({"other.py": "def"})
    assert state.get_known_hash("other.py") == "def"
    state.forget_known_hash("main.py")
    assert state.get_known_hash("main.py") is None


def test_format_time_helpers(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    os.utime(file_path, (1_700_000_000, 1_700_000_000))

    assert cli._format_file_time(file_path) != "unknown"
    assert cli._format_file_time(tmp_path / "missing.txt") == "unknown"
    assert cli._format_server_time("2026-07-04T12:34:00+00:00") == "Jul 04 12:34"
    assert cli._format_server_time("bad") == "unknown"
    assert cli._format_server_time(None) == "unknown"


def test_should_skip_path_checks_repo_path_alias():
    spec = SimpleNamespace(match_file=lambda path: path == "apps/demo/build/output.js")

    assert cli._should_skip_path("build/output.js", spec, "apps/demo/build/output.js")
    assert not cli._should_skip_path("main.py", spec, "apps/demo/main.py")


def test_stdio_glyph_and_summary_helpers(monkeypatch, capsys):
    captured_stdout = cli.sys.stdout

    class Stream:
        encoding = "cp1252"

        def reconfigure(self, **kwargs):
            self.kwargs = kwargs

    stdout = Stream()
    stderr = Stream()
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    cli._ensure_utf8_stdio()

    assert stdout.kwargs == {"encoding": "utf-8", "errors": "backslashreplace"}
    assert stderr.kwargs == {"encoding": "utf-8", "errors": "backslashreplace"}
    assert cli._stdout_can_encode("plain")
    assert not cli._stdout_can_encode("✓")
    assert cli._check_glyph() == "OK"

    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(encoding="utf-8"))
    assert cli._check_glyph() == "✓"
    monkeypatch.setattr(cli.sys, "stdout", captured_stdout)
    monkeypatch.setattr(cli, "_check_glyph", lambda: "OK")
    cli._print_sync_summary("done")
    assert "OK done" in capsys.readouterr().out


def test_env_file_helpers_update_append_and_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "export BIFROST_API_URL='https://old.example/'\n"
        "KEEP=value\n"
        "BIFROST_ACCESS_TOKEN=tok\n",
        encoding="utf-8",
    )

    cli._upsert_env_vars({
        "BIFROST_API_URL": "https://new.example",
        "BIFROST_REFRESH_TOKEN": "refresh",
    })

    assert env_file.read_text(encoding="utf-8") == (
        "BIFROST_API_URL=https://new.example\n"
        "KEEP=value\n"
        "BIFROST_ACCESS_TOKEN=tok\n"
        "BIFROST_REFRESH_TOKEN=refresh\n"
    )

    assert cli._remove_env_url_line("https://other.example") is False
    assert cli._remove_env_url_line("https://new.example/") is True
    assert "BIFROST_API_URL" not in env_file.read_text(encoding="utf-8")

    assert cli._remove_env_keys({"BIFROST_ACCESS_TOKEN", "BIFROST_REFRESH_TOKEN"})
    remaining = env_file.read_text(encoding="utf-8")
    assert "KEEP=value" in remaining
    assert "BIFROST_ACCESS_TOKEN" not in remaining

    (tmp_path / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    cli._write_env_url("https://api.example")
    assert "BIFROST_API_URL=https://api.example" in env_file.read_text(encoding="utf-8")
    assert ".env" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "Updated" in capsys.readouterr().out

    env_file.write_text("\n", encoding="utf-8")
    assert cli._remove_env_keys({"ANY"}) is False
    env_file.write_text("BIFROST_ACCESS_TOKEN=tok\n", encoding="utf-8")
    assert cli._remove_env_keys({"BIFROST_ACCESS_TOKEN"})
    assert not env_file.exists()


def test_main_dispatches_commands_and_handles_help_version_and_interrupt(monkeypatch, capsys):
    calls: list[tuple[str, list[str]]] = []

    def handler(name):
        def _handle(args):
            calls.append((name, args))
            return 10 + len(calls)
        return _handle

    monkeypatch.setattr(cli, "_check_cli_version", lambda: calls.append(("version-check", [])))
    monkeypatch.setattr(cli, "handle_login", handler("login"))
    monkeypatch.setattr(cli, "handle_logout", handler("logout"))
    monkeypatch.setattr(cli, "handle_auth", handler("auth"))
    monkeypatch.setattr(cli, "handle_run", handler("run"))
    monkeypatch.setattr(cli, "handle_git", handler("git"))
    monkeypatch.setattr(cli, "handle_sync", handler("sync"))
    monkeypatch.setattr(cli, "handle_push", handler("push"))
    monkeypatch.setattr(cli, "handle_pull", handler("pull"))
    monkeypatch.setattr(cli, "handle_watch", handler("watch"))
    monkeypatch.setattr(cli, "handle_api", handler("api"))
    monkeypatch.setattr(cli, "handle_migrate_imports", handler("migrate"))

    assert cli.main([]) == 0
    assert "Bifrost CLI" in capsys.readouterr().out

    assert cli.main(["--version"]) == 0
    assert "bifrost" in capsys.readouterr().out

    for command in [
        "login", "logout", "auth", "run", "git", "sync", "push", "pull",
        "watch", "api", "migrate-imports",
    ]:
        assert cli.main([command, "arg"]) >= 10

    assert ("version-check", []) in calls
    assert ("login", ["arg"]) in calls
    assert ("migrate", ["arg"]) in calls

    calls.clear()
    assert cli.main(["unknown"]) == 1
    assert "Unknown command" in capsys.readouterr().err

    def interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "handle_login", interrupt)
    assert cli.main(["login"]) == 130


def test_main_dispatches_lazy_solution_skill_and_entity_commands(monkeypatch, tmp_path):
    import types

    solution_mod = types.ModuleType("bifrost.commands.solution")
    solution_mod.handle_solution = lambda args: 21
    solution_mod.handle_deploy = lambda args: 22
    skill_mod = types.ModuleType("bifrost.skill")
    skill_mod.handle_skill = lambda args: 23
    commands_mod = types.ModuleType("bifrost.commands")
    commands_mod.ENTITY_GROUPS = {"orgs"}
    commands_mod.dispatch_entity_subgroup = lambda command, args: 24

    monkeypatch.setitem(sys.modules, "bifrost.commands.solution", solution_mod)
    monkeypatch.setitem(sys.modules, "bifrost.skill", skill_mod)
    monkeypatch.setitem(sys.modules, "bifrost.commands", commands_mod)
    monkeypatch.setattr(cli, "_check_cli_version", lambda: None)

    assert cli.main(["solution", "deploy"]) == 21
    assert cli.main(["deploy"]) == 22
    assert cli.main(["skill", "install"]) == 23
    assert cli.main(["orgs", "list"]) == 24


def test_validate_api_endpoint_and_handle_api_input_errors(tmp_path, monkeypatch, capsys):
    assert cli._validate_api_endpoint("/api/workflows") is None
    assert "scheme-relative" in cli._validate_api_endpoint("//evil.test")
    assert "scheme or host" in cli._validate_api_endpoint("https://evil.test/api")
    assert "absolute API path" in cli._validate_api_endpoint("api/workflows")

    assert cli.handle_api([]) == 1
    assert "Usage: bifrost api" in capsys.readouterr().out
    assert cli.handle_api(["--help"]) == 0
    assert "Usage: bifrost api" in capsys.readouterr().out
    assert cli.handle_api(["TRACE", "/api/workflows"]) == 1
    assert "Unsupported method" in capsys.readouterr().err
    assert cli.handle_api(["POST", "/api/workflows", "{bad"]) == 1
    assert "Invalid JSON" in capsys.readouterr().err

    body_file = tmp_path / "body.json"
    body_file.write_text('{"ok": true}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            raise RuntimeError("not logged in")

    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)
    assert cli.handle_api(["POST", "/api/workflows", "@body.json"]) == 1
    assert "not logged in" in capsys.readouterr().err
    assert cli.handle_api(["POST", "/api/workflows", "@missing.json"]) == 1
    assert "Error reading file" in capsys.readouterr().err


def test_handle_api_body_file_cannot_escape_invocation_directory(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": true}', encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert cli.handle_api(["POST", "/api/workflows", "@../outside.json"]) == 1
    assert "outside the current working directory" in capsys.readouterr().err


def test_handle_api_body_file_rejects_canonicalized_symlink_escape(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": true}', encoding="utf-8")
    monkeypatch.chdir(workspace)
    realpath = cli.os.path.realpath
    monkeypatch.setattr(
        cli.os.path,
        "realpath",
        lambda path: str(outside) if path == "body.json" else realpath(path),
    )

    assert cli.handle_api(["POST", "/api/workflows", "@body.json"]) == 1
    assert "outside the current working directory" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_api_request_prints_json_text_and_connection_errors(capsys):
    class Response:
        def __init__(self, status_code, *, body=None, text=""):
            self.status_code = status_code
            self._body = body
            self.text = text

        def json(self):
            if isinstance(self._body, Exception):
                raise self._body
            return self._body

    class Client:
        def __init__(self, response=None, exc=None):
            self.response = response
            self.exc = exc
            self.calls = []

        async def get(self, endpoint, **kwargs):
            self.calls.append(("get", endpoint, kwargs))
            if self.exc:
                raise self.exc
            return self.response

        async def post(self, endpoint, **kwargs):
            self.calls.append(("post", endpoint, kwargs))
            if self.exc:
                raise self.exc
            return self.response

    ok = Client(Response(200, body={"ok": True}))
    assert await cli._api_request("GET", "/api/x", None, client=ok) == 0
    assert '"ok": true' in capsys.readouterr().out.lower()

    bad = Client(Response(404, body=ValueError("not json"), text="missing"))
    assert await cli._api_request("GET", "/api/missing", None, client=bad) == 1
    assert "missing" in capsys.readouterr().out

    posted = Client(Response(201, body={"created": True}))
    assert await cli._api_request("POST", "/api/x", {"a": 1}, client=posted) == 0
    assert posted.calls == [("post", "/api/x", {"json": {"a": 1}})]

    connect_error = Client(exc=httpx.ConnectError("down"))
    assert await cli._api_request("GET", "/api/x", None, client=connect_error) == 1
    assert "could not connect" in capsys.readouterr().err

    generic_error = Client(exc=RuntimeError("boom"))
    assert await cli._api_request("GET", "/api/x", None, client=generic_error) == 1
    assert "boom" in capsys.readouterr().err


def test_handle_git_validates_auth_and_arguments(monkeypatch, capsys):
    import types

    git_mod = types.ModuleType("bifrost.git_commands")
    git_mod.EXIT_CLEAN = 0
    git_mod.EXIT_ERROR = 1
    git_mod.RESOLUTION_MAP = {"keep_local": "local", "keep_remote": "remote"}
    for name in (
        "run_git_commit",
        "run_git_diff",
        "run_git_discard",
        "run_git_fetch",
        "run_git_push",
        "run_git_resolve",
        "run_git_status",
    ):
        setattr(git_mod, name, lambda *args, **kwargs: 0)

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            raise RuntimeError("login first")

    monkeypatch.setitem(sys.modules, "bifrost.git_commands", git_mod)
    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)

    assert cli.handle_git([]) == 0
    assert "Usage: bifrost git" in capsys.readouterr().out

    assert cli.handle_git(["status"]) == 1
    assert "login first" in capsys.readouterr().err

    class AuthenticatedFactory:
        @staticmethod
        def get_instance(require_auth=True):
            return object()

    monkeypatch.setattr(cli, "BifrostClient", AuthenticatedFactory)

    assert cli.handle_git(["commit"]) == 1
    assert "commit requires -m" in capsys.readouterr().err

    assert cli.handle_git(["commit", "-m"]) == 1
    assert "requires a commit message" in capsys.readouterr().err

    assert cli.handle_git(["commit", "--bad"]) == 1
    assert "Unknown option" in capsys.readouterr().err

    assert cli.handle_git(["resolve"]) == 1
    assert "resolve requires" in capsys.readouterr().err

    assert cli.handle_git(["resolve", "app.py=bad"]) == 1
    assert "invalid resolution" in capsys.readouterr().err

    assert cli.handle_git(["diff"]) == 1
    assert "diff requires" in capsys.readouterr().err

    assert cli.handle_git(["discard"]) == 1
    assert "discard requires" in capsys.readouterr().err

    assert cli.handle_git(["unknown"]) == 1
    assert "Unknown git subcommand" in capsys.readouterr().err


def test_handle_git_dispatches_to_git_commands(monkeypatch):
    import types

    calls = []
    client = object()

    def record(name, result):
        def _run(*args):
            calls.append((name, args))
            return result

        return _run

    git_mod = types.ModuleType("bifrost.git_commands")
    git_mod.EXIT_CLEAN = 0
    git_mod.EXIT_ERROR = 1
    git_mod.RESOLUTION_MAP = {"keep_local": "local", "keep_remote": "remote"}
    git_mod.run_git_fetch = record("fetch", 10)
    git_mod.run_git_status = record("status", 11)
    git_mod.run_git_commit = record("commit", 12)
    git_mod.run_git_push = record("push", 13)
    git_mod.run_git_resolve = record("resolve", 14)
    git_mod.run_git_diff = record("diff", 15)
    git_mod.run_git_discard = record("discard", 16)

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            assert require_auth is True
            return client

    monkeypatch.setitem(sys.modules, "bifrost.git_commands", git_mod)
    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)

    assert cli.handle_git(["fetch"]) == 10
    assert cli.handle_git(["status"]) == 11
    assert cli.handle_git(["commit", "-m", "sync files"]) == 12
    assert cli.handle_git(["push"]) == 13
    assert cli.handle_git(["resolve", "app.py=keep_local"]) == 14
    assert cli.handle_git(["diff", "app.py"]) == 15
    assert cli.handle_git(["discard", "app.py", "old.py"]) == 16

    assert calls == [
        ("fetch", (client,)),
        ("status", (client,)),
        ("commit", (client, "sync files")),
        ("push", (client,)),
        ("resolve", (client, {"app.py": "keep_local"})),
        ("diff", (client, "app.py")),
        ("discard", (client, ["app.py", "old.py"])),
    ]


def test_push_sync_pull_and_watch_validate_help_and_paths(tmp_path, capsys):
    missing = tmp_path / "missing"

    assert cli.handle_push(["--help"]) == 0
    assert "Usage: bifrost push" in capsys.readouterr().out
    assert cli.handle_push(["--watch"]) == 1
    assert "bifrost watch" in capsys.readouterr().err
    assert cli.handle_push([str(missing)]) == 1
    assert "not a valid file or directory" in capsys.readouterr().err

    assert cli.handle_sync(["--help"]) == 0
    assert "Usage: bifrost sync" in capsys.readouterr().out
    assert cli.handle_sync([str(missing)]) == 1
    assert "not a valid directory" in capsys.readouterr().err

    assert cli.handle_watch(["--help"]) == 0
    assert "Usage: bifrost watch" in capsys.readouterr().out
    assert cli.handle_watch([str(missing)]) == 1
    assert "not a valid directory" in capsys.readouterr().err

    assert cli.handle_pull(["--help"]) == 0
    assert "Usage: bifrost pull" in capsys.readouterr().out
    assert cli.handle_pull(["--bad"]) == 1
    assert "Unknown option" in capsys.readouterr().err
    assert cli.handle_pull(["one", "two"]) == 1
    assert "Unexpected argument" in capsys.readouterr().err
    assert cli.handle_pull([str(missing)]) == 1
    assert "not a valid directory" in capsys.readouterr().err


def test_push_sync_pull_and_watch_report_lock_or_auth_errors(monkeypatch, tmp_path, capsys):
    class RaisingLock:
        def __init__(self, path, action):
            self.path = path
            self.action = action

        def __enter__(self):
            raise cli.WorkspaceLockError("busy", {"pid": 123, "command": "watch"})

    path = tmp_path / "workspace"
    path.mkdir()
    monkeypatch.setattr(cli, "WorkspaceLock", RaisingLock)

    assert cli.handle_push([str(path)]) == 1
    assert "another bifrost session" in capsys.readouterr().err
    assert cli.handle_sync([str(path)]) == 1
    assert "another bifrost session" in capsys.readouterr().err
    assert cli.handle_watch([str(path)]) == 1
    assert "another bifrost session" in capsys.readouterr().err
    assert cli.handle_pull([str(path)]) == 1
    assert "another bifrost session" in capsys.readouterr().err

    class Lock:
        def __init__(self, path, action):
            pass

        def __enter__(self):
            return self

        def __exit__(self):
            pass

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            raise RuntimeError("login required")

    monkeypatch.setattr(cli, "WorkspaceLock", Lock)
    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)

    assert cli.handle_push([str(path)]) == 1
    assert "login required" in capsys.readouterr().err
    assert cli.handle_sync([str(path)]) == 1
    assert "login required" in capsys.readouterr().err
    assert cli.handle_watch([str(path)]) == 1
    assert "login required" in capsys.readouterr().err
    assert cli.handle_pull([str(path)]) == 1
    assert "login required" in capsys.readouterr().err


def test_push_sync_and_pull_dispatch_to_sync_files(monkeypatch, tmp_path):
    calls = []
    locks = []
    client = object()

    class Lock:
        def __init__(self, path, action):
            locks.append((path, action))

        def __enter__(self):
            return self

        def __exit__(self, _exc_type=None, _exc_value=None, _traceback=None):
            locks.append(("exit", None))

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            assert require_auth is True
            return client

    async def sync_files(local_path, **kwargs):
        calls.append(("sync_files", local_path, kwargs))
        return len(calls) + 40

    monkeypatch.setattr(cli, "WorkspaceLock", Lock)
    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)
    monkeypatch.setattr(cli, "_warn_if_git_workspace", lambda path: None)
    monkeypatch.setattr(cli, "_sync_files", sync_files)

    root = tmp_path / "workspace"
    root.mkdir()
    file_path = root / "main.py"
    file_path.write_text("print('ok')\n", encoding="utf-8")

    assert cli.handle_push([str(file_path), "--mirror", "--validate", "--yes"]) == 41
    assert calls[-1][0] == "sync_files"
    assert calls[-1][1] == str(file_path)
    assert calls[-1][2]["mirror"] is True
    assert calls[-1][2]["validate"] is True
    assert calls[-1][2]["force"] is True
    assert calls[-1][2]["client"] is client
    assert calls[-1][2]["single_file"] == str(file_path)
    assert calls[-1][2]["one_way"] is False

    assert cli.handle_sync([str(root), "--mirror", "--validate", "--yes"]) == 42
    assert calls[-1][1] == str(root)
    assert calls[-1][2]["mirror"] is True
    assert calls[-1][2]["validate"] is True
    assert calls[-1][2]["force"] is True
    assert calls[-1][2]["client"] is client

    assert cli.handle_pull([str(root), "--mirror", "--force"]) == 43
    assert calls[-1][1] == str(root)
    assert calls[-1][2]["mirror"] is True
    assert calls[-1][2]["force"] is True
    assert calls[-1][2]["client"] is client
    assert {action for _, action in locks if isinstance(action, str)} == {
        "push",
        "sync",
        "pull",
    }


def test_watch_dispatches_and_stops_server_on_interrupt(monkeypatch, tmp_path, capsys):
    calls = []
    root = tmp_path / "workspace"
    root.mkdir()

    class Lock:
        def __init__(self, path, action):
            calls.append(("lock", action, path))

        def __enter__(self):
            return self

        def __exit__(self, _exc_type=None, _exc_value=None, _traceback=None):
            calls.append(("unlock",))

    class Client:
        api_url = "https://api.example.test"

        def post_sync(self, path, json=None):
            calls.append(("post_sync", path, json))

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            return Client()

    async def push_with_precheck(local_path, **kwargs):
        calls.append(("push_with_precheck", local_path, kwargs))
        return 55

    monkeypatch.setattr(cli, "WorkspaceLock", Lock)
    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)
    monkeypatch.setattr(cli, "_warn_if_git_workspace", lambda path: None)
    monkeypatch.setattr(cli, "_push_with_precheck", push_with_precheck)
    monkeypatch.chdir(tmp_path)

    assert cli.handle_watch([str(root), "--mirror", "--validate", "--force"]) == 55
    assert calls[-2][0] == "push_with_precheck"
    assert calls[-2][1] == str(root)
    assert calls[-2][2]["mirror"] is True
    assert calls[-2][2]["validate"] is True
    assert calls[-2][2]["watch"] is True
    assert calls[-2][2]["force"] is True

    async def interrupted(local_path, **kwargs):
        raise KeyboardInterrupt

    calls.clear()
    monkeypatch.setattr(cli, "_push_with_precheck", interrupted)

    assert cli.handle_watch([str(root)]) == 130
    assert "Stopping watch" in capsys.readouterr().out
    assert calls[-2] == (
        "post_sync",
        "/api/files/watch",
        {"action": "stop", "prefix": "workspace"},
    )
