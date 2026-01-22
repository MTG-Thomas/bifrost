# GitHub Sync Simplification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify the GitHub sync system by removing unused complexity, building reference maps once per operation, and caching clones between preview and execute phases.

**Architecture:** Three phases - (0) remove unused `child_files` code, (1) pass maps through call chains instead of rebuilding, (2) cache clones in scheduler with TTL.

**Tech Stack:** Python, SQLAlchemy, FastAPI, pytest

---

## Task 1: Remove child_files from VirtualFile

**Files:**
- Modify: `api/src/services/github_sync_virtual_files.py:54-83` (VirtualFile dataclass)
- Modify: `api/src/services/github_sync_virtual_files.py:308-434` (_get_app_files method)
- Modify: `api/src/services/github_sync_virtual_files.py:436-459` (_compute_combined_sha method)

**Step 1: Remove child_files field from VirtualFile dataclass**

In `github_sync_virtual_files.py`, update the VirtualFile dataclass (around line 54-83):

```python
@dataclass
class VirtualFile:
    """
    A virtual file representing a platform entity.

    Virtual files are generated on-the-fly from database entities (forms, agents, apps)
    and can participate in GitHub sync without being stored in workspace_files.

    Attributes:
        path: Virtual file path, e.g., "forms/{uuid}.form.json" or "apps/{slug}/app.json"
        entity_type: Type of entity - "form", "agent", "app", or "app_file"
        entity_id: Stable identifier - UUID for forms/agents, "app::{uuid}" for apps, path for app_files
        content: Serialized content as bytes
        computed_sha: Git blob SHA of content
    """

    path: str
    entity_type: str
    entity_id: str
    content: bytes | None = None
    computed_sha: str | None = None
```

**Step 2: Simplify _get_app_files to emit individual files only**

Replace `_get_app_files` method (around line 308-434) with this simplified version that only emits individual app files:

```python
async def _get_app_files(self) -> VirtualFileResult:
    """
    Generate virtual files for all applications.

    Each app produces multiple virtual files:
    - apps/{slug}/app.json - portable metadata
    - apps/{slug}/{path} - each code file (pages/*.tsx, components/*.tsx, etc.)

    Uses the app's active_version if published, otherwise draft_version.
    Code files have useWorkflow UUIDs transformed to portable refs.
    """
    # Build workflow ref map for transforming UUIDs to portable refs
    workflow_map = await build_workflow_ref_map(self.db)

    # Query apps with their versions and files eagerly loaded
    stmt = (
        select(Application)
        .options(
            selectinload(Application.active_version).selectinload(AppVersion.files),
            selectinload(Application.draft_version_ref).selectinload(AppVersion.files),
        )
    )
    result = await self.db.execute(stmt)
    apps = result.scalars().all()

    virtual_files: list[VirtualFile] = []
    errors: list[SerializationError] = []

    for app in apps:
        # Use active_version if published, otherwise draft
        version = app.active_version or app.draft_version_ref
        if not version:
            logger.debug(f"App {app.slug} has no version, skipping")
            continue

        app_dir = f"apps/{app.slug}"
        app_entity_id = f"app::{app.id}"  # Stable ID regardless of slug/directory

        # 1. Serialize app.json (portable metadata only)
        try:
            app_json_content = _serialize_app_to_json(app)
            app_json_sha = compute_git_blob_sha(app_json_content)

            virtual_files.append(
                VirtualFile(
                    path=f"{app_dir}/app.json",
                    entity_type="app",
                    entity_id=app_entity_id,
                    content=app_json_content,
                    computed_sha=app_json_sha,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to serialize app {app.slug}: {e}")
            errors.append(
                SerializationError(
                    entity_type="app",
                    entity_id=app_entity_id,
                    entity_name=app.name,
                    path=f"{app_dir}/app.json",
                    error=str(e),
                )
            )
            continue  # Skip files if app.json fails

        # 2. Serialize each code file with UUID -> ref transformation
        for file in version.files:
            file_path = f"{app_dir}/{file.path}"
            try:
                # Transform UUIDs to portable refs
                transformed_source, _ = transform_app_source_uuids_to_refs(
                    file.source, workflow_map
                )
                file_content = transformed_source.encode("utf-8")
                file_sha = compute_git_blob_sha(file_content)

                virtual_files.append(
                    VirtualFile(
                        path=file_path,
                        entity_type="app_file",
                        entity_id=file_path,
                        content=file_content,
                        computed_sha=file_sha,
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to serialize app file {file.path}: {e}")
                errors.append(
                    SerializationError(
                        entity_type="app_file",
                        entity_id=str(file.id),
                        entity_name=file.path,
                        path=file_path,
                        error=str(e),
                    )
                )

    return VirtualFileResult(files=virtual_files, errors=errors)
```

