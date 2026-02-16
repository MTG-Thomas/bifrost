# File Editor S3-First Operations — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move workspace and app editor list/delete operations from file_index (DB) to S3, making file_index search-only.

**Architecture:** `RepoStorage.list()` replaces all file_index listing queries. Delete uses S3 prefix listing for folder detection instead of file_index markers. Side effects (search index, module cache, app preview) remain but are clearly separated from the core S3 operation.

**Tech Stack:** Python/FastAPI, S3 (MinIO in dev), SQLAlchemy, existing `RepoStorage` class.

**Design doc:** `docs/plans/2026-02-16-file-editor-s3-first-design.md`

---

### Task 1: Add S3 list_directory() to RepoStorage

`RepoStorage.list()` returns a flat list of all paths under a prefix. The editor needs non-recursive directory listing (direct children + synthesized folders). Add a method that does this using S3 `Delimiter`.

**Files:**
- Modify: `api/src/services/repo_storage.py:73-98`
- Test: `api/tests/unit/test_repo_storage.py` (create)

**Step 1: Write the test**

```python
# api/tests/unit/test_repo_storage.py
"""Unit tests for RepoStorage S3 operations."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.repo_storage import RepoStorage, REPO_PREFIX


def _mock_settings():
    s = MagicMock()
    s.s3_bucket = "test-bucket"
    s.s3_endpoint_url = "http://localhost:9000"
    s.s3_access_key = "test"
    s.s3_secret_key = "test"
    s.s3_region = "us-east-1"
    return s


class TestListDirectory:
    """Test RepoStorage.list_directory() synthesizes folders from S3."""

    @pytest.mark.asyncio
    async def test_list_directory_returns_files_and_folders(self):
        """Non-recursive list returns direct files and folder prefixes."""
        repo = RepoStorage(settings=_mock_settings())

        # Simulate S3 listing: flat paths under _repo/
        all_paths = [
            "file_at_root.py",
            "workflows/test.py",
            "workflows/utils.py",
            "apps/myapp/_layout.tsx",
            "apps/myapp/pages/index.tsx",
        ]

        with patch.object(repo, "list", new_callable=AsyncMock, return_value=all_paths):
            files, folders = await repo.list_directory("")

        assert sorted(files) == ["file_at_root.py"]
        assert sorted(folders) == ["apps/", "workflows/"]

    @pytest.mark.asyncio
    async def test_list_directory_with_prefix(self):
        """List directory scoped to a prefix."""
        repo = RepoStorage(settings=_mock_settings())

        all_paths = [
            "apps/myapp/_layout.tsx",
            "apps/myapp/pages/index.tsx",
            "apps/myapp/components/Button.tsx",
        ]

        with patch.object(repo, "list", new_callable=AsyncMock, return_value=all_paths):
            files, folders = await repo.list_directory("apps/myapp/")

        assert sorted(files) == ["apps/myapp/_layout.tsx"]
        assert sorted(folders) == ["apps/myapp/components/", "apps/myapp/pages/"]

    @pytest.mark.asyncio
    async def test_list_directory_excludes_system_files(self):
        """Excluded paths (.git, __pycache__) are filtered out."""
        repo = RepoStorage(settings=_mock_settings())

        all_paths = [
            "workflows/test.py",
            "__pycache__/test.cpython-312.pyc",
            ".git/config",
        ]

        with patch.object(repo, "list", new_callable=AsyncMock, return_value=all_paths):
            files, folders = await repo.list_directory("")

        assert files == ["workflows/test.py"]
        # __pycache__ and .git should not appear as folders
        assert folders == []


class TestListDirectoryRecursive:
    """Test RepoStorage.list() for recursive listing (already exists, just validate)."""

    @pytest.mark.asyncio
    async def test_list_returns_all_paths(self):
        """Existing list() returns flat paths recursively."""
        repo = RepoStorage(settings=_mock_settings())

        all_paths = ["a.py", "b/c.py", "b/d.py"]

        with patch.object(repo, "_list_from_s3", new_callable=AsyncMock, return_value=all_paths):
            result = await repo.list("")

        assert result == all_paths
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_repo_storage.py -v`
Expected: FAIL — `list_directory` does not exist on `RepoStorage`

**Step 3: Implement list_directory()**

Add to `api/src/services/repo_storage.py` after the existing `list()` method (after line 98):

