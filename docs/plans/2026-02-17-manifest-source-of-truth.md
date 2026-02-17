# Manifest Source of Truth Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `.bifrost/` a complete declarative environment template — pull it and the DB matches.

**Architecture:** Add organization and role import as the first two steps in the import chain, fix the import dependency order (apps before tables), add role assignment and access_level sync to all entity imports, add `_resolve_workflow_ref` for robust cross-env workflow resolution, and add soft-delete for orgs/roles removed from manifest. Every import method gets the same ID-first-then-name upsert pattern.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (async), PostgreSQL, pytest

---

## Status

**Tasks 1–6: COMPLETED** (2026-02-17) — All implemented and tested. 64 tests pass (39 existing + 25 new). Ruff clean.

**Task 7 (follow-up): NOT STARTED** — Extract shared dependency knowledge between validation and import to prevent drift.

---

### Task 1: Add `_import_organization` method — COMPLETED

**Files:**
- Modify: `api/src/services/github_sync.py` (add method before `_import_workflow` at ~line 1121)
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing tests**

Add new test class after existing test classes (after `TestPullUpsertNaturalKeys`):

```python
class TestOrgImport:
    """Organization import from manifest — CREATE / UPDATE / RENAME / DEACTIVATE."""

    @pytest.mark.asyncio
    async def test_create_org(self, sync_service, working_clone, db_session):
        """Org in manifest, not in DB → created."""
        org_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)

        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "Acme Corp"},
        ]))
        # Write empty files for other entity types so manifest parses
        for f in ["roles.yaml", "workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n" if f != "roles.yaml" else "[]\n")

        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("add org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

        from src.models.orm.organizations import Organization
        row = await db_session.execute(
            select(Organization).where(Organization.name == "Acme Corp")
        )
        org = row.scalar_one()
        assert str(org.id) == org_id
        assert org.is_active is True

    @pytest.mark.asyncio
    async def test_create_multiple_orgs(self, sync_service, working_clone, db_session):
        """Two orgs in manifest → both created."""
        org1_id, org2_id = str(uuid4()), str(uuid4())
        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)

        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org1_id, "name": "Acme Corp"},
            {"id": org2_id, "name": "Globex Inc"},
        ]))
        for f in ["roles.yaml", "workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n" if f != "roles.yaml" else "[]\n")

        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("add orgs")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        from src.models.orm.organizations import Organization
        r1 = await db_session.execute(select(Organization).where(Organization.name == "Acme Corp"))
        r2 = await db_session.execute(select(Organization).where(Organization.name == "Globex Inc"))
        assert r1.scalar_one() is not None
        assert r2.scalar_one() is not None

    @pytest.mark.asyncio
    async def test_update_org_by_id_rename(self, sync_service, working_clone, db_session):
        """Org exists by ID, manifest has new name → name updated."""
        from src.models.orm.organizations import Organization
        org_id = uuid4()
        db_session.add(Organization(id=org_id, name="Old Name", created_by="test"))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": str(org_id), "name": "New Name"},
        ]))
        for f in ["roles.yaml", "workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n" if f != "roles.yaml" else "[]\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("rename org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        await db_session.refresh(db_session.get_one(Organization, org_id))
        org = await db_session.get_one(Organization, org_id)
        assert org.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_org_by_name_new_id(self, sync_service, working_clone, db_session):
        """Org exists by name with different UUID → ID updated (cross-env)."""
        from src.models.orm.organizations import Organization
        old_id = uuid4()
        new_id = uuid4()
        db_session.add(Organization(id=old_id, name="Acme Corp", created_by="test"))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": str(new_id), "name": "Acme Corp"},
        ]))
        for f in ["roles.yaml", "workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n" if f != "roles.yaml" else "[]\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("new id")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        row = await db_session.execute(
            select(Organization).where(Organization.name == "Acme Corp")
        )
        org = row.scalar_one()
        assert str(org.id) == str(new_id)

    @pytest.mark.asyncio
    async def test_update_org_preserves_domain_and_settings(self, sync_service, working_clone, db_session):
        """Org has domain/is_provider/settings in DB, pull doesn't overwrite."""
        from src.models.orm.organizations import Organization
        org_id = uuid4()
        db_session.add(Organization(
            id=org_id, name="Acme Corp", domain="acme.com",
            is_provider=True, settings={"theme": "dark"}, created_by="test",
        ))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": str(org_id), "name": "Acme Corp"},
        ]))
        for f in ["roles.yaml", "workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n" if f != "roles.yaml" else "[]\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("sync")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        org = await db_session.get_one(Organization, org_id)
        await db_session.refresh(org)
        assert org.domain == "acme.com"
        assert org.is_provider is True
        assert org.settings == {"theme": "dark"}

    @pytest.mark.asyncio
    async def test_org_idempotent(self, sync_service, working_clone, db_session):
        """Same manifest pulled twice → no errors, same state."""
        org_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "Acme Corp"},
        ]))
        for f in ["roles.yaml", "workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n" if f != "roles.yaml" else "[]\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("org")
        working_clone.remotes.origin.push()

        result1 = await sync_service.desktop_pull()
        assert result1.success
        result2 = await sync_service.desktop_pull()
        assert result2.success

        from src.models.orm.organizations import Organization
        r = await db_session.execute(select(Organization).where(Organization.name == "Acme Corp"))
        assert r.scalar_one() is not None

    @pytest.mark.asyncio
    async def test_deactivate_removed_org(self, sync_service, working_clone, db_session):
        """Org in DB (is_active=True), not in manifest → is_active=False."""
        from src.models.orm.organizations import Organization
        org_id = uuid4()
        db_session.add(Organization(id=org_id, name="Defunct Corp", created_by="git-sync"))
        await db_session.flush()
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        # Empty orgs list — Defunct Corp should be deactivated
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        for f in ["roles.yaml", "workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n" if f != "roles.yaml" else "[]\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("remove org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        org = await db_session.get_one(Organization, org_id)
        await db_session.refresh(org)
        assert org.is_active is False

    @pytest.mark.asyncio
    async def test_reactivate_org(self, sync_service, working_clone, db_session):
        """Org in DB (is_active=False), appears in manifest → is_active=True."""
        from src.models.orm.organizations import Organization
        org_id = uuid4()
        db_session.add(Organization(id=org_id, name="Revived Corp", is_active=False, created_by="git-sync"))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": str(org_id), "name": "Revived Corp"},
        ]))
        for f in ["roles.yaml", "workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n" if f != "roles.yaml" else "[]\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("revive org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        org = await db_session.get_one(Organization, org_id)
        await db_session.refresh(org)
        assert org.is_active is True
```

