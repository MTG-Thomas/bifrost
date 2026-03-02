# Hardcoded Path Cleanup + CLI Refactor

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate remaining hardcoded `apps/` directory assumptions that cause bugs for apps with custom `repo_path`, and refactor the CLI to remove duplicated logic and a 260-line god function.

**Architecture:** The `Application` ORM model gets a `repo_prefix` property that encapsulates the `(repo_path or f"apps/{slug}").rstrip("/") + "/"` pattern. All consumers switch to it. The CLI gets structural cleanup: shared arg parsing, single `repo_prefix` computation, deduplicated filtering, and `_watch_and_push` broken into focused helpers.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy ORM, watchdog (file watcher)

---

## Background: What `repo_path` Is

When you create an app in the UI, Bifrost stores its files at `apps/{slug}/` by convention. The string `"apps/dashboard"` is saved in `Application.repo_path`. But apps imported via git can live anywhere — `client/dashboard/`, `my-stuff/app/`, etc. The `.bifrost/apps.yaml` manifest declares each app's `path`, which becomes `repo_path` in the DB.

**The bugs:** Several places skip the DB `repo_path` and hardcode `f"apps/{slug}/"`. These silently look in the wrong directory for any app not at the default location — dependency graphs miss files, validation finds nothing, entity usage scans return empty results.

**Convention defaults are fine for writes** — when creating a new app, `repo_path=f"apps/{slug}"` is the sensible default. The issue is only with **reads** that assume the convention instead of checking the DB.

---

### Task 1: Add `repo_prefix` Property to Application ORM

**Files:**
- Modify: `api/src/models/orm/applications.py:94-110`
- Test: `api/tests/unit/models/test_application_repo_prefix.py`

**Step 1: Write the failing test**

Create `api/tests/unit/models/test_application_repo_prefix.py`:

```python
"""Tests for Application.repo_prefix property."""
from src.models.orm.applications import Application


class TestApplicationRepoPrefix:
    def test_uses_repo_path_when_set(self):
        app = Application(slug="dashboard", repo_path="custom/dashboard")
        assert app.repo_prefix == "custom/dashboard/"

    def test_falls_back_to_apps_slug(self):
        app = Application(slug="dashboard", repo_path=None)
        assert app.repo_prefix == "apps/dashboard/"

    def test_strips_trailing_slash(self):
        app = Application(slug="dashboard", repo_path="custom/dashboard/")
        assert app.repo_prefix == "custom/dashboard/"

    def test_empty_string_repo_path_falls_back(self):
        app = Application(slug="dashboard", repo_path="")
        assert app.repo_prefix == "apps/dashboard/"
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/models/test_application_repo_prefix.py -v`
Expected: FAIL — `Application` has no attribute `repo_prefix`

**Step 3: Write the property**

In `api/src/models/orm/applications.py`, add after the `has_unpublished_changes` property:

```python
@property
def repo_prefix(self) -> str:
    """Return the repo path prefix for this app, with trailing slash.

    Uses repo_path from DB (set by git sync / manifest import).
    Falls back to convention default apps/{slug} for legacy apps.
    """
    base = self.repo_path or f"apps/{self.slug}"
    return f"{base.rstrip('/')}/"
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/models/test_application_repo_prefix.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/models/orm/applications.py api/tests/unit/models/test_application_repo_prefix.py
git commit -m "feat: add Application.repo_prefix property"
```

---

### Task 2: Replace All Hardcoded `apps/` Prefix Lookups

**Files:**
- Modify: `api/src/services/dependency_graph.py:229,393`
- Modify: `api/src/routers/workflows.py:215-224,594-606`
- Modify: `api/src/services/mcp_server/tools/apps.py:612`
- Modify: `api/src/services/file_storage/file_ops.py:322,447`
- Modify: `api/src/routers/maintenance.py:348`
- Modify: `api/src/routers/applications.py` (~4 places)
- Modify: `api/src/routers/app_code_files.py:240-243`
- Modify: `api/src/services/mcp_server/tools/apps.py:815`

**Step 1: Fix `dependency_graph.py` (2 bugs)**