**Step 3: Delete _compute_combined_sha method**

Remove the entire `_compute_combined_sha` static method (around line 436-459) - it's no longer used.

**Step 4: Run tests to verify**

```bash
./test.sh tests/unit/services/test_github_sync_virtual_files.py -v
```

Expected: Tests pass (some may need adjustment if they test child_files)

**Step 5: Commit**

```bash
git add api/src/services/github_sync_virtual_files.py
git commit -m "refactor(sync): remove unused child_files from VirtualFile

- Remove child_files field from VirtualFile dataclass
- Simplify _get_app_files to emit individual files only
- Remove unused _compute_combined_sha method
- Keep stable entity_id format (app::uuid for apps)"
```

---

## Task 2: Remove child_files filter from github_sync.py

**Files:**
- Modify: `api/src/services/github_sync.py:1166-1172`

**Step 1: Update the local_virtual_by_id map building**

In `github_sync.py`, around line 1166-1172, remove the `child_files is None` filter since child_files no longer exists:

Change from:
```python
# Build local map, excluding composite app entries (they have child_files)
# We compare individual files for now to maintain backward compatibility
local_virtual_by_id = {
    vf.entity_id: vf
    for vf in local_virtual_result.files
    if vf.child_files is None  # Skip composite entries
}
```

To:
```python
# Build local map by entity ID for comparison
local_virtual_by_id = {
    vf.entity_id: vf
    for vf in local_virtual_result.files
}
```

**Step 2: Run type check**

```bash
cd api && pyright
```

Expected: No errors related to child_files

**Step 3: Run tests**

```bash
./test.sh tests/unit/services/test_github_sync.py -v
```

Expected: Tests pass

**Step 4: Commit**

```bash
git add api/src/services/github_sync.py
git commit -m "refactor(sync): remove child_files filter from sync comparison

child_files field was removed from VirtualFile, so the filter is no longer needed."
```

---

## Task 3: Delete test file for child_files

**Files:**
- Delete: `api/tests/unit/services/test_virtual_file_child_files.py`

**Step 1: Check if the file exists and delete it**

```bash
rm -f api/tests/unit/services/test_virtual_file_child_files.py
```

**Step 2: Commit**

```bash
git add -A
git commit -m "test(sync): remove obsolete child_files test file"
```

---

## Task 4: Add workflow_map parameter to VirtualFileProvider

**Files:**
- Modify: `api/src/services/github_sync_virtual_files.py:143-181` (get_all_virtual_files)
- Modify: `api/src/services/github_sync_virtual_files.py:308` (_get_app_files)

**Step 1: Update get_all_virtual_files to accept workflow_map parameter**

Change the method signature and body (around line 143-181):