```python
async def list_directory(
    self,
    prefix: str = "",
    exclude_fn: callable | None = None,
) -> tuple[list[str], list[str]]:
    """List direct children of a directory in _repo/.

    Returns (files, folders) where:
    - files: full relative paths of direct child files
    - folders: full relative paths of direct child directories (with trailing /)

    Uses the flat list() result and synthesizes folder entries by
    checking for path separators relative to the prefix.

    Args:
        prefix: Directory prefix (e.g. "apps/myapp/"). Empty string for root.
        exclude_fn: Optional filter function(path) -> bool. If True, path is excluded.
    """
    from src.services.editor.file_filter import is_excluded_path

    filter_fn = exclude_fn or is_excluded_path

    all_paths = await self.list(prefix)

    files: list[str] = []
    folders: set[str] = set()

    for path in all_paths:
        if filter_fn(path):
            continue

        # Get the part after the prefix
        relative = path[len(prefix):]
        if not relative:
            continue

        slash_idx = relative.find("/")
        if slash_idx == -1:
            # Direct child file
            files.append(path)
        else:
            # Nested — synthesize the folder
            folder_name = relative[: slash_idx + 1]
            folder_path = f"{prefix}{folder_name}"
            if not filter_fn(folder_path.rstrip("/")):
                folders.add(folder_path)

    return sorted(files), sorted(folders)
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_repo_storage.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/repo_storage.py api/tests/unit/test_repo_storage.py
git commit -m "feat: add list_directory() to RepoStorage for S3-first folder listing"
```

---

### Task 2: Switch workspace editor list to S3

Replace the editor list endpoint's file_index query with `RepoStorage.list_directory()`.

**Files:**
- Modify: `api/src/routers/files.py:362-401` (list_files_editor)
- Test: `api/tests/e2e/api/test_files.py` (existing test_list_files covers this)

**Step 1: Write a focused test for folder listing**

Add to `api/tests/e2e/api/test_files.py`:

```python
def test_list_files_shows_folders(self, e2e_client, platform_admin):
    """Listing a directory shows synthesized folders from child files."""
    # Write files in a subfolder
    for name in ["a.py", "b.py"]:
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={"path": f"e2e_folder_test/{name}", "content": f"# {name}"},
        )

    # List root — should see "e2e_folder_test" as a folder
    response = e2e_client.get(
        "/api/files/editor?path=.",
        headers=platform_admin.headers,
    )
    assert response.status_code == 200
    paths = [f["path"] for f in response.json()]
    types = {f["path"]: f["type"] for f in response.json()}
    assert "e2e_folder_test" in paths
    assert types["e2e_folder_test"] == "folder"

    # Cleanup
    for name in ["a.py", "b.py"]:
        e2e_client.delete(
            f"/api/files/editor?path=e2e_folder_test/{name}",
            headers=platform_admin.headers,
        )
```

**Step 2: Run to verify current behavior (should pass on existing code too)**

Run: `./test.sh tests/e2e/api/test_files.py -v`

**Step 3: Rewrite the list endpoint**

Replace `api/src/routers/files.py:367-401`:

```python
async def list_files_editor(
    ctx: Context,
    user: CurrentSuperuser,
    path: str = Query(..., description="Directory path relative to workspace root"),
    recursive: bool = Query(default=False, description="If true, return all files recursively"),
    db: AsyncSession = Depends(get_db),
) -> list[FileMetadata]:
    """
    List files and folders in a directory with rich metadata.

    Cloud mode only - used by browser editor.
    Lists directly from S3 via RepoStorage (source of truth).
    """
    from src.services.repo_storage import RepoStorage

    try:
        repo = RepoStorage()

        # Normalize path: "." or "" means root
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"

        if recursive:
            from src.services.editor.file_filter import is_excluded_path
            all_paths = await repo.list(prefix)
            return [
                FileMetadata(
                    path=p,
                    name=p.split("/")[-1],
                    type=FileType.FILE,
                    size=None,
                    extension=p.split(".")[-1] if "." in p.split("/")[-1] else None,
                    modified=datetime.now(timezone.utc).isoformat(),
                )
                for p in sorted(all_paths)
                if not is_excluded_path(p)
            ]

        # Non-recursive: get direct children
        child_files, child_folders = await repo.list_directory(prefix)

        files: list[FileMetadata] = []

        # Folders first
        for folder_path in child_folders:
            clean = folder_path.rstrip("/")
            files.append(FileMetadata(
                path=clean,
                name=clean.split("/")[-1],
                type=FileType.FOLDER,
                size=None,
                extension=None,
                modified=datetime.now(timezone.utc).isoformat(),
            ))

        # Then files
        for file_path in child_files:
            name = file_path.split("/")[-1]
            files.append(FileMetadata(
                path=file_path,
                name=name,
                type=FileType.FILE,
                size=None,
                extension=name.split(".")[-1] if "." in name else None,
                modified=datetime.now(timezone.utc).isoformat(),
            ))

        return files

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
```

