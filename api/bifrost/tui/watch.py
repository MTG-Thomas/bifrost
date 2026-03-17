"""Watch mode dashboard TUI."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

from bifrost.tui.theme import BifrostApp

_SPINNER = "\u280b\u2819\u2838\u2830\u2826\u2807"


class _BatchRow(Static):
    """A single log row that can animate a spinner then freeze."""

    DEFAULT_CSS = """
    _BatchRow {
        height: auto;
        padding: 0 2;
    }
    """

    def __init__(self, text: str, style: str = "") -> None:
        markup = f"  [{style}]{text}[/]" if style else f"  {text}"
        super().__init__(markup)

    def set_spinning(self, label: str) -> None:
        """Start showing spinner text (called each frame)."""
        self._label = label

    def update_spinner(self, frame: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.update(f"  [#7aa2f7]{ts}  {frame} {self._label}[/]")

    def freeze(self, level: str, icon: str, text: str) -> None:
        """Finalize this row with a static message."""
        ts = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "push": "#7aa2f7",
            "pull": "#9ece6a",
            "success": "#9ece6a",
            "error": "#f7768e",
            "warning": "#e0af68",
            "info": "#6e7681",
        }
        color = color_map.get(level, "")
        if color:
            self.update(f"  [{color}]{ts}  {icon} {text}[/]")
        else:
            self.update(f"  {ts}  {icon} {text}")


class WatchApp(BifrostApp[None]):
    """Persistent watch mode dashboard with scrolling activity log."""

    CSS = """
    #activity-log {
        height: 1fr;
        margin: 0 0;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        padding: 0 2;
        background: #21262d;
        color: #6e7681;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Stop watching", priority=True),
        Binding("ctrl+q", "quit", "Stop watching", show=False, priority=True),
    ]

    def __init__(self, workspace: str, session_id: str) -> None:
        super().__init__()
        self.title = f"bifrost watch \u2014 {workspace}"
        self.sub_title = f"session {session_id[:8]}"
        self._pending = 0
        self._last_sync = "\u2014"
        self._work_coro: Coroutine[Any, Any, None] | None = None

    def set_work(self, coro: Coroutine[Any, Any, None]) -> None:
        """Set the coroutine to run as a worker when the app mounts."""
        self._work_coro = coro

    async def on_mount(self) -> None:
        if self._work_coro is not None:
            self.run_worker(self._work_coro)

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="activity-log")
        yield Static(self._format_status(), id="status-bar")
        yield Footer()

    def _format_status(self) -> str:
        return f"  Watching \u00b7 {self._pending} pending \u00b7 last sync {self._last_sync}  "

    def _update_status(self) -> None:
        try:
            self.query_one("#status-bar", Static).update(self._format_status())
        except Exception:
            pass

    def _add_row(self, level: str, icon: str, text: str) -> _BatchRow:
        """Add a finalized row to the log."""
        row = _BatchRow("")
        try:
            scroll = self.query_one("#activity-log", VerticalScroll)
            scroll.mount(row)
            scroll.scroll_end(animate=False)
        except Exception:
            pass
        row.freeze(level, icon, text)
        return row

    def create_batch_row(self, label: str) -> _BatchRow:
        """Create a row with a spinner for an in-progress operation."""
        row = _BatchRow("")
        row.set_spinning(label)
        try:
            scroll = self.query_one("#activity-log", VerticalScroll)
            scroll.mount(row)
            scroll.scroll_end(animate=False)
        except Exception:
            pass
        return row

    async def spin_row(self, row: _BatchRow) -> None:
        """Animate a spinner on a row until cancelled."""
        try:
            i = 0
            while True:
                row.update_spinner(_SPINNER[i % len(_SPINNER)])
                i += 1
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            pass

    def log_push(self, filename: str) -> None:
        self._add_row("push", "\u2192", f"Push  {filename}")

    def log_pull(self, filename: str, user: str = "") -> None:
        suffix = f"  ({user})" if user else ""
        self._add_row("pull", "\u2190", f"Pull  {filename}{suffix}")

    def log_delete(self, filename: str, user: str = "") -> None:
        suffix = f"  ({user})" if user else ""
        self._add_row("warning", "\u2717", f"Delete  {filename}{suffix}")

    def log_success(self, message: str) -> None:
        self._last_sync = datetime.now().strftime("%H:%M:%S")
        self._update_status()
        self._add_row("success", "\u2713", message)

    def log_error(self, message: str) -> None:
        self._add_row("error", "\u26a0", message)

    def log_info(self, message: str) -> None:
        self._add_row("info", "\u00b7", message)

    def set_pending(self, count: int) -> None:
        self._pending = count
        self._update_status()