**Step 2: Run tests to verify they fail**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestOrgImport -v`
Expected: FAIL — `_import_organization` doesn't exist, orgs not imported

**Step 3: Write `_import_organization` method**

In `api/src/services/github_sync.py`, add before `_import_workflow` (~line 1121):

```python
    async def _import_organization(self, morg) -> None:
        """Import an organization from manifest into the DB.

        ID-first, name-fallback upsert. Does NOT touch domain, is_provider,
        or settings — those are env-specific and not in the manifest.
        """
        from uuid import UUID

        from sqlalchemy import update as sa_update
        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.organizations import Organization

        org_id = UUID(morg.id)

        # 1. Check by ID first (stable identity, handles renames)
        by_id = await self.db.execute(
            select(Organization.id).where(Organization.id == org_id)
        )
        existing_by_id = by_id.scalar_one_or_none()

        # 2. Check by name (cross-env where IDs differ)
        by_name = await self.db.execute(
            select(Organization.id).where(Organization.name == morg.name)
        )
        existing_by_name = by_name.scalar_one_or_none()

        if existing_by_id is not None:
            # ID match — update name (rename), ensure active
            stmt = (
                sa_update(Organization)
                .where(Organization.id == org_id)
                .values(name=morg.name, is_active=True, updated_at=datetime.now(timezone.utc))
            )
            await self.db.execute(stmt)
        elif existing_by_name is not None:
            # Name match, different ID — update ID (cross-env sync)
            stmt = (
                sa_update(Organization)
                .where(Organization.id == existing_by_name)
                .values(id=org_id, is_active=True, updated_at=datetime.now(timezone.utc))
            )
            await self.db.execute(stmt)
        else:
            # New org — insert
            stmt = insert(Organization).values(
                id=org_id,
                name=morg.name,
                is_active=True,
                created_by="git-sync",
            )
            await self.db.execute(stmt)
```

Update `_import_all_entities` — add orgs to `has_entities` and add step 0a before workflows:

```python
        has_entities = (
            manifest.organizations or manifest.roles
            or manifest.workflows or manifest.forms or manifest.agents or manifest.apps
            or manifest.integrations or manifest.configs or manifest.tables
            or manifest.events
        )
        if not has_entities:
            return 0

        count = 0

        # 0a. Import organizations (no deps — must come first)
        for morg in manifest.organizations:
            await self._import_organization(morg)
            count += 1
```

Update `_delete_removed_entities` — add org soft-delete at end of method:

```python
        # Soft-delete organizations not in manifest
        from src.models.orm.organizations import Organization
        present_org_ids = {morg.id for morg in manifest.organizations}
        org_result = await self.db.execute(
            select(Organization.id).where(Organization.is_active == True)  # noqa: E712
        )
        for row in org_result.all():
            org_id = str(row[0])
            if org_id not in present_org_ids:
                logger.info(f"Deactivating organization {org_id} — removed from manifest")
                await self.db.execute(
                    sa_update(Organization)
                    .where(Organization.id == UUID(org_id))
                    .values(is_active=False, updated_at=datetime.now(timezone.utc))
                )
```

Also add cleanup to `cleanup_test_data` fixture:

```python
    from src.models.orm.organizations import Organization
    await db_session.execute(
        delete(Organization).where(Organization.created_by.in_(["git-sync", "test"]))
    )
```

**Step 4: Run tests to verify they pass**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestOrgImport -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "feat(sync): import organizations from manifest with ID-first upsert"
```

---

### Task 2: Add `_import_role` method — COMPLETED

**Files:**
- Modify: `api/src/services/github_sync.py`
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing tests**

```python
class TestRoleImport:
    """Role import from manifest — CREATE / UPDATE / RENAME / DEACTIVATE."""

    @pytest.mark.asyncio
    async def test_create_role(self, sync_service, working_clone, db_session):
        """Role in manifest → created."""
        role_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": role_id, "name": "Admin"},
        ]))
        for f in ["workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("add role")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        from src.models.orm.users import Role
        row = await db_session.execute(select(Role).where(Role.name == "Admin"))
        role = row.scalar_one()
        assert str(role.id) == role_id
        assert role.is_active is True

    @pytest.mark.asyncio
    async def test_create_multiple_roles(self, sync_service, working_clone, db_session):
        """Two roles → both created."""
        r1, r2 = str(uuid4()), str(uuid4())
        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": r1, "name": "Admin"}, {"id": r2, "name": "Viewer"},
        ]))
        for f in ["workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("roles")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        from src.models.orm.users import Role
        r = await db_session.execute(select(Role).where(Role.name.in_(["Admin", "Viewer"])))
        assert len(r.all()) == 2

    @pytest.mark.asyncio
    async def test_update_role_by_id_rename(self, sync_service, working_clone, db_session):
        """Role exists by ID, manifest has new name → name updated."""
        from src.models.orm.users import Role
        role_id = uuid4()
        db_session.add(Role(id=role_id, name="Old Role", created_by="test"))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(role_id), "name": "New Role"},
        ]))
        for f in ["workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("rename role")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        role = await db_session.get_one(Role, role_id)
        await db_session.refresh(role)
        assert role.name == "New Role"

    @pytest.mark.asyncio
    async def test_update_role_by_name_new_id(self, sync_service, working_clone, db_session):
        """Role exists by name, different UUID → ID updated."""
        from src.models.orm.users import Role
        old_id, new_id = uuid4(), uuid4()
        db_session.add(Role(id=old_id, name="Admin", created_by="test"))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(new_id), "name": "Admin"},
        ]))
        for f in ["workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("new id")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        row = await db_session.execute(select(Role).where(Role.name == "Admin"))
        role = row.scalar_one()
        assert str(role.id) == str(new_id)

    @pytest.mark.asyncio
    async def test_update_role_preserves_permissions(self, sync_service, working_clone, db_session):
        """Role has permissions/description in DB, pull doesn't overwrite."""
        from src.models.orm.users import Role
        role_id = uuid4()
        db_session.add(Role(
            id=role_id, name="Admin", description="Full access",
            permissions={"admin": True}, created_by="test",
        ))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(role_id), "name": "Admin"},
        ]))
        for f in ["workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("sync")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        role = await db_session.get_one(Role, role_id)
        await db_session.refresh(role)
        assert role.description == "Full access"
        assert role.permissions == {"admin": True}

    @pytest.mark.asyncio
    async def test_role_idempotent(self, sync_service, working_clone, db_session):
        """Same manifest pulled twice → no errors."""
        role_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": role_id, "name": "Admin"},
        ]))
        for f in ["workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("role")
        working_clone.remotes.origin.push()

        r1 = await sync_service.desktop_pull()
        assert r1.success
        r2 = await sync_service.desktop_pull()
        assert r2.success

    @pytest.mark.asyncio
    async def test_deactivate_removed_role(self, sync_service, working_clone, db_session):
        """Role in DB not in manifest → is_active=False."""
        from src.models.orm.users import Role
        role_id = uuid4()
        db_session.add(Role(id=role_id, name="Defunct Role", created_by="git-sync"))
        await db_session.flush()
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text("[]\n")
        for f in ["workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("remove role")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        role = await db_session.get_one(Role, role_id)
        await db_session.refresh(role)
        assert role.is_active is False

    @pytest.mark.asyncio
    async def test_reactivate_role(self, sync_service, working_clone, db_session):
        """Inactive role reappears in manifest → is_active=True."""
        from src.models.orm.users import Role
        role_id = uuid4()
        db_session.add(Role(id=role_id, name="Revived Role", is_active=False, created_by="git-sync"))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(role_id), "name": "Revived Role"},
        ]))
        for f in ["workflows.yaml", "forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("revive role")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        role = await db_session.get_one(Role, role_id)
        await db_session.refresh(role)
        assert role.is_active is True
```

