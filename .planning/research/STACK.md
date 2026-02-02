# Technology Stack: Multi-Tenant Module Scoping

**Project:** Bifrost - Organization-Scoped Modules with Cascade Resolution
**Researched:** 2026-02-02
**Confidence:** HIGH (existing patterns verified in codebase)

## Executive Summary

**No new libraries required.** The existing stack fully supports multi-tenant module scoping with cascade resolution. The implementation uses established patterns already proven in the codebase for workflows, forms, apps, and agents.

## Current Stack (Validated)

| Technology | Version | Purpose | Status |
|------------|---------|---------|--------|
| SQLAlchemy | 2.0+ | ORM with async support | Already installed |
| PostgreSQL | 15+ | Primary database | Already running |
| Redis | 5.0+ (merged aioredis) | Module cache | Already running |
| Alembic | Latest | Database migrations | Already installed |

## Required Stack Changes

### Database Layer

**No new dependencies.** Changes are schema and pattern adjustments:

| Change | Component | Rationale |
|--------|-----------|-----------|
| Add `organization_id` column | `workspace_files` table | Enables org scoping for modules |
| Add foreign key constraint | `workspace_files.organization_id` | References `organizations.id`, nullable for global modules |
| Update unique constraint | `workspace_files` | Change from `(path)` to `(organization_id, path)` - allows same path in different orgs |
| Add partial index | `workspace_files` | Index on `(organization_id, path)` for fast lookups |

**Migration approach:**
```sql
-- Add org_id column (nullable for global modules)
ALTER TABLE workspace_files ADD COLUMN organization_id UUID REFERENCES organizations(id);

-- Update unique constraint to be org-scoped
ALTER TABLE workspace_files DROP CONSTRAINT uq_workspace_files_path;
ALTER TABLE workspace_files ADD CONSTRAINT uq_workspace_files_org_path
  UNIQUE (organization_id, path);

-- Index for cascade lookups: org-specific first, then global
CREATE INDEX ix_workspace_files_org_path
  ON workspace_files(organization_id, path)
  WHERE NOT is_deleted;
```

### Repository Layer

**No new dependencies.** Extend existing `OrgScopedRepository` pattern:

| Change | Rationale |
|--------|-----------|
| Create `ModuleRepository(OrgScopedRepository[WorkspaceFile])` | Follows established pattern from FormRepository, WorkflowRepository |
| Use `_apply_cascade_scope()` for list operations | Existing helper provides org + global filtering |
| Use cascade lookup in `get(path=...)` | Org-specific first, global fallback |

**Key insight:** The existing `OrgScopedRepository` base class already implements exactly the cascade logic needed:
- `_apply_cascade_scope()` - Returns `WHERE (organization_id = org_id OR organization_id IS NULL)`
- `get(name=...)` pattern - Tries org-specific first, falls back to global
- `is_superuser` handling - Trusts scope, skips role checks

### Redis Cache Layer

**No new dependencies.** Key pattern changes only:

| Current | Proposed | Rationale |
|---------|----------|-----------|
| `bifrost:module:{path}` | `bifrost:module:{org_id}:{path}` | Org-scoped module cache |
| N/A | `bifrost:module:global:{path}` | Global module cache (org_id=NULL) |
| `bifrost:module:index` | `bifrost:module:index:{org_id}` | Per-org module index |
| N/A | `bifrost:module:index:global` | Global module index |

**Cache warming strategy:**
```python
# Warm cache from DB with org scoping
async def warm_cache_from_db(org_id: UUID | None = None):
    query = select(WorkspaceFile).where(
        WorkspaceFile.entity_type == "module",
        WorkspaceFile.is_deleted == False,
    )
    if org_id is not None:
        query = query.where(WorkspaceFile.organization_id == org_id)
    else:
        query = query.where(WorkspaceFile.organization_id.is_(None))
    # ... cache modules
```

### Virtual Import System

**No new dependencies.** Logic changes only:

| Current | Proposed | Rationale |
|---------|----------|-----------|
| `get_module_sync(path)` | `get_module_sync(path, org_id)` | Add org context to lookup |
| Single Redis key lookup | Cascade: org key first, then global key | Org-specific module takes priority |

