# File Storage Dead Code Cleanup + Bug Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove all dead code from the file storage system and fix two bugs found during review, establishing a clean baseline for the S3-first editor refactor.

**Architecture:** Pure cleanup — delete dead files/methods, update stale `app.json` references to `app.yaml`, fix S3Backend double-prefix bug and `get_module()` misuse. No new features, no behavioral changes except the two bug fixes.

**Tech Stack:** Python/FastAPI, SQLAlchemy, S3 (MinIO in dev), Redis.

**Prerequisite plan:** `docs/plans/2026-02-16-file-editor-s3-first.md` (the S3-first refactor this cleanup prepares for)

---

## Phase 0: Dead Code Cleanup + Bug Fixes

### Task 1: Baseline test run

Establish green baseline before making changes.

**Step 1: Run full test suite**

Run: `./test.sh -v`
Expected: All tests pass (record any pre-existing failures)

**Step 2: Run static checks**

Run: `cd api && pyright && ruff check .`
Expected: Clean

---

### Task 2: Delete dead `github_sync_virtual_files.py`

`VirtualFileProvider` is never imported by any file in `api/src/`. The entire module is dead — replaced by manifest-based git sync.

**Files:**
- Delete: `api/src/services/github_sync_virtual_files.py`
- Delete: `api/tests/unit/services/test_github_sync_virtual_files.py`

**Step 1: Delete both files**

```bash
rm api/src/services/github_sync_virtual_files.py
rm api/tests/unit/services/test_github_sync_virtual_files.py
```

**Step 2: Verify no broken imports**

Run: `cd api && ruff check . && pyright`
Expected: Clean (no file imports from these)

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove dead VirtualFileProvider module (replaced by manifest-based sync)"
```

---

### Task 3: Delete dead `AppIndexer` class and `_serialize_app_to_json`

`AppIndexer` is never instantiated outside its own file. `service.py:31` imports `WorkflowIndexer, FormIndexer, AgentIndexer` — NOT `AppIndexer`. Every method (`index_app_json`, `import_app`, `index_app_file`, `delete_app`, `delete_app_file`) has zero external callers. `_serialize_app_to_json` was only consumed by the now-deleted `github_sync_virtual_files.py`.

**Files:**
- Delete: `api/src/services/file_storage/indexers/app.py`
- Modify: `api/src/services/file_storage/indexers/__init__.py`

**Step 1: Delete the app indexer file**

```bash
rm api/src/services/file_storage/indexers/app.py
```

**Step 2: Update `__init__.py`**

Replace the full file content of `api/src/services/file_storage/indexers/__init__.py` with:

```python
"""
Entity indexers for file storage service.

Provides modular indexing for different entity types:
- WorkflowIndexer: Python files with @workflow/@tool/@data_provider decorators
- FormIndexer: .form.yaml files
- AgentIndexer: .agent.yaml files
"""

from .agent import AgentIndexer, _serialize_agent_to_yaml
from .form import FormIndexer, _serialize_form_to_yaml
from .workflow import WorkflowIndexer

__all__ = [
    "WorkflowIndexer",
    "FormIndexer",
    "AgentIndexer",
    "_serialize_form_to_yaml",
    "_serialize_agent_to_yaml",
]
```

**Step 3: Verify no broken imports**

Run: `cd api && ruff check . && pyright`
Expected: Clean

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove dead AppIndexer class and _serialize_app_to_json"
```

---

### Task 4: Fix entity_detector — `app.json` to `app.yaml`

The entity detector checks for `app.json` but all runtime code uses `app.yaml`. The `"app"` return value is currently never triggered in production.

**Files:**
- Modify: `api/src/services/file_storage/entity_detector.py:52`
- Modify: `api/tests/unit/services/test_entity_detector.py:79,102-106`

**Step 1: Update entity_detector.py**

In `api/src/services/file_storage/entity_detector.py`, change line 52:

```python
# Before:
        if len(parts) >= 3 and parts[2] == "app.json":
# After:
        if len(parts) >= 3 and parts[2] == "app.yaml":
```

**Step 2: Update test**

