# Textual TUI for Bifrost CLI

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all DIY terminal rendering in `bifrost push`, `bifrost pull`, and `bifrost watch` with Textual TUI apps — proper file selection, progress display, and a live watch dashboard.

**Architecture:** Three Textual apps that slot into existing async CLI functions. Each app runs briefly (full-screen mode for cross-platform compatibility), captures user input or displays progress, then exits and returns control to the CLI. Watch mode is a persistent Textual app with panels for log output and sync status. All apps are in a new `bifrost/tui/` package, keeping `cli.py` as the orchestrator. All CLI functions calling these apps are already `async`, so we use `await app.run_async()` throughout.

**Tech Stack:** Textual (TUI framework), Rich (rendering — comes with Textual)

**Key design rules:**
- Item-building code in `cli.py` passes **plain text** status values (`"new"`, `"changed"`, `"delete"`) — the TUI apps handle coloring via Rich markup. No raw ANSI codes enter Textual.
- All TUI apps use `await app.run_async()` (not `app.run()`) since callers are already in an async context.
- Work that runs concurrently with a Textual app (progress updates, watch events) is launched via `self.run_worker()` inside the app's `on_mount` — not external concurrency.
- `_CliColors`, `_get_colors`, `_format_count_summary`, and `_render_entity_changes_table` are **kept** — entity table rendering is not TUI'd.

---

## UI Mocks

### File Selector (push and pull)

Used by `bifrost push` and `bifrost pull` when there are changed files. Replaces the current broken DIY multi-select.

```
┌─ Select files to push ──────────────────────────────────────────────────────┐
│                                                                             │
│  ↑/↓ navigate · Space toggle · a all · n none · Enter confirm · Esc cancel  │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ [✓] workflows/onboarding.py            changed  Mar 17 01:50          │ │
│  │ [✓] workflows/offboarding.py           changed  Mar 17 01:48          │ │
│  │ [✓] agents/a8ea0e98-e516-449e-b33…     changed  Mar 17 01:50  jack    │ │
│  │ [ ] forms/ticket_form.py               new                            │ │
│  │ [✓] .bifrost/integrations.yaml         changed  Mar 17 00:12          │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  4/5 selected                                          [Enter] Push  [Esc]  │
└─────────────────────────────────────────────────────────────────────────────┘
```

- Rows are a `SelectionList` widget with checkbox column
- Status column is color-coded via Rich markup: `[green]new[/]`, `[yellow]changed[/]`, `[red]delete[/]`
- File paths auto-truncate with `…` when too long
- Footer shows count + action buttons
- Scrolls when list exceeds terminal height

### Push/Pull Progress

Shown during file upload/download after selection. Brief full-screen overlay.

```
┌─ Pushing files ─────────────────────────────────────────────────────────────┐
│                                                                             │
│  workflows/onboarding.py ··························· ✓                      │
│  workflows/offboarding.py ·························· ✓                      │
│  agents/a8ea0e98-e516-449e-b336-7e0ddc99f ·········· ◌                     │
│  forms/ticket_form.py                                                       │
│                                                                             │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  3/4                  │
│                                                                             │
│  Applying manifest...                                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

- Each file shows status: ✓ done, ◌ in progress, (blank) pending
- Progress bar at bottom
- Auto-exits when complete (brief pause so user can see 100%)
- Errors shown inline with red ✗ and message — app waits for keypress before exiting if there were errors

### Watch Mode Dashboard

Persistent TUI for `bifrost watch`. Replaces scattered `print()` statements.

```
┌─ bifrost watch ── workspace/my-project ── session a8ea0e98 ─────────────────┐
│                                                                              │
│  ┌─ Activity Log ──────────────────────────────────────────────────────────┐ │
│  │ 14:23:01  → Push   workflows/onboarding.py                             │ │
│  │ 14:23:01  → Push   workflows/offboarding.py                            │ │
│  │ 14:23:05  ✓ Pushed 2 file(s)                                           │ │
│  │ 14:24:12  ← Pull   agents/router.py                          (sarah)   │ │
│  │ 14:25:00  ♥ Heartbeat                                                   │ │
│  │ 14:25:33  → Push   forms/ticket_form.py                                │ │
│  │ 14:25:34  ✓ Pushed 1 file(s), manifest applied                         │ │
│  │ 14:26:01  ⚠ Push error: HTTP 500 (retrying)                            │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  Watching · 0 pending · last sync 14:25:34 ──────────── Ctrl+C to stop      │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Scrolling `RichLog` widget for the activity log
- Status bar at bottom: sync state, pending changes count, last sync time
- Title bar: workspace path + session ID
- Color-coded log entries: → push (blue), ← pull (green), ✓ success (green), ⚠ error (red), ♥ heartbeat (dim)

