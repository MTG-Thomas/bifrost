# Project Research Summary

**Project:** Organization-Scoped Modules
**Domain:** Multi-tenant MSP automation platform
**Researched:** 2026-02-02
**Confidence:** HIGH

## Executive Summary

Adding organization scoping to the Bifrost module system is a pattern application, not a greenfield build. The existing `OrgScopedRepository` pattern used by workflows, forms, agents, and apps provides the exact cascade mechanism needed: org-specific lookups first with global fallback. Zero new dependencies are required—this milestone extends established patterns across four interconnected systems: database schema, Redis caching, virtual import, and GitHub sync.

The critical path is database-first: add `organization_id` to `workspace_files` with a compound unique constraint `(organization_id, path)` allowing the same path across orgs. Then restructure Redis keys from `bifrost:module:{path}` to `bifrost:module:{org_id}:{path}` (or `bifrost:module:global:{path}` for NULL). Update the virtual import hook to receive org context via thread-local storage and perform cascade lookups. Finally, enforce one-scope-per-repo in GitHub sync to prevent path collisions.

The highest-risk pitfalls all stem from cache key collisions and context leakage. If Redis keys aren't scoped before org_id is used, two orgs with the same module path will overwrite each other's cached code—a silent cross-tenant code execution vulnerability. If the virtual importer doesn't receive org context, scoped modules become unreachable. These are blocking issues that must be prevented through careful phase ordering: schema first, cache restructure second, import integration third.

## Key Findings

### Recommended Stack

**No new libraries required.** The existing stack (SQLAlchemy 2.0+, PostgreSQL 15+, Redis 5.0+, Alembic) fully supports multi-tenant module scoping. All required patterns already exist in the codebase.

**Core technologies:**
- **SQLAlchemy**: ORM with async support — add `organization_id` column with compound unique constraint
- **PostgreSQL**: Primary database — standard FK pattern with partial indexes for NULL handling
- **Redis**: Module cache — namespace keys by org_id for isolation
- **Thread-local storage**: Context propagation — pass org_id to import hook (matches existing recursion guard pattern)

**Key schema change:** Alter `workspace_files` unique constraint from `(path)` to `(organization_id, path)` with NULL-aware semantics (PostgreSQL 15+ `NULLS NOT DISTINCT` or partial indexes).

### Expected Features

**Must have (table stakes):**
- **Cascade resolution** — org-specific modules override global (users expect consistency with workflows/forms)
- **Backward compatibility** — existing global modules (NULL org_id) continue working unchanged
- **Same import syntax** — `from shared import halopsa` works without modification
- **Org isolation** — Org A cannot see or load Org B's modules
- **Hot reload** — module changes reflect immediately via cache invalidation

**Should have (competitive):**
- **Transparent override** — user writes `halopsa.py` in their repo, it "just works" for their org
- **No explicit registration** — module exists = module available (convention over configuration)
- **Override visibility UI** — shows which modules are overridden in admin interface

**Defer (v2+):**
- **Diff view** — compare org version to global version (power user feature)
- **Version pinning** — org pins to specific global version (enterprise complexity)

**Explicitly avoid (anti-features):**
- **Partial function override** — inherit some functions, override others (fragile, hard to reason about)
- **Cross-org module sharing** — Org A shares with Org B (security nightmare)
- **Module marketplace** — user-submitted modules for other orgs (support burden)

### Architecture Approach

The module pipeline flows: GitHub Sync → workspace_files → Redis Cache → Virtual Importer → Worker Execution. Currently, none of these steps have org context. The architecture adds `org_id` at each stage while maintaining backward compatibility (NULL = global).

**Major components:**
1. **workspace_files table** — Stores module content with org scope; compound unique constraint `(organization_id, path)`
2. **ModuleCache (Redis)** — Org-prefixed caching with TTL; keys: `bifrost:module:{org_id}:{path}` and `bifrost:module:global:{path}`
3. **VirtualModuleFinder** — Cascade lookup (org → global); reads org context from thread-local storage
4. **WorkerContext** — Propagates org_id to import system before execution starts
5. **GitHubSyncService** — Associates each repo with exactly one scope (org or global)

**Data flow transformation:**
```
CURRENT: GitHub → workspace_files(path unique) → Redis(path key) → Import(path only) → Execute
PROPOSED: GitHub(org scope) → workspace_files(org+path unique) → Redis(org:path key) → Import(cascade) → Execute(org context)
```

**Patterns to follow:**
- **OrgScopedRepository** — cascade get with org → global fallback (existing pattern)
- **Thread-local context** — store org_id for import system (matches existing `_thread_local.in_find_spec`)
- **Dual-key caching** — separate cache entries for scoped vs global (no metadata checks needed)

### Critical Pitfalls

1. **Cache key collision** — Redis keys not scoped by org_id causes two orgs with same path to overwrite each other's cached code. **Prevention:** Update cache key format to `bifrost:module:{org_id}:{path}` before any org_id usage. Phase 1 blocker.

