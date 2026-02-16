# Git Sync Manifest Regeneration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Regenerate `.bifrost/*.yaml` manifest files from the DB before every git operation, so the working tree always reflects the current platform state before git compares, merges, or commits.

**Architecture:** Add `_regenerate_manifest_to_dir` as the first step of `desktop_fetch`, `desktop_status`, and `desktop_pull`. Change `sync_execute` order from pull→commit→push to commit→pull→push so local state is committed before merging remote.

**Tech Stack:** Python (FastAPI), GitPython, SQLAlchemy, pytest

---

### Task 1: Add manifest regeneration to `desktop_fetch`

**Files:**
- Modify: `api/src/services/github_sync.py:136-176` (`desktop_fetch`)

**Step 1: Add regeneration call**

In `desktop_fetch`, add the regeneration call immediately after opening the repo, before fetching:

```python
async def desktop_fetch(self) -> "FetchResult":
    """Git fetch origin. Compute ahead/behind counts."""
    from src.models.contracts.github import FetchResult

    try:
        async with self.repo_manager.checkout() as work_dir:
            repo = self._open_or_init(work_dir)

            # Regenerate manifest from DB so working tree reflects current platform state
            await self._regenerate_manifest_to_dir(self.db, work_dir)

            # Fetch remote
            remote_exists = True
            # ... rest unchanged
```

