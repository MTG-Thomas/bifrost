# Domain Pitfalls: Adding Organization Scoping to Existing System

**Domain:** Multi-tenant module scoping for MSP automation platform
**Researched:** 2026-02-02
**Confidence:** HIGH (based on codebase analysis + industry patterns)

## Context

Bifrost is adding `organization_id` to `workspace_files` to enable org-scoped modules. This is being added to an existing system with:
- Global modules (no org_id) that must remain working
- Redis cache with path-based keys (`bifrost:module:{path}`)
- Virtual import system that loads modules by path from Redis
- GitHub sync that assumes path uniqueness
- SDK (`bifrost.files`) that workflows use for file operations

---

## Critical Pitfalls

Mistakes that cause rewrites, data loss, or production outages.

### Pitfall 1: Cache Key Collision After Scoping

**What goes wrong:** After adding org_id to workspace_files, the Redis module cache still uses path-only keys (`bifrost:module:shared/halopsa.py`). Two orgs with the same module path will overwrite each other's cached content.

**Why it happens:**
- The existing cache key format in `module_cache.py` is `bifrost:module:{path}` (lines 20-21)
- Cache writes happen in `file_ops.py` during `write_file()` (line 312)
- When org B writes `shared/halopsa.py`, it overwrites org A's cached version
- Virtual importer then serves org B's code to org A's workflows

**Consequences:**
- **Silent data leakage**: Org A's workflow executes org B's code
- **Security breach**: Cross-tenant code execution
- **Intermittent failures**: Cache TTL (24hr) means symptoms come and go

**Warning signs:**
- Workflows behaving differently after another org modifies same path
- `warm_cache_from_db()` output doesn't match expected org behavior
- Module cache `scard` count lower than expected unique (org, path) pairs

**Prevention:**
1. Update cache key format to include org_id: `bifrost:module:{org_id}:{path}` or `bifrost:module:org:{org_id}:{path}`
2. Update `MODULE_KEY_PREFIX` and all cache functions in `module_cache.py`
3. Update `module_cache_sync.py` (sync version used by workers)
4. Update `warm_cache_from_db()` to load all org-scoped modules with correct keys
5. Add org_id parameter to `get_module()`, `set_module()`, `invalidate_module()`

**Phase to address:** Phase 1 (Cache Architecture) - Must be done before any org_id usage

**Code locations to modify:**
- `/home/jack/GitHub/bifrost/api/src/core/module_cache.py` - Async cache functions
- `/home/jack/GitHub/bifrost/api/src/core/module_cache_sync.py` - Sync cache functions
- `/home/jack/GitHub/bifrost/api/src/services/file_storage/file_ops.py` - Cache writes
- `/home/jack/GitHub/bifrost/api/scripts/init_container.py` - Cache warming

---

### Pitfall 2: Virtual Importer Ignores Organization Context

**What goes wrong:** The `VirtualModuleFinder` in `virtual_import.py` has no concept of organization. It fetches modules by path alone, so even with scoped cache keys, it doesn't know which org to query.

**Why it happens:**
- `_find_spec_impl()` calls `get_module_sync(file_path)` with path only (line 324)
- Worker process doesn't receive org_id context for module resolution
- Import statement `from shared import halopsa` contains no org information

**Consequences:**
- Org-scoped modules are unreachable by virtual importer
- Only global modules (org_id=NULL) work
- The entire scoping feature is broken even with correct DB/cache schema

**Warning signs:**
- `ModuleNotFoundError` for modules that exist in workspace_files with org_id
- Import works in one org, fails in another for "same" module

**Prevention:**
1. Pass org_id to worker process via execution context (already in Redis pending key)
2. Store org_id in thread-local storage when worker starts
3. Update `get_module_sync()` to accept and use org_id parameter
4. Implement cascade: try org-specific first, fall back to global (org_id=NULL)

**Phase to address:** Phase 2 (Virtual Import Updates) - After cache architecture is fixed

**Code locations to modify:**
- `/home/jack/GitHub/bifrost/api/src/services/execution/virtual_import.py` - Import hook
- `/home/jack/GitHub/bifrost/api/src/core/module_cache_sync.py` - get_module_sync
- `/home/jack/GitHub/bifrost/api/src/jobs/consumers/workflow_execution.py` - Pass org context

---

### Pitfall 3: Database Unique Constraint Prevents Scoped Modules

**What goes wrong:** `workspace_files` has a unique constraint on `path` alone (`UniqueConstraint("path", name="uq_workspace_files_path")`). Two orgs cannot have the same path.

**Why it happens:**
- Original design assumed global-only modules
- Constraint was created in migration `20251207_000000_add_workspace_files_table.py`
- Upsert logic in `file_ops.py` uses `on_conflict_do_update` on path (line 290)

**Consequences:**
- When org B creates `shared/halopsa.py`, it overwrites org A's record
- File content from org A is lost without warning
- Database audit logs show update, not the expected insert

