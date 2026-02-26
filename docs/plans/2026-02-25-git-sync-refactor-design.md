# Git Sync Refactor: Persistent Local Working Tree + GitHub Desktop UX

## Context

The git integration is painfully slow and has a broken conflict resolution flow. Every single git operation (fetch, status, commit, pull, push, diff, discard) does a full `aws s3 sync` of the entire `_repo/` (including `.git/`) down to a temp dir, runs the git command, syncs back up, and deletes the temp dir. This means even checking status takes 10-30 seconds.

The UI also presents separate Pull and Push buttons with no enforced ordering, leading to confusing states where you can pull with uncommitted changes, get conflicts, and have no clear path to resolution.

**Goal:** Make git operations feel instant by keeping a persistent local working tree, and simplify the UX to match GitHub Desktop's morphing button pattern.

---

## Design

### Operation Model

| Operation | S3 Sync Down | Local Git | S3 Sync Up | Entity Import |
|-----------|:---:|:---:|:---:|:---:|
| **Fetch** | Yes | `git fetch` | No | No |
| **Status** | No | `git status` | No | No |
| **Commit** | No | `git add + commit` | No | No |
| **Diff** | No | `git diff` | No | No |
| **Sync (Pull+Push)** | No | `git pull + push` | Yes | Yes |
| **Resolve (Complete Merge)** | No | resolve + `git add + commit` | No | No |
| **Abort Merge** | No | `git merge --abort` | No | No |
| **Discard** | No | `git checkout` | Yes | No |

### Morphing Button States

```
1. Has conflicts        → "Complete Merge" button (= git commit) + "Abort Merge" button
2. commitsBehind > 0    → "Pull origin (N)"   (calls sync = pull+push)
3. commitsAhead > 0     → "Push origin (N)"   (calls sync = pull+push)
4. Default              → "Fetch origin"       (calls fetch)
```

### Fetch Overwrites Local — By Design

`aws s3 sync` on fetch overwrites the local working tree with S3 state. This is correct because editor saves go to S3 via `RepoStorage`, so sync-down picks those up. The morphing button prevents dangerous fetch-after-commit: if you have unpushed commits (`commitsAhead > 0`), the button shows "Push" not "Fetch."

### Conflict Flow

1. User clicks "Pull origin" → `git pull` → conflicts detected
2. Sync pauses, returns conflict list to UI
3. UI shows all conflicts with Keep Ours / Keep Theirs per file, plus "Abort Merge" option
4. **Happy path:** User resolves all, clicks "Complete Merge" (= just `git add` + `git commit`, creating a merge commit since `MERGE_HEAD` exists). Merge commit becomes a local commit, button morphs to "Push origin (N)", user pushes when ready.
5. **Abort path:** User clicks "Abort Merge" → `git merge --abort` → returns to pre-pull state, button returns to "Pull origin (N)"

**"Complete Merge" is just a commit.** No push or entity import happens here — the merge commit is local. The user then pushes via the morphing button (which now shows "Push origin"), and S3 sync-up + entity import happen on push.

---

## Implementation Steps

### Step 1: Refactor `GitRepoManager` — persistent local dir

**File:** `api/src/services/git_repo_manager.py`

- Replace temp dir with persistent path (`/tmp/bifrost-git-work`)
- `work_dir` property returns the persistent path (creates if needed)
- `is_initialized` property checks for `.git/` existence
- `sync_down()` and `sync_up()` become standalone async methods (default to `work_dir`)
- Expose `lock()` as standalone context manager (Redis lock without sync)
- Keep `checkout()` and `checkout_readonly()` for backward compat in tests — internally use persistent dir

### Step 2: Add `SyncResult` model

**File:** `api/src/models/contracts/github.py`

Add `SyncResult` combining `PullResult` + `PushResult` + `entities_imported: int`.

Also add to `api/shared/models.py` if needed for OpenAPI type generation.

### Step 3: Refactor `GithubSyncService` desktop methods

**File:** `api/src/services/github_sync.py`

**`desktop_fetch()`** — Lock → S3 sync down → regenerate manifest → `git fetch` → compute ahead/behind. Returns `FetchResult`.

**`desktop_status()`** — No lock, no S3. Regenerate manifest → `git status`. Returns `WorkingTreeStatus`. Returns empty if not initialized.

**`desktop_commit(message)`** — Lock, no S3. Regenerate manifest → stage → preflight → commit. Returns `CommitResult`.

**`desktop_sync(job_id)`** — Lock. `git pull` (stash uncommitted, merge, pop stash). If conflicts: return early with conflict list. If clean: `git push` → S3 sync up → entity import. This is the only place entity import + S3 sync-up happen. Returns `SyncResult`.

**`desktop_resolve(resolutions)`** — Lock. Resolve conflict files → `git add` → `git commit` (merge commit). That's it — no push, no S3 sync, no entity import. The merge commit is local. Returns `ResolveResult`.