**Step 2: Run tests to verify they fail**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestRoleImport -v`
Expected: FAIL

**Step 3: Write `_import_role` method**

Same pattern as `_import_organization`, using `Role` ORM model from `src.models.orm.users`:

```python
    async def _import_role(self, mrole) -> None:
        """Import a role from manifest into the DB.

        ID-first, name-fallback upsert. Does NOT touch description or
        permissions — those are env-specific and not in the manifest.
        """
        from uuid import UUID

        from sqlalchemy import update as sa_update
        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.users import Role

        role_id = UUID(mrole.id)

        by_id = await self.db.execute(
            select(Role.id).where(Role.id == role_id)
        )
        existing_by_id = by_id.scalar_one_or_none()

        by_name = await self.db.execute(
            select(Role.id).where(Role.name == mrole.name)
        )
        existing_by_name = by_name.scalar_one_or_none()

        if existing_by_id is not None:
            stmt = (
                sa_update(Role)
                .where(Role.id == role_id)
                .values(name=mrole.name, is_active=True, updated_at=datetime.now(timezone.utc))
            )
            await self.db.execute(stmt)
        elif existing_by_name is not None:
            stmt = (
                sa_update(Role)
                .where(Role.id == existing_by_name)
                .values(id=role_id, is_active=True, updated_at=datetime.now(timezone.utc))
            )
            await self.db.execute(stmt)
        else:
            stmt = insert(Role).values(
                id=role_id,
                name=mrole.name,
                is_active=True,
                created_by="git-sync",
            )
            await self.db.execute(stmt)
```

Add step 0b to `_import_all_entities` after orgs:

```python
        # 0b. Import roles (no deps — must come before role junction tables)
        for mrole in manifest.roles:
            await self._import_role(mrole)
            count += 1
```

Add role soft-delete to `_delete_removed_entities`:

```python
        # Soft-delete roles not in manifest
        from src.models.orm.users import Role
        present_role_ids = {mrole.id for mrole in manifest.roles}
        role_result = await self.db.execute(
            select(Role.id).where(Role.is_active == True)  # noqa: E712
        )
        for row in role_result.all():
            role_id = str(row[0])
            if role_id not in present_role_ids:
                logger.info(f"Deactivating role {role_id} — removed from manifest")
                await self.db.execute(
                    sa_update(Role)
                    .where(Role.id == UUID(role_id))
                    .values(is_active=False, updated_at=datetime.now(timezone.utc))
                )
```

Add role cleanup to fixture:

```python
    from src.models.orm.users import Role
    await db_session.execute(
        delete(Role).where(Role.created_by.in_(["git-sync", "test"]))
    )
```

**Step 4: Run tests to verify they pass**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestRoleImport -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "feat(sync): import roles from manifest with ID-first upsert"
```

---

### Task 3: Fix import order (apps before tables) and add `_resolve_workflow_ref` — COMPLETED

**Files:**
- Modify: `api/src/services/github_sync.py`
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing tests**

```python
class TestImportOrder:
    """Import dependency chain — verify entities import in correct order."""

    @pytest.mark.asyncio
    async def test_table_with_application_id(self, sync_service, working_clone, db_session):
        """Table references app, both in manifest → no FK error."""
        org_id = str(uuid4())
        app_id = str(uuid4())
        table_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)

        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "Test Org"},
        ]))
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")

        # App YAML file
        apps_dir = work_dir / "apps" / "test-app"
        apps_dir.mkdir(parents=True)
        (apps_dir / "app.yaml").write_text(yaml.dump({"name": "Test App"}))
        (manifest_dir / "apps.yaml").write_text(yaml.dump({
            "Test App": {
                "id": app_id,
                "path": "apps/test-app/app.yaml",
                "slug": "test-app",
                "organization_id": org_id,
                "roles": [],
                "access_level": "authenticated",
            },
        }))

        # Table referencing the app
        (manifest_dir / "tables.yaml").write_text(yaml.dump({
            "Test Table": {
                "id": table_id,
                "description": "A test table",
                "organization_id": org_id,
                "application_id": app_id,
                "schema": {"columns": []},
            },
        }))

        working_clone.index.add([".bifrost/", "apps/"])
        working_clone.index.commit("app + table")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

        from src.models.orm.tables import Table
        row = await db_session.execute(select(Table).where(Table.name == "Test Table"))
        table = row.scalar_one()
        assert str(table.application_id) == app_id

    @pytest.mark.asyncio
    async def test_event_sub_workflow_ref_by_uuid(self, sync_service, working_clone, db_session):
        """Event subscription with valid workflow UUID → resolves."""
        wf_id = uuid4()
        es_id = str(uuid4())
        sub_id = str(uuid4())

        # Create the workflow in DB first
        db_session.add(Workflow(
            id=wf_id, name="Target WF", function_name="target_wf",
            path="workflows/target.py", is_active=True,
        ))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "events.yaml").write_text(yaml.dump({
            "Daily Report": {
                "id": es_id,
                "source_type": "schedule",
                "organization_id": None,
                "is_active": True,
                "cron_expression": "0 8 * * *",
                "timezone": "UTC",
                "schedule_enabled": True,
                "subscriptions": [{
                    "id": sub_id,
                    "workflow_id": str(wf_id),
                    "event_type": None,
                    "filter_expression": None,
                    "input_mapping": None,
                    "is_active": True,
                }],
            },
        }))

        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("event source")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

        from src.models.orm.events import EventSubscription
        row = await db_session.execute(
            select(EventSubscription).where(EventSubscription.id == uuid4.__class__(sub_id))
        )

    @pytest.mark.asyncio
    async def test_event_sub_workflow_ref_by_path(self, sync_service, working_clone, db_session):
        """Event subscription workflow_id as path::func → resolves to UUID."""
        wf_id = uuid4()
        es_id = str(uuid4())
        sub_id = str(uuid4())

        db_session.add(Workflow(
            id=wf_id, name="Target WF", function_name="target_wf",
            path="workflows/target.py", is_active=True,
        ))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "events.yaml").write_text(yaml.dump({
            "Daily Report": {
                "id": es_id,
                "source_type": "schedule",
                "organization_id": None,
                "is_active": True,
                "cron_expression": "0 8 * * *",
                "timezone": "UTC",
                "schedule_enabled": True,
                "subscriptions": [{
                    "id": sub_id,
                    "workflow_id": "workflows/target.py::target_wf",
                    "event_type": None,
                    "filter_expression": None,
                    "input_mapping": None,
                    "is_active": True,
                }],
            },
        }))

        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("event ref")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

        from src.models.orm.events import EventSubscription
        from uuid import UUID as UUIDType
        row = await db_session.execute(
            select(EventSubscription).where(EventSubscription.id == UUIDType(sub_id))
        )
        sub = row.scalar_one()
        assert sub.workflow_id == wf_id

    @pytest.mark.asyncio
    async def test_config_with_integration_id(self, sync_service, working_clone, db_session):
        """Config refs integration → FK satisfied (integrations imported before configs)."""
        integ_id = str(uuid4())
        config_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "integrations.yaml").write_text(yaml.dump({
            "TestInteg": {
                "id": integ_id,
                "config_schema": [],
                "mappings": [],
            },
        }))
        (manifest_dir / "configs.yaml").write_text(yaml.dump({
            "api_url": {
                "id": config_id,
                "integration_id": integ_id,
                "key": "api_url",
                "config_type": "string",
                "description": "API URL",
                "organization_id": None,
                "value": "https://api.example.com",
            },
        }))

        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("integ + config")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

    @pytest.mark.asyncio
    async def test_full_manifest_all_entity_types(self, sync_service, working_clone, db_session):
        """Full manifest with all entity types cross-referenced → single pull succeeds."""
        org_id = str(uuid4())
        role_id = str(uuid4())
        wf_id = str(uuid4())
        integ_id = str(uuid4())
        config_id = str(uuid4())
        app_id = str(uuid4())
        table_id = str(uuid4())
        es_id = str(uuid4())
        sub_id = str(uuid4())
        form_id = str(uuid4())
        agent_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)

        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "Full Test Org"},
        ]))
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": role_id, "name": "Full Test Role"},
        ]))
        (manifest_dir / "workflows.yaml").write_text(yaml.dump({
            "Full WF": {
                "id": wf_id,
                "path": "workflows/full_wf.py",
                "function_name": "full_wf",
                "type": "workflow",
                "organization_id": org_id,
                "roles": [role_id],
                "access_level": "role_based",
            },
        }))
        (manifest_dir / "integrations.yaml").write_text(yaml.dump({
            "TestInteg": {
                "id": integ_id,
                "config_schema": [{"key": "url", "type": "string", "required": True, "position": 0}],
                "mappings": [{"organization_id": org_id, "entity_id": "123", "entity_name": "Test"}],
            },
        }))
        (manifest_dir / "configs.yaml").write_text(yaml.dump({
            "api_url": {
                "id": config_id,
                "integration_id": integ_id,
                "key": "api_url",
                "config_type": "string",
                "organization_id": org_id,
                "value": "https://api.example.com",
            },
        }))

        # App
        apps_dir = work_dir / "apps" / "full-app"
        apps_dir.mkdir(parents=True)
        (apps_dir / "app.yaml").write_text(yaml.dump({"name": "Full App"}))
        (manifest_dir / "apps.yaml").write_text(yaml.dump({
            "Full App": {
                "id": app_id, "path": "apps/full-app/app.yaml", "slug": "full-app",
                "organization_id": org_id, "roles": [role_id], "access_level": "authenticated",
            },
        }))

        (manifest_dir / "tables.yaml").write_text(yaml.dump({
            "Full Table": {
                "id": table_id, "description": "Test", "organization_id": org_id,
                "application_id": app_id, "schema": {"columns": []},
            },
        }))

        (manifest_dir / "events.yaml").write_text(yaml.dump({
            "Daily Report": {
                "id": es_id, "source_type": "schedule", "organization_id": org_id,
                "is_active": True, "cron_expression": "0 8 * * *", "timezone": "UTC",
                "schedule_enabled": True,
                "subscriptions": [{
                    "id": sub_id, "workflow_id": wf_id,
                    "event_type": None, "is_active": True,
                }],
            },
        }))

        # Form file
        forms_dir = work_dir / "forms"
        forms_dir.mkdir(exist_ok=True)
        (forms_dir / f"{form_id}.form.yaml").write_text(yaml.dump({
            "name": "Full Form", "workflow": wf_id,
        }))
        (manifest_dir / "forms.yaml").write_text(yaml.dump({
            "Full Form": {
                "id": form_id, "path": f"forms/{form_id}.form.yaml",
                "organization_id": org_id, "roles": [role_id], "access_level": "role_based",
            },
        }))

        # Agent file
        agents_dir = work_dir / "agents"
        agents_dir.mkdir(exist_ok=True)
        (agents_dir / f"{agent_id}.agent.yaml").write_text(yaml.dump({
            "name": "Full Agent", "system_prompt": "You are a test agent.",
            "tool_ids": [wf_id],
        }))
        (manifest_dir / "agents.yaml").write_text(yaml.dump({
            "Full Agent": {
                "id": agent_id, "path": f"agents/{agent_id}.agent.yaml",
                "organization_id": org_id, "roles": [role_id], "access_level": "role_based",
            },
        }))

        # Workflow file
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "full_wf.py").write_text(SAMPLE_WORKFLOW_PY)

        working_clone.index.add([".bifrost/", "workflows/", "forms/", "agents/", "apps/"])
        working_clone.index.commit("full manifest")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"
```

