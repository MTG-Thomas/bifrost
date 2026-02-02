# Architecture: Organization-Scoped Modules

**Domain:** Multi-tenant module scoping for MSP automation platform
**Researched:** 2026-02-02
**Confidence:** HIGH (based on codebase analysis)

## Executive Summary

Adding organization scoping to modules requires modifications across four interconnected systems: database schema, Redis caching, virtual import, and GitHub sync. The existing `OrgScopedRepository` pattern provides a proven cascade mechanism (org-specific first, global fallback) that should be extended to modules.

The key insight is that modules flow through a pipeline:
1. **GitHub Sync** writes module to `workspace_files` table
2. **File Storage Service** detects module type and caches in Redis
3. **Virtual Importer** loads modules from Redis during worker execution

Currently, none of these steps have org context. The architecture must add `org_id` at each stage while maintaining backward compatibility with existing global modules (NULL org_id).

## Current Architecture

### Data Flow (Current)

```
GitHub Sync ──> workspace_files (path as unique key)
                      │
                      ▼
              Redis Cache: bifrost:module:{path}
                      │
                      ▼
              Virtual Import (MetaPathFinder)
                      │
                      ▼
              Worker Execution
```

### Component Details

| Component | File | Current Behavior |
|-----------|------|-----------------|
| workspace_files table | `api/src/models/orm/workspace.py` | No org_id column, path is unique |
| Module cache | `api/src/core/module_cache.py` | Keys: `bifrost:module:{path}`, index: `bifrost:module:index` |
| Virtual import | `api/src/services/execution/virtual_import.py` | Looks up by path only |
| File storage | `api/src/services/file_storage/file_ops.py` | Writes to cache after DB upsert |
| GitHub sync | `api/src/services/github_sync.py` | No org awareness |

### Key Code Paths

**Module Write Path:**
```python
# file_ops.py line 311-313
if platform_entity_type == "module" and inline_content:
    await set_module(path, inline_content, content_hash)
```

**Module Read Path (virtual import):**
```python
# module_cache_sync.py line 49-77
def get_module_sync(path: str) -> CachedModule | None:
    key = f"{MODULE_KEY_PREFIX}{path}"
    data = client.get(key)
    # No org context available
```

**Worker Setup:**
```python
# worker.py line 30-35
from src.services.execution.virtual_import import install_virtual_import_hook
install_virtual_import_hook()
# Hook installed at import time, no org context
```

## Recommended Architecture

### Data Flow (Proposed)

```
GitHub Sync ──> workspace_files (org_id + path as unique key)
  │                   │
  │                   ▼
  │           Redis Cache: bifrost:module:{org_id}:{path}
  │                   │              │
  │                   │              └── bifrost:module:global:{path}
  │                   ▼
  │           Virtual Import with Org Context
  │                   │
  │                   ▼
  └────────── Worker Execution (knows org_id from context)
```

### Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `workspace_files` table | Store module content with org scope | File storage, reindex |
| `ModuleCache` (Redis) | Org-prefixed caching with TTL | Virtual import, file ops |
| `VirtualModuleFinder` | Cascade lookup: org -> global | Redis cache only |
| `WorkerContext` | Propagate org_id to import system | Virtual import |
| `GitHubSyncService` | Determine org scope from repo config | workspace_files |

### Schema Changes

**workspace_files table:**
```sql
-- Add organization_id column
ALTER TABLE workspace_files
ADD COLUMN organization_id UUID REFERENCES organizations(id);

-- Replace unique constraint
ALTER TABLE workspace_files
DROP CONSTRAINT uq_workspace_files_path;

ALTER TABLE workspace_files
ADD CONSTRAINT uq_workspace_files_org_path
UNIQUE (organization_id, path);

-- Add index for cascade queries
CREATE INDEX ix_workspace_files_org_path
ON workspace_files (organization_id, path)
WHERE NOT is_deleted;
```

**Rationale:** Follow existing pattern from workflows, forms, agents, apps tables. NULL org_id = global module.

### Redis Key Structure

**Current:**
```
bifrost:module:{path}                    # Module content
bifrost:module:index                     # Set of all paths
```

**Proposed:**
```
bifrost:module:{org_id}:{path}           # Org-specific module
bifrost:module:global:{path}             # Global module (org_id=NULL)
bifrost:module:index:{org_id}            # Set of paths for org
bifrost:module:index:global              # Set of global paths
```

**Example:**
```
bifrost:module:abc-123-def:shared/halopsa.py  # Org's custom version
bifrost:module:global:shared/halopsa.py       # Global fallback
```

### Virtual Import Cascade

The virtual importer needs to know the current org context to perform cascade lookups:

```python
# Pseudocode for cascade lookup
def find_module(path: str, org_id: str | None) -> CachedModule | None:
    # Step 1: Try org-specific
    if org_id:
        module = redis.get(f"bifrost:module:{org_id}:{path}")
        if module:
            return module

    # Step 2: Fall back to global
    return redis.get(f"bifrost:module:global:{path}")
```