```python
async def get_all_virtual_files(
    self,
    workflow_map: dict[str, str] | None = None,
) -> VirtualFileResult:
    """
    Get all platform entities as virtual files.

    Retrieves all forms, agents, and apps from the database, serializes them
    to JSON/source with portable workflow refs, and returns them as VirtualFile
    objects with computed git SHAs. Also collects any serialization errors.

    Args:
        workflow_map: Optional pre-built workflow ref map. If not provided,
                      will be built internally. Pass this to avoid redundant
                      database queries when the caller already has the map.

    Returns:
        VirtualFileResult containing files and any errors encountered
    """
    # Build workflow ref map if not provided
    if workflow_map is None:
        workflow_map = await build_workflow_ref_map(self.db)
    logger.debug(f"Using workflow ref map with {len(workflow_map)} entries")

    virtual_files: list[VirtualFile] = []
    errors: list[SerializationError] = []

    # Get all entity types, passing workflow_map through
    form_result = await self._get_form_files(workflow_map)
    agent_result = await self._get_agent_files(workflow_map)
    app_result = await self._get_app_files(workflow_map)

    virtual_files.extend(form_result.files)
    virtual_files.extend(agent_result.files)
    virtual_files.extend(app_result.files)

    errors.extend(form_result.errors)
    errors.extend(agent_result.errors)
    errors.extend(app_result.errors)

    logger.info(
        f"Generated {len(virtual_files)} virtual files: "
        f"{len(form_result.files)} forms, {len(agent_result.files)} agents, "
        f"{len(app_result.files)} app files, "
        f"{len(errors)} errors"
    )

    return VirtualFileResult(files=virtual_files, errors=errors)
```

**Step 2: Update _get_app_files to accept workflow_map parameter**

Change the method signature (around line 308):

```python
async def _get_app_files(
    self,
    workflow_map: dict[str, str],
) -> VirtualFileResult:
    """
    Generate virtual files for all applications.
    ...
    """
    # Remove the line that builds workflow_map internally:
    # workflow_map = await build_workflow_ref_map(self.db)  <- DELETE THIS

    # ... rest of method uses the passed workflow_map
```

**Step 3: Run type check**

```bash
cd api && pyright
```

Expected: No errors

**Step 4: Run tests**

```bash
./test.sh tests/unit/services/test_github_sync_virtual_files.py -v
```

Expected: Tests pass

**Step 5: Commit**

```bash
git add api/src/services/github_sync_virtual_files.py
git commit -m "feat(sync): add workflow_map parameter to VirtualFileProvider

Allows callers to pass a pre-built workflow ref map to avoid
redundant database queries. The map is now built once and passed
through to all internal methods."
```

---

## Task 5: Add ref_to_uuid parameter to FormIndexer

**Files:**
- Modify: `api/src/services/file_storage/indexers/form.py:101-106`

**Step 1: Update index_form method signature**

Add optional `ref_to_uuid` parameter (around line 101):

```python
async def index_form(
    self,
    path: str,
    content: bytes,
    workspace_file: Any = None,
    ref_to_uuid: dict[str, str] | None = None,
) -> bool:
    """
    Parse and index form from .form.json file.
    ...
    """
```

**Step 2: Use provided map or build internally**

Update the body (around line 138-142) to use the provided map:

Change from:
```python
# Always transform portable refs to UUIDs
# The model annotations tell us which fields contain workflow refs
from src.services.file_storage.ref_translation import build_ref_to_uuid_map
ref_to_uuid = await build_ref_to_uuid_map(self.db)
form_data = transform_refs_for_import(form_data, FormPublic, ref_to_uuid)
```

To:
```python
# Always transform portable refs to UUIDs
# The model annotations tell us which fields contain workflow refs
if ref_to_uuid is None:
    from src.services.file_storage.ref_translation import build_ref_to_uuid_map
    ref_to_uuid = await build_ref_to_uuid_map(self.db)
form_data = transform_refs_for_import(form_data, FormPublic, ref_to_uuid)
```

**Step 3: Run type check**

```bash
cd api && pyright
```

Expected: No errors

**Step 4: Commit**

```bash
git add api/src/services/file_storage/indexers/form.py
git commit -m "feat(sync): add ref_to_uuid parameter to FormIndexer.index_form

Allows callers to pass a pre-built ref map to avoid redundant
database queries during batch imports."
```

---

## Task 6: Add ref_to_uuid parameter to AgentIndexer

**Files:**
- Modify: `api/src/services/file_storage/indexers/agent.py:84-89`

**Step 1: Update index_agent method signature**

Add optional `ref_to_uuid` parameter (around line 84):

```python
async def index_agent(
    self,
    path: str,
    content: bytes,
    workspace_file: Any = None,
    ref_to_uuid: dict[str, str] | None = None,
) -> bool:
    """
    Parse and index agent from .agent.json file.
    ...
    """
```

**Step 2: Use provided map or build internally**