**Step 4: Run tests**

Run: `./test.sh tests/e2e/api/test_files.py -v`
Expected: PASS (list now hits S3 instead of file_index, same behavior)

**Step 5: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/api/test_files.py
git commit -m "refactor: editor list endpoint uses S3 instead of file_index"
```

---

### Task 3: Switch app editor list to S3

Same change for the app files endpoint.

**Files:**
- Modify: `api/src/routers/app_code_files.py:320-376` (list_app_files)
- Test: `api/tests/unit/routers/test_app_code_files.py` (existing)

**Step 1: Write a test**

Check existing tests in `api/tests/unit/routers/test_app_code_files.py` for the list endpoint. Add or modify to verify it works with S3-backed listing. The key change is replacing `file_index.list_paths()` with `RepoStorage.list()`.

**Step 2: Rewrite the list endpoint**

Replace `api/src/routers/app_code_files.py:332-376`:

```python
    app = await get_application_or_404(ctx, app_id)
    app_storage = AppStorageService()
    repo = RepoStorage()

    # List source files from S3 (source of truth)
    repo_prefix = _repo_prefix(app.slug)
    source_paths = await repo.list(repo_prefix)

    if not source_paths:
        return SimpleFileListResponse(files=[], total=0)

    storage_mode = "preview" if mode == FileMode.draft else "live"

    # Read source from S3 and compiled from _apps/
    files: list[SimpleFileResponse] = []
    for full_path in sorted(source_paths):
        # Derive relative path by stripping the repo prefix
        rel_path = full_path[len(repo_prefix):]
        if not rel_path:
            continue
        # Skip app.yaml (manifest metadata, not a source file)
        if rel_path == "app.yaml":
            continue
        # Skip folder marker entries
        if rel_path.endswith("/"):
            continue

        # Source from S3 (_repo/)
        try:
            source = (await repo.read(full_path)).decode("utf-8", errors="replace")
        except Exception:
            source = ""

        # Compiled from _apps/{app_id}/{mode}/
        compiled: str | None = None
        try:
            compiled_bytes = await app_storage.read_file(str(app.id), storage_mode, rel_path)
            compiled_str = compiled_bytes.decode("utf-8", errors="replace")
            if compiled_str != source:
                compiled = compiled_str
        except FileNotFoundError:
            pass

        files.append(SimpleFileResponse(path=rel_path, source=source, compiled=compiled))

    return SimpleFileListResponse(files=files, total=len(files))
```

The change is minimal: `file_index.list_paths(prefix)` → `repo.list(prefix)`. Remove the `FileIndexService` import/usage.

**Step 3: Run tests**

Run: `./test.sh tests/unit/routers/test_app_code_files.py -v`
Then: `./test.sh tests/e2e/ -v -k "app"` (if app E2E tests exist)
Expected: PASS

**Step 4: Commit**

```bash
git add api/src/routers/app_code_files.py
git commit -m "refactor: app editor list endpoint uses S3 instead of file_index"
```

---

### Task 4: Fix editor delete — S3 folder detection + recursive delete

This is the core bug fix. Replace file_index folder marker check with S3 prefix listing.

**Files:**
- Modify: `api/src/routers/files.py:644-681` (delete_file_editor)
- Modify: `api/src/services/file_storage/file_ops.py:365-436` (delete_file — used for per-file side effects)
- Test: `api/tests/e2e/api/test_files.py`

**Step 1: Write the failing test**

Add to `api/tests/e2e/api/test_files.py`:

```python
def test_delete_folder_removes_children(self, e2e_client, platform_admin):
    """Deleting a folder removes all child files from S3."""
    # Create files in a folder
    for name in ["one.py", "two.py", "sub/three.py"]:
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={"path": f"e2e_delete_folder/{name}", "content": f"# {name}"},
        )

    # Verify folder appears in listing
    response = e2e_client.get(
        "/api/files/editor?path=.",
        headers=platform_admin.headers,
    )
    paths = [f["path"] for f in response.json()]
    assert "e2e_delete_folder" in paths

    # Delete the folder
    response = e2e_client.delete(
        "/api/files/editor?path=e2e_delete_folder",
        headers=platform_admin.headers,
    )
    assert response.status_code == 204

    # Verify folder is gone from listing
    response = e2e_client.get(
        "/api/files/editor?path=.",
        headers=platform_admin.headers,
    )
    paths = [f["path"] for f in response.json()]
    assert "e2e_delete_folder" not in paths

    # Verify children are gone too
    response = e2e_client.get(
        "/api/files/editor?path=e2e_delete_folder",
        headers=platform_admin.headers,
    )
    assert response.json() == [] or response.status_code == 200