---

## File Structure

```
api/bifrost/
├── cli.py                     # MODIFY: replace DIY rendering calls with TUI app calls
├── pyproject.toml             # MODIFY: add textual dependency
└── tui/
    ├── __init__.py            # Exports: FileSelectApp, ProgressApp, WatchApp
    ├── file_select.py         # File selector TUI app (push/pull)
    ├── progress.py            # Push/pull progress TUI app
    └── watch.py               # Watch mode dashboard TUI app
```

---

## Chunk 1: File Selector App

### Task 1: Add Textual dependency and create tui package

**Files:**
- Modify: `api/bifrost/pyproject.toml`
- Create: `api/bifrost/tui/__init__.py`

- [ ] **Step 1: Add textual to dependencies**

In `api/bifrost/pyproject.toml`, add `"textual>=1.0.0"` to the `dependencies` list.

- [ ] **Step 2: Create tui package**

```bash
mkdir -p api/bifrost/tui
```

Create `api/bifrost/tui/__init__.py`:
```python
"""Textual TUI components for the Bifrost CLI."""
```

- [ ] **Step 3: Install and verify**

Run: `pip install -e api/bifrost/` (or reinstall via pipx)
Run: `python3 -c "from textual.app import App; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add api/bifrost/pyproject.toml api/bifrost/tui/__init__.py
git commit -m "feat: add textual dependency and tui package"
```

### Task 2: Build FileSelectApp

**Files:**
- Create: `api/bifrost/tui/file_select.py`

The app takes a list of item dicts and column definitions, displays a scrollable checklist with Rich-formatted labels, and returns selected items via `self.exit()`. The wrapper function is `async` since callers are in an async context.

- [ ] **Step 1: Write file_select.py**

```python
"""Interactive file selector TUI for push/pull operations."""

from __future__ import annotations

import sys

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, SelectionList, Static
from textual.widgets.selection_list import Selection
from rich.text import Text


class FileSelectApp(App[list[dict[str, str]] | None]):
    """Full-screen file selector with checkboxes.

    Returns list of selected item dicts, or None if cancelled.
    """

    CSS = """
    Screen {
        background: $surface;
    }
    SelectionList {
        height: 1fr;
        margin: 1 2;
    }
    #count {
        dock: bottom;
        height: 1;
        margin: 0 2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
        Binding("a", "select_all", "All", show=True),
        Binding("n", "select_none", "None", show=True),
    ]

    def __init__(
        self,
        items: list[dict[str, str]],
        columns: list[tuple[str, str, int]],
        prompt_text: str = "Select files",
    ) -> None:
        super().__init__()
        self._items = items
        self._columns = columns
        self.title = prompt_text

    def _build_label(self, item: dict[str, str]) -> Text:
        """Build a Rich Text label from an item dict using column definitions."""
        text = Text()
        for i, (key, _, width) in enumerate(self._columns):
            val = item.get(key) or ""
            # Color the status column
            if key == "status":
                status_colors = {
                    "new": "green",
                    "changed": "yellow",
                    "delete": "red",
                }
                color = status_colors.get(val, "")
                if color:
                    text.append(val.ljust(width), style=color)
                else:
                    text.append(val.ljust(width))
            else:
                # Truncate with ellipsis if too long
                if len(val) > width:
                    val = val[: width - 1] + "…"
                text.append(val.ljust(width))
            if i < len(self._columns) - 1:
                text.append("  ")
        return text

    def compose(self) -> ComposeResult:
        yield Header()
        selections: list[Selection[int]] = []
        for i, item in enumerate(self._items):
            label = self._build_label(item)
            # Value is the index — used to map back to self._items on confirm
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

    app = FileSelectApp(items, columns, prompt_text)
    return await app.run_async()
```

- [ ] **Step 2: Verify import**

Run: `python3 -c "from bifrost.tui.file_select import interactive_file_select; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add api/bifrost/tui/file_select.py
git commit -m "feat: add Textual file selector app"
```

### Task 3: Wire FileSelectApp into push/pull and remove old code

**Files:**
- Modify: `api/bifrost/cli.py`