**Step 2: Run to verify failure**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestImportOrder -v`

**Step 3: Implement import order fix and `_resolve_workflow_ref`**

In `_import_all_entities`, move apps before tables:

```python
        # 4. Import apps (moved up — tables ref application_id)
        for _app_name, mapp in manifest.apps.items():
            app_path = work_dir / mapp.path
            if app_path.exists():
                content = app_path.read_bytes()
                await self._import_app(mapp, content)
                count += 1

        # 5. Import tables (refs org + app UUIDs)
        for table_name, mtable in manifest.tables.items():
            await self._import_table(table_name, mtable)
            count += 1

        # 6. Import event sources + subscriptions
        for es_name, mes in manifest.events.items():
            await self._import_event_source(es_name, mes)
            count += 1

        # 7. Import forms
        ...
        # 8. Import agents
        ...
```

Add `_resolve_workflow_ref`:

```python
    async def _resolve_workflow_ref(self, ref: str) -> UUID | None:
        """Resolve a workflow reference by UUID, path::function_name, or name."""
        from uuid import UUID
        from src.models.orm.workflows import Workflow

        # 1. Try as UUID
        try:
            wf_id = UUID(ref)
            result = await self.db.execute(
                select(Workflow.id).where(Workflow.id == wf_id)
            )
            if result.scalar_one_or_none():
                return wf_id
        except ValueError:
            pass

        # 2. Try as path::function_name
        if "::" in ref:
            path, func = ref.rsplit("::", 1)
            result = await self.db.execute(
                select(Workflow.id).where(
                    Workflow.path == path, Workflow.function_name == func
                )
            )
            wf_id = result.scalar_one_or_none()
            if wf_id:
                return wf_id

        # 3. Try as workflow name
        result = await self.db.execute(
            select(Workflow.id).where(Workflow.name == ref)
        )
        wf_id = result.scalar_one_or_none()
        if wf_id:
            return wf_id

        return None
```

Update `_import_event_source` to use `_resolve_workflow_ref` and accept `es_name`:

```python
    async def _import_event_source(self, es_name: str, mes) -> None:
        # ... existing code but:
        # Line 1653: name=es_name  (was name=mes.id)
        # Line 1709: resolve workflow ref
        wf_ref = await self._resolve_workflow_ref(msub.workflow_id)
        if wf_ref is None:
            logger.warning(f"Event subscription {msub.id}: cannot resolve workflow ref '{msub.workflow_id}', skipping")
            continue
        # Use wf_ref instead of UUID(msub.workflow_id)
```

**Step 4: Run tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestImportOrder -v`

**Step 5: Commit**

```bash
git commit -m "feat(sync): fix import order (apps before tables), add workflow ref resolver"
```

---

### Task 4: Add role assignment sync and access_level/org_id to entity imports — COMPLETED

**Files:**
- Modify: `api/src/services/github_sync.py`
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the failing tests**