Update the body (around line 121-125) to use the provided map:

Change from:
```python
# Always transform portable refs to UUIDs
# The model annotations tell us which fields contain workflow refs
from src.services.file_storage.ref_translation import build_ref_to_uuid_map
ref_to_uuid = await build_ref_to_uuid_map(self.db)
agent_data = transform_refs_for_import(agent_data, AgentPublic, ref_to_uuid)
```

To:
```python
# Always transform portable refs to UUIDs
# The model annotations tell us which fields contain workflow refs
if ref_to_uuid is None:
    from src.services.file_storage.ref_translation import build_ref_to_uuid_map
    ref_to_uuid = await build_ref_to_uuid_map(self.db)
agent_data = transform_refs_for_import(agent_data, AgentPublic, ref_to_uuid)
```

**Step 3: Run type check**

```bash
cd api && pyright
```

Expected: No errors

**Step 4: Commit**

```bash
git add api/src/services/file_storage/indexers/agent.py
git commit -m "feat(sync): add ref_to_uuid parameter to AgentIndexer.index_agent

Allows callers to pass a pre-built ref map to avoid redundant
database queries during batch imports."
```

---

## Task 7: Add ref_to_uuid parameter to AppIndexer methods

**Files:**
- Modify: `api/src/services/file_storage/indexers/app.py:194-198` (index_app_file)
- Modify: `api/src/services/file_storage/indexers/app.py:369-375` (import_app)

**Step 1: Update index_app_file method signature**

Add optional `ref_to_uuid` parameter (around line 194):

```python
async def index_app_file(
    self,
    path: str,
    content: bytes,
    ref_to_uuid: dict[str, str] | None = None,
) -> bool:
    """
    Parse and index an app code file.
    ...
    """
```

**Step 2: Use provided map or build internally in index_app_file**

Update the body (around line 243-247):

Change from:
```python
# Transform portable workflow refs to UUIDs
ref_to_uuid = await build_ref_to_uuid_map(self.db)
transformed_source, unresolved_refs = transform_app_source_refs_to_uuids(
    source, ref_to_uuid
)
```

To:
```python
# Transform portable workflow refs to UUIDs
if ref_to_uuid is None:
    ref_to_uuid = await build_ref_to_uuid_map(self.db)
transformed_source, unresolved_refs = transform_app_source_refs_to_uuids(
    source, ref_to_uuid
)
```

**Step 3: Update import_app method signature**

Add optional `ref_to_uuid` parameter (around line 369):

```python
async def import_app(
    self,
    app_dir: str,
    files: dict[str, bytes],
    ref_to_uuid: dict[str, str] | None = None,
) -> Application | None:
    """
    Import an app atomically with all its files.
    ...
    """
```

**Step 4: Use provided map or build internally in import_app**

Update the body (around line 424-425):

Change from:
```python
# Build ref map once for all file transformations
ref_to_uuid = await build_ref_to_uuid_map(self.db)
```

To:
```python
# Build ref map once for all file transformations (if not provided)
if ref_to_uuid is None:
    ref_to_uuid = await build_ref_to_uuid_map(self.db)
```

**Step 5: Run type check**

```bash
cd api && pyright
```

Expected: No errors

**Step 6: Commit**

```bash
git add api/src/services/file_storage/indexers/app.py
git commit -m "feat(sync): add ref_to_uuid parameter to AppIndexer methods

Allows callers to pass a pre-built ref map to avoid redundant
database queries during batch imports."
```

---

## Task 8: Update github_sync.py to build and pass maps

**Files:**
- Modify: `api/src/services/github_sync.py` (get_sync_preview and execute_sync methods)

**Step 1: Find where VirtualFileProvider.get_all_virtual_files is called in get_sync_preview**

Search for the call (around line 1163-1164) and pass workflow_map:

```python
# Build workflow_map once for virtual file serialization
from src.services.file_storage.ref_translation import build_workflow_ref_map
workflow_map = await build_workflow_ref_map(self.db)

provider = VirtualFileProvider(self.db)
local_virtual_result = await provider.get_all_virtual_files(workflow_map=workflow_map)
```

**Step 2: Find where indexers are called in execute_sync**