**Context Propagation:**
Worker receives org_id in execution context. This must be made available to the import hook. Options:

1. **Thread-local storage** (recommended): Set org_id in thread-local before execution, importer reads it
2. **Environment variable**: Set `BIFROST_ORG_ID` before worker runs
3. **Module-level global**: Less clean but simple

Thread-local is recommended because:
- Workers are single-threaded per execution
- No cross-contamination between executions
- Pattern already used in import restrictor (`_thread_local.in_find_spec`)

### Integration Points

#### 1. GitHub Sync -> workspace_files

**Current:** `GitHubSyncService` writes files without org context
**Change:** Repo must be associated with an org; sync uses that org_id

```python
# github_sync.py changes
class GitHubSyncService:
    def __init__(self, ..., org_id: UUID | None = None):
        self.org_id = org_id  # From repo configuration

    async def _write_file(self, path: str, content: bytes):
        # Pass org_id to file storage
        await file_storage.write_file(path, content, org_id=self.org_id)
```

**Repo Scoping:** Each GitHub repo should be associated with exactly one scope (org or global). This prevents path collisions and simplifies sync logic.

#### 2. File Storage -> Redis Cache

**Current:** `file_ops.py` calls `set_module(path, content, hash)`
**Change:** Include org_id in cache key

```python
# module_cache.py changes
async def set_module(path: str, content: str, content_hash: str, org_id: UUID | None = None) -> None:
    if org_id:
        key = f"{MODULE_KEY_PREFIX}{org_id}:{path}"
        index_key = f"{MODULE_INDEX_KEY}:{org_id}"
    else:
        key = f"{MODULE_KEY_PREFIX}global:{path}"
        index_key = f"{MODULE_INDEX_KEY}:global"

    await redis.setex(key, 86400, json.dumps(cached))
    await redis.sadd(index_key, path)
```

#### 3. Worker Context -> Virtual Import

**Current:** `install_virtual_import_hook()` called at module load time with no context
**Change:** Set org context before execution starts

```python
# worker.py changes
async def _run_execution(execution_id: str, context_data: dict[str, Any]) -> dict[str, Any]:
    # Set org context for import system
    org_data = context_data.get("organization")
    org_id = org_data["id"] if org_data else None

    set_import_org_context(org_id)  # Thread-local setter
    try:
        # ... existing execution logic
    finally:
        clear_import_org_context()
```

#### 4. Cache Warm-up

**Current:** `warm_cache_from_db()` loads all modules into flat namespace
**Change:** Load with org prefixes

```python
async def warm_cache_from_db(session=None) -> int:
    stmt = select(WorkspaceFile).where(
        WorkspaceFile.entity_type == "module",
        WorkspaceFile.is_deleted == False,
        WorkspaceFile.content.isnot(None),
    )
    result = await session.execute(stmt)
    modules = result.scalars().all()

    for module in modules:
        await set_module(
            path=module.path,
            content=module.content,
            content_hash=module.content_hash or "",
            org_id=module.organization_id,  # NEW: pass org_id
        )
```

## Patterns to Follow

### Pattern 1: Cascade Scoping (from OrgScopedRepository)

**What:** Org-specific first, global fallback
**When:** All name/path-based lookups
**Example from existing code:**
```python
# org_scoped.py lines 159-176
# Step 1: Try org-specific lookup (if we have an org)
if self.org_id is not None:
    org_query = query.where(_org_filter(self.model, self.org_id))
    result = await self.session.execute(org_query)
    entity = result.scalar_one_or_none()
    if entity:
        return entity

# Step 2: Fall back to global
global_query = query.where(_org_is_null(self.model))
result = await self.session.execute(global_query)
return result.scalar_one_or_none()
```

### Pattern 2: Thread-Local Context

**What:** Store execution context in thread-local storage
**When:** Propagating context through callstacks that don't pass context explicitly
**Example:**
```python
import threading
_org_context = threading.local()

def set_import_org_context(org_id: UUID | None) -> None:
    _org_context.org_id = org_id

def get_import_org_context() -> UUID | None:
    return getattr(_org_context, "org_id", None)
```

### Pattern 3: Dual-Key Caching

**What:** Separate cache entries for scoped vs global
**When:** Need to distinguish org-specific from global without additional lookups
**Example:**
```
bifrost:module:{org_id}:{path}  # Org's version
bifrost:module:global:{path}    # Global version
```

This avoids needing to check "is this org's or global?" after retrieving.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Single Key with Metadata

**What:** Store org_id inside the cached value, not the key
**Why bad:** Requires deserializing to check scope; can't do cascade without two full fetches
**Instead:** Include org_id in key structure for O(1) cascade check

### Anti-Pattern 2: Global Import Hook State

**What:** Store org_id in module-level global variable
**Why bad:** If worker handles multiple orgs (future), state bleeds between executions
**Instead:** Use thread-local storage, cleared after each execution

### Anti-Pattern 3: Lazy Migration

