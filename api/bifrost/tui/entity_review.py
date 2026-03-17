"""Entity changes review TUI for push operations."""

from __future__ import annotations

import sys

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, SelectionList, Static
from textual.widgets.selection_list import Selection
from rich.text import Text

from bifrost.tui.theme import BifrostApp

# Action → bold style for visibility on highlighted rows
_ACTION_STYLES = {
    "add": "bold #9ece6a",
    "update": "bold #e0af68",
    "delete": "bold #f7768e",
    "keep": "bold #6e7681",
}


class EntityReviewApp(BifrostApp[list[dict[str, str]] | None]):
    """Entity changes selector with checkboxes.

    Returns list of selected entity dicts, or None if cancelled.
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
    #warning {
        dock: bottom;
        height: auto;
        margin: 0 2;
        color: #f7768e;
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
        changes: list[dict[str, str]],
        has_deletions: bool = False,
    ) -> None:
        super().__init__()
        self._changes = changes
        self._has_deletions = has_deletions
        self.title = "Review entity changes"

        adds = sum(1 for c in changes if c.get("action") == "add")
        updates = sum(1 for c in changes if c.get("action") == "update")
        deletes = sum(1 for c in changes if c.get("action") == "delete")
        keeps = sum(1 for c in changes if c.get("action") == "keep")
        parts = []
        if adds:
            parts.append(f"{adds} add{'s' if adds != 1 else ''}")
        if updates:
            parts.append(f"{updates} update{'s' if updates != 1 else ''}")
        if deletes:
            parts.append(f"{deletes} delete{'s' if deletes != 1 else ''}")
        if keeps:
            parts.append(f"{keeps} kept")
        self.sub_title = ", ".join(parts)

        # Compute column widths from data
        self._col_type_w = max((len(c.get("entity_type", "")) for c in changes), default=4)
        self._col_type_w = max(self._col_type_w, 4)
        self._col_name_w = max((len(c.get("name", "")) for c in changes), default=4)
        self._col_name_w = max(self._col_name_w, 4)
        self._col_org_w = max((len(c.get("organization", "")) for c in changes), default=12)
        self._col_org_w = max(self._col_org_w, 12)
        self._col_action_w = max((len(c.get("action", "")) for c in changes), default=6)
        self._col_action_w = max(self._col_action_w, 6)

    def _build_header_text(self) -> Text:
        text = Text()
        text.append("Type".ljust(self._col_type_w), style="bold #6e7681")
        text.append("  ")
        text.append("Name".ljust(self._col_name_w), style="bold #6e7681")
        text.append("  ")
        text.append("Organization".ljust(self._col_org_w), style="bold #6e7681")
        text.append("  ")
        text.append("Action".ljust(self._col_action_w), style="bold #6e7681")
        return text

    def _build_label(self, change: dict[str, str]) -> Text:
        action = change.get("action", "")
        text = Text()
        text.append(change.get("entity_type", "").ljust(self._col_type_w))
        text.append("  ")
        name = change.get("name", "")
        if len(name) > self._col_name_w:
            name = name[: self._col_name_w - 1] + "\u2026"
        text.append(name.ljust(self._col_name_w))
        text.append("  ")
        org = change.get("organization", "") or "Global"
        if len(org) > self._col_org_w:
            org = org[: self._col_org_w - 1] + "\u2026"
        text.append(org.ljust(self._col_org_w))
        text.append("  ")
        style = _ACTION_STYLES.get(action, "")
        text.append(action.ljust(self._col_action_w), style=style)
        return text

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._build_header_text(), id="column-header")
        selections: list[Selection[int]] = []
        for i, change in enumerate(self._changes):
            label = self._build_label(change)
            selections.append(Selection(label, i, initial_state=True))
        yield SelectionList(*selections)
        if self._has_deletions:
            yield Static(
                "  [#f7768e]Entities marked 'delete' will be removed.[/]",
                id="warning",
            )
        yield Static(self._format_count(len(self._changes)), id="count")
        yield Footer()

    def _format_count(self, selected: int) -> str:
        return f"  {selected}/{len(self._changes)} selected"

    def on_selection_list_selection_toggled(self) -> None:
        sel_list = self.query_one(SelectionList)
        self.query_one("#count", Static).update(
            self._format_count(len(sel_list.selected))
        )

    def action_confirm(self) -> None:
        sel_list = self.query_one(SelectionList)
        selected_indices = set(sel_list.selected)
        result = [
            self._changes[i]
            for i in range(len(self._changes))
            if i in selected_indices
        ]
        self.exit(result)

    def action_cancel(self) -> None:
        self.exit(None)

    def action_select_all(self) -> None:
        self.query_one(SelectionList).select_all()

    def action_select_none(self) -> None:
        self.query_one(SelectionList).deselect_all()


async def interactive_entity_review(
    changes: list[dict[str, str]],
    has_deletions: bool = False,
) -> list[dict[str, str]] | None:
    """Show entity changes for review. Returns selected entities, or None if cancelled.

    Non-TTY fallback: prints table via _render_entity_changes_table and returns all.
    """
    if not changes:
        return []

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        # Non-TTY: print the table and return all
        from bifrost.cli import _render_entity_changes_table
        _render_entity_changes_table(changes)
        return list(changes)

    app = EntityReviewApp(changes, has_deletions=has_deletions)
    return await app.run_async()
