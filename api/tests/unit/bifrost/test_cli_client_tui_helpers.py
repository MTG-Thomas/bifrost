from __future__ import annotations

import httpx
import pytest

from bifrost import client
from bifrost.tui.file_select import FileSelectApp, interactive_file_select
from bifrost.tui.progress import ProgressApp, _FileRow
from bifrost.tui.theme import BIFROST_THEME, BifrostApp


def _response(status_code: int, body: object) -> httpx.Response:
    request = httpx.Request("GET", "https://api.example.test/api/demo")
    return httpx.Response(status_code, json=body, request=request)


def test_raise_for_status_with_detail_prefers_api_detail_fields():
    for body, expected in [
        ({"detail": "detail text"}, "detail text"),
        ({"message": "message text"}, "message text"),
        ({"error": "error text"}, "error text"),
    ]:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.raise_for_status_with_detail(_response(400, body))

        assert expected in str(exc_info.value)
        assert "400 Bad Request" in str(exc_info.value)


def test_raise_for_status_with_detail_falls_back_for_non_mapping_json():
    response = _response(418, ["not", "a", "mapping"])

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.raise_for_status_with_detail(response)

    assert exc_info.value.response is response
    assert "418" in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_with_5xx_retry_skips_non_idempotent_methods(monkeypatch):
    calls: list[float] = []

    async def no_sleep(delay: float) -> None:
        calls.append(delay)

    sent = 0

    async def do_send() -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(503)

    monkeypatch.setattr(client.asyncio, "sleep", no_sleep)

    response = await client._send_with_5xx_retry("post", do_send)

    assert response.status_code == 503
    assert sent == 1
    assert calls == []


@pytest.mark.asyncio
async def test_send_with_5xx_retry_uses_backoff_until_success(monkeypatch):
    sleeps: list[float] = []
    statuses = [504, 503, 200]

    async def no_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def do_send() -> httpx.Response:
        return httpx.Response(statuses.pop(0))

    monkeypatch.setattr(client.asyncio, "sleep", no_sleep)

    response = await client._send_with_5xx_retry("GET", do_send)

    assert response.status_code == 200
    assert sleeps == list(client.SDK_RETRY_BACKOFF_SECONDS[:2])


def test_send_sync_with_5xx_retry_exhausts_retry_budget(monkeypatch):
    sleeps: list[float] = []
    sent = 0

    def do_send() -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(502)

    monkeypatch.setattr(client.time, "sleep", lambda delay: sleeps.append(delay))

    response = client._send_sync_with_5xx_retry("delete", do_send)

    assert response.status_code == 502
    assert sent == 1 + len(client.SDK_RETRY_BACKOFF_SECONDS)
    assert sleeps == list(client.SDK_RETRY_BACKOFF_SECONDS)


def test_file_select_builds_plain_header_labels_and_counts():
    app = FileSelectApp(
        [
            {
                "status": "changed with warning",
                "path": "averyverylongfilename.py",
                "time": "Jul 05 09:30",
            }
        ],
        [("status", "Status", 12), ("path", "Path", 10), ("time", "Time", 12)],
        prompt_text="Pick files",
        subtitle_text="Demo",
    )

    assert app.title == "Pick files"
    assert app.sub_title == "Demo"
    assert app._format_count(1) == "  1/1 selected"

    header = app._build_header_text()
    assert header.plain == "Status        Path        Time        "

    label = app._build_label(app._items[0])
    assert "changed with warning" in label.plain
    assert "averyvery" in label.plain
    assert "averyverylongfilename.py" not in label.plain


@pytest.mark.asyncio
async def test_interactive_file_select_returns_all_items_when_not_tty(monkeypatch):
    items = [{"path": "main.py"}]
    monkeypatch.setattr("bifrost.tui.file_select.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("bifrost.tui.file_select.sys.stdout.isatty", lambda: True)

    assert await interactive_file_select(items, [("path", "Path", 20)]) is items
    assert await interactive_file_select([], [("path", "Path", 20)]) == []


def test_progress_file_row_status_updates_track_file_name():
    row = _FileRow("main.py")

    updates: list[str] = []
    row.update = lambda content="": updates.append(str(content))  # type: ignore[method-assign]

    row.set_working("*")
    assert "main.py" in updates[-1]
    assert "*" in updates[-1]

    row.set_done()
    assert "main.py" in updates[-1]

    row.set_error("failed")
    assert "main.py" in updates[-1]
    assert "failed" in updates[-1]


def test_progress_app_dismiss_and_force_quit_exit_with_errors(monkeypatch):
    app = ProgressApp("Pushing files", [], lambda _data, _name: None)
    exits: list[list[str]] = []
    monkeypatch.setattr(app, "exit", lambda result=None: exits.append(result))

    app.action_dismiss()
    assert exits == []

    app._errors.append("main.py: failed")
    app._done = True
    app.action_dismiss()
    app.action_force_quit()

    assert exits == [["main.py: failed"], ["main.py: failed"]]


def test_bifrost_app_registers_theme_and_help_quit_exits(monkeypatch):
    app = BifrostApp()
    exits: list[object] = []
    monkeypatch.setattr(app, "exit", lambda result=None: exits.append(result))

    assert BIFROST_THEME.name == "bifrost"
    assert app.theme == "bifrost"

    app.action_help_quit()
    assert exits == [None]