In execute_sync, find where entities are imported (around line 1595+). Build the ref_to_uuid map once before the import loop:

Add near the start of the import phase:
```python
# Build ref_to_uuid map once for all imports
from src.services.file_storage.ref_translation import build_ref_to_uuid_map
ref_to_uuid = await build_ref_to_uuid_map(self.db)
```

Then pass it to each indexer call. Search for calls like:
- `indexer.index_form(...)` -> add `ref_to_uuid=ref_to_uuid`
- `indexer.index_agent(...)` -> add `ref_to_uuid=ref_to_uuid`
- `indexer.index_app_file(...)` -> add `ref_to_uuid=ref_to_uuid`
- `indexer.import_app(...)` -> add `ref_to_uuid=ref_to_uuid`

**Step 3: Run type check**

```bash
cd api && pyright
```

Expected: No errors

**Step 4: Run tests**

```bash
./test.sh tests/unit/services/test_github_sync.py -v
```

Expected: Tests pass

**Step 5: Commit**

```bash
git add api/src/services/github_sync.py
git commit -m "feat(sync): build reference maps once per sync operation

- Build workflow_map once in get_sync_preview, pass to VirtualFileProvider
- Build ref_to_uuid map once in execute_sync, pass to all indexers
- Eliminates redundant database queries (was 2-3 per preview, N per import)"
```

---

## Task 9: Add clone cache to Scheduler

**Files:**
- Modify: `api/src/scheduler/main.py`

**Step 1: Add clone cache dict and TTL to Scheduler.__init__**

Find the `__init__` method and add:

```python
def __init__(self, ...):
    # ... existing init code ...

    # Clone cache for GitHub sync: org_id -> (clone_path, commit_sha, timestamp)
    self._sync_clone_cache: dict[str, tuple[Path, str, float]] = {}
    self._clone_cache_ttl = 300  # 5 minutes
```

Add import at top of file:
```python
from pathlib import Path
import time
```

**Step 2: Update _handle_git_sync_preview_request to cache clone**

At the end of successful preview (around line 662+), after the preview is generated:

```python
# After: preview = await sync_service.get_sync_preview(...)
# Cache the clone path for potential execute
if hasattr(preview, 'clone_path') and preview.clone_path and org_id:
    self._sync_clone_cache[org_id] = (
        Path(preview.clone_path),
        preview.commit_sha or "",
        time.time(),
    )
    logger.debug(f"Cached clone for org {org_id}")
```

Note: We need to check if SyncPreview has clone_path. It may need to be added.

**Step 3: Update _handle_git_sync_request to use cached clone**

At the start of execute (around line 465), before calling execute_sync:

```python
# Check for cached clone
cached_clone_path = None
if org_id and org_id in self._sync_clone_cache:
    path, sha, ts = self._sync_clone_cache[org_id]
    if (time.time() - ts < self._clone_cache_ttl and path.exists()):
        cached_clone_path = path
        logger.info(f"Using cached clone for org {org_id}")

# Execute the sync
sync_result = await sync_service.execute_sync(
    conflict_resolutions=conflict_resolutions,
    confirm_orphans=confirm_orphans,
    confirm_unresolved_refs=confirm_unresolved_refs,
    progress_callback=progress_callback,
    log_callback=log_callback,
    cached_clone_path=cached_clone_path,  # Add this parameter
)

# Clear cache after execute
if org_id:
    self._sync_clone_cache.pop(org_id, None)
```

**Step 4: Run type check**

```bash
cd api && pyright
```

Expected: May have errors if execute_sync doesn't accept cached_clone_path yet

**Step 5: Commit**

```bash
git add api/src/scheduler/main.py
git commit -m "feat(sync): add clone cache to Scheduler

Caches clone path between preview and execute phases with 5-minute TTL.
Avoids redundant repo cloning when user executes sync shortly after preview."
```

---

## Task 10: Add cached_clone_path parameter to execute_sync

**Files:**
- Modify: `api/src/services/github_sync.py` (execute_sync method)

**Step 1: Update execute_sync method signature**

Find the execute_sync method (around line 1595) and add the parameter:

