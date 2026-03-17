"""Push/pull progress TUI with per-file status and progress bar."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, ProgressBar, Static

from bifrost.tui.theme import BifrostApp

# Spinner frames for in-progress animation
_SPINNER = "\u280b\u2819\u2838\u2830\u2826\u2807"


class _FileRow(Static):
    """A single file row that updates in-place: spinner → ✓/✗."""

    DEFAULT_CSS = """
    _FileRow {
        height: 1;
        padding: 0 2;
    }
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"  [dim]\u2800[/] {name}")
        self.file_name = name

    def set_working(self, frame: str) -> None:
        self.update(f"  [#7aa2f7]{frame}[/] {self.file_name}")

    def set_done(self) -> None:
        self.update(f"  [#9ece6a]\u2713[/] {self.file_name}")

    def set_error(self, err: str) -> None:
        self.update(f"  [#f7768e]\u2717[/] {self.file_name}  [dim]{err}[/]")


class ProgressApp(BifrostApp[list[str]]):
    """Displays push/pull progress with per-file status and progress bar.

    Returns a list of error strings (empty = all succeeded).
    Work is executed inside the app via run_worker.
    """

    CSS = """
    #file-list {
        height: 1fr;
        margin: 0 0;
    }
    ProgressBar {
        margin: 0 2 0 2;
    }
    #summary {
        height: auto;
        margin: 0 2;
        color: #6e7681;
    }
    """

    BINDINGS = [
        Binding("enter", "dismiss", "Continue", show=False),
        Binding("escape", "dismiss", "Continue", show=False),
        Binding("ctrl+c", "force_quit", "Quit", show=False, priority=True),
        Binding("ctrl+q", "force_quit", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        file_items: list[tuple[str, Any]],
        worker_fn: Callable[[Any, str], Awaitable[None]],
        post_fn: Callable[[list[str]], Awaitable[str]] | None = None,
    ) -> None:
        """
        Args:
            title: "Pushing files" or "Pulling files"
            file_items: list of (display_name, work_data) tuples
            worker_fn: async fn(work_data, display_name) that processes one file.
                       Raise on error.
            post_fn: optional async fn(errors) called after all items complete.
                     Returns a summary string to display before exit.
        """
        super().__init__()
        self.title = title
        self._file_items = file_items
        self._worker_fn = worker_fn
        self._post_fn = post_fn
        self._errors: list[str] = []
        self._done = False
        self._rows: list[_FileRow] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="file-list"):
            for name, _ in self._file_items:
                row = _FileRow(name)
                self._rows.append(row)
                yield row
        yield ProgressBar(total=len(self._file_items))
        yield Static("", id="summary")
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._do_work())

    async def _do_work(self) -> None:
        bar = self.query_one(ProgressBar)
        scroll = self.query_one("#file-list", VerticalScroll)

        for i, (name, data) in enumerate(self._file_items):
            row = self._rows[i]

            # Animate spinner while working
            spinner_task = asyncio.create_task(self._spin(row))
            try:
                await self._worker_fn(data, name)
                spinner_task.cancel()
                row.set_done()
            except Exception as e:
                spinner_task.cancel()
                self._errors.append(f"{name}: {e}")
                row.set_error(str(e))

            bar.advance(1)
            # Auto-scroll to keep current item visible
            scroll.scroll_end(animate=False)

        # Run post-work (manifest import, etc.) and get summary
        summary_text = ""
        if self._post_fn:
            summary_widget = self.query_one("#summary", Static)
            summary_widget.update("  [dim]Applying...[/]")
            try:
                summary_text = await self._post_fn(self._errors)
            except Exception as e:
                self._errors.append(f"post-processing: {e}")

        self._done = True
        summary = self.query_one("#summary", Static)

        if self._errors:
            if summary_text:
                summary.update(f"  {summary_text}  [dim]\u2014 press Enter[/]")
            else:
                summary.update(f"  [#f7768e]{len(self._errors)} error(s)[/]  [dim]\u2014 press Enter[/]")
        else:
            if summary_text:
                summary.update(f"  [#9ece6a]\u2713[/] {summary_text}")
            await asyncio.sleep(1.0)
            self.exit(self._errors)

    async def _spin(self, row: _FileRow) -> None:
        """Animate a spinner on the row until cancelled."""
        try:
            i = 0
            while True:
                row.set_working(_SPINNER[i % len(_SPINNER)])
                i += 1
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            pass

    def action_dismiss(self) -> None:
        if self._done:
            self.exit(self._errors)

    def action_force_quit(self) -> None:
        self.exit(self._errors)