**Warning signs:**
- `workspace_files` row count doesn't increase when new org adds modules
- `updated_at` changes on files that org didn't modify
- Git sync shows unexpected modifications

**Prevention:**
1. Drop `uq_workspace_files_path` constraint
2. Create new unique constraint on `(path, organization_id)`
3. Handle NULL org_id in constraint (PostgreSQL `NULLS NOT DISTINCT` or partial indexes)
4. Update `on_conflict_do_update` to use new constraint columns
5. Migration must handle existing data (all existing = global)

**Phase to address:** Phase 1 (Schema Migration) - Before any org_id writes

**Migration considerations:**
- Use `postgresql_nulls_not_distinct=True` if supporting Postgres 15+
- Or use partial indexes: one for NULL org_id, one for non-NULL
- See existing pattern in `/home/jack/GitHub/bifrost/api/src/models/orm/knowledge.py` line 93-95

---

### Pitfall 4: GitHub Sync Path Collision Across Orgs

**What goes wrong:** GitHub sync uses path as the entity identifier. Two orgs syncing repos with the same file path cause confusion about which DB record to update.

**Why it happens:**
- `GITHUB_SYNC.md` shows path is primary identifier for workspace files
- `compute_git_blob_sha()` and SHA comparison are path-based
- No org context in sync logic for non-virtual files

**Consequences:**
- Pushing from org A and org B with same path creates race condition
- Pulling to org A might overwrite org B's version
- Orphan detection fails (can't distinguish which org's workflow is affected)

**Warning signs:**
- Sync preview shows unexpected modifications
- Content mismatch after sync completes
- Multiple orgs' git status shows "modified" for files they didn't touch

**Prevention:**
1. Enforce "one repo = one scope" rule strictly
2. Store org_id in GitHub config table, use during sync
3. All sync queries must filter by org_id
4. Add org_id column to file lookup queries in `github_sync.py`
5. Update `_get_local_file_shas()` to scope by org

**Phase to address:** Phase 3 (GitHub Sync Updates) - After schema and virtual import work

---

## Moderate Pitfalls

Mistakes that cause delays, technical debt, or degraded experience.

### Pitfall 5: Cache Warming Misses Org-Scoped Modules

**What goes wrong:** `warm_cache_from_db()` loads all modules with `entity_type='module'` but doesn't correctly key them by org, or misses the cascade logic.

**Why it happens:**
- Current implementation queries all modules regardless of org (line 128-133 in module_cache.py)
- After schema change, it might only cache global modules
- Worker starts with partial cache, causing cold-start failures

**Prevention:**
1. Update `warm_cache_from_db()` to load ALL modules (global + org-scoped)
2. Cache each with correct org-prefixed key
3. Add integration test verifying multi-org cache warming
4. Consider lazy loading for less-used org modules

**Phase to address:** Phase 1 (Cache Architecture)

---

### Pitfall 6: SDK File Operations Not Org-Aware

**What goes wrong:** `bifrost.files` SDK operations (`read`, `write`, `list`, `delete`) don't pass org context, so they operate on global scope only.

**Why it happens:**
- SDK calls `/api/files/*` endpoints
- Endpoints receive user context but may not filter workspace_files by user's org
- `FileOperationsService` queries by path without org filter

**Prevention:**
1. Ensure API endpoints extract org_id from authenticated user
2. Pass org_id to `FileOperationsService` methods
3. Update workspace_files queries to filter by (path, org_id)
4. Add fallback to global for read operations (cascade)

**Phase to address:** Phase 4 (SDK Updates)

---

### Pitfall 7: Inconsistent NULL Handling for Global Scope

**What goes wrong:** Different code paths handle `organization_id=NULL` (meaning "global") inconsistently - some treat NULL as "any org", others as "no org".

**Why it happens:**
- SQL `= NULL` never matches (must use `IS NULL`)
- ORM queries might use `.filter(org_id == value)` which fails for NULL
- Cascade logic requires checking both org-specific AND NULL

**Prevention:**
1. Define explicit constant `GLOBAL_ORG_ID = None` for clarity
2. Use SQLAlchemy `or_(column == value, column.is_(None))` for cascade queries
3. Create helper function `scope_query(query, org_id)` used everywhere
4. Add unit tests for NULL org_id behavior in all query paths
5. Follow existing pattern in `OrgScopedRepository`

**Phase to address:** All phases - establish pattern in Phase 1

---

### Pitfall 8: Migration Data Corruption

**What goes wrong:** During migration adding org_id column, existing rows get incorrect values or constraints fail unexpectedly.

**Why it happens:**
- Adding non-nullable column without default causes migration failure
- Setting wrong default (e.g., empty string instead of NULL) corrupts semantics
- Backfill logic might timeout on large tables

**Prevention:**
1. Add column as `nullable=True` with `default=None`
2. All existing rows automatically get NULL (global scope) - correct behavior
3. Do NOT backfill existing modules to any org
4. Create constraint AFTER column add, not simultaneously
5. Test migration on copy of production data before deploy