```python
class TestRoleAssignmentSync:
    """Role assignment sync — junction tables (ADD / REMOVE / REPLACE)."""

    @pytest.mark.asyncio
    async def test_workflow_role_assignment_created(self, sync_service, working_clone, db_session):
        """Workflow with roles:[A,B] → workflow_roles has A,B."""
        from src.models.orm.users import Role
        from src.models.orm.workflow_roles import WorkflowRole
        role_a, role_b = uuid4(), uuid4()
        wf_id = str(uuid4())
        db_session.add(Role(id=role_a, name="RoleA", created_by="test"))
        db_session.add(Role(id=role_b, name="RoleB", created_by="test"))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(role_a), "name": "RoleA"},
            {"id": str(role_b), "name": "RoleB"},
        ]))
        (manifest_dir / "workflows.yaml").write_text(yaml.dump({
            "Test WF": {
                "id": wf_id,
                "path": "workflows/test_wf.py",
                "function_name": "test_wf",
                "organization_id": None,
                "roles": [str(role_a), str(role_b)],
                "access_level": "role_based",
            },
        }))
        for f in ["forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")

        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "test_wf.py").write_text(SAMPLE_WORKFLOW_PY)

        working_clone.index.add([".bifrost/", "workflows/"])
        working_clone.index.commit("wf with roles")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

        from uuid import UUID as UUIDType
        rows = await db_session.execute(
            select(WorkflowRole.role_id).where(WorkflowRole.workflow_id == UUIDType(wf_id))
        )
        assigned = {row[0] for row in rows.all()}
        assert assigned == {role_a, role_b}

    @pytest.mark.asyncio
    async def test_workflow_role_assignment_removed(self, sync_service, working_clone, db_session):
        """Existing roles A,B; manifest has only A → B removed."""
        from src.models.orm.users import Role
        from src.models.orm.workflow_roles import WorkflowRole
        role_a, role_b = uuid4(), uuid4()
        wf_id = uuid4()

        db_session.add(Role(id=role_a, name="RoleA2", created_by="test"))
        db_session.add(Role(id=role_b, name="RoleB2", created_by="test"))
        db_session.add(Workflow(
            id=wf_id, name="Test WF2", function_name="test_wf2",
            path="workflows/test_wf2.py", is_active=True,
        ))
        await db_session.flush()
        # Pre-assign both roles
        db_session.add(WorkflowRole(workflow_id=wf_id, role_id=role_a, assigned_by="test"))
        db_session.add(WorkflowRole(workflow_id=wf_id, role_id=role_b, assigned_by="test"))
        await db_session.flush()
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(role_a), "name": "RoleA2"},
            {"id": str(role_b), "name": "RoleB2"},
        ]))
        (manifest_dir / "workflows.yaml").write_text(yaml.dump({
            "Test WF2": {
                "id": str(wf_id),
                "path": "workflows/test_wf2.py",
                "function_name": "test_wf2",
                "organization_id": None,
                "roles": [str(role_a)],  # Only A — B should be removed
                "access_level": "role_based",
            },
        }))
        for f in ["forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "test_wf2.py").write_text(SAMPLE_WORKFLOW_PY)

        working_clone.index.add([".bifrost/", "workflows/"])
        working_clone.index.commit("remove role B")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        rows = await db_session.execute(
            select(WorkflowRole.role_id).where(WorkflowRole.workflow_id == wf_id)
        )
        assigned = {row[0] for row in rows.all()}
        assert assigned == {role_a}  # B removed

    @pytest.mark.asyncio
    async def test_workflow_role_empty_list_clears_all(self, sync_service, working_clone, db_session):
        """Workflow with roles:[] → all assignments removed."""
        from src.models.orm.users import Role
        from src.models.orm.workflow_roles import WorkflowRole
        role_a = uuid4()
        wf_id = uuid4()

        db_session.add(Role(id=role_a, name="RoleC", created_by="test"))
        db_session.add(Workflow(
            id=wf_id, name="Test WF3", function_name="test_wf3",
            path="workflows/test_wf3.py", is_active=True,
        ))
        await db_session.flush()
        db_session.add(WorkflowRole(workflow_id=wf_id, role_id=role_a, assigned_by="test"))
        await db_session.flush()
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(role_a), "name": "RoleC"},
        ]))
        (manifest_dir / "workflows.yaml").write_text(yaml.dump({
            "Test WF3": {
                "id": str(wf_id), "path": "workflows/test_wf3.py",
                "function_name": "test_wf3", "roles": [],
                "access_level": "role_based",
            },
        }))
        for f in ["forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "test_wf3.py").write_text(SAMPLE_WORKFLOW_PY)
        working_clone.index.add([".bifrost/", "workflows/"])
        working_clone.index.commit("clear roles")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        rows = await db_session.execute(
            select(WorkflowRole.role_id).where(WorkflowRole.workflow_id == wf_id)
        )
        assert len(rows.all()) == 0

    @pytest.mark.asyncio
    async def test_form_role_assignment_synced(self, sync_service, working_clone, db_session):
        """Form with roles → form_roles synced."""
        from src.models.orm.forms import FormRole
        from src.models.orm.users import Role
        role_id = uuid4()
        form_id = str(uuid4())
        wf_id = uuid4()

        db_session.add(Role(id=role_id, name="FormRole", created_by="test"))
        db_session.add(Workflow(
            id=wf_id, name="Form WF", function_name="form_wf",
            path="workflows/form_wf.py", is_active=True,
        ))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(role_id), "name": "FormRole"},
        ]))
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text(yaml.dump({
            "Test Form": {
                "id": form_id, "path": f"forms/{form_id}.form.yaml",
                "organization_id": None,
                "roles": [str(role_id)],
                "access_level": "role_based",
            },
        }))
        forms_dir = work_dir / "forms"
        forms_dir.mkdir(exist_ok=True)
        (forms_dir / f"{form_id}.form.yaml").write_text(yaml.dump({
            "name": "Test Form", "workflow": str(wf_id),
        }))
        working_clone.index.add([".bifrost/", "forms/"])
        working_clone.index.commit("form with role")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

        from uuid import UUID as UUIDType
        rows = await db_session.execute(
            select(FormRole.role_id).where(FormRole.form_id == UUIDType(form_id))
        )
        assert {row[0] for row in rows.all()} == {role_id}

    @pytest.mark.asyncio
    async def test_role_assignment_idempotent(self, sync_service, working_clone, db_session):
        """Same manifest pulled twice → same assignments, no duplicates."""
        from src.models.orm.users import Role
        from src.models.orm.workflow_roles import WorkflowRole
        role_id = uuid4()
        wf_id = str(uuid4())
        db_session.add(Role(id=role_id, name="IdempRole", created_by="test"))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text(yaml.dump([
            {"id": str(role_id), "name": "IdempRole"},
        ]))
        (manifest_dir / "workflows.yaml").write_text(yaml.dump({
            "Idemp WF": {
                "id": wf_id, "path": "workflows/idemp_wf.py",
                "function_name": "idemp_wf", "roles": [str(role_id)],
            },
        }))
        for f in ["forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "idemp_wf.py").write_text(SAMPLE_WORKFLOW_PY)
        working_clone.index.add([".bifrost/", "workflows/"])
        working_clone.index.commit("idemp")
        working_clone.remotes.origin.push()

        r1 = await sync_service.desktop_pull()
        assert r1.success
        r2 = await sync_service.desktop_pull()
        assert r2.success

        rows = await db_session.execute(
            select(WorkflowRole).where(WorkflowRole.workflow_id == uuid4.__class__(wf_id))
        )
        assert len(rows.all()) == 1  # Not duplicated
```