In `api/tests/unit/services/test_entity_detector.py`:

Rename `test_detect_app_json` → `test_detect_app_yaml` and update the test body:

```python
    def test_detect_app_yaml(self):
        """app.yaml files should be detected as 'app' entity type."""
        content = b"name: My App\nslug: my-app\n"
        result = detect_platform_entity_type("apps/my-app/app.yaml", content)
        assert result == "app"
```

The `test_unknown_extensions_return_none` parametrize list (line 79) still includes `my_app.app.json` — leave it, it correctly returns `None` now.

**Step 3: Run tests**

Run: `./test.sh tests/unit/services/test_entity_detector.py -v`
Expected: All pass

**Step 4: Commit**

```bash
git add api/src/services/file_storage/entity_detector.py api/tests/unit/services/test_entity_detector.py
git commit -m "fix: entity detector recognizes app.yaml instead of dead app.json"
```

---

### Task 5: Fix entity metadata — `app.json` to `app.yaml`

`github_sync_entity_metadata.py` matches `app.json` paths for sync UI labels. Should match `app.yaml`.

**Files:**
- Modify: `api/src/services/github_sync_entity_metadata.py:20,57,68-69`
- Modify: `api/tests/unit/services/test_github_sync_entity_metadata.py:32-41`
- Modify: `api/tests/unit/routers/test_github_sync_preview.py:28`

**Step 1: Update entity_metadata.py**

In `api/src/services/github_sync_entity_metadata.py`:

Line 20 — rename pattern:
```python
# Before:
APP_JSON_PATTERN = re.compile(r"^apps/([^/]+)/app\.json$")
# After:
APP_YAML_PATTERN = re.compile(r"^apps/([^/]+)/app\.yaml$")
```

Line 57 — update reference:
```python
# Before:
    match = APP_JSON_PATTERN.match(path)
# After:
    match = APP_YAML_PATTERN.match(path)
```

Lines 68-69 — update comment and check:
```python
# Before:
        # Skip app.json (handled above)
        if relative_path != "app.json":
# After:
        # Skip app.yaml (handled above)
        if relative_path != "app.yaml":
```

**Step 2: Update test_github_sync_entity_metadata.py**

In `api/tests/unit/services/test_github_sync_entity_metadata.py`, update test around line 32:

```python
    def test_app_yaml_extracts_name(self):
        """App app.yaml extracts name as display_name."""
        path = "apps/dashboard/app.yaml"
        content = b"name: Dashboard\nslug: dashboard\n"

        result = extract_entity_metadata(path, content)

        assert result.entity_type == "app"
        assert result.display_name == "Dashboard"
        assert result.parent_slug == "dashboard"
```

**Step 3: Update test_github_sync_preview.py**

In `api/tests/unit/routers/test_github_sync_preview.py`, update test around line 25:

```python
def test_extract_entity_metadata_for_app():
    """App metadata should extract name and include parent_slug."""
    content = b"name: Dashboard App\nversion: '1.0'\n"
    metadata = extract_entity_metadata("apps/dashboard/app.yaml", content)

    assert metadata.entity_type == "app"
    assert metadata.display_name == "Dashboard App"
    assert metadata.parent_slug == "dashboard"
```

**Step 4: Run tests**

Run: `./test.sh tests/unit/services/test_github_sync_entity_metadata.py tests/unit/routers/test_github_sync_preview.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add api/src/services/github_sync_entity_metadata.py api/tests/unit/services/test_github_sync_entity_metadata.py api/tests/unit/routers/test_github_sync_preview.py
git commit -m "fix: entity metadata extraction matches app.yaml instead of app.json"
```

---

### Task 6: Remove vestigial `app.json` exclusion filters

Seven `~FileIndex.path.endswith("/app.json")` clauses filter data that no longer exists. Remove them.

**Files:**
- Modify: `api/src/routers/applications.py:515,566`
- Modify: `api/src/routers/workflows.py:227,610`
- Modify: `api/src/routers/maintenance.py:352`
- Modify: `api/src/services/dependency_graph.py:233,398`

**Step 1: Verify no legacy app.json rows exist in file_index**