2. **Virtual importer ignores org context** — Import hook has no way to know which org's modules to load. **Prevention:** Pass org_id via thread-local storage, implement cascade lookup (org → global). Phase 2 blocker.

3. **Database unique constraint prevents scoped modules** — Current `uq_workspace_files_path` constraint allows only one org per path. **Prevention:** Replace with compound constraint `(organization_id, path)` with NULL handling. Phase 1 blocker.

4. **GitHub sync path collision** — Two orgs syncing repos with same file path create race conditions. **Prevention:** Enforce one-scope-per-repo rule strictly. Phase 3 blocker.

5. **Worker process leaks org context** — Thread-local org_id not cleared between executions causes cross-contamination. **Prevention:** Clear context at start AND end of each execution with context managers. Phase 2 high priority.

## Implications for Roadmap

Based on research, suggested phase structure (4 phases, ~3.5 days total):

### Phase 1: Database Foundation
**Rationale:** All downstream systems depend on the schema. Must be first. Safe to add with NULL default (preserves existing behavior).
**Delivers:**
- `organization_id` column on `workspace_files` (nullable, references organizations)
- Compound unique constraint `(organization_id, path)` replacing `(path)`
- Partial indexes for efficient cascade queries
- Alembic migration with rollback safety

**Addresses:**
- Table stakes: backward compatibility (NULL = global)
- Architecture: workspace_files component update

**Avoids:**
- Pitfall #3: unique constraint prevents scoped modules
- Pitfall #8: migration data corruption

**Implementation notes:**
- Add column as `nullable=True, default=None`
- All existing rows get NULL (correct global behavior)
- Use PostgreSQL 15+ `NULLS NOT DISTINCT` or partial indexes for NULL handling
- Update `on_conflict_do_update` to use new constraint columns

### Phase 2: Cache Restructure
**Rationale:** Import system reads from cache. Cache keys must be scoped before virtual import changes. Depends on schema existing but not required to be populated with org_ids yet.
**Delivers:**
- Updated cache key format: `bifrost:module:{org_id}:{path}` and `bifrost:module:global:{path}`
- Thread-local `ImportOrgContext` module for context propagation
- Updated `module_cache.py` (async) and `module_cache_sync.py` (sync) for cascade lookup
- Rewritten `warm_cache_from_db()` to load all orgs with correct keys

**Addresses:**
- Table stakes: org isolation
- Architecture: ModuleCache and Redis key structure

**Avoids:**
- Pitfall #1: cache key collision (BLOCKING)
- Pitfall #5: cache warming misses org-scoped modules

**Implementation notes:**
- Create `api/src/core/import_context.py` with thread-local get/set/clear
- Add org_id parameter to `get_module()`, `set_module()`, `invalidate_module()`
- Implement cascade in `get_module_sync()`: try org-specific first, then global
- Clear all old cache keys on deployment (optional, TTL handles automatically)

### Phase 3: Virtual Import Integration
**Rationale:** Depends on cache structure and org context infrastructure. Connects execution context to module resolution. Highest-risk for context leakage.
**Delivers:**
- Updated `virtual_import.py` to read org context from thread-local
- Worker initialization sets org context before execution
- Context cleanup after execution (even on error)
- Cascade resolution in `_find_spec_impl()`

**Addresses:**
- Table stakes: cascade resolution, same import syntax
- Differentiator: transparent override

**Avoids:**
- Pitfall #2: virtual importer ignores org context (BLOCKING)
- Pitfall #9: worker process leaks org context (HIGH)

**Implementation notes:**
- Update `worker.py` and `simple_worker.py` to call `set_import_org_context(org_id)` before execution
- Use context manager pattern to ensure cleanup: `try/finally` or `@contextmanager`
- Test sequential execution with different orgs
- Consider clearing relevant `sys.modules` entries between executions

### Phase 4: Write Path & Sync
**Rationale:** Read path must work first for testing. Write path is last to integrate. GitHub sync is the source of org context for incoming modules.
**Delivers:**
- Updated `file_ops.py` to pass org_id to `set_module()`
- Updated `FileStorageService` to accept and propagate org_id
- GitHub sync enforcement: one repo = one scope
- API endpoints extract org_id from authenticated user
- SDK operations (`bifrost.files`) scope by user's org with global fallback

**Addresses:**
- Table stakes: hot reload
- Architecture: GitHubSyncService and FileStorageService updates

**Avoids:**
- Pitfall #4: GitHub sync path collision (BLOCKING)
- Pitfall #6: SDK not org-aware (MEDIUM)

**Implementation notes:**
- Store org_id in GitHub config table, use during sync
- All sync queries filter by org_id
- File API endpoints extract org_id from request context
- Workspace file queries use `(path, org_id)` with cascade for reads

### Phase Ordering Rationale

- **Database-first approach:** Schema changes are foundational and backward-compatible (NULL default). Safe to deploy early.
- **Cache before import:** Virtual import reads from cache, so cache keys must be scoped first. Prevents Pitfall #1 (collision).
- **Read before write:** Import system (read path) must work for testing before write path is updated. Enables incremental validation.
- **Sync last:** GitHub sync is the origin of modules, but write path integration can happen after import works. Lowest priority for core functionality.