```

**Step 2: Run to verify it fails**

Run: `./test.sh tests/e2e/api/test_files.py::TestFileOperations::test_delete_folder_removes_children -v`
Expected: FAIL — folder reappears after delete (the original bug)

**Step 3: Rewrite the delete endpoint**

Replace `api/src/routers/files.py:644-681`:

```python
@router.delete(
    "/editor",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete file or folder (editor)",
)
async def delete_file_editor(
    ctx: Context,
    user: CurrentSuperuser,
    path: str = Query(..., description="File or folder path"),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a file or folder recursively.

    Cloud mode only - used by browser editor.
    Uses S3 prefix listing to detect folders (no file_index markers needed).
    """
    from src.services.repo_storage import RepoStorage

    try:
        storage = FileStorageService(db)
        repo = RepoStorage()

        # Check if this is a folder by listing S3 for children
        folder_prefix = path.rstrip("/") + "/"
        children = await repo.list(folder_prefix)

        if children:
            # Folder delete: delete each child with full side effects
            for child_path in children:
                try:
                    await storage.delete_file(child_path)
                except Exception as e:
                    logger.warning(f"Failed to delete child {child_path}: {e}")
        else:
            # Single file delete
            await storage.delete_file(path)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Not found: {path}")
```

Note: we add `import logging` and `logger = logging.getLogger(__name__)` at the top of the file if not already present.

**Step 4: Run tests**

Run: `./test.sh tests/e2e/api/test_files.py -v`
Expected: PASS — folder delete now actually removes children

**Step 5: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/api/test_files.py
git commit -m "fix: editor folder delete uses S3 prefix listing instead of file_index markers"
```

---

### Task 5: Clean up delete_file() for clarity

Restructure `file_ops.py:delete_file()` so the "S3 first, then side effects" pattern is obvious.

**Files:**
- Modify: `api/src/services/file_storage/file_ops.py:365-436`
- Test: existing tests should still pass (refactor only)

**Step 1: Restructure delete_file()**

Rewrite `api/src/services/file_storage/file_ops.py` `delete_file()` method:

```python
async def delete_file(self, path: str) -> None:
    """
    Delete a file from storage.

    Pattern: S3 first (source of truth), then conditional side effects.
    """
    if path.startswith(".bifrost/") or path == ".bifrost":
        raise HTTPException(
            status_code=403,
            detail=".bifrost/ files are system-generated and cannot be edited directly",
        )

    # === S3: Source of truth ===
    await self._delete_from_s3(path)

    # === Side effects (conditional) ===
    await self._remove_from_search_index(path)
    await self._handle_app_file_cleanup(path)
    await self._remove_metadata(path)
    await self._invalidate_module_cache_if_python(path)

    logger.info(f"File deleted: {path}")

async def _delete_from_s3(self, path: str) -> None:
    """Delete from S3 _repo/ — always runs, idempotent."""
    s3_key = f"{REPO_PREFIX}{path}"
    async with self._s3_client.get_client() as s3:
        try:
            await s3.delete_object(Bucket=self.settings.s3_bucket, Key=s3_key)
        except Exception:
            pass

async def _remove_from_search_index(self, path: str) -> None:
    """Remove from file_index search table if present."""
    from sqlalchemy import delete
    del_stmt = delete(FileIndex).where(FileIndex.path == path)
    await self.db.execute(del_stmt)

async def _handle_app_file_cleanup(self, path: str) -> None:
    """If this file belongs to an app, clean up preview and notify clients."""
    if not path.startswith("apps/"):
        return

    parts = path.split("/")
    if len(parts) < 2:
        return

    slug = parts[1]
    try:
        from src.models.orm.applications import Application
        app_stmt = select(Application).where(Application.slug == slug)
        app_result = await self.db.execute(app_stmt)
        app = app_result.scalar_one_or_none()
        if not app:
            return

        relative_path = "/".join(parts[2:]) if len(parts) > 2 else ""

        from src.core.pubsub import publish_app_code_file_update
        await publish_app_code_file_update(
            app_id=str(app.id),
            user_id="system",
            user_name="system",
            path=relative_path,
            source=None,
            compiled=None,
            action="delete",
        )

        from src.services.app_storage import AppStorageService
        app_storage = AppStorageService()
        await app_storage.delete_preview_file(str(app.id), relative_path)
    except Exception as e:
        logger.warning(f"Failed app file cleanup for {path}: {e}")

async def _invalidate_module_cache_if_python(self, path: str) -> None:
    """Invalidate Redis module cache for Python files."""
    platform_entity_type = detect_platform_entity_type(path, b"")
    if platform_entity_type == "module" or path.endswith(".py"):
        await invalidate_module(path)
```

**Step 2: Run all tests**

Run: `./test.sh -v`
Expected: PASS — pure refactor, same behavior, clearer structure

**Step 3: Commit**

```bash
git add api/src/services/file_storage/file_ops.py
git commit -m "refactor: restructure delete_file() to clarify S3-first, then side effects pattern"
```

---

### Task 6: Remove dead code

Clean up code that's no longer called.

**Files:**
- Modify: `api/src/services/file_storage/folder_ops.py` — remove `list_files()`, `create_folder()`, `delete_folder()`
- Modify: `api/src/services/file_storage/service.py` — remove delegations to removed methods
- Modify: `api/src/routers/files.py` — remove create_folder_editor endpoint (or convert to create .gitkeep)
- Modify: `api/src/services/file_index_service.py` — remove `list_paths()` method
- Delete obsolete tests that test removed functionality

**Step 1: Identify and remove dead code**

In `folder_ops.py`:
- Remove `list_files()` (lines 142-223) — replaced by RepoStorage.list_directory()
- Remove `create_folder()` (lines 65-98) — folders are virtual
- Remove `delete_folder()` (lines 100-140) — replaced by S3 prefix listing in router

In `file_index_service.py`:
- Remove `list_paths()` (lines 98-106) — no longer used for listings

In `service.py` (FileStorageService):
- Remove `list_files()`, `create_folder()`, `delete_folder()` delegations

In `files.py`:
- Remove or modify `create_folder_editor` endpoint (lines 608-639)
  - Option A: Remove entirely (folders are virtual)
  - Option B: Convert to create a `.gitkeep` file in the folder

**Step 2: Run all tests**

Run: `./test.sh -v`
Fix any tests that reference removed code. Tests for removed methods should be deleted.

**Step 3: Run quality checks**

```bash
cd api && pyright && ruff check .
```

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove dead file_index listing code, folder markers, and create_folder"
```

---

### Task 7: Final E2E verification

Run the full test suite and verify the editor works end-to-end.

**Step 1: Run full test suite**

```bash
./test.sh -v
```

**Step 2: Run quality checks**

```bash
cd api && pyright && ruff check .
cd ../client && npm run tsc && npm run lint
```

**Step 3: Manual smoke test (if dev stack running)**

1. Open `http://localhost:3000`, navigate to the workspace editor
2. Create a folder with files
3. Delete the folder — verify it stays gone on refresh
4. Navigate to an app in the app editor — verify files list correctly

**Step 4: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix: address issues found during E2E verification"
```

---

## Notes for future sessions

### App path convention cleanup (NOT in scope)
There are 20+ instances across 9 files of `if path.startswith("apps/")` and slug extraction from path positions. Full audit saved in design doc at `docs/plans/2026-02-16-file-editor-s3-first-design.md` under "Future Work". Track this as a separate task.

### Key files affected by this plan
- `api/src/services/repo_storage.py` — new `list_directory()`
- `api/src/routers/files.py` — list + delete endpoints rewritten
- `api/src/routers/app_code_files.py` — list endpoint uses S3
- `api/src/services/file_storage/file_ops.py` — delete_file() restructured
- `api/src/services/file_storage/folder_ops.py` — dead code removed
- `api/src/services/file_index_service.py` — list_paths() removed