**Phase to address:** Phase 1 (Schema Migration)

---

### Pitfall 9: Worker Process Leaks Org Context

**What goes wrong:** Worker processes execute multiple workflows. Org context from one execution leaks into the next.

**Why it happens:**
- Thread-local storage for org_id not cleared between executions
- Module cache lookups use stale org_id
- Imported modules remain in `sys.modules` with wrong org's code

**Prevention:**
1. Clear org context at start AND end of each execution
2. Clear relevant `sys.modules` entries between executions (for reloaded modules)
3. Use context managers to ensure cleanup even on exceptions
4. Add tests for sequential execution with different orgs

**Phase to address:** Phase 2 (Virtual Import Updates)

---

## Minor Pitfalls

Mistakes that cause annoyance but are fixable without major rework.

### Pitfall 10: Index Bloat from Partial Indexes

**What goes wrong:** Creating too many partial indexes for the unique constraint pattern causes index bloat and slower writes.

**Prevention:**
- Use single index with `NULLS NOT DISTINCT` if Postgres 15+
- Limit partial indexes to 2 max (one for NULL, one for non-NULL)
- Monitor index size in production

---

### Pitfall 11: Log Messages Don't Include Org Context

**What goes wrong:** After adding scoping, logs like "Cached module: shared/halopsa.py" become ambiguous - which org?

**Prevention:**
- Update log messages to include `org_id` where relevant
- Format: `Cached module: shared/halopsa.py (org={org_id})`
- Add org_id to structured logging fields

---

### Pitfall 12: Test Fixtures Assume Global Scope

**What goes wrong:** Existing tests create modules without org_id, pass on their own, but fail when combined with org-scoped tests.

**Prevention:**
- Audit test fixtures for hardcoded path-only lookups
- Create fixtures for both global and org-scoped modules
- Add multi-org integration tests early

---

## Phase-Specific Warnings

| Phase | Likely Pitfall | Mitigation | Priority |
|-------|---------------|------------|----------|
| Schema Migration | Unique constraint conflict (#3) | Change to (path, org_id) constraint | BLOCKING |
| Schema Migration | Migration data corruption (#8) | Add nullable column, don't backfill | BLOCKING |
| Cache Architecture | Cache key collision (#1) | Include org_id in cache keys | BLOCKING |
| Cache Architecture | Cache warming incomplete (#5) | Load all orgs, test multi-org | HIGH |
| Virtual Import | No org context (#2) | Pass org via thread-local | BLOCKING |
| Virtual Import | Context leaks (#9) | Clear context between executions | HIGH |
| GitHub Sync | Path collision across orgs (#4) | Enforce one repo = one scope | BLOCKING |
| SDK Updates | SDK not org-aware (#6) | Extract org from auth context | MEDIUM |
| All Phases | NULL handling (#7) | Use helper function consistently | HIGH |

---

## Validation Checklist

Before deploying each phase:

- [ ] Can two orgs have module at same path? (DB constraint correct)
- [ ] Does org A's workflow load org A's module? (virtual import scoped)
- [ ] Does global fallback work? (cascade logic implemented)
- [ ] Does cache invalidation clear correct key? (org-prefixed keys)
- [ ] Do existing global modules still work? (backward compatibility)
- [ ] Do logs show which org? (observability)
- [ ] Do tests cover multi-org scenarios? (regression prevention)

---

## Sources

**Codebase Analysis:**
- `/home/jack/GitHub/bifrost/api/src/core/module_cache.py` - Cache key patterns
- `/home/jack/GitHub/bifrost/api/src/services/execution/virtual_import.py` - Import hook
- `/home/jack/GitHub/bifrost/api/src/models/orm/workspace.py` - Unique constraint
- `/home/jack/GitHub/bifrost/api/src/services/GITHUB_SYNC.md` - Sync architecture
- `/home/jack/GitHub/bifrost/api/src/models/orm/knowledge.py` - NULL handling pattern

**Industry Research:**
- [Multi-Tenant Database Architecture Patterns](https://www.bytebase.com/blog/multi-tenant-database-architecture-patterns-explained/) - Schema migration patterns
- [Multi-Tenant Caching Strategies](https://medium.com/@okan.yurt/multi-tenant-caching-strategies-why-redis-alone-isnt-enough-hybrid-pattern-f404877632e5) - Cache key namespace isolation
- [Azure Cache for Redis Multitenancy](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/cache-redis) - Noisy neighbor and isolation
- [Django Multi-Tenant Pitfalls](https://books.agiliq.com/projects/django-multi-tenant/en/latest/) - Context propagation issues
- [Citus Multi-Tenant Migration](https://docs.citusdata.com/en/v7.4/develop/migration_mt_schema.html) - Primary key modifications
- [Crunchy Data Multi-Tenancy](https://www.crunchydata.com/blog/designing-your-postgres-database-for-multi-tenancy) - tenant_id on every table pattern
