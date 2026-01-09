# GitHub Integration: API-Based Sync with Orphan Protection

## Overview

Replace the current file-based git sync (Dulwich + local `/tmp/bifrost/git` folder) with a GitHub API-only approach. This eliminates multi-container issues and simplifies the architecture.

**Key Principles:**
1. No local git folder - all operations via GitHub API
2. DB is the source of truth for "local" state
3. Production protection via orphaned workflows (never auto-deactivate)
4. Simple conflict resolution (pick one side, no 3-way merge)
5. Sync is dumb and safe; workflow management is separate

---

## Implementation Status

### Phase 1: Core Services

- [x] Create `api/src/services/github_api.py` - GitHub REST API client
- [x] Create `api/src/services/github_sync.py` - GitHubSyncService (API-only sync)
- [x] Create `api/src/services/workflow_orphan.py` - Orphan management service
- [x] Create `api/src/services/git_serialization.py` - Serialization helpers
- [x] Create unit tests for all services

### Phase 2: Database Schema

- [x] Add `github_sha` column to `workspace_files` table
- [x] Add `is_orphaned` column to `workflows` table
- [x] Create migration `api/alembic/versions/20260108_010000_add_github_sync_columns.py`
- [x] **DROP** `last_git_commit_hash` column (unused legacy)
- [x] Create migration to drop `last_git_commit_hash` (`20260109_drop_last_git_commit_hash.py`)

### Phase 3: API Endpoints

**Sync Endpoints (Refactored):**
- [x] **REFACTOR** `POST /api/github/sync/preview` → `GET /api/github/sync`
  - Change to GET (read-only operation)
  - Returns: `{ to_pull, to_push, conflicts, will_orphan, is_empty }`
- [x] **REFACTOR** `POST /api/github/sync/execute` → `POST /api/github/sync`
  - Takes: `{ conflict_resolutions: {path: "keep_local"|"keep_remote"}, confirm_orphans: bool }`
  - Only actionable items passed (not full file lists)
  - Returns job_id for WebSocket progress tracking

**Commit History Endpoint (Refactored):**
- [x] **REFACTOR** `GET /api/github/commits` to use GitHub API
  - Add `list_commits()` method to `GitHubAPIClient`
  - Remove dependency on `git_integration.py`

**Orphan Management Endpoints:**
- [ ] Create `GET /api/workflows/orphaned` endpoint
- [ ] Create `POST /api/workflows/{id}/replace` endpoint
- [ ] Create `POST /api/workflows/{id}/recreate` endpoint
- [ ] Create `POST /api/workflows/{id}/deactivate` endpoint
- [ ] Create `GET /api/workflows/{id}/compatible-replacements` endpoint

### Phase 4: Fix Async Job Streaming (BLOCKING ISSUE)

**Problem:** `/sync/execute` returns `job_id` but job never starts. Worker uses deprecated `GitIntegrationService`.

**Solution:** Move to scheduler pattern (like maintenance/reindex) with per-file progress reporting.

- [x] Add `publish_git_sync_request()` in `api/src/core/pubsub.py`
- [x] Add `publish_git_sync_progress()` in `api/src/core/pubsub.py` (for per-file progress)
- [x] Add `publish_git_sync_log()` in `api/src/core/pubsub.py`
- [x] Add `publish_git_sync_completed()` in `api/src/core/pubsub.py`
- [x] Subscribe scheduler to `scheduler:git-sync` channel in `api/src/scheduler/main.py`
- [x] Add `_handle_git_sync_request()` handler in scheduler with progress callbacks
- [x] Update `POST /api/github/sync` to use Redis pubsub (not RabbitMQ)
- [x] Add progress callbacks to `GitHubSyncService.execute_sync()` for ALL phases:
  - [x] Per-file progress for PULLING (e.g., "pulling 1/4000: workflows/foo.py")
  - [x] Per-file progress for CONFLICTS (e.g., "resolving 1/5: workflows/bar.py")
  - [x] Per-file progress for PUSHING (e.g., "pushing 1/100: workflows/baz.py")