**`desktop_abort_merge()`** — Lock. `git merge --abort` → returns to pre-pull state. Returns success/failure.

**`desktop_diff(path)`** — No lock, no S3. Pure local diff. Returns `DiffResult`.

**`desktop_discard(paths)`** — Lock. `git checkout` files → S3 sync up (so API containers see the revert). Returns `DiscardResult`.

**Key change in `_do_pull()`:** Remove entity import calls (`_import_all_entities`, `_delete_removed_entities`, `_update_file_index`). These move to `desktop_sync()` after push succeeds.

### Step 4: Update scheduler dispatch

**File:** `api/src/scheduler/main.py`

- Add `git_sync` operation type → calls `desktop_sync()`
- `git_fetch` → calls `desktop_fetch()` + `desktop_status()` (both fast now, return combined result)
- `git_status` → calls `desktop_status()` (instant, no S3)
- `git_commit` → calls `desktop_commit()` (instant, no S3)
- Remove `git_pull` and `git_push` as separate operation types
- Remove `git_sync_preview` and `git_sync_execute` (replaced by `git_fetch` + `git_sync`)

### Step 5: Update router/handlers

**File:** `api/src/routers/github.py`

- `POST /api/github/sync` → queues `git_sync` (replaces old sync_preview/sync_execute)
- `POST /api/github/abort-merge` → queues `git_abort_merge` (new)
- Remove/deprecate `POST /api/github/pull` and `POST /api/github/push`
- Keep `POST /api/github/fetch`, `/commit`, `/changes`, `/resolve`, `/diff`, `/discard`

### Step 6: Frontend — morphing button + simplified hooks

**File:** `client/src/hooks/useGitHub.ts`
- Add `useSync()` hook — `POST /api/github/sync`
- Add `useAbortMerge()` hook — `POST /api/github/abort-merge`
- Deprecate `usePull()` and `usePush()`

**File:** `client/src/components/editor/SourceControlPanel.tsx`
- Replace separate Pull/Push buttons with single morphing button in header
- Button state logic: conflicts → behind → ahead → default fetch
- Add `handleSync()` handler (replaces `handlePull` + `handlePush`)
- Remove Pull/Push buttons from ChangesSection
- Keep commit section as-is (textarea + commit button)
- Update loading states: remove "pulling"/"pushing", add "syncing"

### Step 7: Update tests

- Update `api/tests/e2e/platform/test_git_sync_local.py` for new sync flow
- Update any unit tests referencing removed `desktop_pull`/`desktop_push` methods
- Add test: fetch populates persistent dir, subsequent status is instant (no S3 call)
- Add test: sync does pull+push+s3 sync up in one operation
- Add test: conflicts pause sync, resolve completes it

---

## Critical Files

| File | Change |
|------|--------|
| `api/src/services/git_repo_manager.py` | Persistent dir, standalone sync/lock methods |
| `api/src/services/github_sync.py` | Refactor all desktop_* methods, move entity import |
| `api/src/models/contracts/github.py` | Add SyncResult model |
| `api/src/scheduler/main.py` | New operation dispatch |
| `api/src/routers/github.py` | New sync endpoint, deprecate pull/push |
| `client/src/hooks/useGitHub.ts` | Add useSync hook |
| `client/src/components/editor/SourceControlPanel.tsx` | Morphing button UI |
| `api/shared/models.py` | SyncResult if needed for OpenAPI |

## Existing code to reuse

- `_do_fetch()`, `_do_status()`, `_do_commit()`, `_do_pull()`, `_do_push()`, `_do_resolve()` — all internal helpers already accept `work_dir` and `repo` params, well factored
- `checkout_readonly()` — already exists but unused for fetch, pattern to reference
- `_regenerate_manifest_to_dir()` — keep using for commit and status
- `_import_all_entities()`, `_delete_removed_entities()`, `_update_file_index()`, `_sync_app_previews()` — move call site but reuse as-is
- `runGitOp()` frontend helper — reuse for the new sync operation
- `publish_git_operation()` / `publish_git_op_completed()` pubsub helpers — reuse

## Verification

1. Start dev stack: `./debug.sh`
2. Configure GitHub integration in Settings
3. Test fetch: click "Fetch origin" — should sync down from S3 + show ahead/behind
4. Test status: should be instant after fetch (no loading delay)
5. Test commit: enter message, click commit — should be instant
6. Test sync: button should morph to "Push origin (1)" — click and verify push + S3 sync up
7. Test conflicts: create conflicting changes on remote, click "Pull origin" — should show conflicts, resolve, complete merge
8. Run `./test.sh tests/e2e/platform/test_git_sync_local.py`
9. Run `cd client && npm run tsc && npm run lint`
10. Run `cd api && pyright && ruff check .`
