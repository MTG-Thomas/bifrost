from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock

from bifrost import client
from bifrost.tui.file_select import FileSelectApp, interactive_file_select
from bifrost.tui.progress import ProgressApp, _FileRow
from bifrost.tui.sync_app import SyncApp, SyncResult, SyncRow, _SectionHeader, interactive_sync
from bifrost.tui.theme import BIFROST_THEME, BifrostApp
from bifrost.tui.watch import WatchApp, _BatchRow, _format_row


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


def test_sync_row_formats_truncated_columns_and_cycles_actions(monkeypatch):
    item = {
        "name": "very/long/path/to/workflow.yaml",
        "default_action": "push",
        "valid_actions": ["push", "pull", "skip"],
        "why": "changed remotely and locally",
        "modified": "2026-07-05T12:00:00Z",
        "author": "averylonguser@example.test",
    }
    row = SyncRow(item, item_col_width=12)
    monkeypatch.setattr(row, "_update_label", lambda: None)

    label = row._build_label()

    assert "very/long/p…" in label.plain
    assert "changed remot…" in label.plain
    assert "2026-07-0…" in label.plain
    assert "averylonguser@examp…" in label.plain
    assert row.action == "push"

    row.cycle_forward()
    assert row.action == "pull"
    row.cycle_backward()
    assert row.action == "push"
    row.set_skip()
    assert row.action == "skip"
    row.reset_to_default()
    assert row.action == "push"


def test_sync_app_status_header_confirm_and_cancel(monkeypatch):
    push = {"name": "a.py", "default_action": "push"}
    pull = {"name": "b.py", "default_action": "pull"}
    unknown = {"name": "c.py", "default_action": "archive"}
    app = SyncApp([push, pull, unknown], file_count=1, entity_count=2)

    assert "1 file" in app._status_text()
    assert "2 entities" in app._status_text()
    assert app._build_header_text().plain.startswith("Item")

    rows = [
        SimpleRow(push, "push"),
        SimpleRow(pull, "pull"),
        SimpleRow(unknown, "archive"),
    ]
    monkeypatch.setattr(app, "query", lambda _kind: rows)
    exits: list[SyncResult | None] = []
    monkeypatch.setattr(app, "exit", lambda result=None: exits.append(result))

    app.action_confirm()
    app.action_cancel()

    result = exits[0]
    assert isinstance(result, SyncResult)
    assert result.push == [push]
    assert result.pull == [pull]
    assert result.skip == [unknown]
    assert exits[1] is None


def test_sync_app_composes_grouped_sections_and_focus_actions(monkeypatch):
    file_item = {"name": "file.py", "section": "files", "default_action": "push"}
    entity_item = {"name": "Agent", "section": "entities", "default_action": "pull"}
    other_item = {"name": "other", "default_action": "skip"}
    app = SyncApp([file_item, entity_item, other_item])

    composed = list(app.compose())

    list_view = next(widget for widget in composed if widget.__class__.__name__ == "ListView")
    children = list(getattr(list_view, "_nodes", [])) or list(getattr(list_view, "_pending_children", []))
    assert any(isinstance(child, _SectionHeader) for child in children)
    assert sum(isinstance(child, SyncRow) for child in children) == 3

    row = SyncRow({"name": "row", "default_action": "push", "valid_actions": ["push", "skip"]}, 20)
    monkeypatch.setattr(row, "_update_label", lambda: None)
    monkeypatch.setattr(app, "_get_focused_sync_row", lambda: row)
    app.action_cycle_forward()
    assert row.action == "skip"
    app.action_cycle_backward()
    assert row.action == "push"

    rows = [
        SyncRow({"name": "a", "default_action": "push", "valid_actions": ["push", "skip"]}, 20),
        SyncRow({"name": "b", "default_action": "pull", "valid_actions": ["pull"]}, 20),
    ]
    for sync_row in rows:
        monkeypatch.setattr(sync_row, "_update_label", lambda: None)
    monkeypatch.setattr(app, "query", lambda _kind: rows)
    app.action_reset_all()
    assert [sync_row.action for sync_row in rows] == ["push", "pull"]
    app.action_skip_all()
    assert [sync_row.action for sync_row in rows] == ["skip", "pull"]


