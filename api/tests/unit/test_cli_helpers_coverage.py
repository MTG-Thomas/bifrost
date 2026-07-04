import base64
import os
from types import SimpleNamespace

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