```python
async def execute_sync(
    self,
    conflict_resolutions: dict[str, str] | None = None,
    confirm_orphans: bool = False,
    confirm_unresolved_refs: bool = False,
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
    cached_clone_path: Path | None = None,  # Add this
) -> SyncResult:
```

Add import at top if not present:
```python
from pathlib import Path
```

**Step 2: Use cached clone if valid**

In execute_sync, find where the repo is cloned (search for `_clone_to_temp`). Add logic to use cached clone:

```python
# Use cached clone if provided and valid
clone_dir: str | None = None
if cached_clone_path and cached_clone_path.exists():
    # Verify the clone is still at expected commit (if we have a way to check)
    clone_dir = str(cached_clone_path)
    logger.info("Using cached clone from preview")
else:
    # Clone fresh
    clone_dir = self._clone_to_temp()
```

**Step 3: Run type check**

```bash
cd api && pyright
```

Expected: No errors

**Step 4: Run tests**

```bash
./test.sh tests/unit/services/test_github_sync.py -v
```

Expected: Tests pass

**Step 5: Commit**

```bash
git add api/src/services/github_sync.py
git commit -m "feat(sync): accept cached_clone_path in execute_sync

Allows scheduler to pass a cached clone from the preview phase,
avoiding redundant cloning when preview and execute happen close together."
```

---

## Task 11: Add clone_path to SyncPreview response

**Files:**
- Modify: `api/src/services/github_sync.py` (SyncPreview class and get_sync_preview)

**Step 1: Check if SyncPreview already has clone_path**

Search for the SyncPreview class definition. If it doesn't have clone_path, add it:

```python
@dataclass
class SyncPreview:
    # ... existing fields ...
    clone_path: str | None = None  # Path to temp clone dir (for caching)
    commit_sha: str | None = None  # Current HEAD SHA
```

**Step 2: Return clone_path from get_sync_preview**

At the end of get_sync_preview, before returning the SyncPreview, don't delete the clone_dir yet. Instead, return it:

Find where the preview is constructed and add:
```python
return SyncPreview(
    # ... existing fields ...
    clone_path=clone_dir,
    commit_sha=self._get_head_sha(clone_dir) if clone_dir else None,
)
```

Note: The clone cleanup should happen in the scheduler after caching, not in get_sync_preview.

**Step 3: Run type check**

```bash
cd api && pyright
```

Expected: No errors

**Step 4: Commit**

```bash
git add api/src/services/github_sync.py
git commit -m "feat(sync): return clone_path from get_sync_preview

Allows scheduler to cache the clone for subsequent execute phase."
```

---

## Task 12: Final verification

**Step 1: Run full type check**

```bash
cd api && pyright
```

Expected: No errors

**Step 2: Run linting**

```bash
cd api && ruff check .
```

Expected: No errors (or only pre-existing ones)

**Step 3: Run all sync-related tests**

```bash
./test.sh tests/unit/services/test_github_sync.py tests/unit/services/test_github_sync_virtual_files.py -v
```

Expected: All tests pass

**Step 4: Manual verification**

1. Start dev environment: `./debug.sh`
2. Connect a test GitHub repo
3. Create a form or agent locally
4. Run sync preview - verify correct files shown
5. Execute sync immediately - check logs for "Using cached clone"
6. Verify files pushed to GitHub correctly

**Step 5: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix(sync): address review feedback from final verification"
```

---

## Summary of Changes

| Phase | Files Modified | Description |
|-------|---------------|-------------|
| 0 | `github_sync_virtual_files.py`, `github_sync.py` | Remove unused child_files complexity |
| 1 | `github_sync_virtual_files.py`, `form.py`, `agent.py`, `app.py`, `github_sync.py` | Build maps once, pass through |
| 2 | `scheduler/main.py`, `github_sync.py` | Clone caching with TTL |

## Expected Outcomes

| Metric | Before | After |
|--------|--------|-------|
| Map builds per sync preview | 2-3 | 1 |
| Map builds per import | N (per entity) | 1 |
| Repo clones per preview+execute | 2 | 1 (if within TTL) |
| Unused code (child_files) | Present | Removed |