Line 229 — `_app_uses_workflow()`: Has `app` object loaded. Change:
```python
# Before
prefix = f"apps/{app.slug}/"
# After
prefix = app.repo_prefix
```

Line 393 — `_get_app_dependents()`: Same pattern, same fix.

**Step 2: Fix `routers/workflows.py` (2 bugs)**

Line 215-224 — `_get_app_workflow_ids()`: Currently selects only `Application.slug`. Change:
```python
# Before
app_result = await db.execute(
    select(Application.slug).where(Application.id == app_id)
)
slug = app_result.scalar_one_or_none()
if not slug:
    return set()
prefix = f"apps/{slug}/"

# After
app_result = await db.execute(
    select(Application).where(Application.id == app_id)
)
app = app_result.scalar_one_or_none()
if not app:
    return set()
prefix = app.repo_prefix
```

Line 594-606 — `get_entity_usage()`: Currently selects `Application.id, .name, .slug`. Add `.repo_path`:
```python
# Before
select(Application.id, Application.name, Application.slug)
# ...
prefix = f"apps/{app_row.slug}/"

# After
select(Application.id, Application.name, Application.slug, Application.repo_path)
# ...
prefix = (app_row.repo_path or f"apps/{app_row.slug}").rstrip("/") + "/"
```

(Can't use `.repo_prefix` property here because `app_row` is a Row tuple, not an ORM instance. Inline the fallback.)

**Step 3: Fix `mcp_server/tools/apps.py` line 612 (bug)**

`validate_app()` — `app` object is loaded at line 608. Change:
```python
# Before
prefix = f"apps/{app.slug}/"
# After
prefix = app.repo_prefix
```

**Step 4: Replace safe fallback patterns with `app.repo_prefix`**

These are already working but can be simplified now that the property exists:

- `file_ops.py:322` and `file_ops.py:447`: `(app.repo_path or f"apps/{app.slug}").rstrip("/") + "/"` → `app.repo_prefix`
- `maintenance.py:348`: same pattern → `app.repo_prefix`
- `applications.py` (~4 places): same pattern → `app.repo_prefix`
- `mcp_server/tools/apps.py:815`: same pattern → `app_obj.repo_prefix`
- `app_code_files.py:240-243`: Delete the local `_repo_prefix()` function, replace calls with `app.repo_prefix`

**Step 5: Run type checker and linter**

Run: `cd api && pyright && ruff check .`
Expected: 0 errors

**Step 6: Run tests**

Run: `./test.sh tests/unit/ -v`
Expected: All pass

**Step 7: Commit**

```bash
git add -u
git commit -m "fix: use Application.repo_prefix instead of hardcoded apps/ paths"
```

---

### Task 3: CLI — Fix Stale Reference + Extract Shared Arg Parsing

**Files:**
- Modify: `api/bifrost/cli.py`

**Step 1: Fix stale `bifrost sync` reference**

In `_check_repo_status()` (~line 1166), change:
```python
# Before
"Run 'bifrost sync' to commit platform changes first,"
# After
"Run 'bifrost git commit' to commit platform changes first,"
```

**Step 2: Extract shared arg parsing**

Add near the top of the push/watch section (after imports, before `handle_push`):

```python
@dataclass
class _PushWatchArgs:
    """Parsed arguments for push/watch commands."""
    local_path: str = "."
    clean: bool = False
    validate: bool = False
    force: bool = False


def _parse_push_watch_args(args: list[str]) -> _PushWatchArgs | None:
    """Parse shared arguments for push/watch commands. Returns None on error."""
    result = _PushWatchArgs()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--clean":
            result.clean = True
        elif arg == "--validate":
            result.validate = True
        elif arg == "--force":
            result.force = True
        elif arg.startswith("--"):
            print(f"Unknown option: {arg}", file=sys.stderr)
            return None
        elif result.local_path == ".":
            result.local_path = arg
        else:
            print(f"Unexpected argument: {arg}", file=sys.stderr)
            return None
        i += 1
    return result
```

Add `from dataclasses import dataclass` to the imports at the top of the file.

**Step 3: Refactor `handle_push` to use shared parser**

Replace the manual parsing loop (lines 1005-1025) with:
```python
# Keep --watch migration guard before shared parsing
if args and args[0] == "--watch":
    # Check before shared parser so it doesn't treat --watch as unknown
    print("--watch has moved to its own command: bifrost watch", file=sys.stderr)
    return 1

parsed = _parse_push_watch_args(args)
if parsed is None:
    return 1
```

Then use `parsed.local_path`, `parsed.clean`, `parsed.validate`, `parsed.force`.

**Step 4: Refactor `handle_watch` to use shared parser**

Replace the manual parsing loop (lines 1087-1104) with:
```python
parsed = _parse_push_watch_args(args)
if parsed is None:
    return 1
```

**Step 5: Run tests**

Run: `./test.sh tests/unit/ -v`
Expected: All pass (CLI has no unit tests but verify nothing breaks)

**Step 6: Commit**

```bash
git add api/bifrost/cli.py
git commit -m "refactor: extract shared arg parsing for push/watch CLI commands"
```

---

### Task 4: CLI — Thread `repo_prefix`, Deduplicate Filter, Remove Dead Imports

**Files:**
- Modify: `api/bifrost/cli.py`

**Step 1: Extract `_should_skip_path()` helper**

Add a module-level function (before `_collect_push_files`):

```python
def _should_skip_path(rel_parts: tuple[str, ...], suffix: str) -> bool:
    """Check if a relative path should be skipped during push/watch."""
    if any(p.startswith(".") and p != ".bifrost" for p in rel_parts):
        return True
    if any(p in ("__pycache__", "node_modules") for p in rel_parts):
        return True
    if suffix.lower() in BINARY_EXTENSIONS:
        return True
    return False
```

**Step 2: Update `_collect_push_files` to use it**

Replace the inline filtering (lines 1712-1721) with:
```python
rel_parts = file_path.relative_to(path).parts
if _should_skip_path(rel_parts, file_path.suffix):
    skipped += 1 if file_path.suffix.lower() in BINARY_EXTENSIONS else 0
    continue
```

Note: only count as `skipped` for binary files (matching current behavior — hidden/cache files are silently ignored, binaries are counted).

Actually, re-read the current code more carefully — the current `_collect_push_files` has separate checks where binary extension increments `skipped` but the others just `continue`. Preserve this:

```python
rel_parts = file_path.relative_to(path).parts
if any(p.startswith(".") and p != ".bifrost" for p in rel_parts):
    continue
if any(p in ("__pycache__", "node_modules") for p in rel_parts):
    continue
if file_path.suffix.lower() in BINARY_EXTENSIONS:
    skipped += 1
    continue
```

Hmm, that's the same as before. The dedup is really between `_collect_push_files` and `ChangeHandler._should_skip`. Let's keep `_collect_push_files` as-is (it has the `skipped` counter logic) and update `ChangeHandler._should_skip` to delegate:

```python
def _should_skip(self, file_path: str) -> bool:
    rel_parts = pathlib.Path(file_path).relative_to(path).parts
    return _should_skip_path(rel_parts, pathlib.Path(file_path).suffix)
```

**Step 3: Thread `repo_prefix` through call chain**

Change `_push_with_precheck` to pass `repo_prefix` to its callees:

```python
# In _push_with_precheck, after computing repo_prefix:
if watch:
    return await _watch_and_push(local_path, repo_prefix=repo_prefix, clean=clean, validate=validate, client=client)
else:
    return await _push_files(local_path, repo_prefix=repo_prefix, clean=clean, validate=validate, client=client)
```

Update `_push_files` signature:
```python
async def _push_files(local_path: str, repo_prefix: str = "", clean: bool = False, validate: bool = False, client: "BifrostClient | None" = None) -> int:
```

Remove `repo_prefix = _detect_repo_prefix(path)` from inside `_push_files` (line 1772) and `_watch_and_push` (line 1315).

Remove `import pathlib` from inside `_push_files` (line 1760) and `_watch_and_push` (line 1304) — it's already imported at module level.

In `handle_watch`, the `repo_prefix` computed at line 1131 is only used in the `KeyboardInterrupt` handler. Instead of computing it separately, recompute it inline in the except block:
```python
except KeyboardInterrupt:
    print("\nStopping watch...", flush=True)
    try:
        prefix = _detect_repo_prefix(resolved)
        client.post_sync("/api/files/watch", json={"action": "stop", "prefix": prefix})
    except Exception:
        pass
    return 130
```

**Step 4: Fix `apps/` hardcode at line 1816**

Change:
```python
# Before
if validate and repo_prefix.startswith("apps/"):
    slug = repo_prefix.split("/")[1] if "/" in repo_prefix else repo_prefix

# After
if validate and repo_prefix:
    # Extract slug (last path component) — server 404s gracefully if not an app
    slug = repo_prefix.rstrip("/").rsplit("/", 1)[-1]
```

Keep the rest of the validation logic as-is. The `/api/applications/{slug}` endpoint returns 404 if the slug doesn't match an app, which is already handled by the existing `try/except`.

**Step 5: Run linter and type checker**

Run: `cd api && ruff check . && pyright`
Expected: Clean

**Step 6: Commit**

```bash
git add api/bifrost/cli.py
git commit -m "refactor: deduplicate CLI filter logic, thread repo_prefix, fix apps/ hardcode"
```

---

### Task 5: CLI — Break Up `_watch_and_push`

**Files:**
- Modify: `api/bifrost/cli.py`

This is the biggest change. The 260-line function has 5 distinct phases. We'll extract helpers for the inner class and the batch processing logic.

**Step 1: Promote `ChangeHandler` to module-level `_WatchChangeHandler`**

Move the inner class out of `_watch_and_push` and make it a module-level class. It needs access to `path`, `pending_changes`, `pending_deletes`, `lock`, and `writeback_paused`. Bundle these into a simple state container:

```python
class _WatchState:
    """Mutable shared state between the watcher thread and the async main loop."""

    def __init__(self, base_path: pathlib.Path):
        self.base_path = base_path
        self.pending_changes: set[str] = set()
        self.pending_deletes: set[str] = set()
        self.lock = threading.Lock()
        self.writeback_paused = False

    def drain(self) -> tuple[set[str], set[str]]:
        """Atomically drain pending changes and deletes."""
        with self.lock:
            changes = self.pending_changes.copy()
            deletes = self.pending_deletes.copy()
            self.pending_changes.clear()
            self.pending_deletes.clear()
        return changes, deletes

    def requeue(self, changes: set[str], deletes: set[str]) -> None:
        """Put changes back for retry."""
        with self.lock:
            self.pending_changes.update(changes)
            self.pending_deletes.update(deletes)

    def discard_writeback_paths(self, paths: set[str]) -> None:
        """Remove paths generated by server writeback from pending sets."""
        with self.lock:
            self.pending_changes -= paths
            self.pending_deletes -= paths


class _WatchChangeHandler(FileSystemEventHandler):
    """Watchdog event handler that tracks file changes for push."""

    def __init__(self, state: _WatchState):
        self.state = state

    def _should_skip(self, file_path: str) -> bool:
        rel_parts = pathlib.Path(file_path).relative_to(self.state.base_path).parts
        return _should_skip_path(rel_parts, pathlib.Path(file_path).suffix)

    def on_any_event(self, event: FileSystemEvent) -> None:
        if self.state.writeback_paused or event.is_directory:
            return

        if event.event_type == "moved":
            dest = str(getattr(event, "dest_path", ""))
            if not dest or self._should_skip(dest):
                return
            with self.state.lock:
                self.state.pending_changes.add(dest)
                self.state.pending_deletes.discard(dest)
            return

        src = str(event.src_path)
        if self._should_skip(src):
            return
        with self.state.lock:
            if event.event_type == "deleted":
                self.state.pending_deletes.add(src)
                self.state.pending_changes.discard(src)
            elif event.event_type in ("created", "modified", "closed"):
                self.state.pending_changes.add(src)
                self.state.pending_deletes.discard(src)
```

Add `import threading` to the module-level imports (currently imported inside `_watch_and_push`). Also move the watchdog imports to module level (or keep them lazy if watchdog is optional — check if it's always available).

**Step 2: Extract `_process_watch_deletes()`**

```python
async def _process_watch_deletes(
    client: BifrostClient,
    deletes: set[str],
    base_path: pathlib.Path,
    repo_prefix: str,
) -> tuple[int, list[str]]:
    """Process pending file deletions. Returns (count, relative_paths)."""
    deleted_count = 0
    deleted_rels: list[str] = []

    for abs_path_str in deletes:
        abs_p = pathlib.Path(abs_path_str)
        if not abs_p.exists():
            rel = abs_p.relative_to(base_path)
            if str(rel).startswith(".bifrost/") or str(rel).startswith(".bifrost\\"):
                continue
            repo_path = f"{repo_prefix}/{rel}" if repo_prefix else str(rel)
            try:
                resp = await client.post("/api/files/delete", json={
                    "path": repo_path, "location": "workspace", "mode": "cloud",
                })
                if resp.status_code == 204:
                    deleted_count += 1
                    deleted_rels.append(str(rel))
            except Exception as del_err:
                status_code = getattr(getattr(del_err, "response", None), "status_code", None)
                if status_code == 404:
                    deleted_count += 1
                    deleted_rels.append(str(rel))
                else:
                    ts = datetime.now().strftime('%H:%M:%S')
                    print(f"  [{ts}] Delete error for {rel}: {del_err}", flush=True)

    return deleted_count, deleted_rels
```

**Step 3: Extract `_process_watch_batch()`**

```python
async def _process_watch_batch(
    client: BifrostClient,
    changes: set[str],
    deletes: set[str],
    base_path: pathlib.Path,
    repo_prefix: str,
    state: _WatchState,
) -> None:
    """Process a batch of file changes and deletions."""
    deleted_count, deleted_rels = await _process_watch_deletes(
        client, deletes, base_path, repo_prefix,
    )

    # Build files dict from changed paths
    push_files: dict[str, str] = {}
    for abs_path_str in changes:
        abs_p = pathlib.Path(abs_path_str)
        if abs_p.exists():
            try:
                content = abs_p.read_text(encoding="utf-8")
                rel = abs_p.relative_to(base_path)
                repo_path = f"{repo_prefix}/{rel}" if repo_prefix else str(rel)
                push_files[repo_path] = content
            except (UnicodeDecodeError, OSError):
                continue

    ts = datetime.now().strftime('%H:%M:%S')
    for repo_path in sorted(push_files):
        print(f"  [{ts}] File changed: {repo_path}", flush=True)
    for rel_path in sorted(deleted_rels):
        print(f"  [{ts}] File deleted: {rel_path}", flush=True)

    if push_files:
        # Local manifest validation
        has_manifest = any(".bifrost/" in k for k in push_files)
        if has_manifest:
            val_errors = _validate_manifest_locally(base_path)
            if val_errors:
                print(f"  [{ts}] Manifest invalid, push skipped:", flush=True)
                for err in val_errors:
                    print(f"    - {err}", flush=True)
                state.requeue(changes, deletes)
                return

        result = await _do_push(
            push_files, extra_headers={"X-Bifrost-Watch": "true"}, client=client,
        )
        if result:
            ts = datetime.now().strftime('%H:%M:%S')
            parts = []
            if result.get("created"):
                parts.append(f"{result['created']} created")
            if result.get("updated"):
                parts.append(f"{result['updated']} updated")
            if deleted_count:
                parts.append(f"{deleted_count} deleted")
            if result.get("unchanged"):
                parts.append(f"{result['unchanged']} unchanged")
            print(f"  [{ts}] Pushed → {', '.join(parts) if parts else 'no changes'}", flush=True)

            if result.get("errors"):
                for error in result["errors"]:
                    print(f"    Error: {error}", flush=True)
            if result.get("warnings"):
                for warning in result["warnings"]:
                    print(f"    Warning: {warning}", flush=True)

            # Write back server files (pause watcher to avoid re-trigger)
            if result.get("manifest_files") or result.get("modified_files"):
                state.writeback_paused = True
                try:
                    writeback_paths = _write_back_server_files(base_path, repo_prefix, result)
                finally:
                    await asyncio.sleep(0.2)
                    state.discard_writeback_paths(writeback_paths)
                    state.writeback_paused = False
```

**Step 4: Simplify `_watch_and_push`**

```python
async def _watch_and_push(
    local_path: str,
    repo_prefix: str,
    clean: bool,
    validate: bool,
    client: BifrostClient,
) -> int:
    """Watch directory for changes and auto-push."""
    from watchdog.observers import Observer

    path = pathlib.Path(local_path).resolve()
    if not path.exists() or not path.is_dir():
        print(f"Error: {local_path} is not a valid directory", file=sys.stderr)
        return 1

    # Notify server
    try:
        await client.post("/api/files/watch", json={"action": "start", "prefix": repo_prefix})
    except Exception:
        pass

    # Initial full push
    print(f"Initial push of {path}...", flush=True)
    await _push_files(str(path), repo_prefix=repo_prefix, clean=clean, validate=validate, client=client)

    # Set up file watcher
    state = _WatchState(path)
    handler = _WatchChangeHandler(state)
    observer = Observer()
    observer.schedule(handler, str(path), recursive=True)
    observer.start()

    print(f"Watching {path} for changes... (Ctrl+C to stop)", flush=True)

    heartbeat_interval = WATCH_HEARTBEAT_SECONDS
    last_heartbeat = asyncio.get_event_loop().time()
    consecutive_errors = 0

    try:
        while True:
            await asyncio.sleep(0.5)

            # Restart observer if thread died
            if not observer.is_alive():
                print("  ⚠ File watcher died, attempting restart...", flush=True)
                try:
                    observer = Observer()
                    observer.schedule(handler, str(path), recursive=True)
                    observer.start()
                    print("  ✓ File watcher restarted", flush=True)
                except Exception as e:
                    print(f"  ✗ Could not restart file watcher: {e}", file=sys.stderr, flush=True)
                    break

            changes, deletes = state.drain()
            if changes or deletes:
                try:
                    await _process_watch_batch(client, changes, deletes, path, repo_prefix, state)
                    consecutive_errors = 0
                except KeyboardInterrupt:
                    raise
                except Exception as batch_err:
                    consecutive_errors += 1
                    ts = datetime.now().strftime('%H:%M:%S')
                    print(f"  [{ts}] Push error: {batch_err}", flush=True)
                    state.requeue(changes, deletes)
                    if consecutive_errors >= 10:
                        print(f"  [{ts}] ⚠ {consecutive_errors} consecutive errors, backing off to 5s", flush=True)
                        await asyncio.sleep(5)

            # Heartbeat
            now = asyncio.get_event_loop().time()
            if now - last_heartbeat > heartbeat_interval:
                try:
                    await client.post("/api/files/watch", json={"action": "heartbeat", "prefix": repo_prefix})
                except Exception:
                    pass
                last_heartbeat = now

    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()

    return 0
```

**Step 5: Run linter and type checker**

Run: `cd api && ruff check . && pyright`
Expected: Clean

**Step 6: Commit**

```bash
git add api/bifrost/cli.py
git commit -m "refactor: break up _watch_and_push into focused helpers"
```

---

### Task 6: Final Verification

**Step 1: Full lint + type check**

Run: `cd api && ruff check . && pyright`
Expected: 0 errors

**Step 2: Unit tests**

Run: `./test.sh tests/unit/ -v`
Expected: All pass

**Step 3: Git sync E2E tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All pass

**Step 4: Grep for remaining hardcoded `apps/` in non-convention contexts**

Run: `cd api && grep -rn 'f"apps/{' --include='*.py' | grep -v test | grep -v migration | grep -v manifest_generator | grep -v 'repo_path=f"apps/'`

Expected: Only creation defaults (e.g., `repo_path=f"apps/{data.slug}"` in `create_application`).

**Step 5: Commit any fixes**

If step 4 finds remaining issues, fix and commit.