**Step 2: Run to verify failure**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestRoleAssignmentSync -v`

**Step 3: Add role assignment sync to each import method**

Add a helper method to `GitHubSyncService`:

```python
    async def _sync_role_assignments(self, entity_id: UUID, manifest_roles: list[str],
                                      role_model, entity_id_column: str) -> None:
        """Sync role assignments: add new, then remove stale (no permission gap)."""
        from uuid import UUID
        from sqlalchemy import delete as sa_delete
        from sqlalchemy.dialects.postgresql import insert

        desired = {UUID(r) for r in manifest_roles}

        result = await self.db.execute(
            select(role_model.role_id).where(
                getattr(role_model, entity_id_column) == entity_id
            )
        )
        current = {row[0] for row in result.all()}

        # Add first (no permission gap)
        for role_id in desired - current:
            await self.db.execute(
                insert(role_model).values(**{
                    entity_id_column: entity_id,
                    "role_id": role_id,
                    "assigned_by": "git-sync",
                }).on_conflict_do_nothing()
            )

        # Then remove stale
        for role_id in current - desired:
            await self.db.execute(
                sa_delete(role_model).where(
                    getattr(role_model, entity_id_column) == entity_id,
                    role_model.role_id == role_id,
                )
            )
```

Then call from each import method:

In `_import_workflow`, after the insert/update:
```python
        from src.models.orm.workflow_roles import WorkflowRole
        await self._sync_role_assignments(wf_id, mwf.roles, WorkflowRole, "workflow_id")
```

Also add `organization_id` and `access_level` to both UPDATE paths in `_import_workflow`:
```python
        # In both existing_by_natural and existing_by_id branches, add:
        organization_id=org_id,
        access_level=getattr(mwf, "access_level", "role_based"),
        endpoint_enabled=getattr(mwf, "endpoint_enabled", False),
        timeout_seconds=getattr(mwf, "timeout_seconds", 1800),
        public_endpoint=getattr(mwf, "public_endpoint", False),
        category=getattr(mwf, "category", "General"),
```

In `_import_form`, after FormIndexer:
```python
        from src.models.orm.forms import Form, FormRole
        # Update org_id and access_level
        from sqlalchemy import update as sa_update
        await self.db.execute(
            sa_update(Form).where(Form.id == UUID(mform.id)).values(
                organization_id=UUID(mform.organization_id) if mform.organization_id else None,
                access_level=getattr(mform, "access_level", "role_based"),
            )
        )
        await self._sync_role_assignments(UUID(mform.id), mform.roles, FormRole, "form_id")
```

In `_import_agent`, after AgentIndexer:
```python
        from src.models.orm.agents import Agent, AgentRole
        from sqlalchemy import update as sa_update
        await self.db.execute(
            sa_update(Agent).where(Agent.id == UUID(magent.id)).values(
                organization_id=UUID(magent.organization_id) if magent.organization_id else None,
                access_level=getattr(magent, "access_level", "role_based"),
            )
        )
        await self._sync_role_assignments(UUID(magent.id), magent.roles, AgentRole, "agent_id")
```

In `_import_app`, after insert/update:
```python
        from src.models.orm.app_roles import AppRole
        await self._sync_role_assignments(app_id, mapp.roles, AppRole, "app_id")
```

Also add `access_level` to the app UPDATE path.

**Step 4: Run tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestRoleAssignmentSync -v`

**Step 5: Commit**

```bash
git commit -m "feat(sync): add role assignment sync and org_id/access_level to all entity imports"
```

---

### Task 5: Add org-scoped entity tests — COMPLETED

**Files:**
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

**Step 1: Write the tests**

```python
class TestOrgScopedEntities:
    """organization_id FK resolution across all entity types."""

    @pytest.mark.asyncio
    async def test_workflow_with_org_id(self, sync_service, working_clone, db_session):
        """Workflow references org from manifest → FK satisfied."""
        org_id = str(uuid4())
        wf_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "WF Org"},
        ]))
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text(yaml.dump({
            "Org WF": {
                "id": wf_id, "path": "workflows/org_wf.py",
                "function_name": "org_wf", "organization_id": org_id,
                "roles": [], "access_level": "role_based",
            },
        }))
        for f in ["forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "org_wf.py").write_text(SAMPLE_WORKFLOW_PY)
        working_clone.index.add([".bifrost/", "workflows/"])
        working_clone.index.commit("org wf")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

        from uuid import UUID as UUIDType
        row = await db_session.execute(select(Workflow).where(Workflow.id == UUIDType(wf_id)))
        wf = row.scalar_one()
        assert str(wf.organization_id) == org_id

    @pytest.mark.asyncio
    async def test_workflow_org_id_updated_on_pull(self, sync_service, working_clone, db_session):
        """Existing workflow with no org, manifest adds org → org_id updated."""
        org_id = str(uuid4())
        wf_id = uuid4()
        db_session.add(Workflow(
            id=wf_id, name="No Org WF", function_name="no_org_wf",
            path="workflows/no_org_wf.py", is_active=True, organization_id=None,
        ))
        await db_session.flush()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "New Org"},
        ]))
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text(yaml.dump({
            "No Org WF": {
                "id": str(wf_id), "path": "workflows/no_org_wf.py",
                "function_name": "no_org_wf", "organization_id": org_id,
                "roles": [],
            },
        }))
        for f in ["forms.yaml", "agents.yaml", "apps.yaml"]:
            (manifest_dir / f).write_text("{}\n")
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "no_org_wf.py").write_text(SAMPLE_WORKFLOW_PY)
        working_clone.index.add([".bifrost/", "workflows/"])
        working_clone.index.commit("add org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        wf = await db_session.get_one(Workflow, wf_id)
        await db_session.refresh(wf)
        assert str(wf.organization_id) == org_id

    @pytest.mark.asyncio
    async def test_integration_mapping_with_org_id(self, sync_service, working_clone, db_session):
        """IntegrationMapping references org from manifest → no FK error."""
        org_id = str(uuid4())
        integ_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "Mapping Org"},
        ]))
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "integrations.yaml").write_text(yaml.dump({
            "TestMapping": {
                "id": integ_id,
                "config_schema": [],
                "mappings": [{
                    "organization_id": org_id,
                    "entity_id": "tenant-123",
                    "entity_name": "Test Tenant",
                }],
            },
        }))
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("mapping with org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

    @pytest.mark.asyncio
    async def test_config_with_org_id(self, sync_service, working_clone, db_session):
        """Config references org → FK satisfied."""
        org_id = str(uuid4())
        integ_id = str(uuid4())
        config_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "Config Org"},
        ]))
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "integrations.yaml").write_text(yaml.dump({
            "ConfigInteg": {"id": integ_id, "config_schema": [], "mappings": []},
        }))
        (manifest_dir / "configs.yaml").write_text(yaml.dump({
            "org_url": {
                "id": config_id, "integration_id": integ_id, "key": "org_url",
                "config_type": "string", "organization_id": org_id,
                "value": "https://org.example.com",
            },
        }))
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("config with org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

    @pytest.mark.asyncio
    async def test_event_source_with_org_id(self, sync_service, working_clone, db_session):
        """EventSource references org → FK satisfied."""
        org_id = str(uuid4())
        es_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "Event Org"},
        ]))
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "events.yaml").write_text(yaml.dump({
            "Org Schedule": {
                "id": es_id, "source_type": "schedule", "organization_id": org_id,
                "is_active": True, "cron_expression": "0 8 * * *", "timezone": "UTC",
                "schedule_enabled": True, "subscriptions": [],
            },
        }))
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("event with org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

    @pytest.mark.asyncio
    async def test_table_with_org_id(self, sync_service, working_clone, db_session):
        """Table references org → FK satisfied."""
        org_id = str(uuid4())
        table_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text(yaml.dump([
            {"id": org_id, "name": "Table Org"},
        ]))
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "tables.yaml").write_text(yaml.dump({
            "Org Table": {
                "id": table_id, "description": "Test", "organization_id": org_id,
                "schema": {"columns": []},
            },
        }))
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("table with org")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success, f"Pull failed: {result.error}"

    @pytest.mark.asyncio
    async def test_secret_config_value_preserved(self, sync_service, working_clone, db_session):
        """Config type=secret with non-null value, pull doesn't overwrite."""
        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration
        integ_id = uuid4()
        config_id = uuid4()

        db_session.add(Integration(id=integ_id, name="SecretInteg"))
        await db_session.flush()
        db_session.add(Config(
            id=config_id, integration_id=integ_id, key="api_secret",
            config_type="secret", value="super-secret-value", updated_by="user",
        ))
        await db_session.flush()
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "organizations.yaml").write_text("[]\n")
        (manifest_dir / "roles.yaml").write_text("[]\n")
        (manifest_dir / "workflows.yaml").write_text("{}\n")
        (manifest_dir / "forms.yaml").write_text("{}\n")
        (manifest_dir / "agents.yaml").write_text("{}\n")
        (manifest_dir / "apps.yaml").write_text("{}\n")
        (manifest_dir / "integrations.yaml").write_text(yaml.dump({
            "SecretInteg": {"id": str(integ_id), "config_schema": [], "mappings": []},
        }))
        (manifest_dir / "configs.yaml").write_text(yaml.dump({
            "api_secret": {
                "id": str(config_id), "integration_id": str(integ_id),
                "key": "api_secret", "config_type": "secret",
                "value": None,  # Manifest has null for secrets
            },
        }))
        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("secret config")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_pull()
        assert result.success

        cfg = await db_session.get_one(Config, config_id)
        await db_session.refresh(cfg)
        assert cfg.value == "super-secret-value"  # NOT overwritten
```