**Dependency chain:**
```
Phase 1 (Schema) → Phase 2 (Cache) → Phase 3 (Import) → Phase 4 (Sync)
       ↓                    ↓                  ↓                 ↓
    Database          Redis keys      Virtual import    Write path
    org_id column     scoped          cascade lookup    propagates org_id
```

### Research Flags

**Phases with standard patterns (skip research-phase):**
- **Phase 1:** Database schema — well-documented FK + index pattern, used throughout Bifrost
- **Phase 2:** Redis caching — simple key namespace change, existing module cache provides blueprint
- **Phase 3:** Virtual import — clear integration point, thread-local pattern established
- **Phase 4:** File operations — API endpoints already use auth context, straightforward propagation

**No phases require additional research.** All patterns exist in the codebase and are thoroughly documented in ARCHITECTURE.md. Implementation can proceed directly to execution.

**Testing emphasis:**
- Multi-org integration tests are critical for catching context leakage and cascade bugs
- Test sequential execution with different orgs (Pitfall #9)
- Test cache warming with mixed global and org-scoped modules (Pitfall #5)
- Test NULL handling in all query paths (Pitfall #7)

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | **HIGH** | Zero new dependencies; all patterns verified in codebase |
| Features | **HIGH** | Table stakes derived from existing OrgScopedRepository behavior; anti-features identified from industry patterns |
| Architecture | **HIGH** | Data flow fully traced from GitHub sync to worker execution; integration points clear |
| Pitfalls | **HIGH** | Critical pitfalls identified from codebase analysis (cache keys, unique constraints, context propagation) |

**Overall confidence:** **HIGH**

All research findings are grounded in existing codebase patterns. STACK.md verified zero new dependencies required. ARCHITECTURE.md traced the complete module pipeline and identified all integration points. PITFALLS.md analyzed concrete code locations where issues will arise (cache key format, unique constraints, import hook signatures). FEATURES.md correctly classified table stakes vs differentiators based on consistency with existing entities.

### Gaps to Address

**Minor gaps (resolvable during implementation):**

- **NULL handling consistency**: Pattern exists in `OrgScopedRepository` but needs explicit helper function (`scope_query(query, org_id)`) to ensure consistency. Decision: create in Phase 1, use throughout.

- **Cache warming strategy**: Should all org modules be warmed on startup, or lazy-load less-used orgs? Decision: warm all in Phase 2 for consistency, optimize later if performance issue.

- **sys.modules cleanup**: Should imported modules be cleared from `sys.modules` between worker executions to prevent stale code? Decision: test in Phase 3, implement cleanup if multi-execution tests show contamination.

**No blocking gaps.** All questions have clear decision paths that can be resolved during phase planning without additional research.

## Sources

### Primary (HIGH confidence)
- `/home/jack/GitHub/bifrost/api/src/repositories/org_scoped.py` — OrgScopedRepository cascade pattern (lines 159-176)
- `/home/jack/GitHub/bifrost/api/src/core/module_cache.py` — Redis module cache implementation
- `/home/jack/GitHub/bifrost/api/src/services/execution/virtual_import.py` — Virtual import hook (line 324 shows path-only lookup)
- `/home/jack/GitHub/bifrost/api/src/models/orm/workspace.py` — workspace_files schema (unique constraint)
- `/home/jack/GitHub/bifrost/api/src/services/file_storage/file_ops.py` — Module write path (line 312)
- `/home/jack/GitHub/bifrost/docs/plans/2026-01-15-org-scoped-repository-design.md` — Cascade scoping design doc
- `/home/jack/GitHub/bifrost/.planning/PROJECT.md` — Project constraints and context

### Secondary (MEDIUM confidence)
- [Multi-Tenant SaaS Architecture Guide 2026](https://www.clickittech.com/saas/multi-tenant-architecture/) — Tenant isolation patterns
- [AWS Multi-Tenant SaaS Systems](https://aws.amazon.com/blogs/architecture/lets-architect-building-multi-tenant-saas-systems/) — Schema design patterns
- [Python Import System Documentation](https://docs.python.org/3/reference/import.html) — MetaPathFinder behavior
- [Multi-Tenant Database Architecture Patterns](https://www.bytebase.com/blog/multi-tenant-database-architecture-patterns-explained/) — Schema migration patterns
- [Azure Cache for Redis Multitenancy](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/cache-redis) — Cache isolation strategies

### Tertiary (LOW confidence)
- [Citus Multi-Tenant Migration](https://docs.citusdata.com/en/v7.4/develop/migration_mt_schema.html) — Primary key modification strategies (validated against Bifrost schema)
- [Django Multi-Tenant Pitfalls](https://books.agiliq.com/projects/django-multi-tenant/en/latest/) — Context propagation issues (thread-local pattern confirmed in Bifrost)

---
*Research completed: 2026-02-02*
*Ready for roadmap: yes*
