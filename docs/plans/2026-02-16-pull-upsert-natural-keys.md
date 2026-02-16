# Pull Upsert Natural Keys Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all `_import_*` methods in `github_sync.py` to use natural unique keys instead of `id` for conflict resolution, so that git pull imports never fail with IntegrityError when the manifest ID differs from the existing DB row.

**Architecture:** For entities with simple unique constraints (workflow, integration, event subscription), change `on_conflict_do_update` to target the natural key and include `id` in the update set. For entities with functional/partial unique indexes (config, app), use a two-step SELECT-then-INSERT/UPDATE pattern since PostgreSQL doesn't support ON CONFLICT with those index types.

**Tech Stack:** Python (FastAPI), SQLAlchemy, PostgreSQL, pytest

---

### Task 1: Fix `_import_workflow` to use natural key

**Files:**
- Modify: `api/src/services/github_sync.py:1110-1137` (`_import_workflow`)
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing test**

Add to `test_git_sync_local.py` at the end of the file. This test creates a workflow in the DB with one ID, then imports via the pull path with the same `(path, function_name)` but a different ID. Currently this triggers the IntegrityError from the bug report.

```python
@pytest.mark.e2e
@pytest.mark.asyncio
class TestPullUpsertNaturalKeys:
    """Test that _import_* methods handle ID mismatches by matching on natural keys."""

    async def test_workflow_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When a workflow exists with (path, function_name) but a different ID,
        the import should update the existing row's ID to match the manifest."""
        # Create a workflow in the DB with ID_A
        id_a = uuid4()
        wf = Workflow(
            id=id_a,
            name="Original",
            function_name="natural_key_test_wf",
            path="workflows/natural_key_test.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write the workflow file to the persistent dir
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/natural_key_test.py",
            SAMPLE_WORKFLOW_PY,
        )

        # Push from "another instance" (working_clone) with same (path, function_name) but ID_B
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        # Write workflow file
        wf_dir = clone_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "natural_key_test.py").write_text(SAMPLE_WORKFLOW_PY)

        # Write manifest with ID_B for the same path+function_name
        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "workflows": {
                "natural_key_test_wf": {
                    "id": str(id_b),
                    "path": "workflows/natural_key_test.py",
                    "function_name": "natural_key_test_wf",
                    "type": "workflow",
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            "workflows/natural_key_test.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("Add workflow with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — this should NOT raise IntegrityError
        pull_result = await sync_service.desktop_pull()
        assert pull_result.success, f"Pull failed: {pull_result.error}"

        # Verify: only one workflow row exists with the manifest's ID (id_b)
        result = await db_session.execute(
            select(Workflow).where(
                Workflow.path == "workflows/natural_key_test.py",
                Workflow.function_name == "natural_key_test_wf",
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 workflow row, got {len(rows)}"
        assert rows[0].id == id_b, f"Expected manifest ID {id_b}, got {rows[0].id}"
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_workflow_import_with_different_id -v`
Expected: FAIL with `IntegrityError: duplicate key value violates unique constraint "workflows_path_function_key"`

**Step 3: Fix `_import_workflow`**

In `api/src/services/github_sync.py`, change `_import_workflow` to use the natural key constraint:

```python
async def _import_workflow(self, manifest_name: str, mwf, _content: bytes) -> None:
    """Import a workflow from repo into the DB."""
    from uuid import UUID

    from sqlalchemy.dialects.postgresql import insert

    from src.models.orm.workflows import Workflow

    stmt = insert(Workflow).values(
        id=UUID(mwf.id),
        name=manifest_name,
        function_name=mwf.function_name,
        path=mwf.path,
        type=getattr(mwf, "type", "workflow"),
        is_active=True,
        organization_id=UUID(mwf.organization_id) if mwf.organization_id else None,
    ).on_conflict_do_update(
        constraint="workflows_path_function_key",
        set_={
            "id": UUID(mwf.id),
            "name": manifest_name,
            "function_name": mwf.function_name,
            "path": mwf.path,
            "type": getattr(mwf, "type", "workflow"),
            "is_active": True,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    await self.db.execute(stmt)
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_workflow_import_with_different_id -v`
Expected: PASS