**Step 2: Run to verify they fail (org-scoped ones will fail without Task 1-3)**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py::TestOrgScopedEntities -v`

**Step 3: These should pass after Tasks 1-4 are implemented. If any fail, fix the import method.**

**Step 4: Commit**

```bash
git commit -m "test(sync): exhaustive org-scoped entity and secret preservation tests"
```

---

### Task 6: Run full test suite and verify no regressions — COMPLETED

**Step 1: Run ALL sync tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: All tests pass (existing 39 + new ~40)

**Step 2: Type checking and linting**

Run: `cd api && pyright && ruff check .`
Expected: 0 errors

**Step 3: Run full E2E suite to check for regressions**

Run: `./test.sh tests/e2e/ -v`

**Step 4: Final commit if any cleanup was needed**

```bash
git commit -m "chore: cleanup and verify full test suite"
```

---

### Task 7: Unify validation and import via operations queue — NOT STARTED

**IMPORTANT — Indexer Boundary (discovered during research):**

Forms and Agents have a two-phase import:
1. **Manifest metadata** (id, org_id, access_level, roles) → handled by ops queue
2. **File content** (form fields, agent system_prompt, tool_ids, delegations) → handled by FormIndexer/AgentIndexer

The indexers (`FormIndexer.index_form`, `AgentIndexer.index_agent`) are file-content processors that:
- Parse YAML, upsert the base entity, delete-and-recreate child relationships (FormField, AgentTool, AgentDelegation)
- Resolve portable refs (workflow names → UUIDs)
- Preserve org_id/access_level (never overwrite from file)

**The ops queue does NOT replace indexers.** The `_resolve_form` / `_resolve_agent` methods should produce ops for the metadata layer only (upsert org_id/access_level, SyncRoles). The indexer call stays as a separate side-effect in `_import_all_entities`, running after the ops are executed.

Similarly, `_import_app` reads `app.yaml` for name/description — this file-parsing logic stays in the import method. The ops queue handles the declarative metadata (id, org_id, access_level, slug, roles).

**Problem:**

`validate_manifest()` in `api/src/services/manifest.py` (lines 358–462) and `_import_all_entities()` in `api/src/services/github_sync.py` encode the same dependency knowledge separately. When a new entity type or cross-reference field is added, a developer must update both — and there's no check that they stay in sync.

**Current state:**

| Concern | `validate_manifest()` | `_import_all_entities()` |
|---------|----------------------|--------------------------|
| Dependency order | Implicit in check ordering | Explicit in step numbering |
| Cross-ref checks | `if x.org_id not in org_ids: warn` | FK constraint catches it at DB level |
| Missing ref handling | Adds warning to `issues` list | Logs warning and skips |

**Why this matters:**

- Validation runs during `desktop_commit()` (preflight). Import runs during `desktop_pull()`. They're called from different code paths.
- If validation accepts something that import can't handle (or vice versa), the user gets a confusing experience: commit succeeds but pull fails, or commit warns about something that import handles fine.
- Adding a new FK field to an import method but forgetting the validation (or vice versa) is the expected failure mode as the codebase grows.

**Note on portable refs:** `_resolve_workflow_ref` (3-tier: UUID, path::func, name) is over-engineered for manifest data. The manifest generator (`manifest_generator.py:437`) always exports `workflow_id=str(sub.workflow_id)` — a UUID from the DB. Portable refs (`path::func`) are a runtime convenience for app code (executing workflows by readable name), not a manifest concern. Event subscription `workflow_id` in the manifest is always a UUID. This simplifies the design — validation's UUID-only check is correct, and `_resolve_workflow_ref` can be removed. `_resolve_portable_ref` stays for form/agent YAML file content (which is separate from the manifest).

**Approach: Operations queue**

Instead of sprinkling `if dry_run` checks throughout every import method, separate resolution from execution. Import methods don't write to the DB — they resolve references and produce a list of operation objects. At the end, either execute them all (real import) or inspect them (validation/dry-run).

```python
# Conceptual model — each import method appends ops instead of calling db.execute()

@dataclass
class Upsert:
    """Insert or update a row."""
    model: type          # ORM model class
    id: UUID
    values: dict         # column values
    match_on: str = "id" # "id", "natural_key", "name"

@dataclass
class SyncRoles:
    """Replace role assignments for an entity."""
    junction_model: type
    entity_fk: str       # e.g. "workflow_id"
    entity_id: UUID
    role_ids: set[UUID]

@dataclass
class Delete:
    """Hard-delete a row."""
    model: type
    id: UUID

@dataclass
class Deactivate:
    """Soft-delete (set is_active=False)."""
    model: type
    id: UUID
