# Finalize Workspace Redesign Branch

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Commit the unstaged workspace redesign changes, verify tests pass, and create a PR to merge `feat/workspace-redesign` into `main`.

**Context:** This branch replaces `workflows.code` and `code_hash` columns with `file_index` table + `_repo/` S3 storage. All code changes are already written — this plan is about verification and shipping.

**Branch:** `feat/workspace-redesign` (4 commits already, unstaged changes pending)

**Design doc:** `docs/plans/2026-02-10-workspace-redesign-implementation.md`

---

## Task 1: Run the full test suite

**Why:** Verify all unstaged changes work together before committing.

Run: `./test.sh`

If tests fail, debug and fix. The unstaged changes touch:
- Code loading (execution service, MCP tools, editor search, workflow orphan, workflow router — all read from `file_index` instead of `workflows.code`)
- Column removal (`workflows.code` and `code_hash` dropped from ORM, indexer no longer writes them)
- Prewarming removal (`warm_cache_from_db` gone from module_cache, `_sync_module_cache` gone from consumer, init_container simplified)
- Dual-write improvements (savepoint isolation in file_ops)
- S3 fallback improvements (module_cache_sync)

---

## Task 2: Commit the unstaged changes

**Why:** All the Phase 2 migration + column drops + prewarming removal are a single logical unit.

Stage all modified and new files:

```bash
git add \
  api/scripts/init_container.py \
  api/src/core/module_cache.py \
  api/src/core/module_cache_sync.py \
  api/src/jobs/consumers/workflow_execution.py \
  api/src/models/orm/workflows.py \
  api/src/routers/workflows.py \
  api/src/services/editor/search.py \
  api/src/services/execution/engine.py \
  api/src/services/execution/module_loader.py \
  api/src/services/execution/service.py \
  api/src/services/file_storage/entity_detector.py \
  api/src/services/file_storage/file_ops.py \
  api/src/services/file_storage/indexers/workflow.py \
  api/src/services/mcp_server/tools/code_editor.py \
  api/src/services/workflow_orphan.py \
  api/tests/e2e/api/test_db_first_storage.py \
  api/tests/integration/platform/test_virtual_import_integration.py \
  api/tests/unit/jobs/consumers/test_workflow_execution_session.py \
  api/tests/unit/services/execution/test_service.py \
  api/tests/unit/services/mcp_server/test_code_editor_tools.py \
  api/tests/unit/services/test_workflow_orphan.py \
  api/tests/unit/test_execution_pinning.py \
  api/tests/unit/test_repo_storage.py \
  api/tests/unit/test_virtual_import_s3_fallback.py \
  api/alembic/versions/20260210_drop_workflow_code_columns.py \
  docs/plans/2026-02-10-workspace-redesign-implementation.md
```

Commit message:

```
feat: drop workflows.code column, remove prewarming, migrate all readers to file_index

- Drop workflows.code and code_hash columns (migration 20260210_drop_code)
- Migrate all code readers to file_index: execution service, MCP tools,
  editor search, workflow orphan service, workflow router
- Remove warm_cache_from_db and _sync_module_cache — virtual importer
  is self-sufficient with Redis→S3 fallback
- Simplify init_container to 2 steps (migrations + requirements cache)
- Improve dual-write with savepoint isolation
- Enhance S3 fallback in module_cache_sync
```

---

## Task 3: Run the full test suite again post-commit

**Why:** Sanity check after commit. Same command: `./test.sh`

---

## Task 4: Create a PR

Create a pull request from `feat/workspace-redesign` to `main`.

The PR should summarize all 5 commits on the branch:
1. Phase 1 infrastructure (file_index table, manifest parser, reserved prefixes)
2. Phase 1 services (manifest generator, entity serializers)
3. Phase 1 storage (RepoStorage, FileIndexService, S3 fallback, reconciler)
4. Phase 2 migration (dual-write, sync lock)
5. Phase 3 cleanup (drop columns, remove prewarming, migrate readers)

Key points for the PR description:
- `workflows.code` and `code_hash` columns are dropped — code lives in `file_index` table and `_repo/` S3
- Workers load code via Redis→S3 fallback (no prewarming needed)
- All code readers (execution, MCP tools, search, orphan service) migrated to `file_index`
- `workspace_files` table and `portable_ref` column are NOT removed (deferred to follow-up)
- Git sync (`github_sync.py`) is unaffected — it reads via FileStorageService which already uses `file_index`