**Step 5: Run all git sync tests to check for regressions**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "fix(sync): workflow import uses natural key (path, function_name) for upsert"
```

---

### Task 2: Fix `_import_integration` to use natural key

**Files:**
- Modify: `api/src/services/github_sync.py:1298-1340` (`_import_integration`)
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing test**

Add to the `TestPullUpsertNaturalKeys` class:

```python
    async def test_integration_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When an integration exists with same name but different ID,
        the import should update the existing row's ID to match the manifest."""
        from src.models.orm.integrations import Integration

        # Create integration in DB with ID_A
        id_a = uuid4()
        integ = Integration(id=id_a, name="NaturalKeyTestInteg", is_deleted=False)
        db_session.add(integ)
        await db_session.commit()

        # Push from "another instance" with same name but ID_B
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "integrations": {
                "NaturalKeyTestInteg": {
                    "id": str(id_b),
                    "entity_id": "tenant_id",
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([".bifrost/metadata.yaml"])
        working_clone.index.commit("Add integration with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — should NOT raise IntegrityError
        pull_result = await sync_service.desktop_pull()
        assert pull_result.success, f"Pull failed: {pull_result.error}"

        # Verify: one integration with manifest ID
        result = await db_session.execute(
            select(Integration).where(Integration.name == "NaturalKeyTestInteg")
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 integration, got {len(rows)}"
        assert rows[0].id == id_b, f"Expected manifest ID {id_b}, got {rows[0].id}"
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_integration_import_with_different_id -v`
Expected: FAIL with IntegrityError on `integrations_name_key` (or similar unique violation)

**Step 3: Fix `_import_integration`**

In `_import_integration`, change the Integration upsert from `index_elements=["id"]` to `index_elements=["name"]` and add `"id"` to the update set:

```python
        stmt = insert(Integration).values(
            id=integ_id,
            name=integ_name,
            entity_id=minteg.entity_id,
            entity_id_name=minteg.entity_id_name,
            default_entity_id=minteg.default_entity_id,
            list_entities_data_provider_id=(
                UUID(minteg.list_entities_data_provider_id)
                if minteg.list_entities_data_provider_id else None
            ),
            is_deleted=False,
        ).on_conflict_do_update(
            index_elements=["name"],
            set_={
                "id": integ_id,
                "name": integ_name,
                "entity_id": minteg.entity_id,
                "entity_id_name": minteg.entity_id_name,
                "default_entity_id": minteg.default_entity_id,
                "list_entities_data_provider_id": (
                    UUID(minteg.list_entities_data_provider_id)
                    if minteg.list_entities_data_provider_id else None
                ),
                "is_deleted": False,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await self.db.execute(stmt)
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_integration_import_with_different_id -v`
Expected: PASS

**Step 5: Run all git sync tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "fix(sync): integration import uses name as natural key for upsert"
```

---

### Task 3: Fix `_import_app` to use two-step upsert

**Files:**
- Modify: `api/src/services/github_sync.py:1263-1296` (`_import_app`)
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing test**

Add to the `TestPullUpsertNaturalKeys` class:

```python
    async def test_app_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When an app exists with same slug but different ID,
        the import should update the existing row's ID to match the manifest."""
        from src.models.orm.applications import Application

        # Create app in DB with ID_A
        id_a = uuid4()
        app = Application(
            id=id_a, name="Natural Key App", slug="natural-key-app",
            organization_id=None,
        )
        db_session.add(app)
        await db_session.commit()

        # Push from "another instance" with same slug but ID_B
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        # Write app.yaml file
        app_dir = clone_dir / "apps" / "natural-key-app"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "app.yaml").write_text(yaml.dump({
            "name": "Natural Key App Updated",
            "description": "Updated from remote",
        }, default_flow_style=False))

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "apps": {
                "natural-key-app": {
                    "id": str(id_b),
                    "path": "apps/natural-key-app/app.yaml",
                    "slug": "natural-key-app",
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            "apps/natural-key-app/app.yaml",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("Add app with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — should NOT raise IntegrityError
        pull_result = await sync_service.desktop_pull()
        assert pull_result.success, f"Pull failed: {pull_result.error}"

        # Verify: one app with manifest ID
        result = await db_session.execute(
            select(Application).where(Application.slug == "natural-key-app")
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 app, got {len(rows)}"
        assert rows[0].id == id_b, f"Expected manifest ID {id_b}, got {rows[0].id}"
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_app_import_with_different_id -v`
Expected: FAIL with IntegrityError on slug unique index

**Step 3: Fix `_import_app`**

Replace the current ON CONFLICT upsert with a two-step SELECT + INSERT/UPDATE:

```python
    async def _import_app(self, mapp, content: bytes) -> None:
        """Import an app from repo into the DB (metadata only)."""
        from uuid import UUID

        from sqlalchemy import update
        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.applications import Application

        data = yaml.safe_load(content.decode("utf-8"))
        if not data:
            return

        # Slug from manifest entry, or derive from path (e.g. "apps/tickbox-grc/app.yaml" -> "tickbox-grc")
        slug = mapp.slug or (mapp.path.split("/")[1] if mapp.path else None)
        if not slug:
            logger.warning(f"App {mapp.id} has no slug or path, skipping")
            return

        app_id = UUID(mapp.id)
        org_id = UUID(mapp.organization_id) if mapp.organization_id else None

        # Two-step: check for existing app by natural key (org_id, slug)
        existing = await self.db.execute(
            select(Application.id).where(
                Application.slug == slug,
                Application.organization_id == org_id if org_id else Application.organization_id.is_(None),
            )
        )
        existing_id = existing.scalar_one_or_none()

        if existing_id is not None:
            # Update existing row (including ID if it changed)
            stmt = (
                update(Application)
                .where(Application.id == existing_id)
                .values(
                    id=app_id,
                    name=data.get("name", ""),
                    description=data.get("description"),
                    slug=slug,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await self.db.execute(stmt)
        else:
            # Insert new row
            stmt = insert(Application).values(
                id=app_id,
                name=data.get("name", ""),
                description=data.get("description"),
                slug=slug,
                organization_id=org_id,
            ).on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": data.get("name", ""),
                    "description": data.get("description"),
                    "slug": slug,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await self.db.execute(stmt)
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_app_import_with_different_id -v`
Expected: PASS

**Step 5: Run all git sync tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "fix(sync): app import uses two-step upsert on (org_id, slug) natural key"
```

---

### Task 4: Fix `_import_config` to use two-step upsert

**Files:**
- Modify: `api/src/services/github_sync.py:1406-1453` (`_import_config`)
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing test**

Add to the `TestPullUpsertNaturalKeys` class:

```python
    async def test_config_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When a config exists with same (integration_id, org_id, key) but different ID,
        the import should update the existing row's ID to match the manifest."""
        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration

        # Create integration first (needed for FK)
        integ_id = uuid4()
        integ = Integration(id=integ_id, name="ConfigTestInteg", is_deleted=False)
        db_session.add(integ)

        # Create config in DB with ID_A
        id_a = uuid4()
        cfg = Config(
            id=id_a, key="natural_key_cfg", value={"test": True},
            integration_id=integ_id, updated_by="test",
        )
        db_session.add(cfg)
        await db_session.commit()

        # Push from "another instance" with same natural key but ID_B
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "integrations": {
                "ConfigTestInteg": {
                    "id": str(integ_id),
                    "entity_id": "tenant_id",
                }
            },
            "configs": {
                "natural_key_cfg": {
                    "id": str(id_b),
                    "key": "natural_key_cfg",
                    "integration_id": str(integ_id),
                    "config_type": "string",
                    "value": {"test": True, "updated": True},
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([".bifrost/metadata.yaml"])
        working_clone.index.commit("Add config with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — should NOT raise IntegrityError
        pull_result = await sync_service.desktop_pull()
        assert pull_result.success, f"Pull failed: {pull_result.error}"

        # Verify: one config row with manifest ID
        result = await db_session.execute(
            select(Config).where(
                Config.key == "natural_key_cfg",
                Config.integration_id == integ_id,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 config, got {len(rows)}"
        assert rows[0].id == id_b, f"Expected manifest ID {id_b}, got {rows[0].id}"
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_config_import_with_different_id -v`
Expected: FAIL with IntegrityError on `ix_configs_integration_org_key`

**Step 3: Fix `_import_config`**

Replace the current ON CONFLICT upsert with a two-step SELECT + INSERT/UPDATE. The config unique index uses COALESCE on nullable columns, so ON CONFLICT can't target it. Preserve the existing secret-skip logic.

```python
    async def _import_config(self, mcfg) -> None:
        """Import a config entry from manifest into the DB.

        Skips writing value if type=SECRET and existing value is non-null
        (don't overwrite manually-entered secrets).
        """
        from uuid import UUID

        from sqlalchemy import update
        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.config import Config

        cfg_id = UUID(mcfg.id)
        integ_id = UUID(mcfg.integration_id) if mcfg.integration_id else None
        org_id = UUID(mcfg.organization_id) if mcfg.organization_id else None

        # Check for existing config by natural key (integration_id, org_id, key)
        existing_query = select(Config.id, Config.value).where(Config.key == mcfg.key)
        if integ_id:
            existing_query = existing_query.where(Config.integration_id == integ_id)
        else:
            existing_query = existing_query.where(Config.integration_id.is_(None))
        if org_id:
            existing_query = existing_query.where(Config.organization_id == org_id)
        else:
            existing_query = existing_query.where(Config.organization_id.is_(None))

        result = await self.db.execute(existing_query)
        existing = result.first()

        if existing is not None:
            existing_id, existing_value = existing

            # Secret with existing value — don't overwrite
            if mcfg.config_type == "secret" and existing_value is not None:
                return

            # Update existing row (including ID if it changed)
            update_values: dict = {
                "id": cfg_id,
                "key": mcfg.key,
                "config_type": mcfg.config_type,
                "description": mcfg.description,
                "integration_id": integ_id,
                "organization_id": org_id,
                "updated_by": "git-sync",
                "updated_at": datetime.now(timezone.utc),
            }
            if mcfg.config_type != "secret":
                update_values["value"] = mcfg.value if mcfg.value is not None else {}

            stmt = (
                update(Config)
                .where(Config.id == existing_id)
                .values(**update_values)
            )
            await self.db.execute(stmt)
        else:
            # Insert new row
            stmt = insert(Config).values(
                id=cfg_id,
                key=mcfg.key,
                config_type=mcfg.config_type,
                description=mcfg.description,
                integration_id=integ_id,
                organization_id=org_id,
                value=mcfg.value if mcfg.value is not None else {},
                updated_by="git-sync",
            ).on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "key": mcfg.key,
                    "config_type": mcfg.config_type,
                    "description": mcfg.description,
                    "integration_id": integ_id,
                    "organization_id": org_id,
                    "updated_by": "git-sync",
                    "updated_at": datetime.now(timezone.utc),
                    **({"value": mcfg.value if mcfg.value is not None else {}} if mcfg.config_type != "secret" else {}),
                },
            )
            await self.db.execute(stmt)
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_config_import_with_different_id -v`
Expected: PASS

**Step 5: Run all git sync tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "fix(sync): config import uses two-step upsert on natural key"
```

---

### Task 5: Fix EventSubscription upsert to use natural key

**Files:**
- Modify: `api/src/services/github_sync.py:1566-1588` (subscription loop in `_import_event_source`)
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing test**

Add to the `TestPullUpsertNaturalKeys` class:

```python
    async def test_event_subscription_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When an event subscription exists with same (event_source_id, workflow_id)
        but different ID, the import should update the existing row."""
        from src.models.orm.events import EventSource, EventSubscription
        from src.models.orm.workflows import Workflow

        # Create a workflow (needed for FK)
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id, name="SubTestWF", function_name="sub_test_wf",
            path="workflows/sub_test.py", is_active=True,
        )
        db_session.add(wf)

        # Create event source
        es_id = uuid4()
        es = EventSource(
            id=es_id, name="SubTestSource", source_type="schedule",
            is_active=True, created_by="test",
        )
        db_session.add(es)

        # Create subscription with ID_A
        sub_id_a = uuid4()
        sub = EventSubscription(
            id=sub_id_a, event_source_id=es_id, workflow_id=wf_id,
            is_active=True, created_by="test",
        )
        db_session.add(sub)
        await db_session.commit()

        # Push from "another instance" with same (event_source_id, workflow_id) but ID_B
        sub_id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Need workflow file + manifest for the pull to work
        wf_dir = clone_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "sub_test.py").write_text(SAMPLE_WORKFLOW_PY)

        # Write persistent workflow file too
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/sub_test.py",
            SAMPLE_WORKFLOW_PY,
        )

        manifest_content = yaml.dump({
            "workflows": {
                "sub_test_wf": {
                    "id": str(wf_id),
                    "path": "workflows/sub_test.py",
                    "function_name": "sub_test_wf",
                    "type": "workflow",
                }
            },
            "events": {
                str(es_id): {
                    "id": str(es_id),
                    "source_type": "schedule",
                    "is_active": True,
                    "cron_expression": "0 * * * *",
                    "subscriptions": [
                        {
                            "id": str(sub_id_b),
                            "workflow_id": str(wf_id),
                            "is_active": True,
                        }
                    ],
                }
            },
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            "workflows/sub_test.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("Add event subscription with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — should NOT raise IntegrityError
        pull_result = await sync_service.desktop_pull()
        assert pull_result.success, f"Pull failed: {pull_result.error}"

        # Verify: one subscription with manifest ID
        result = await db_session.execute(
            select(EventSubscription).where(
                EventSubscription.event_source_id == es_id,
                EventSubscription.workflow_id == wf_id,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 subscription, got {len(rows)}"
        assert rows[0].id == sub_id_b, f"Expected manifest ID {sub_id_b}, got {rows[0].id}"
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_event_subscription_import_with_different_id -v`
Expected: FAIL with IntegrityError on `ix_event_subscriptions_unique_source_workflow`

**Step 3: Fix EventSubscription upsert in `_import_event_source`**

In the subscription loop inside `_import_event_source`, change from `index_elements=["id"]` to `index_elements=["event_source_id", "workflow_id"]` and add `"id"` to the update set:

```python
        # Sync subscriptions: upsert each
        for msub in mes.subscriptions:
            sub_stmt = insert(EventSubscription).values(
                id=UUID(msub.id),
                event_source_id=es_id,
                workflow_id=UUID(msub.workflow_id),
                event_type=msub.event_type,
                filter_expression=msub.filter_expression,
                input_mapping=msub.input_mapping,
                is_active=msub.is_active,
                created_by="git-sync",
            ).on_conflict_do_update(
                index_elements=["event_source_id", "workflow_id"],
                set_={
                    "id": UUID(msub.id),
                    "workflow_id": UUID(msub.workflow_id),
                    "event_type": msub.event_type,
                    "filter_expression": msub.filter_expression,
                    "input_mapping": msub.input_mapping,
                    "is_active": msub.is_active,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await self.db.execute(sub_stmt)
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestPullUpsertNaturalKeys::test_event_subscription_import_with_different_id -v`
Expected: PASS

**Step 5: Run all git sync tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "fix(sync): event subscription import uses (source_id, workflow_id) natural key"
```

---

### Task 6: Final verification

**Step 1: Run full E2E test suite**

Run: `./test.sh tests/e2e/ -v`
Expected: All PASS

**Step 2: Run unit tests**

Run: `./test.sh tests/unit/ -v`
Expected: All PASS

**Step 3: Run pyright and ruff**

Run: `cd /home/jack/GitHub/bifrost/api && pyright && ruff check .`
Expected: Clean

**Step 4: Commit any fixups**

```bash
git add -A
git commit -m "fix: address review feedback from pull upsert natural key changes"
```
