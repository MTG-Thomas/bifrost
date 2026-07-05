"""A failed server file-listing must ABORT the sync, never mass-push.

Regression guard: `_sync_files` used to swallow any non-200 (or exception) from
`/api/files/list` and proceed with an empty server view — so every local file
looked "new locally" and got pushed. On an auth/permission failure (403/401)
that silently overwrites the server with the entire local tree. The fetch is
now fatal: bail with rc=1 and write NOTHING.
"""
import asyncio
import base64

import pytest

from bifrost import cli


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FailingListClient:
    """Server rejects the listing; records any write attempts."""

    def __init__(self, status_code: int, payload: dict | None = None):
        self._status = status_code
        self._payload = payload
        self.writes: list[dict] = []

    async def post(self, url: str, json: dict | None = None):  # noqa: A002
        if url == "/api/files/list":
            return _Resp(self._status, self._payload)
        if url == "/api/files/write":
            self.writes.append(json or {})
            return _Resp(204)
        return _Resp(200, {})

    async def get(self, url: str):
        return _Resp(404)


class _SyncClient:
    def __init__(
        self,
        *,
        files_metadata: list[dict] | None = None,
        read_payload: dict | None = None,
        validate_payload: dict | None = None,
    ):
        self.files_metadata = files_metadata or []
        self.read_payload = read_payload or {}
        self.validate_payload = validate_payload or {"errors": [], "warnings": []}
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    async def post(self, url: str, json: dict | None = None):  # noqa: A002
        self.posts.append((url, json or {}))
        if url == "/api/files/list":
            return _Resp(200, {"files_metadata": self.files_metadata})
        if url == "/api/files/write":
            return _Resp(204)
        if url == "/api/files/read":
            return _Resp(200, self.read_payload)
        if url.endswith("/validate"):
            return _Resp(200, self.validate_payload)
        return _Resp(204)

    async def get(self, url: str):
        self.gets.append(url)
        if url == "/api/applications/app":
            return _Resp(200, {"id": "app-1"})
        return _Resp(404)


def _fake_collect(path, repo_prefix, single_file=None):
    files = {
        f"app/file{i}.txt": base64.b64encode(f"content {i}".encode()).decode()
        for i in range(1, 4)
    }
    return files, 0


@pytest.mark.parametrize("status_code", [403, 401, 500])
def test_failed_list_aborts_without_pushing(monkeypatch, capsys, tmp_path, status_code):
    monkeypatch.setattr(cli, "_is_tty", False)
    monkeypatch.setattr(cli, "_collect_push_files", _fake_collect)
    client = _FailingListClient(status_code, {"detail": "Forbidden"})

    rc = asyncio.run(
        cli._sync_files(str(tmp_path), repo_prefix="app", client=client, one_way=True)
    )

    assert rc == 1, "a failed listing must abort with a non-zero exit code"
    assert client.writes == [], "no files may be pushed when the listing failed"
    err = capsys.readouterr().err
    assert "Aborting" in err
    assert str(status_code) in err


def test_list_network_error_aborts(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_is_tty", False)
    monkeypatch.setattr(cli, "_collect_push_files", _fake_collect)

    class _BoomClient:
        def __init__(self):
            self.writes: list[dict] = []

        async def post(self, url: str, json: dict | None = None):  # noqa: A002
            if url == "/api/files/list":
                raise ConnectionError("connection refused")
            if url == "/api/files/write":
                self.writes.append(json or {})
                return _Resp(204)
            return _Resp(200, {})

    client = _BoomClient()
    rc = asyncio.run(
        cli._sync_files(str(tmp_path), repo_prefix="app", client=client, one_way=True)
    )

    assert rc == 1
    assert client.writes == []
    assert "Aborting" in capsys.readouterr().err


def test_one_way_sync_pushes_new_files_with_line_progress(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_is_tty", False)
    local_file = tmp_path / "main.py"
    local_file.write_text("print('ok')\n", encoding="utf-8")
    client = _SyncClient(validate_payload={"warnings": [{"message": "minor"}]})

    rc = asyncio.run(
        cli._sync_files(
            str(tmp_path),
            repo_prefix="apps/app",
            client=client,
            one_way=True,
            validate=True,
        )
    )

    assert rc == 0
    write_calls = [call for call in client.posts if call[0] == "/api/files/write"]
    assert len(write_calls) == 1
    assert write_calls[0][1]["path"] == "apps/app/main.py"
    assert client.gets == ["/api/applications/app"]
    assert any(call[0] == "/api/applications/app-1/validate" for call in client.posts)
    out = capsys.readouterr().out
    assert "Scanning files" in out
    assert "Uploading 1/1 main.py" in out
    assert "Warnings (1)" in out


def test_bidirectional_sync_pulls_server_only_file(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_is_tty", False)
    content = base64.b64encode(b"remote\n").decode("ascii")
    client = _SyncClient(
        files_metadata=[
            {
                "path": "apps/app/remote.txt",
                "etag": "etag",
                "content_hash": "hash",
                "last_modified": "2026-07-05T10:00:00+00:00",
                "updated_by": "dev@example.test",
            }
        ],
        read_payload={"content": content},
    )

    rc = asyncio.run(
        cli._sync_files(str(tmp_path), repo_prefix="apps/app", client=client)
    )

    assert rc == 0
    assert (tmp_path / "remote.txt").read_text(encoding="utf-8") == "remote\n"
    read_calls = [call for call in client.posts if call[0] == "/api/files/read"]
    assert read_calls == [
        (
            "/api/files/read",
            {
                "path": "apps/app/remote.txt",
                "mode": "cloud",
                "location": "workspace",
                "binary": True,
            },
        )
    ]
    assert "1 pulled" in capsys.readouterr().out


def test_sync_reports_read_errors(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_is_tty", False)

    class ReadFailClient(_SyncClient):
        async def post(self, url: str, json: dict | None = None):  # noqa: A002
            if url == "/api/files/read":
                self.posts.append((url, json or {}))
                return _Resp(500)
            return await super().post(url, json=json)

    client = ReadFailClient(
        files_metadata=[
            {
                "path": "apps/app/remote.txt",
                "etag": "etag",
                "content_hash": "hash",
                "last_modified": "bad-date",
                "updated_by": "dev@example.test",
            }
        ],
    )

    rc = asyncio.run(
        cli._sync_files(str(tmp_path), repo_prefix="apps/app", client=client)
    )

    assert rc == 1
    err = capsys.readouterr().err
    assert "Pull remote.txt" in err
    assert "HTTP 500" in err
