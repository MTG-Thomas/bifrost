"""Interactive file selector TUI for push/pull operations."""

from __future__ import annotations

import sys

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, SelectionList, Static
from textual.widgets.selection_list import Selection
from rich.text import Text

from bifrost.tui.theme import BifrostApp


class FileSelectApp(BifrostApp[list[dict[str, str]] | None]):
    """Full-screen file selector with checkboxes.

    Returns list of selected item dicts, or None if cancelled.
    """

    CSS = """
    SelectionList {
        height: 1fr;
        margin: 0 2;
        border: none;
        padding: 0;
        background: #0d1117;
    }
    #column-header {
        height: 1;
        margin: 1 2 0 2;
        padding: 0 6;
        color: #6e7681;
    }
    #count {
        dock: bottom;
        height: 1;
        margin: 0 2;
        color: #6e7681;
    }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Confirm", show=True, priority=True),
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False, priority=True),
        Binding("ctrl+q", "cancel", "Cancel", show=False, priority=True),
        Binding("a", "select_all", "All", show=True, priority=True),
        Binding("n", "select_none", "None", show=True, priority=True),
    ]

    def __init__(
        self,
        items: list[dict[str, str]],
        columns: list[tuple[str, str, int]],
        prompt_text: str = "Select files",
        subtitle_text: str = "",
    ) -> None:
        super().__init__()
        self._items = items
        self._columns = columns
        self.title = prompt_text
        if subtitle_text:
            self.sub_title = subtitle_text

    def _build_header_text(self) -> Text:
        """Build a Rich Text header row matching column widths."""
        text = Text()
        for i, (_, header, width) in enumerate(self._columns):
            text.append(header.ljust(width), style="bold #6e7681")
            if i < len(self._columns) - 1:
                text.append("  ")
        return text

    def _build_label(self, item: dict[str, str]) -> Text:
        """Build a Rich Text label from an item dict using column definitions."""
        text = Text()
        for i, (key, _, width) in enumerate(self._columns):
            val = item.get(key) or ""
            # Color the status column with bold so it's visible on highlight
            if key == "status":
                # Strip warning suffix for color lookup
                base_val = val.split(" ")[0] if " " in val else val
                status_colors = {
                    "new": "bold #2ea043",
                    "changed": "bold #d29922",
                    "delete": "bold #da3633",
                }
                color = status_colors.get(base_val, "")
                if color:
                    text.append(val.ljust(width), style=color)
                else:
                    text.append(val.ljust(width))
            else:
                # Truncate with ellipsis if too long
                if len(val) > width:
                    val = val[: width - 1] + "\u2026"
                text.append(val.ljust(width))
            if i < len(self._columns) - 1:
                text.append("  ")
        return text

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._build_header_text(), id="column-header")
        selections: list[Selection[int]] = []
        for i, item in enumerate(self._items):
            label = self._build_label(item)
            # Value is the index -- used to map back to self._items on confirm
            selections.append(Selection(label, i, initial_state=True))
        yield SelectionList(*selections)
        yield Static(self._format_count(len(self._items)), id="count")
        yield Footer()

    def _format_count(self, selected: int) -> str:
        return f"  {selected}/{len(self._items)} selected"

    def on_selection_list_selection_toggled(self) -> None:
        """Update count when selections change."""
        sel_list = self.query_one(SelectionList)
        self.query_one("#count", Static).update(
            self._format_count(len(sel_list.selected))
        )

    def action_confirm(self) -> None:
        sel_list = self.query_one(SelectionList)
        selected_indices = set(sel_list.selected)
        result = [
            self._items[i]
            for i in range(len(self._items))
            if i in selected_indices
        ]
        self.exit(result)

    def action_cancel(self) -> None:
        self.exit(None)

    def action_select_all(self) -> None:
        self.query_one(SelectionList).select_all()

    def action_select_none(self) -> None:
        self.query_one(SelectionList).deselect_all()


async def interactive_file_select(
    items: list[dict[str, str]],
    columns: list[tuple[str, str, int]],
    prompt_text: str = "Select files",
    subtitle_text: str = "",
) -> list[dict[str, str]] | None:
    """Run the file selector TUI. Drop-in replacement for _interactive_file_select.

    Returns selected items, or None if cancelled. Returns all items if not a TTY.
    Must be called from an async context.
    """
    if not items:
        return []

    # Non-TTY fallback: return all items (same as --force behavior)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return items

    app = FileSelectApp(items, columns, prompt_text, subtitle_text)
    return await app.run_async()