If the dev stack is running, check directly:
```bash
docker exec bifrost-dev-postgres-1 psql -U bifrost -d bifrost -c "SELECT path FROM file_index WHERE path LIKE '%/app.json' LIMIT 5;"
```

If the stack is not running, add a safety check to the E2E tests in Task 10 — grep for `app.json` in the test output.

If legacy rows DO exist, add a cleanup step before removing filters:
```sql
DELETE FROM file_index WHERE path LIKE '%/app.json';
```

**Step 2: Remove all seven filter lines**

Each is a single line in a `.where()` chain. Remove the line containing `~FileIndex.path.endswith("/app.json")` from each location. The surrounding query structure stays the same — just delete the filter line and fix any trailing comma if needed.

**Step 3: Verify**

Run: `cd api && ruff check . && pyright`
Expected: Clean

**Step 4: Run tests**

Run: `./test.sh -v`
Expected: All pass (these filters were no-ops)

**Step 5: Commit**

```bash
git add api/src/routers/applications.py api/src/routers/workflows.py api/src/routers/maintenance.py api/src/services/dependency_graph.py
git commit -m "chore: remove vestigial app.json exclusion filters from queries"
```

---

### Task 7: Remove dead `download_workspace()` method

`FileStorageService.download_workspace()` is never called from outside the `file_storage/` module. No router, job, or sync code invokes it. Additionally it has a bug (downloads entire bucket, not just `_repo/`), but since it's dead we just delete it.

**Files:**
- Modify: `api/src/services/file_storage/folder_ops.py:250-286` — delete `download_workspace()` method
- Modify: `api/src/services/file_storage/service.py:173-175` — delete delegation method

**Step 1: Delete from folder_ops.py**

Remove the entire `download_workspace` method (lines 250-286) from `api/src/services/file_storage/folder_ops.py`.

**Step 2: Delete from service.py**

Remove the `download_workspace` method (lines 173-175) from `api/src/services/file_storage/service.py`.

**Step 3: Update reindex.py docstring**

In `api/src/services/file_storage/reindex.py:120`, the docstring says "Called after download_workspace()". Update or remove this reference.

**Step 4: Verify**

Run: `cd api && ruff check . && pyright`
Expected: Clean

**Step 5: Commit**

```bash
git add api/src/services/file_storage/folder_ops.py api/src/services/file_storage/service.py api/src/services/file_storage/reindex.py
git commit -m "chore: remove dead download_workspace method"
```

---

### Task 8: Fix S3Backend double-prefix bug

`S3Backend._resolve_path()` prepends `_repo/` to workspace paths. It then passes the prefixed path to `FileStorageService.read_file()`/`write_file()`/`delete_file()`, which prepend `_repo/` again internally. Result: workspace operations hit `_repo/_repo/{path}` in S3.

**Important:** Not all methods have this bug. The downstream APIs have inconsistent expectations:

| Method | Downstream API | Expects | Current passes | Fix |
|--------|---------------|---------|----------------|-----|
| `read_file()` | `file_ops.py` | workspace-relative path (adds `_repo/` internally) | `_repo/path` (double prefix) | Pass `path` |
| `write_file()` | `file_ops.py` | workspace-relative path (adds `_repo/` internally) | `_repo/path` (double prefix) | Pass `path` |
| `delete_file()` | `file_ops.py` | workspace-relative path (adds `_repo/` internally) | `_repo/path` (double prefix) | Pass `path` |
| `list_files()` | `folder_ops.py` | workspace-relative directory (queries file_index) | `_repo/dir` (wrong prefix) | Pass `directory` |
| `file_exists()` | `service.py` | **raw S3 key** (passes directly to `head_object`) | `_repo/path` (**correct!**) | **No change** |

**File:**
- Modify: `api/src/services/file_backend.py:157-209`

**Step 1: Fix four workspace method branches (NOT exists)**

In `api/src/services/file_backend.py`:

`read()` line 164:
```python
# Before:
            content, _ = await self.storage.read_file(s3_path)
# After:
            content, _ = await self.storage.read_file(path)
```