- [x] Refactor `_push_changes()` to support per-file progress (currently batches)
- [x] Update frontend WebSocket handler for `git_progress` messages
- [x] Update Source Control panel to show progress (current/total)

### Phase 5: Delete Legacy Code ✅ COMPLETE

**Files DELETED:**
- [x] `api/src/jobs/consumers/git_sync.py` - Replaced by scheduler handler
- [x] `api/src/jobs/consumers/github_setup.py` - Empty stub, never used
- [x] `api/src/services/git_integration.py` - **DELETED** (3,353 lines removed)
- [x] `api/tests/integration/platform/test_git_modules.py` - Obsolete tests deleted

**Files CREATED:**
- [x] `api/src/services/github_config.py` - New config management service (~165 lines)

**Code REMOVED:**
- [x] Remove `GitSyncConsumer` from `api/src/worker/main.py`
- [x] Remove `GitSyncConsumer` from `api/src/jobs/consumers/__init__.py`
- [x] Remove `GITHUB_SETUP_LOCK_NAME` from `api/src/core/locks.py`
- [x] Remove imports of `git_integration` from `api/src/routers/github.py`
- [x] Remove `get_git_service()` helper and usages

**Endpoints DELETED (11 total):**
- [x] `POST /api/github/pull` - Legacy endpoint (published to dead queue)
- [x] `POST /api/github/push` - Replaced by POST /sync
- [x] `POST /api/github/refresh` - Replaced by GET /sync
- [x] `POST /api/github/init` - Local folder concept, not used
- [x] `POST /api/github/commit` - Local commit, not relevant
- [x] `GET /api/github/changes` - Replaced by GET /sync
- [x] `GET /api/github/conflicts` - Local merge, not relevant
- [x] `POST /api/github/abort-merge` - Local merge, not relevant
- [x] `POST /api/github/discard-unpushed` - Local commits, not relevant
- [x] `POST /api/github/discard-commit` - Local commits, not relevant
- [x] `POST /api/github/resolve-refs` - Used local folder path

**Endpoints MIGRATED to new services (8 total):**
- [x] `GET /api/github/config` - Uses `GitHubConfigService`
- [x] `POST /api/github/validate` - Uses `GitHubAPIClient.list_repositories()`
- [x] `POST /api/github/configure` - Uses `GitHubConfigService`
- [x] `GET /api/github/repositories` - Uses `GitHubAPIClient.list_repositories()`
- [x] `GET /api/github/branches` - Uses `GitHubAPIClient.list_branches()`
- [x] `POST /api/github/create-repository` - Uses `GitHubAPIClient.create_repository()`
- [x] `POST /api/github/disconnect` - Uses `GitHubConfigService`
- [x] `GET /api/github/commits` - Uses `GitHubAPIClient.list_commits()`

**Endpoints UNCHANGED (already using new services):**
- [x] `GET /api/github/sync` - Uses `GitHubSyncService`
- [x] `POST /api/github/sync` - Uses Redis pubsub + scheduler

### Phase 6: Clean Up Database Schema

**Current fields in `workspace_files`:**
- `git_status` (enum) - UI indicator: UNTRACKED, SYNCED, MODIFIED, DELETED
- ~~`last_git_commit_hash`~~ - **DROPPED** - legacy from old git folder approach
- `github_sha` - **ACTIVE** - blob SHA for change detection

**Actions:**
- [x] Create migration to DROP `last_git_commit_hash` column
- [x] Remove all references to `last_git_commit_hash` in code
- [x] Update `git_tracker.py` to use `github_sha` instead
- [ ] Remove `update_git_status` and `bulk_update_git_status` from `file_storage/service.py` (still in use)
- [ ] Review if `git_status` enum is still needed (may compute from `github_sha` at runtime)

### Phase 7: Frontend

- [x] Create sync preview UI in Source Control panel
- [x] Add conflict resolution UI
- [x] Add orphan confirmation UI
- [ ] Create `OrphanedWorkflowDialog` component
- [ ] Add orphan badge to workflow cards
- [ ] Update Workflows page to show orphaned workflows
- [x] Add progress indicator for sync operations (show current/total files)
- [x] Handle `git_progress` WebSocket messages

