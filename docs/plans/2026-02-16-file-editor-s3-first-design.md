# File Editor: S3-First Operations

**Date:** 2026-02-16
**Branch:** `feature/file-index-search-only-cleanup`
**Status:** Approved design

## Problem

The workspace file editor's list and delete operations use `file_index` (PostgreSQL) as their source of truth instead of S3. `file_index` should be a search-only write-through index. This causes:

- **Folder deletes silently fail.** The delete endpoint checks `file_index` for explicit folder marker rows. Folders created implicitly (by writing files underneath them) have no marker, so the endpoint treats `DELETE bifrost-workspace` as a single-file delete. The children survive in S3, and the folder reappears on refresh.
- **Listings can drift from S3 state.** Any file that exists in S3 but wasn't indexed (binary files, race conditions, manual S3 uploads) is invisible to the editor.

## Principles

**S3 (`_repo/`) is the source of truth for what files exist.** All file operations — list, read, write, delete — start with S3. Everything else is a side effect:

```
S3 operation (always)
  → file_index update (if text file, for search)
  → Redis module cache (if .py, for worker execution)
  → App preview compile + cache invalidation (if app file)
  → Platform entity metadata update (if workflow/form/agent .py)
```

Write already follows this pattern correctly. List and delete do not.

## Changes

### 1. List: S3 via RepoStorage

**Workspace editor** (`GET /api/files/editor`):
- Currently: `folder_ops.list_files()` → `SELECT * FROM file_index WHERE path LIKE ...`
- After: `RepoStorage.list(prefix)` → S3 `ListObjectsV2`
- Folder structure synthesized from S3 results (CommonPrefixes or path parsing)

**App editor** (`GET /api/applications/{app_id}/files`):
- Currently: `file_index.list_paths(prefix=f"apps/{slug}/")` → DB query
- After: `RepoStorage.list(f"apps/{slug}/")` → S3

### 2. Delete: S3-First with Side Effects

**Folder detection** (router level):
- Currently: checks `file_index` for folder marker row → fragile, markers often missing
- After: `RepoStorage.list(path + "/")` → if results exist, it's a folder

**Folder delete**:
- Currently: `folder_ops.delete_folder()` finds children via `SELECT FROM file_index`
- After: find children via `RepoStorage.list(prefix)`, delete each from S3, then run side effects per file

**Single file delete**: unchanged (already deletes from S3 first)

**Side effects per deleted file** (unchanged logic, just called from S3-discovered file list):
- Remove from `file_index` (if row exists)
- If `.py`: invalidate Redis module cache
- If app file: delete preview, invalidate render cache, fire pubsub
- If platform entity: remove metadata from DB

### 3. Kill Folder Markers

`folder_ops.create_folder()` is removed. Folders are virtual — they exist when files exist under them. The "Create Folder" UI action creates a placeholder file (e.g., `.gitkeep`) or is removed.

### 4. Code Clarity: Make Side Effects Obvious

`file_ops.py` currently mixes the core S3 operation with ~50 lines of conditional side effects inline. Restructure `write_file()` and `delete_file()` so the pattern is visually obvious:

```python
async def delete_file(self, path: str) -> None:
    # === S3: Source of truth ===
    await self._delete_from_s3(path)

    # === Side effects (conditional) ===
    await self._remove_from_search_index(path)
    await self._invalidate_module_cache_if_python(path)
    await self._handle_app_side_effects_if_applicable(path, action="delete")
    await self._remove_platform_entity_metadata(path)
```

Same for `write_file()`. This makes it immediately clear that S3 is the operation and everything else is a consequence.

## What Gets Removed

- `folder_ops.list_files()` — replaced by `RepoStorage.list()`
- `folder_ops.delete_folder()` child discovery via file_index — replaced by S3 prefix list
- `folder_ops.create_folder()` — folders are virtual
- `FileIndexService.list_paths()` — no longer used for listings (only `search()` remains)
- Folder marker rows in file_index — no longer created or queried

## What Stays Unchanged

- `file_ops.write_file()` core logic — already S3-first
- `file_ops.delete_file()` side effects — same logic, just better organized
- `folder_ops.download_workspace()` / `upload_from_directory()` — git sync concern
- `FileIndexService.write()` / `search()` / `delete()` — search index operations
- `RepoStorage` — already has `list()`, `read()`, `write()`, `delete()`

## Future Work: App Path Convention Cleanup

**NOT in scope for this change. Track separately.**

There are 20+ instances across 9 files where code does `if path.startswith("apps/")` and extracts a slug from path position to determine app ownership. While the current pattern works (it validates the slug against the Application table), it's fragile and makes the codebase harder to reason about.

The app editor endpoints (`/api/applications/{app_id}/files`) already do it right — they receive `app_id` from the URL and pass it through. But when they call into `FileStorageService`, the app context is lost and `file_ops.py` has to reverse-engineer it from the path.

Affected files:
- `file_ops.py` (write + delete app side effects)
- `entity_detector.py`
- `indexers/app.py` (4 instances)
- `github_sync_virtual_files.py` (5 instances)
- `mcp_server/tools/apps.py`
- `applications.py`, `workflows.py`, `maintenance.py`, `dependency_graph.py` (prefix queries)

The fix would involve either:
1. Passing optional entity context (app_id, entity_type) through the service boundary
2. A registry/lookup service that maps paths → entities via DB metadata