`write()` line 183:
```python
# Before:
            await self.storage.write_file(s3_path, content, updated_by)
# After:
            await self.storage.write_file(path, content, updated_by)
```

`delete()` line 193:
```python
# Before:
            await self.storage.delete_file(s3_path)
# After:
            await self.storage.delete_file(path)
```

`list()` line 203:
```python
# Before:
            files = await self.storage.list_files(s3_dir)
# After:
            files = await self.storage.list_files(directory)
```

**Do NOT change `exists()` — `file_exists()` expects a full S3 key and the current code is correct.**

**Step 2: Write regression test**

Create `api/tests/unit/services/test_file_backend.py`:

```python
"""Unit tests for S3Backend path handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.file_backend import S3Backend


class TestS3BackendPathHandling:
    """Verify S3Backend passes correct path format to each downstream method."""

    def _make_backend(self):
        mock_db = MagicMock()
        with patch("src.services.file_backend.FileStorageService") as MockFSS:
            backend = S3Backend(mock_db)
            backend.storage = MockFSS.return_value
            return backend

    @pytest.mark.asyncio
    async def test_read_passes_workspace_relative_path(self):
        """read() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.read_file = AsyncMock(return_value=(b"content", None))

        await backend.read("workflows/test.py", "workspace")

        backend.storage.read_file.assert_called_once_with("workflows/test.py")

    @pytest.mark.asyncio
    async def test_write_passes_workspace_relative_path(self):
        """write() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.write_file = AsyncMock()

        await backend.write("workflows/test.py", b"content", "workspace", "user")

        backend.storage.write_file.assert_called_once_with(
            "workflows/test.py", b"content", "user"
        )

    @pytest.mark.asyncio
    async def test_delete_passes_workspace_relative_path(self):
        """delete() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.delete_file = AsyncMock()

        await backend.delete("workflows/test.py", "workspace")

        backend.storage.delete_file.assert_called_once_with("workflows/test.py")

    @pytest.mark.asyncio
    async def test_list_passes_workspace_relative_directory(self):
        """list() should pass workspace-relative directory, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.list_files = AsyncMock(return_value=[])

        await backend.list("workflows", "workspace")

        backend.storage.list_files.assert_called_once_with("workflows")

    @pytest.mark.asyncio
    async def test_exists_passes_full_s3_key(self):
        """exists() should pass _repo/ prefixed path (file_exists expects S3 key)."""
        backend = self._make_backend()
        backend.storage.file_exists = AsyncMock(return_value=True)

        await backend.exists("workflows/test.py", "workspace")

        backend.storage.file_exists.assert_called_once_with("_repo/workflows/test.py")
```

**Step 3: Run tests**

Run: `./test.sh tests/unit/services/test_file_backend.py -v`
Expected: All pass

**Step 4: Commit**

```bash
git add api/src/services/file_backend.py api/tests/unit/services/test_file_backend.py
git commit -m "fix: S3Backend double-prefix bug — workspace ops passed _repo/_repo/ paths"
```

---

### Task 9: Guard `get_module()` to Python files only

`file_ops.py:read_file()` calls `get_module(path)` for every file type — TSX, YAML, JSON, etc. — adding a pointless Redis round-trip each time. `get_module()` is a runtime module cache for Python workflow execution only.

**File:**
- Modify: `api/src/services/file_storage/file_ops.py:144-148`
- Create: `api/tests/unit/services/test_file_ops_read.py` (regression test)

**Step 1: Add `.py` guard**

```python
# Before (line 144-148):
        # Everything else: Redis cache → S3 _repo/ (file_index is search-only)
        from src.core.module_cache import get_module
        cached = await get_module(path)
        if cached:
            return cached["content"].encode("utf-8"), None

# After:
        # Python modules: Redis cache → S3 fallback (for fast worker imports)
        if path.endswith(".py"):
            from src.core.module_cache import get_module
            cached = await get_module(path)
            if cached:
                return cached["content"].encode("utf-8"), None
```

**Step 2: Write regression test**

Create `api/tests/unit/services/test_file_ops_read.py`:

