# Feature Landscape: Multi-Tenant Module Scoping

**Domain:** Multi-tenant code execution platform with customizable modules
**Researched:** 2026-02-02
**Confidence:** MEDIUM (patterns derived from existing Bifrost architecture + industry research)

## Table Stakes

Features users expect. Missing = product feels incomplete or broken.

| Feature | Why Expected | Complexity | Existing Support | Notes |
|---------|--------------|------------|------------------|-------|
| **Cascade Resolution** | Standard multi-tenant pattern: org-specific overrides global | Low | OrgScopedRepository pattern exists | Must apply to modules |
| **Backward Compatibility** | Existing global modules must continue working | Low | NULL org_id = global | No breaking changes |
| **Same Import Syntax** | `from shared import halopsa` works unchanged | Low | Virtual import hook exists | Module resolution changes, not import syntax |
| **Consistent Behavior** | Same cascade logic as workflows/forms/apps | Low | Pattern documented | Follow existing OrgScopedRepository |
| **Isolation Between Orgs** | Org A cannot see Org B's modules | Low | Standard multi-tenant | Redis key namespacing |
| **Hot Reload** | Module changes reflect immediately | Medium | Redis cache invalidation exists | Extend to org-scoped keys |
| **Traceback Clarity** | Errors show which module (global vs org) | Low | __file__ already set | May need org context in path |

## Differentiators

Features that set product apart. Not expected, but valued.

| Feature | Value Proposition | Complexity | Existing Support | Notes |
|---------|-------------------|------------|------------------|-------|
| **Transparent Override** | User writes `halopsa.py`, it "just works" for their org | Low | Virtual import hook | Key UX differentiator |
| **No Explicit Registration** | Module exists = module available (no config) | Low | Entity-type detection | Convention over configuration |
| **Override Visibility** | UI shows which modules are overridden | Medium | None | Helps debugging |
| **Diff View** | Compare org version to global version | High | None | Power user feature |
| **Version Pinning** | Org can pin to specific global version | High | None | Enterprise feature |
| **Partial Override** | Override single function, inherit rest | Very High | None | Complex, likely anti-feature |

## Anti-Features

Features to explicitly NOT build. Common mistakes in this domain.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Explicit Module Registration** | Adds friction, diverges from Python semantics | Auto-detect from file extension and path |
| **Complex Inheritance Chains** | Org A inherits from Org B which inherits from global - nightmare | Single level only: org OR global |
| **Partial Function Override** | "Import foo from global, override bar()" - fragile, hard to reason about | Override entire module or nothing |
| **Cross-Org Module Sharing** | Org A shares module with Org B - security/isolation nightmare | Global is the only sharing mechanism |
| **Module Marketplace** | User-submitted modules for other orgs - support burden, security risk | Curated global modules only |
| **Dynamic Cascade Rules** | Per-module cascade config - complexity explosion | Single consistent cascade: org > global |
| **Module Dependencies Graph** | Track which modules import which - analysis paralysis | Trust Python's import system |
| **Automatic Version Migration** | Auto-upgrade org modules when global changes - breaking change risk | Org modules are independent copies |

## Feature Dependencies

```
Existing Infrastructure (Already Built)
    |
    +-- Virtual Import Hook (virtual_import.py)
    |       |
    |       +-- Redis Module Cache (module_cache.py)
    |               |
    |               +-- Module Index (bifrost:module:index)
    |
    +-- OrgScopedRepository Pattern
    |       |
    |       +-- Cascade Get (org first, global fallback)
    |
    +-- WorkspaceFile Model (entity_type='module')

New Features (To Build)
    |
    +-- [TABLE STAKES] Add org_id to workspace_files
    |       |
    |       +-- [TABLE STAKES] Migration for existing data (NULL = global)
    |
    +-- [TABLE STAKES] Org-Scoped Redis Keys
    |       |
    |       +-- bifrost:module:{org_id}:{path} OR bifrost:module:global:{path}
    |       |
    |       +-- [TABLE STAKES] Virtual Import Hook Cascade
    |               |
    |               +-- Try org-specific key first
    |               +-- Fall back to global key
    |
    +-- [TABLE STAKES] GitHub Sync One-Scope-Per-Repo
    |       |
    |       +-- Repo config declares org_id OR global
    |       +-- Enforce at sync time
    |
    +-- [DIFFERENTIATOR] Override Visibility UI
            |
            +-- List view shows (override) badge
            +-- Detail view shows "Overrides global: halopsa.py"
```