**Step 2: Run existing tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All existing tests PASS (regeneration is additive, doesn't break anything)

**Step 3: Commit**

```bash
git add api/src/services/github_sync.py
git commit -m "fix(sync): regenerate manifest before git fetch"
```

---

### Task 2: Add manifest regeneration to `desktop_status`

**Files:**
- Modify: `api/src/services/github_sync.py:178-274` (`desktop_status`)

**Step 1: Add regeneration call**

In `desktop_status`, add after opening repo but before conflict check:

```python
async def desktop_status(self) -> "WorkingTreeStatus":
    """Get working tree status (uncommitted changes)."""
    from src.models.contracts.github import ChangedFile, MergeConflict, WorkingTreeStatus

    try:
        async with self.repo_manager.checkout() as work_dir:
            repo = self._open_or_init(work_dir)

            # Regenerate manifest from DB so working tree reflects current platform state
            await self._regenerate_manifest_to_dir(self.db, work_dir)

            # Check for unresolved conflicts BEFORE git add
            # ... rest unchanged
```

**Step 2: Run existing tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add api/src/services/github_sync.py
git commit -m "fix(sync): regenerate manifest before desktop_status"
```

---

### Task 3: Add manifest regeneration to `desktop_pull`

**Files:**
- Modify: `api/src/services/github_sync.py:399-546` (`desktop_pull`)

**Step 1: Add regeneration call**

In `desktop_pull`, add after opening repo but before fetch/stash:

```python
async def desktop_pull(self) -> "PullResult":
    """Pull remote changes. On success, import entities. On conflict, return conflicts."""
    from src.models.contracts.github import MergeConflict, PullResult

    try:
        async with self.repo_manager.checkout() as work_dir:
            repo = self._open_or_init(work_dir)

            # Regenerate manifest from DB so working tree reflects current platform state.
            # This ensures git stash captures the real local state before merging remote.
            await self._regenerate_manifest_to_dir(self.db, work_dir)

            # Fetch first
            remote_exists = True
            # ... rest unchanged
```

**Step 2: Run existing tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add api/src/services/github_sync.py
git commit -m "fix(sync): regenerate manifest before desktop_pull"
```

---

### Task 4: Change `sync_execute` order to commit → pull → push

**Files:**
- Modify: `api/src/scheduler/main.py:470-523` (`git_sync_execute` handler)

**Step 1: Reorder to commit → pull → push**

Replace the `git_sync_execute` block:

```python
elif op_type == "git_sync_execute":
    # Full sync: commit + pull + push
    # Commit first to lock local platform state into git history,
    # then pull merges remote changes via three-way merge.
    conflict_resolutions = data.get("conflict_resolutions", {})

    # Step 1: Commit local changes (regenerates manifest from DB)
    commit_result = await sync_service.desktop_commit("Sync from Bifrost")

    # Step 2: Pull remote changes
    pull_result = await sync_service.desktop_pull()
    if not pull_result.success:
        if pull_result.conflicts and conflict_resolutions:
            resolve_result = await sync_service.desktop_resolve(conflict_resolutions)
            if not resolve_result.success:
                await publish_git_op_completed(
                    job_id, status="conflict", result_type="sync_execute",
                    error=resolve_result.error,
                )
                continue
            # Resolution succeeded — fall through to push
        elif pull_result.conflicts:
            await publish_git_op_completed(
                job_id, status="conflict", result_type="sync_execute",
                error="Merge conflicts detected",
            )
            continue
        else:
            await publish_git_op_completed(
                job_id, status="failed", result_type="sync_execute",
                error=pull_result.error,
            )
            continue

    # Step 3: Push
    push_result = await sync_service.desktop_push()
    await publish_git_op_completed(
        job_id,
        status="success" if push_result.success else "failed",
        result_type="sync_execute",
        pulled=pull_result.pulled if pull_result.success else 0,
        pushed=(commit_result.files_committed if commit_result and commit_result.success else 0),
        commit_sha=push_result.commit_sha,
        error=push_result.error if not push_result.success else None,
    )
```

Note: Check the exact control flow structure (the scheduler uses `if/elif` not loops, so use nested if/else rather than `continue` — adjust to match the surrounding code pattern).

**Step 2: Run existing tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add api/src/scheduler/main.py
git commit -m "fix(sync): reorder sync_execute to commit -> pull -> push"
```

---

### Task 5: Add E2E test for cross-instance manifest reconciliation

**Files:**
- Modify: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the test**

Add a new test class at the end of the file. This test simulates the scenario from our shell test: one instance deletes a config, another adds one, and the merge reconciles correctly.

```python
@pytest.mark.e2e
@pytest.mark.asyncio
class TestCrossInstanceManifestReconciliation:
    """Test that manifest regeneration + commit + pull correctly reconciles
    cross-instance changes to .bifrost/*.yaml files."""

    async def test_config_add_and_delete_merge(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
        tmp_path,
    ):
        """
        Instance A (working_clone) adds a config.
        Instance B (sync_service/prod) deletes a config.
        After sync, both changes should be reflected.
        """
        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration

        # --- Setup: Create initial state with an integration and 2 configs ---
        integ_id = uuid4()
        config_1_id = uuid4()
        config_2_id = uuid4()

        integ = Integration(id=integ_id, name="TestInteg", is_deleted=False)
        db_session.add(integ)
        cfg1 = Config(
            id=config_1_id, key="keep_this", value="yes",
            integration_id=integ_id, updated_by="git-sync",
        )
        cfg2 = Config(
            id=config_2_id, key="delete_this", value="remove_me",
            integration_id=integ_id, updated_by="git-sync",
        )
        db_session.add_all([cfg1, cfg2])

        # Also need a workflow so the manifest isn't empty
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id, name="Reconcile Test WF",
            function_name="reconcile_test_wf",
            path="workflows/git_sync_test_reconcile.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write workflow file + manifest to persistent dir
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_reconcile.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit + push initial state
        commit_result = await sync_service.desktop_commit("initial with configs")
        assert commit_result.success
        push_result = await sync_service.desktop_push()
        assert push_result.success

        # --- Instance A (working_clone): Pull, add config-3, push ---
        working_clone.remotes.origin.pull("main")
        clone_dir = Path(working_clone.working_dir)

        # Read current configs.yaml and add a new config
        configs_yaml = yaml.safe_load(
            (clone_dir / ".bifrost" / "configs.yaml").read_text()
        )
        config_3_id = str(uuid4())
        configs_yaml["configs"]["new_from_dev"] = {
            "id": config_3_id,
            "key": "new_from_dev",
            "value": "hello_from_dev",
            "integration_id": str(integ_id),
        }
        (clone_dir / ".bifrost" / "configs.yaml").write_text(
            yaml.dump(configs_yaml, default_flow_style=False, sort_keys=False)
        )
        working_clone.index.add([".bifrost/configs.yaml"])
        working_clone.index.commit("Dev: add new_from_dev config")
        working_clone.remotes.origin.push("main")

        # --- Instance B (prod/sync_service): Delete config-2 from DB ---
        await db_session.execute(
            delete(Config).where(Config.id == config_2_id)
        )
        await db_session.commit()

        # --- Sync: commit (regenerates manifest without config-2) then pull ---
        commit_result = await sync_service.desktop_commit("Prod: delete config-2")
        assert commit_result.success

        pull_result = await sync_service.desktop_pull()
        assert pull_result.success, f"Pull failed: {pull_result.error}"

        # --- Verify: manifest should have config-1 and new_from_dev, NOT config-2 ---
        persistent_dir = sync_service._persistent_dir
        final_manifest = read_manifest_from_dir(persistent_dir / ".bifrost")

        config_keys = set(final_manifest.configs.keys())
        assert "keep_this" in config_keys, f"config-1 should be preserved, got: {config_keys}"
        assert "new_from_dev" in config_keys, f"dev's new config should be merged in, got: {config_keys}"
        assert "delete_this" not in config_keys, f"config-2 should be deleted, got: {config_keys}"

    async def test_empty_repo_pull_imports_remote_state(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
        tmp_path,
    ):
        """
        Instance A pushes configs/integrations to remote.
        Instance B has empty _repo (post-upgrade), pulls.
        All remote entities should be imported, not deleted.
        """
        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration

        # --- Instance A: Push state with integration + config ---
        clone_dir = Path(working_clone.working_dir)
        (clone_dir / ".bifrost").mkdir(exist_ok=True)

        integ_id = str(uuid4())
        config_id = str(uuid4())

        (clone_dir / ".bifrost" / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "TestRemoteInteg": {
                    "id": integ_id,
                    "entity_id": "tenant_id",
                }
            }
        }, default_flow_style=False))

        (clone_dir / ".bifrost" / "configs.yaml").write_text(yaml.dump({
            "configs": {
                "remote_config": {
                    "id": config_id,
                    "key": "remote_config",
                    "value": "from_remote",
                    "integration_id": integ_id,
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/integrations.yaml", ".bifrost/configs.yaml"])
        working_clone.index.commit("Remote: add integration and config")
        working_clone.remotes.origin.push("main")

        # --- Instance B: Pull from empty _repo ---
        # The sync_service starts with an empty persistent dir (no .bifrost/ files).
        # desktop_pull should regenerate (producing empty manifest), then merge remote.
        pull_result = await sync_service.desktop_pull()
        assert pull_result.success, f"Pull failed: {pull_result.error}"

        # Verify the remote entities were imported into DB
        integ_result = await db_session.execute(
            select(Integration).where(Integration.name == "TestRemoteInteg")
        )
        imported_integ = integ_result.scalar_one_or_none()
        assert imported_integ is not None, "Integration should be imported from remote"

        config_result = await db_session.execute(
            select(Config).where(Config.key == "remote_config")
        )
        imported_config = config_result.scalar_one_or_none()
        assert imported_config is not None, "Config should be imported from remote"
```

**Step 2: Run the new tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestCrossInstanceManifestReconciliation -v`
Expected: Both tests PASS

**Step 3: Run all git sync tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add api/tests/e2e/platform/test_git_sync_local.py
git commit -m "test: add cross-instance manifest reconciliation E2E tests"
```

---

### Task 6: Update test helper to not clear non-file entities

**Files:**
- Modify: `api/tests/e2e/platform/test_git_sync_local.py:130-159` (`write_manifest_to_repo`)

The existing `write_manifest_to_repo` helper clears integrations, configs, tables, knowledge, and events (lines 154-158). This was done to prevent leaks from other tests, but now that we're testing these entity types, the helper needs to preserve them when they exist.

**Step 1: Update the helper**

Remove the blanket clearing and instead filter to only entities relevant to the test's persistent dir:

```python
async def write_manifest_to_repo(db_session: AsyncSession, persistent_dir: Path) -> None:
    """Generate manifest from DB and write to persistent dir, simulating RepoSyncWriter.regenerate_manifest()."""
    from src.services.manifest_generator import generate_manifest
    from src.services.manifest import write_manifest_to_dir
    manifest = await generate_manifest(db_session)
    # Filter out file-based entities whose files don't exist in the persistent dir
    manifest.workflows = {
        k: v for k, v in manifest.workflows.items()
        if (persistent_dir / v.path).exists()
    }
    manifest.forms = {
        k: v for k, v in manifest.forms.items()
        if (persistent_dir / v.path).exists()
    }
    manifest.agents = {
        k: v for k, v in manifest.agents.items()
        if (persistent_dir / v.path).exists()
    }
    manifest.apps = {
        k: v for k, v in manifest.apps.items()
        if (persistent_dir / v.path).exists()
    }
    # Non-file entities (integrations, configs, etc.) are included as-is from DB
    write_manifest_to_dir(manifest, persistent_dir / ".bifrost")
```

Note: This may cause existing tests to include leaked integrations/configs from the DB. If that happens, the cleanup fixture already handles it. Run all tests to verify.

**Step 2: Run all tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_git_sync_local.py
git commit -m "fix(test): preserve non-file entities in write_manifest_to_repo helper"
```

---

### Task 7: Final verification

**Step 1: Run full E2E test suite**

Run: `./test.sh tests/e2e/ -v`
Expected: All PASS

**Step 2: Run unit tests**

Run: `./test.sh tests/unit/ -v`
Expected: All PASS

**Step 3: Run pyright and ruff**

Run: `cd api && pyright && ruff check .`
Expected: Clean

**Step 4: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix: address review feedback from manifest regeneration changes"
```