**What:** Keep old key format, gradually migrate
**Why bad:** Two code paths forever; cache coherence issues; complexity
**Instead:** Migrate all at once during startup, invalidate old keys

### Anti-Pattern 4: Org Context in Every Function Signature

**What:** Pass org_id as parameter through entire callstack
**Why bad:** Intrusive changes, error-prone, breaks import system (find_spec signature is fixed)
**Instead:** Thread-local context for import system; explicit param for DB operations

## Components: New vs Modified

### New Components

| Component | Purpose | Location |
|-----------|---------|----------|
| `ImportOrgContext` | Thread-local storage for import org context | `api/src/core/import_context.py` (new) |

### Modified Components

| Component | Changes | Risk |
|-----------|---------|------|
| `workspace_files` model | Add `organization_id` column, update constraints | LOW - additive |
| `module_cache.py` | Add org_id to keys, update set/get/invalidate | MEDIUM - key format change |
| `module_cache_sync.py` | Add org_id lookup with cascade | MEDIUM - worker-side change |
| `virtual_import.py` | Read org context, cascade lookup | MEDIUM - import system change |
| `worker.py` | Set org context before execution | LOW - small addition |
| `file_ops.py` | Pass org_id to set_module | LOW - parameter addition |
| `warm_cache_from_db` | Include org_id in cache keys | LOW - startup change |
| `github_sync.py` | Associate repo with org scope | MEDIUM - config change |

## Build Order

Based on dependencies, suggested build order:

### Phase 1: Database Foundation
1. Add `organization_id` column to `workspace_files` (migration)
2. Update unique constraint to `(organization_id, path)`
3. Add indexes for efficient cascade queries
4. Update existing rows: NULL for all (maintaining global behavior)

**Why first:** Everything depends on the schema. Safe to add with NULL default.

### Phase 2: Redis Cache Restructure
1. Create `ImportOrgContext` thread-local module
2. Update `module_cache.py` with org-prefixed keys
3. Update `module_cache_sync.py` for cascade lookup
4. Update `warm_cache_from_db` to use new keys

**Why second:** Must be ready before virtual import changes.

### Phase 3: Import System Integration
1. Update `virtual_import.py` to read org context
2. Update `worker.py` to set org context before execution
3. Update `simple_worker.py` similarly

**Why third:** Depends on cache and context infrastructure.

### Phase 4: Write Path Updates
1. Update `file_ops.py` to pass org_id to cache
2. Update `FileStorageService` to accept org_id
3. Update GitHub sync to use repo's org scope

**Why fourth:** Read path must work first for testing.

### Phase 5: API and SDK
1. Update file API endpoints to scope by request's org
2. Verify `bifrost.files` SDK works without code changes
3. Add admin endpoints for managing global modules

**Why last:** User-facing changes after core infrastructure is solid.

## Migration Strategy

### Data Migration

1. **Add column as nullable:** No data loss, existing code works
2. **Update unique constraint:** Must handle existing data
3. **Backfill org_id:** For existing modules, decide:
   - Option A: All become global (NULL) - simplest
   - Option B: Infer from associated workflows - complex

**Recommendation:** Option A. Existing modules are already global in behavior.

### Cache Migration

1. **Startup migration:** On API/worker start, `warm_cache_from_db` rewrites all keys
2. **TTL expiration:** Old keys (24hr TTL) expire naturally
3. **Clear on deploy:** Optional manual clear to avoid stale keys

### Rollback Plan

If issues arise:
1. Column addition is backward compatible (NULL default)
2. Cache keys can be cleared; startup will rewarm
3. Import context defaulting to None preserves global behavior

## Testing Strategy

### Unit Tests

1. `test_module_cache.py`: New key format, cascade behavior
2. `test_virtual_import.py`: Org context propagation, cascade lookup
3. `test_import_context.py`: Thread-local get/set/clear

### Integration Tests

1. `test_workspace_files_scoping.py`: DB queries with org_id
2. `test_module_cascade_e2e.py`: Full flow from write to import

### Manual Verification

1. Create org-specific module, verify it loads for that org
2. Create global module, verify fallback works
3. Create both, verify org-specific takes precedence

## Sources

All findings based on codebase analysis of:
- `/home/jack/GitHub/bifrost/api/src/repositories/org_scoped.py` - OrgScopedRepository pattern
- `/home/jack/GitHub/bifrost/api/src/services/execution/virtual_import.py` - Virtual import system
- `/home/jack/GitHub/bifrost/api/src/core/module_cache.py` - Async Redis caching
- `/home/jack/GitHub/bifrost/api/src/core/module_cache_sync.py` - Sync Redis for import hook
- `/home/jack/GitHub/bifrost/api/src/services/file_storage/file_ops.py` - Module write path
- `/home/jack/GitHub/bifrost/api/src/models/orm/workspace.py` - workspace_files model
- `/home/jack/GitHub/bifrost/api/src/services/execution/worker.py` - Worker execution context
- `/home/jack/GitHub/bifrost/.planning/PROJECT.md` - Project context and constraints