@pytest.mark.asyncio
async def test_interactive_sync_non_tty_groups_defaults(monkeypatch):
    monkeypatch.setattr("bifrost.tui.sync_app.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("bifrost.tui.sync_app.sys.stdout.isatty", lambda: True)
    items = [
        {"name": "push.py", "default_action": "push"},
        {"name": "delete.py", "default_action": "delete"},
        {"name": "unknown.py", "default_action": "archive"},
    ]

    empty = await interactive_sync([])
    result = await interactive_sync(items)

    assert empty == SyncResult()
    assert result is not None
    assert [item["name"] for item in result.push] == ["push.py"]
    assert [item["name"] for item in result.delete] == ["delete.py"]
    assert [item["name"] for item in result.skip] == ["unknown.py"]


def test_watch_format_row_truncates_path_and_user():
    row = _format_row(
        48,
        "12:34:56",
        "#fff",
        "!",
        "Push",
        "very/long/path/to/a/workflow/file.py",
        "long-username@example.test",
    )

    assert "12:34:56" in row
    assert "! Push" in row
    assert "…file.py" in row
    assert "long-usernam" in row


def test_batch_row_spinner_and_freeze_update_markup(monkeypatch):
    row = _BatchRow("starting", "green")
    updates: list[str] = []
    monkeypatch.setattr(row, "update", lambda content="": updates.append(str(content)))

    row.set_spinning("Push", "workflow.py")
    row.update_spinner("*")
    row.freeze("success", "+", "Push", "workflow.py", "alice")

    assert any("* Push" in update for update in updates)
    assert any("+ Push" in update for update in updates)
    assert any("alice" in update for update in updates)


def test_watch_app_logs_update_status_and_pending(monkeypatch):
    app = WatchApp("workspace", "session-123456")
    rows: list[tuple[str, str, str, str, str]] = []
    monkeypatch.setattr(
        app,
        "_add_row",
        lambda level, icon, action, path, user="": rows.append(
            (level, icon, action, path, user)
        ),
    )
    status_updates: list[str] = []
    monkeypatch.setattr(app, "_update_status", lambda: status_updates.append(app._format_status()))

    assert "0 pending" in app._format_status()
    app.log_push("push.py")
    app.log_pull("pull.py", "alice")
    app.log_delete("old.py", "bob")
    app.log_error("failed")
    app.log_info("watching")
    app.log_success("done")
    app.set_pending(3)

    assert rows[:5] == [
        ("push", "→", "Push", "push.py", ""),
        ("pull", "←", "Pull", "pull.py", "alice"),
        ("warning", "✗", "Delete", "old.py", "bob"),
        ("error", "⚠", "Error", "failed", ""),
        ("info", "·", "Info", "watching", ""),
    ]
    assert rows[5][0] == "success"
    assert rows[5][3] == "done"
    assert app._pending == 3
    assert any("3 pending" in status for status in status_updates)


@pytest.mark.asyncio
async def test_progress_app_do_work_records_item_and_post_errors(monkeypatch):
    async def worker(data, name):
        if name == "bad.py":
            raise RuntimeError("worker failed")

    async def post(errors):
        assert errors == ["bad.py: worker failed"]
        raise RuntimeError("post failed")

    app = ProgressApp(
        "Pushing files",
        [("good.py", object()), ("bad.py", object())],
        worker,
        post,
    )
    app._rows = [_FakeProgressRow("good.py"), _FakeProgressRow("bad.py")]
    bar = _FakeProgressBar()
    scroll = _FakeScroll()
    summary = _FakeSummary()

    def query_one(selector, *_args):
        if selector == "#file-list":
            return scroll
        if selector == "#summary":
            return summary
        return bar

    monkeypatch.setattr(app, "query_one", query_one)

    await app._do_work()

    assert app._done is True
    assert app._errors == ["bad.py: worker failed", "post-processing: post failed"]
    assert app._rows[0].done is True
    assert app._rows[1].error == "worker failed"
    assert bar.advanced == [1, 1]
    assert scroll.scrolls == 2
    assert "2 error(s)" in summary.values[-1]


@pytest.mark.asyncio
async def test_progress_app_success_post_summary_exits(monkeypatch):
    async def worker(_data, _name):
        return None

    async def post(errors):
        assert errors == []
        return "All done"

    app = ProgressApp("Pulling files", [("a.py", object())], worker, post)
    app._rows = [_FakeProgressRow("a.py")]
    bar = _FakeProgressBar()
    scroll = _FakeScroll()
    summary = _FakeSummary()
    exits: list[list[str]] = []
    monkeypatch.setattr(app, "exit", lambda result=None: exits.append(result))
    monkeypatch.setattr("bifrost.tui.progress.asyncio.sleep", AsyncMock())

    def query_one(selector, *_args):
        if selector == "#file-list":
            return scroll
        if selector == "#summary":
            return summary
        return bar

    monkeypatch.setattr(app, "query_one", query_one)

    await app._do_work()

    assert app._errors == []
    assert summary.values[-1].endswith("All done")
    assert exits == [[]]


class SimpleRow:
    def __init__(self, item, action):
        self.item = item
        self.action = action


class _FakeProgressRow:
    def __init__(self, name):
        self.name = name
        self.frames: list[str] = []
        self.done = False
        self.error: str | None = None

    def set_working(self, frame):
        self.frames.append(frame)

    def set_done(self):
        self.done = True

    def set_error(self, err):
        self.error = err


class _FakeProgressBar:
    def __init__(self):
        self.advanced: list[int] = []

    def advance(self, value):
        self.advanced.append(value)


class _FakeScroll:
    def __init__(self):
        self.scrolls = 0

    def scroll_end(self, animate=False):
        self.scrolls += 1


class _FakeSummary:
    def __init__(self):
        self.values: list[str] = []

    def update(self, value):
        self.values.append(value)