## Cascade Resolution Specification

This is the core algorithm that makes multi-tenant modules work.

### Resolution Order

```
When workflow imports `from shared import halopsa`:

1. Virtual Import Hook intercepts
2. Convert module name to path: "shared/halopsa.py"
3. Get current execution context org_id
4. Try: Redis GET bifrost:module:{org_id}:shared/halopsa.py
   - If found: Load org-specific module, DONE
5. Try: Redis GET bifrost:module:global:shared/halopsa.py
   - If found: Load global module, DONE
6. Return None (let filesystem finder try)
```

### Edge Cases

| Scenario | Expected Behavior | Notes |
|----------|-------------------|-------|
| Org module exists, global does not | Load org module | Normal override case |
| Global exists, org does not | Load global | Normal inheritance case |
| Both exist | Load org (wins) | Override semantics |
| Neither exists | ImportError | Same as today |
| Workflow execution context missing org_id | Load global only | Fallback for edge cases |
| Module imports another module | Cascade applies at each import | Recursive resolution |

### Context Propagation

The org_id must be available during import. Options:

| Approach | Complexity | Notes |
|----------|------------|-------|
| Thread-local context | Low | Already used for recursion guard |
| ContextVar | Low | Standard Python pattern |
| Pass through execution context | Medium | Requires plumbing |

**Recommendation:** Use thread-local (matches existing `_thread_local.in_find_spec` pattern).

## MVP Recommendation

For MVP, prioritize:

1. **Add org_id to workspace_files** - Database schema change
2. **Org-scoped Redis keys** - Cache layer change
3. **Virtual import cascade** - Import hook change
4. **GitHub sync scope enforcement** - One repo = one scope

Defer to post-MVP:

- **Override visibility UI**: Nice UX but not blocking functionality
- **Diff view**: Power user feature, low priority
- **Version pinning**: Enterprise feature, adds significant complexity

## Complexity Budget

| Phase | Features | Estimated Effort |
|-------|----------|------------------|
| Phase 1: Database | org_id column, migration | 0.5 days |
| Phase 2: Cache | Org-scoped Redis keys | 0.5 days |
| Phase 3: Import Hook | Cascade resolution | 1 day |
| Phase 4: GitHub Sync | One-scope-per-repo | 0.5 days |
| Phase 5: Testing | Integration tests for cascade | 1 day |
| **Total MVP** | | **3.5 days** |
| Post-MVP: UI | Override visibility | 1 day |
| Post-MVP: Diff | Compare versions | 2 days |

## Open Questions

1. **How does org_id get into the import context?**
   - Worker subprocess needs to know execution org
   - Currently passed via execution_context
   - Need to set thread-local before imports

2. **What happens if module changes mid-execution?**
   - Python caches modules in sys.modules
   - Cache invalidation only affects next execution
   - Acceptable behavior (matches expectations)

3. **Should org modules be visible in global org's file tree?**
   - Probably not (privacy)
   - Global admin can query DB directly if needed

4. **How to handle module deletion?**
   - Delete from DB and Redis
   - Next import falls through to global (or ImportError)
   - Existing running workflows unaffected (in memory)

## Sources

**Existing Codebase (HIGH confidence):**
- `/api/src/services/execution/virtual_import.py` - Virtual import hook implementation
- `/api/src/core/module_cache.py` - Redis module cache
- `/api/src/repositories/org_scoped.py` - OrgScopedRepository cascade pattern
- `/docs/plans/2026-01-15-org-scoped-repository-design.md` - Cascade scoping design
- `/docs/plans/2026-01-15-cascade-scoping-fixes.md` - Cascade resolution patterns

**Industry Research (MEDIUM confidence):**
- [Python Import System Documentation](https://docs.python.org/3/reference/import.html)
- [Multi-Tenant SaaS Architecture Guide 2026](https://www.clickittech.com/saas/multi-tenant-architecture/)
- [WorkOS Developer Guide to Multi-Tenant Architecture](https://workos.com/blog/developers-guide-saas-multi-tenant-architecture)
- [AWS Multi-Tenant SaaS Systems](https://aws.amazon.com/blogs/architecture/lets-architect-building-multi-tenant-saas-systems/)
- [InfoQ Tenant Isolation in Python](https://www.infoq.com/articles/serverless-tenant-isolation/)