```

The import flow becomes two phases:

```python
# Phase 1: Resolution (reads DB for lookups, produces ops)
ops: list[Op] = []
for morg in manifest.organizations:
    ops.extend(await self._resolve_organization(morg))
for mrole in manifest.roles:
    ops.extend(await self._resolve_role(mrole))
for wf_name, mwf in manifest.workflows.items():
    ops.extend(await self._resolve_workflow(wf_name, mwf, content))
# ... etc, same dependency order as today

# Phase 2: Execution (or inspection)
if dry_run:
    return self._ops_to_issues(ops)  # validation: check for unresolved refs
else:
    for op in ops:
        await op.execute(self.db)
```

**How validation becomes free:**

`validate_manifest()` currently builds ID sets (`org_ids`, `role_ids`, etc.) and checks membership. With the ops queue, validation calls the same resolution methods but passes `dry_run=True`. The resolution methods already discover missing references — that's the same check validation does today, but now it's literally the same code.

The existing `validate_manifest()` in `manifest.py` becomes a thin wrapper:

```python
async def validate_manifest(manifest: Manifest, db: AsyncSession, work_dir: Path) -> list[str]:
    """Validate by doing a dry-run import — same code path, no writes."""
    sync = GitHubSyncService(db, ...)
    ops = await sync._plan_import(manifest, work_dir)
    return [issue for issue in sync._ops_to_issues(ops)]
```

**Important:** This changes `validate_manifest` from a pure function (no DB) to an async function that reads the DB. This is necessary because resolution needs DB lookups (e.g., checking if an org exists by ID vs name). The caller in `desktop_commit()` already has a DB session available, so this is straightforward.

However, the pure manifest-only cross-ref check (does org_id X appear in the organizations list?) is still valuable as a fast pre-check that doesn't need DB access. We keep that as a separate `validate_manifest_refs()` function for use in CLI tooling or offline checks.

**What changes for each import method:**

Current pattern (e.g., `_import_organization`):
```python
async def _import_organization(self, morg) -> None:
    by_id = await self.db.execute(select(...).where(Organization.id == org_id))
    existing_by_id = by_id.scalar_one_or_none()
    if existing_by_id is not None:
        await self.db.execute(update(Organization).where(...).values(...))
    elif existing_by_name is not None:
        await self.db.execute(update(Organization).where(...).values(...))
    else:
        await self.db.execute(insert(Organization).values(...))
```

New pattern:
```python
async def _resolve_organization(self, morg) -> list[Op]:
    by_id = await self.db.execute(select(...).where(Organization.id == org_id))
    existing_by_id = by_id.scalar_one_or_none()
    if existing_by_id is not None:
        return [Upsert(Organization, id=org_id, values={"name": morg.name, "is_active": True})]
    elif existing_by_name is not None:
        return [Upsert(Organization, id=org_id, values={"id": org_id, "name": morg.name, "is_active": True}, match_on="name")]
    else:
        return [Upsert(Organization, id=org_id, values={"name": morg.name, "is_active": True, "created_by": "git-sync"})]
```

The logic is identical — the only difference is returning data instead of executing it.

**What about `_delete_removed_entities`?**

Same treatment. Currently it queries the DB for active entities and compares against manifest IDs. The resolution phase produces `Delete` and `Deactivate` ops for entities not in the manifest. These get appended to the same ops list and either executed or inspected.

**Cleanup: Remove `_resolve_workflow_ref`**

As noted above, event subscription `workflow_id` in the manifest is always a UUID. Remove the 3-tier resolver and use a simple UUID cast, same as every other entity import. `_resolve_portable_ref` stays — it handles form/agent YAML file content (not manifest data).

**Files to modify:**

| File | Changes |
|------|---------|
| `api/src/services/sync_ops.py` | NEW — Op dataclasses (`Upsert`, `SyncRoles`, `Delete`, `Deactivate`) with `execute()` methods |
| `api/src/services/github_sync.py` | Refactor each `_import_*` → `_resolve_*` returning `list[Op]`. Add `_plan_import()` and `_execute_ops()`. Remove `_resolve_workflow_ref`. |
| `api/src/services/manifest.py` | `validate_manifest()` becomes thin wrapper over `_plan_import(dry_run=True)`. Keep pure `validate_manifest_refs()` for offline use. |
| `api/tests/unit/test_sync_ops.py` | NEW — unit tests for Op execution |
| `api/tests/e2e/platform/test_git_sync_local.py` | Verify all 64 existing tests still pass |

**Sub-tasks:**

**7a. Create `sync_ops.py` with Op dataclasses**

Define `Upsert`, `SyncRoles`, `Delete`, `Deactivate` dataclasses. Each has an `async execute(db)` method that runs the SQLAlchemy statement. Each also has a `describe()` method that returns a human-readable string for validation output.

Unit test: create ops, execute against a test DB, verify rows.

**7b. Refactor `_import_organization` and `_import_role` to return ops**

These are the simplest (no file content, no nested entities). Convert to `_resolve_organization` / `_resolve_role` returning `list[Op]`. Update `_import_all_entities` to collect ops from these two, then execute.

Run existing TestOrgImport and TestRoleImport to verify.

**7c. Refactor `_import_workflow` to return ops**

Slightly more complex — has role sync and file content. Convert to `_resolve_workflow` returning `list[Op]` (Upsert + SyncRoles).

Run existing tests to verify.

**7d. Refactor remaining import methods**

Convert `_import_integration`, `_import_config`, `_import_app`, `_import_table`, `_import_event_source`, `_import_form`, `_import_agent` to return ops. Each follows the same pattern.

**Indexer boundary:** For forms, agents, and apps, the ops queue only covers metadata (org_id, access_level, roles, base entity upsert). File-content processing (FormIndexer, AgentIndexer, app.yaml parsing) stays as separate side-effects that run alongside ops execution in `_import_all_entities`. The `_resolve_*` methods produce ops for the metadata layer; the indexer calls remain in the orchestration code.

Remove `_resolve_workflow_ref` — event subscription workflow_id is always a UUID in the manifest.

**7e. Refactor `_delete_removed_entities` to return ops**

Convert to `_resolve_deletions` returning `list[Op]` (Delete + Deactivate).

**7f. Wire up `_plan_import()` and update `validate_manifest()`**

Add `_plan_import(manifest, work_dir) -> list[Op]` that calls all `_resolve_*` methods in dependency order and returns the full ops list.

Update `_import_all_entities` to call `_plan_import()` then `_execute_ops()`.

Update `validate_manifest()` to call `_plan_import()` and convert ops to validation issues.

Run full test suite (64 sync tests + all E2E).

**7g. Add drift-detection test**

A unit test that introspects the Manifest model's fields and verifies every FK-like field (ending in `_id`, plus `roles`) is covered by a `_resolve_*` method. This catches the "forgot to handle a new field" case at test time.

**Acceptance criteria:**

1. Import methods produce operations as data, not side effects
2. `validate_manifest()` and `_import_all_entities()` use the same code path — resolution is shared
3. Adding a new entity type means writing one `_resolve_*` method, not two separate implementations
4. `_resolve_workflow_ref` removed — manifest always has UUIDs for event subscriptions
5. A unit test catches new manifest fields that aren't covered by resolution
6. All existing tests (64 sync tests) still pass
7. `validate_manifest_refs()` preserved as a pure function for offline/CLI use