```python
"""Unit tests for read_file module cache guard."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestReadFileModuleCacheGuard:
    """Verify get_module() is only called for .py files."""

    @pytest.mark.asyncio
    async def test_read_py_file_checks_module_cache(self):
        """Python files should check Redis module cache."""
        with patch("src.services.file_storage.file_ops.get_module", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"content": "print('hello')", "hash": "abc123"}

            # Import after patching
            from src.services.file_storage.file_ops import FileOperationsService

            service = MagicMock(spec=FileOperationsService)
            service.read_file = FileOperationsService.read_file.__get__(service)

            result = await service.read_file("workflows/test.py")

            mock_get.assert_called_once_with("workflows/test.py")
            assert result[0] == b"print('hello')"

    @pytest.mark.asyncio
    async def test_read_tsx_file_skips_module_cache(self):
        """Non-Python files should NOT check Redis module cache."""
        with patch("src.services.file_storage.file_ops.get_module", new_callable=AsyncMock) as mock_get:
            # Set up S3 fallback
            mock_s3_client = AsyncMock()
            mock_response = {"Body": AsyncMock()}
            mock_response["Body"].read = AsyncMock(return_value=b"<div>hello</div>")
            mock_s3_client.get_object = AsyncMock(return_value=mock_response)

            from src.services.file_storage.file_ops import FileOperationsService

            service = MagicMock(spec=FileOperationsService)
            service._s3_client.get_client.return_value.__aenter__ = AsyncMock(return_value=mock_s3_client)
            service._s3_client.get_client.return_value.__aexit__ = AsyncMock()
            service.settings.s3_bucket = "test"
            service.read_file = FileOperationsService.read_file.__get__(service)

            await service.read_file("apps/myapp/pages/index.tsx")

            mock_get.assert_not_called()
```

**Step 3: Run tests**

Run: `./test.sh tests/unit/services/test_file_ops_read.py -v`
Expected: All pass

**Step 4: Commit**

```bash
git add api/src/services/file_storage/file_ops.py api/tests/unit/services/test_file_ops_read.py
git commit -m "fix: only use module cache for Python files in read_file()"
```

---

### Task 10: Post-cleanup verification

**Step 1: Run full test suite**

Run: `./test.sh -v`
Expected: All pass. Compare count with baseline from Task 1 — fewer tests (deleted virtual file provider tests), same pass rate.

**Step 2: Run static checks**

Run: `cd api && pyright && ruff check .`
Expected: Clean

**Step 3: Verify no remaining app.json references in source**

Run: `grep -r "app\.json" api/src/ --include="*.py" -l`
Expected: No results (all references removed)

---

## Phase 1: Re-exploration & Plan Validation

### Task 11: Re-explore the cleaned codebase

After all Phase 0 commits, re-read the key files the S3-first plan will modify. Confirm line numbers and assumptions still hold:

- `api/src/services/repo_storage.py` — confirm `list()` works as S3-first plan expects
- `api/src/routers/files.py` — confirm list/delete endpoint line references
- `api/src/routers/app_code_files.py` — confirm list endpoint references
- `api/src/services/file_storage/file_ops.py` — confirm delete_file/write_file structure post-bugfix
- `api/src/services/file_storage/folder_ops.py` — confirm what remains after download_workspace removal
- `api/src/services/file_index_service.py` — confirm `list_paths()` still present (S3-first plan removes it)
- `api/src/services/file_backend.py` — confirm S3Backend correct post-bugfix

### Task 12: Update the S3-first plan

Based on re-exploration:
1. Update line number references in `docs/plans/2026-02-16-file-editor-s3-first.md` (shifted after deletions)
2. Confirm or revise task scope — note what Phase 0 already handled vs what remains
3. Document any new findings or gaps discovered
4. Append a "Post-Phase 0 Status" section to the plan

### Task 13: Confirm intent alignment

Verify the S3-first plan's goals still hold:
- **file_index is search-only** — confirm all listing/existence code paths that used file_index are identified
- **S3 is source of truth** — confirm no new DB-first code paths were introduced
- **Side effects are best-effort** — confirm delete/write patterns are clearly structured

Document a short summary of remaining work items for the next session.
