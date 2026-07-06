"""Focused unit coverage for CLI watch/sync helper branches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bifrost import cli
from bifrost.cli import (
    _WatchState,
    _extract_error_detail,
    _process_incoming,
    _process_watch_batch,
    _process_watch_deletes,
    _push_with_precheck,
)


def test_extract_error_detail_prefers_json_detail_and_message():
    detail_resp = MagicMock()
    detail_resp.json.return_value = {"detail": "bad path", "message": "fallback"}

    message_resp = MagicMock()
    message_resp.json.return_value = {"message": "message only"}

    assert _extract_error_detail(detail_resp) == "bad path"
    assert _extract_error_detail(message_resp) == "message only"


def test_extract_error_detail_falls_back_to_bounded_text():
    resp = MagicMock()
    resp.json.side_effect = ValueError("not json")
    resp.text = "x" * 250

    assert _extract_error_detail(resp) == "x" * 200


def test_extract_error_detail_handles_unreadable_body():
    resp = MagicMock()
    resp.json.side_effect = ValueError("not json")
    type(resp).text = property(lambda _self: (_ for _ in ()).throw(RuntimeError("boom")))

    assert _extract_error_detail(resp) == ""


@pytest.mark.asyncio
async def test_push_with_precheck_reports_auth_failure(capsys):
    with patch.object(cli.BifrostClient, "get_instance", side_effect=RuntimeError("login needed")):
        result = await _push_with_precheck("missing")

    assert result == 1
    assert "login needed" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_push_with_precheck_routes_watch_and_sync(tmp_path):
    client = object()
    watch_dir = tmp_path / "apps" / "demo"
    watch_dir.mkdir(parents=True)

    with (
        patch("pathlib.Path.cwd", return_value=tmp_path),
        patch.object(cli, "_watch_and_push", AsyncMock(return_value=17)) as watch_push,
        patch.object(cli, "_sync_files", AsyncMock(return_value=23)) as sync_files,
    ):
        watch_result = await _push_with_precheck(str(watch_dir), watch=True, client=client)
        sync_result = await _push_with_precheck(str(watch_dir), force=True, client=client)

    assert watch_result == 17
    watch_push.assert_awaited_once()
    assert watch_push.await_args.kwargs["repo_prefix"] == "apps/demo"

    assert sync_result == 23
    sync_files.assert_awaited_once()
    assert sync_files.await_args.kwargs["repo_prefix"] == "apps/demo"
    assert sync_files.await_args.kwargs["force"] is True


@pytest.mark.asyncio
async def test_process_watch_deletes_counts_404_and_forgets_hash(tmp_path):
    state = _WatchState(tmp_path)
    state.set_known_hash("repo/old.txt", "hash")
    missing = tmp_path / "old.txt"

    response = SimpleNamespace(status_code=404)
    error = RuntimeError("gone")
    error.response = response  # type: ignore[attr-defined]
    client = SimpleNamespace(post=AsyncMock(side_effect=error))

    count, rels = await _process_watch_deletes(
        client, {str(missing)}, tmp_path, "repo", state
    )

    assert count == 1
    assert rels == ["old.txt"]
    assert state.get_known_hash("repo/old.txt") is None
    client.post.assert_awaited_once_with(
        "/api/files/delete",
        json={"path": "repo/old.txt", "location": "workspace", "mode": "cloud"},
        headers={"X-Bifrost-Watch-Session": state.session_id},
    )


@pytest.mark.asyncio
async def test_process_watch_deletes_reports_non_404_error_without_counting(tmp_path, capsys):
    state = _WatchState(tmp_path)
    missing = tmp_path / "old.txt"

    response = SimpleNamespace(status_code=500)
    error = RuntimeError("server error")
    error.response = response  # type: ignore[attr-defined]
    client = SimpleNamespace(post=AsyncMock(side_effect=error))

    count, rels = await _process_watch_deletes(client, {str(missing)}, tmp_path, "", state)

    assert count == 0
    assert rels == []
    assert "Delete error for old.txt: server error" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_process_watch_batch_skips_known_hash_noop_push(tmp_path):
    state = _WatchState(tmp_path)
    changed = tmp_path / "same.txt"
    changed.write_bytes(b"same\n")
    state.set_known_hash("same.txt", cli._hash_for_cache(b"same\n"))
    client = SimpleNamespace(post=AsyncMock())

    with patch.object(cli, "_auto_validate_app", AsyncMock()) as auto_validate:
        await _process_watch_batch(client, {str(changed)}, set(), tmp_path, "", state)

    client.post.assert_not_awaited()
    auto_validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_watch_batch_logs_http_error_detail(tmp_path, capsys):
    state = _WatchState(tmp_path)
    changed = tmp_path / "bad.txt"
    changed.write_text("bad\n")
    resp = MagicMock(status_code=422)
    resp.json.return_value = {"detail": "not allowed"}
    client = SimpleNamespace(post=AsyncMock(return_value=resp))

    with patch.object(cli, "_auto_validate_app", AsyncMock()) as auto_validate:
        await _process_watch_batch(client, {str(changed)}, set(), tmp_path, "", state)

    out = capsys.readouterr().out
    assert "bad.txt: HTTP 422" in out
    assert "not allowed" in out
    auto_validate.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_incoming_skips_when_cache_matches(tmp_path):
    state = _WatchState(tmp_path)
    state.set_known_hash("repo/same.txt", cli._hash_for_cache(b"same"))
    client = SimpleNamespace(
        post=AsyncMock(
            return_value=SimpleNamespace(
                status_code=200,
                json=lambda: {"content": "c2FtZQ=="},
            )
        )
    )

    await _process_incoming(
        client,
        [(["repo/same.txt"], "Alice")],
        [],
        tmp_path,
        "repo",
        state,
    )

    assert not (tmp_path / "same.txt").exists()


@pytest.mark.asyncio
async def test_process_incoming_forgets_hash_when_write_fails(tmp_path):
    state = _WatchState(tmp_path)
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    client = SimpleNamespace(
        post=AsyncMock(
            return_value=SimpleNamespace(
                status_code=200,
                json=lambda: {"content": "bmV3"},
            )
        )
    )

    await _process_incoming(
        client,
        [(["repo/blocked/file.txt"], "Alice")],
        [],
        tmp_path,
        "repo",
        state,
    )

    assert state.get_known_hash("repo/blocked/file.txt") is None


@pytest.mark.asyncio
async def test_process_incoming_delete_forgets_stale_hash_for_missing_file(tmp_path):
    state = _WatchState(tmp_path)
    state.set_known_hash("repo/missing.txt", "stale")
    client = SimpleNamespace(post=AsyncMock())

    await _process_incoming(
        client,
        [],
        [(["repo/missing.txt"], "Alice")],
        tmp_path,
        "repo",
        state,
    )

    assert state.get_known_hash("repo/missing.txt") is None