### Phase 8: Tests

- [x] Unit tests for `GitHubSyncService`
- [x] Unit tests for `GitHubAPIClient`
- [x] Unit tests for `WorkflowOrphanService`
- [x] Unit tests for git serialization
- [ ] E2E tests for sync flow with WebSocket streaming
- [ ] E2E tests for orphan management

---

## What's Complete vs Remaining

### ✅ COMPLETE (Core Sync Flow + Service Consolidation)
1. **Scheduler-based sync execution** - Jobs now run via scheduler, not worker
2. **Per-file progress reporting** - All phases report individual file progress
3. **GET/POST /sync endpoints** - Clean REST API design
4. **GitHub API for commits** - No more Dulwich dependency for commits
5. **Database migration** - `last_git_commit_hash` dropped
6. **Frontend progress UI** - Shows real-time sync progress
7. **Service consolidation** - ONE coherent GitHub service (API-only approach)
8. **Legacy code deleted** - `git_integration.py` (3,353 lines) removed
9. **Config management** - New `GitHubConfigService` for encrypted token storage
10. **All config endpoints migrated** - 8 endpoints now use new services
11. **11 legacy endpoints deleted** - `/pull`, `/push`, `/refresh`, etc. removed
12. **E2E tests updated** - All 14 GitHub E2E tests pass

### ⏳ REMAINING (Orphan Features)
1. **Orphan management endpoints** - Not yet implemented (5 endpoints)
2. **Orphan UI components** - Dialog, badges, workflow page updates
3. **E2E tests for orphan management** - Need WebSocket streaming tests

---

## Architecture

### Data Flow

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   Bifrost   │ ◄─────► │  PostgreSQL │         │   GitHub    │
│     UI      │         │     DB      │ ◄─────► │    API      │
└─────────────┘         └─────────────┘         └─────────────┘
                              │
                              ▼
                        workspace_files
                        workflows
                        forms, apps, agents
```

### Async Job Flow (Scheduler Pattern)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  API Router │     │    Redis    │     │  Scheduler  │     │  WebSocket  │
│  POST /sync │────►│   PubSub    │────►│   Handler   │────►│  Broadcast  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
      │                                        │                    │
      │ Returns job_id                         │ Calls              │ Sends
      │ immediately                            │ GitHubSyncService  │ git_progress
      ▼                                        ▼                    │ git_log
┌─────────────┐                         ┌─────────────┐             │ git_complete
│   Client    │◄────────────────────────│   Service   │             │
│  Subscribes │        WebSocket        │  Progress   │◄────────────┘
│  git:{id}   │         updates         │  Callbacks  │
└─────────────┘                         └─────────────┘
```

---

## Verification

### ✅ Completed Verifications
1. **Progress streaming**: Sync 4000 files, see individual progress in UI
2. **Remove old code**: ✅ No references to `git_integration.py` remain
3. **SHA tracking**: After sync, running preview again shows no changes
4. **Unit tests**: ✅ 64 tests pass (test_github_api.py + test_github_sync.py)
5. **E2E tests**: ✅ 14 tests pass (test_github.py)
6. **Type checking**: ✅ pyright passes with 0 errors
7. **Linting**: ✅ ruff passes

### Remaining Verifications (for orphan features)
1. **Multi-container test**: Run 2+ API containers, sync from each, verify no conflicts
2. **Production safety**: Delete a file in GitHub, sync, verify workflow is orphaned (not deactivated)
3. **Orphan recovery**: Test all three recovery paths work correctly

### Test Commands

```bash
# Start dev stack
./debug.sh

# Run unit tests
./test.sh tests/unit/services/test_github_sync.py tests/unit/services/test_github_api.py

# Run E2E tests
./test.sh tests/e2e/api/test_github.py

# Check logs during sync
docker compose -f docker-compose.dev.yml logs -f scheduler
```