**Cascade resolution in virtual import:**
```python
def _find_spec_impl(self, fullname: str, org_id: UUID | None) -> ModuleSpec | None:
    possible_paths = self._module_name_to_paths(fullname)

    for file_path, is_package in possible_paths:
        # Step 1: Try org-specific module
        if org_id is not None:
            cached = get_module_sync(file_path, org_id=org_id)
            if cached:
                return self._create_spec(fullname, file_path, cached, is_package)

        # Step 2: Fall back to global module
        cached = get_module_sync(file_path, org_id=None)
        if cached:
            return self._create_spec(fullname, file_path, cached, is_package)

    return None
```

## What NOT to Add

| Technology | Why Not |
|------------|---------|
| Separate module storage (S3, etc.) | PostgreSQL + Redis sufficient for module content |
| Custom caching library | Redis handles all caching needs |
| Module versioning system | Over-engineering - git sync provides version history |
| Separate module registry service | Database + OrgScopedRepository pattern sufficient |
| GraphQL for module queries | REST patterns established, no benefit |

## Integration Points

### Execution Context

The execution engine already passes `org_id` through the workflow execution chain:

```python
# Current: ExecutionContext includes org_id
class ExecutionContext:
    org_id: UUID | None
    user_id: UUID | None
    # ...
```

**Required change:** Pass `org_id` to virtual import hook during worker initialization:

```python
# Worker startup
def setup_worker(org_id: UUID | None):
    install_virtual_import_hook(org_id=org_id)
```

### File Storage Service

The `FileStorageService` already handles workspace file operations. Module scoping integrates naturally:

```python
# Current write_file pattern
await file_storage.write_file(path, content)  # No org context

# New pattern
await file_storage.write_file(path, content, org_id=org_id)
```

### GitHub Sync

GitHub sync already operates in org context. Module scoping follows the same pattern:

```python
# Current sync operates per-org
async def sync_from_github(org_id: UUID):
    # Files synced with org context
```

## Deployment Considerations

### Database Migration

- **Backward compatible:** `organization_id` is nullable (existing modules become global)
- **Zero downtime:** Can migrate without service interruption
- **Rollback safe:** Column drop is safe if needed

### Cache Migration

- **Warm cache after migration:** Run cache warming to populate new key structure
- **Gradual rollout:** Old and new key patterns can coexist temporarily
- **TTL-based cleanup:** Old keys expire naturally (24hr TTL)

## Confidence Assessment

| Area | Confidence | Reason |
|------|------------|--------|
| Database schema | HIGH | Standard FK + index pattern, used throughout codebase |
| Repository pattern | HIGH | OrgScopedRepository already exists and works |
| Redis caching | HIGH | Pattern matches existing module cache, just adds org namespace |
| Virtual import | HIGH | Clear integration point, minimal changes |
| Backward compatibility | HIGH | Nullable org_id preserves existing global modules |

## Sources

All patterns verified directly from codebase:

- `/home/jack/GitHub/bifrost/api/src/repositories/org_scoped.py` - OrgScopedRepository base class
- `/home/jack/GitHub/bifrost/api/src/repositories/workflows.py` - WorkflowRepository extending OrgScopedRepository
- `/home/jack/GitHub/bifrost/api/src/repositories/forms.py` - FormRepository with cascade scoping
- `/home/jack/GitHub/bifrost/api/src/models/orm/workspace.py` - WorkspaceFile model (current schema)
- `/home/jack/GitHub/bifrost/api/src/core/module_cache.py` - Redis module caching
- `/home/jack/GitHub/bifrost/api/src/services/execution/virtual_import.py` - Virtual import system
- `/home/jack/GitHub/bifrost/api/src/repositories/README.md` - OrgScopedRepository documentation

## Summary

**Zero new dependencies.** The milestone is purely a pattern application:

1. **Database:** Add `organization_id` column to `workspace_files`, update constraints
2. **Repository:** Create `ModuleRepository` extending `OrgScopedRepository`
3. **Cache:** Namespace Redis keys by org_id
4. **Virtual Import:** Add org_id parameter, implement cascade lookup

All required patterns already exist and are battle-tested in the codebase.