This task does three things: (1) switches to the new TUI, (2) changes item-building to use plain-text status values (no ANSI codes), (3) deletes the old DIY code.

- [ ] **Step 1: Add import**

At the top of `cli.py`, add:
```python
from bifrost.tui.file_select import interactive_file_select
```

- [ ] **Step 2: Update push item-building to use plain-text status**

In `_push_files()`, find the `selector_items` building code. Change all status values from ANSI-colored strings to plain text:

```python
# BEFORE (raw ANSI):
status = f"{cc.green}new{cc.reset}" if is_new else f"{cc.yellow}changed{cc.reset}"
# ...
"status": f"{cc.red}delete{cc.reset}",

# AFTER (plain text — TUI handles coloring):
status = "new" if is_new else "changed"
# ...
"status": "delete",
```

Remove the `cc = _get_colors()` call at the top of this block (it's no longer needed here).

- [ ] **Step 3: Update pull item-building to use plain-text status**

Same change in `_pull_from_server()`:

```python
# BEFORE:
status = f"{c.green}new{c.reset}" if is_new else f"{c.yellow}changed{c.reset}"
# ...
"status": f"{c.red}delete{c.reset}",

# AFTER:
status = "new" if is_new else "changed"
# ...
"status": "delete",
```

Keep the `" ⚠"` suffix for uncommitted files — append it to the plain status: `status + " ⚠"`.

Remove the `c = _get_colors()` call at the top of this block.

- [ ] **Step 4: Make calls async with `await`**

Change both call sites from:
```python
selected = _interactive_file_select(...)
```
to:
```python
selected = await interactive_file_select(...)
```

Both `_push_files` and `_pull_from_server` are already `async def`, so this just works.

- [ ] **Step 5: Delete old `_interactive_file_select` and its helpers**

Remove the entire `_interactive_file_select` function (~lines 2237-2370), including all inner functions (`_trunc_and_pad`, `_render`), the `tty`/`termios` imports, and the raw mode handling.

**Do NOT delete:**
- `_format_file_time` — still used by item-building code in push/pull
- `_format_server_time` — still used by item-building code in push/pull
- `_CliColors` / `_get_colors` — still used by entity table rendering
- `_format_count_summary` — still used by entity summary

- [ ] **Step 6: Verify**

Run: `python3 -c "import ast; ast.parse(open('api/bifrost/cli.py').read()); print('OK')"`
Run: `ruff check api/bifrost/cli.py`

- [ ] **Step 7: Manual test**

Run: `bifrost push` in a workspace with changes → Textual file selector with colored status, truncated paths, scrollable list. Select files, Enter → push proceeds.

Run: `bifrost pull` similarly.

Run: `echo "y" | bifrost push` (non-TTY) → pushes all files without TUI.

- [ ] **Step 8: Commit**

```bash
git add api/bifrost/cli.py
git commit -m "feat: replace DIY file selector with Textual TUI"
```

---

## Chunk 2: Push/Pull Progress App

### Task 4: Build ProgressApp

**Files:**
- Create: `api/bifrost/tui/progress.py`

This app displays per-file status and a progress bar. Work is passed in as a coroutine factory and executed inside the app via `run_worker`. The app auto-exits on completion, or waits for a keypress if there were errors.

- [ ] **Step 1: Write progress.py**

```python
"""Push/pull progress TUI with per-file status and progress bar."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, ProgressBar, RichLog


class ProgressApp(App[list[str]]):
    """Displays push/pull progress with per-file status and progress bar.

    Returns a list of error strings (empty = all succeeded).
    Work is executed inside the app via run_worker.
    """

    CSS = """
    Screen {
        background: $surface;
    }
    RichLog {
        height: 1fr;
        margin: 1 2;
    }
    ProgressBar {
        margin: 0 2 1 2;
    }
    """

    BINDINGS = [
        Binding("enter", "dismiss", "Continue", show=False),
        Binding("escape", "dismiss", "Continue", show=False),
    ]

    def __init__(
        self,
        title: str,
        file_items: list[tuple[str, Any]],
        worker_fn: Callable[[Any, str], Awaitable[None]],
    ) -> None:
        """
        Args:
            title: "Pushing files" or "Pulling files"
            file_items: list of (display_name, work_data) tuples
            worker_fn: async fn(work_data, display_name) that processes one file.
                       Raise on error.
        """
        super().__init__()
        self.title = title
        self._file_items = file_items
        self._worker_fn = worker_fn
        self._errors: list[str] = []
        self._done = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(highlight=True, markup=True)
        yield ProgressBar(total=len(self._file_items))
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._do_work())

    async def _do_work(self) -> None:
        log = self.query_one(RichLog)
        bar = self.query_one(ProgressBar)

        for name, data in self._file_items:
            log.write(f"  [dim]◌[/] {name}")
            try:
                await self._worker_fn(data, name)
                bar.advance(1)
                # Overwrite the last line with success
                log.write(f"  [green]✓[/] {name}")
            except Exception as e:
                bar.advance(1)
                self._errors.append(f"{name}: {e}")
                log.write(f"  [red]✗[/] {name}: {e}")

        self._done = True
        if self._errors:
            log.write("")
            log.write(f"  [red]{len(self._errors)} error(s) — press Enter to continue[/]")
            # Wait for user keypress (handled by action_dismiss binding)
        else:
            await asyncio.sleep(0.5)
            self.exit(self._errors)

    def action_dismiss(self) -> None:
        if self._done:
            self.exit(self._errors)
```

- [ ] **Step 2: Verify import**

Run: `python3 -c "from bifrost.tui.progress import ProgressApp; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add api/bifrost/tui/progress.py
git commit -m "feat: add Textual progress app for push/pull"
```

### Task 5: Wire ProgressApp into push/pull

**Files:**
- Modify: `api/bifrost/cli.py`

- [ ] **Step 1: Add import**

```python
from bifrost.tui.progress import ProgressApp
```

- [ ] **Step 2: Replace upload loop in `_push_files()`**

Find the upload loop (~lines that iterate `files_to_upload` calling `client.post("/api/files/write", ...)`). Replace with:

```python
# Build work items for progress app
upload_items: list[tuple[str, tuple[str, str]]] = [
    (_strip_repo_prefix(rp, repo_prefix), (rp, content))
    for rp, content in files_to_upload.items()
]
# Include deletes in the same progress run
delete_items: list[tuple[str, tuple[str, str]]] = [
    (_strip_repo_prefix(sp, repo_prefix), (sp, ""))
    for sp in files_to_delete_paths
]

all_items = upload_items + delete_items

async def _do_one(work_data: tuple[str, str], name: str) -> None:
    rp, content = work_data
    if content:
        # Upload
        resp = await client.post("/api/files/write", json={
            "path": rp, "content": content,
            "mode": "cloud", "location": "workspace", "binary": True,
        })
        if resp.status_code != 204:
            raise RuntimeError(f"HTTP {resp.status_code}")
    else:
        # Delete
        resp = await client.post("/api/files/delete", json={
            "path": rp, "mode": "cloud", "location": "workspace",
        })
        if resp.status_code != 204:
            raise RuntimeError(f"HTTP {resp.status_code}")

if all_items and sys.stdin.isatty() and sys.stdout.isatty():
    app = ProgressApp("Pushing files", all_items, _do_one)
    errors = await app.run_async() or []
else:
    # Non-TTY fallback
    errors = []
    for name, data in all_items:
        try:
            await _do_one(data, name)
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"  Error: {name}: {e}", file=sys.stderr)
```

After this block, compute `created`/`updated`/`deleted` counts from the items that succeeded (items not in errors):

```python
error_names = {e.split(":")[0] for e in errors}
created = sum(1 for name, (rp, c) in upload_items if name not in error_names and rp not in server_metadata)
updated = sum(1 for name, (rp, c) in upload_items if name not in error_names and rp in server_metadata)
deleted = sum(1 for name, _ in delete_items if name not in error_names)
```

Remove the old upload loop, the old delete loop, and the old `_print_progress` calls.

- [ ] **Step 3: Replace download loop in `_pull_from_server()`**

Same pattern for the download loop. Build items from `files_to_download`, create a `_do_one` that calls `client.post("/api/files/read", ...)` and writes to disk. Include mirror deletes.

- [ ] **Step 4: Remove `_print_progress` if no callers remain**

Grep for `_print_progress` — if no remaining callers, delete the function.

- [ ] **Step 5: Verify and test**

Run lint/syntax checks.
Manual test: `bifrost push` with multiple files → file selector → progress TUI → summary.

- [ ] **Step 6: Commit**

```bash
git add api/bifrost/cli.py api/bifrost/tui/progress.py
git commit -m "feat: wire Textual progress app into push/pull"
```

---

## Chunk 3: Watch Mode Dashboard

### Task 6: Build WatchApp

**Files:**
- Create: `api/bifrost/tui/watch.py`

Persistent TUI for `bifrost watch`. Receives log entries from the watch loop via method calls (the watch loop runs as a `run_worker` inside the app).

- [ ] **Step 1: Write watch.py**

```python
"""Watch mode dashboard TUI."""

from __future__ import annotations

from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, RichLog, Static


class WatchApp(App[None]):
    """Persistent watch mode dashboard with scrolling activity log."""

    CSS = """
    Screen {
        background: $surface;
    }
    RichLog {
        height: 1fr;
        margin: 0 1;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        padding: 0 2;
        background: $accent;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Stop watching"),
    ]

    def __init__(self, workspace: str, session_id: str) -> None:
        super().__init__()
        self.title = f"bifrost watch — {workspace}"
        self.sub_title = f"session {session_id[:8]}"
        self._pending = 0
        self._last_sync = "—"

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(highlight=True, markup=True, id="activity-log")
        yield Static(self._format_status(), id="status-bar")
        yield Footer()

    def _format_status(self) -> str:
        return f"  Watching · {self._pending} pending · last sync {self._last_sync}  "

    def _update_status(self) -> None:
        try:
            self.query_one("#status-bar", Static).update(self._format_status())
        except Exception:
            pass  # App may be shutting down

    def _log(self, level: str, icon: str, text: str) -> None:
        """Write a log entry to the activity log."""
        ts = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "push": "blue",
            "pull": "green",
            "success": "green",
            "error": "red",
            "warning": "yellow",
            "info": "dim",
        }
        color = color_map.get(level, "")
        try:
            log = self.query_one("#activity-log", RichLog)
            if color:
                log.write(f"  [{color}]{ts}  {icon} {text}[/]")
            else:
                log.write(f"  {ts}  {icon} {text}")
        except Exception:
            pass  # App may be shutting down

    def log_push(self, filename: str) -> None:
        self._log("push", "→", f"Push  {filename}")

    def log_pull(self, filename: str, user: str = "") -> None:
        suffix = f"  ({user})" if user else ""
        self._log("pull", "←", f"Pull  {filename}{suffix}")

    def log_delete(self, filename: str, user: str = "") -> None:
        suffix = f"  ({user})" if user else ""
        self._log("warning", "✗", f"Delete  {filename}{suffix}")

    def log_success(self, message: str) -> None:
        self._last_sync = datetime.now().strftime("%H:%M:%S")
        self._update_status()
        self._log("success", "✓", message)

    def log_error(self, message: str) -> None:
        self._log("error", "⚠", message)

    def log_info(self, message: str) -> None:
        self._log("info", "·", message)

    def set_pending(self, count: int) -> None:
        self._pending = count
        self._update_status()
```

- [ ] **Step 2: Verify import**

Run: `python3 -c "from bifrost.tui.watch import WatchApp; print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add api/bifrost/tui/watch.py
git commit -m "feat: add Textual watch mode dashboard"
```

### Task 7: Wire WatchApp into `_watch_and_push`

**Files:**
- Modify: `api/bifrost/cli.py`

The watch loop runs as a `run_worker` inside the `WatchApp`. All `print()` calls in `_process_watch_batch` and `_process_incoming` are replaced with `watch_app.log_*()` calls.

- [ ] **Step 1: Add `watch_app` parameter to helper functions**

Add `watch_app: WatchApp | None = None` to these function signatures:
- `_process_watch_batch(client, changes, deletes, base_path, repo_prefix, state, watch_app=None)`
- `_process_incoming(client, files, deletes, entities, base_path, repo_prefix, watch_app=None)`

- [ ] **Step 2: Replace `print()` calls in `_process_watch_batch`**

For each `print()` in `_process_watch_batch`, add a conditional:

```python
# Replace:
print(f"  [{ts}] File changed: {repo_path}", flush=True)
# With:
if watch_app:
    watch_app.log_push(repo_path)
else:
    print(f"  [{ts}] File changed: {repo_path}", flush=True)
```

Do the same for success messages, error messages, manifest validation errors, etc.

- [ ] **Step 3: Replace `print()` calls in `_process_incoming`**

Same pattern. Replace `print(f"  [{ts}] ← {user_name}: {rel}")` with:

```python
if watch_app:
    watch_app.log_pull(rel, user=user_name)
else:
    print(f"  [{ts}] ← {user_name}: {rel}", flush=True)
```

And for deletes, errors, entity changes.

- [ ] **Step 4: Refactor `_watch_and_push` to run inside WatchApp**

Extract the core watch loop into a separate async function `_watch_loop(...)` that accepts a `watch_app` parameter.

Then in `_watch_and_push`, branch on TTY:

```python
if sys.stdin.isatty() and sys.stdout.isatty():
    from bifrost.tui.watch import WatchApp
    app = WatchApp(str(path), state.session_id)

    async def _run_watch_in_app() -> None:
        # The watch loop runs as a worker inside the Textual app
        app.run_worker(_watch_loop(
            path, repo_prefix, mirror, validate, client, state,
            observer, handler, ws_task, watch_app=app,
        ))

    app.call_after_refresh(_run_watch_in_app)
    await app.run_async()
else:
    # Non-TTY: existing print-based watch loop
    await _watch_loop(
        path, repo_prefix, mirror, validate, client, state,
        observer, handler, ws_task, watch_app=None,
    )
```

The `_watch_loop` function contains the existing `while True` loop from `_watch_and_push`. When `watch_app` is set, it calls `watch_app.log_*()` methods. When `watch_app` is `None`, it calls `print()`.

When the user presses Ctrl+C in the TUI, Textual's `action_quit` fires, which exits the app. The `_watch_loop` worker should detect this (e.g., check `app._exit` or catch `CancelledError`) and clean up the observer/websocket.

- [ ] **Step 5: Replace `print()` in `_watch_and_push` preamble**

The initial messages ("Initial push of ...", "Watching ... for changes", "Bidirectional sync enabled") should also route through `watch_app.log_info()` when available.

- [ ] **Step 6: Manual test**

Run: `bifrost watch --mirror` in a workspace.
- Dashboard appears with activity log and status bar
- Edit a file → push event appears in log with blue "→ Push" prefix
- Another session edits → pull event appears with green "← Pull" prefix
- Ctrl+C → clean exit, terminal restored

- [ ] **Step 7: Commit**

```bash
git add api/bifrost/cli.py api/bifrost/tui/watch.py
git commit -m "feat: wire Textual watch dashboard into watch mode"
```

---

## Chunk 4: Cleanup

### Task 8: Remove dead code and finalize exports

**Files:**
- Modify: `api/bifrost/cli.py`
- Modify: `api/bifrost/tui/__init__.py`

- [ ] **Step 1: Remove `_print_progress` if unused**

Grep for `_print_progress` — delete if no callers remain.

- [ ] **Step 2: Remove any other dead code**

Grep for each of these and delete ONLY if zero callers remain:
- `_format_file_time`
- `_format_server_time`

**Do NOT delete** (still used by entity table/summary):
- `_CliColors`, `_get_colors`
- `_format_count_summary`
- `_render_entity_changes_table`

- [ ] **Step 3: Update tui/__init__.py exports**

```python
"""Textual TUI components for the Bifrost CLI."""

from bifrost.tui.file_select import FileSelectApp, interactive_file_select
from bifrost.tui.progress import ProgressApp
from bifrost.tui.watch import WatchApp

__all__ = [
    "FileSelectApp",
    "ProgressApp",
    "WatchApp",
    "interactive_file_select",
]
```

- [ ] **Step 4: Verify everything**

```bash
python3 -c "import ast; ast.parse(open('api/bifrost/cli.py').read()); print('OK')"
ruff check api/bifrost/cli.py
ruff check api/bifrost/tui/
```

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/cli.py api/bifrost/tui/__init__.py
git commit -m "chore: remove dead code, finalize TUI exports"
```

---

## Verification Checklist

After all tasks complete:

- [ ] `bifrost push` → file selector TUI (colored status, scrollable) → progress TUI → summary
- [ ] `bifrost pull` → file selector TUI → progress TUI → summary
- [ ] `bifrost push --force` → no selector, progress TUI, pushes all files
- [ ] `bifrost pull --force` → no selector, progress TUI, pulls all files
- [ ] `bifrost watch --mirror` → initial push with file selector → watch dashboard with live log
- [ ] Non-TTY (`echo "y" | bifrost push`) → no TUI, processes all files with stderr output
- [ ] Entity changes still show with print-based table + y/N prompt
- [ ] Errors in progress TUI → shown inline, app waits for keypress
- [ ] `ruff check api/bifrost/cli.py api/bifrost/tui/` passes
- [ ] `pyright` passes on bifrost package (pre-existing errors excluded)
