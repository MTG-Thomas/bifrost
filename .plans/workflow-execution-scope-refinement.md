# Workflow Execution Scope Refinement

## Status: PHASE 1 INCOMPLETE - Test Infrastructure Ready

## Summary

Simplify the execution engine's scope resolution to follow these clear rules:
1. **Explicit scope** - If SDK call passes `scope` parameter, use it (developer intent)
2. **Global workflows** - Use caller's org (caller context matters for multi-tenant logic)
3. **Org-scoped workflows** - Use workflow's org (workflow owns the context)

## Background

The current implementation always uses the caller's org first, with the workflow's org only as a fallback when the caller has no org. This means when a platform admin executes a client's workflow, it runs under the Platform org instead of the client's org.

**Current (incorrect) - Still in place:**
```python
# workflow_execution.py:514-520
workflow_org_id = workflow_data.get("organization_id")
if org_id is None and workflow_org_id:
    org_id = workflow_org_id  # Only fallback when caller has no org
```

**Desired:**
- Org-scoped workflow → always use workflow's org
- Global workflow → always use caller's org

---

## Tasks

### Phase 1: Core Scope Resolution - ❌ NOT COMPLETE

- [ ] **Modify scope resolution logic in consumer**
  - File: `api/src/jobs/consumers/workflow_execution.py` (lines 514-520)
  - Replace fallback logic with new rule-based resolution:
    ```python
    if workflow_org_id:
        org_id = workflow_org_id  # Org-scoped: always use workflow's org
    else:
        org_id = caller_org_id    # Global: use caller's org
    ```
  - Add logging to indicate which scope was chosen and why

- [ ] **Update Redis pending execution with resolved scope**
  - After scope resolution, call `update_pending_execution` with resolved `org_id`
  - This ensures result handlers have correct scope context

### Phase 2: Test Fixtures ✅ COMPLETE

- [x] **Create scoped test resources**
  - File: `api/tests/e2e/api/test_scope_execution.py`
  - Tables: `e2e_scope_test_table` with org1, org2, and global data
  - Config: `e2e_scope_test_config` key in org1, org2, and global scopes
  - Knowledge: `e2e_scope_test_namespace` with org1, org2, and global documents
  - Each with unique `scope_marker` field: `"org1"`, `"org2"`, or `"global"`

- [x] **Create scope test workflow fixtures**
  - `comprehensive_scope_workflow` - org-scoped workflow (org1) that tests all SDK modules
  - `global_comprehensive_workflow` - global workflow that tests all SDK modules
  - `scope_override_workflow` - org1 workflow that explicitly overrides scope to org2

### Phase 3: Integration Tests ✅ COMPLETE

- [x] **Create `api/tests/integration/engine/test_sdk_scoping.py`** (351 lines)
  - TestScopeResolution: 6 test cases covering context creation
  - TestScopeResolutionInConsumer: 3 test cases covering logic
  - TestContextPropertyAccessors: 4 test cases
  - TestScopeResolutionFunction: 5 test cases
  - Total: 18 test cases covering all scope scenarios

Test Matrix Covered:
| Test Case | Workflow Scope | Caller | Expected SDK Scope |
|-----------|---------------|--------|-------------------|
| Org workflow + org user (same org) | Org A | User in Org A | Org A |
| Org workflow + superuser (different org) | Org A | Superuser in Platform | Org A |
| Org workflow + superuser (no org) | Org A | Superuser, no org | Org A |
| Global workflow + org user | Global | User in Org A | Org A |
| Global workflow + superuser (with org) | Global | Superuser in Org B | Org B |
| Global workflow + superuser (no org) | Global | Superuser, no org | GLOBAL |

### Phase 4: Authorization Tests ✅ COMPLETE

- [x] **Authorization tests comprehensive**
  - File: `api/tests/unit/services/test_execution_auth.py` (470 lines)
  - 10 test classes, 24 test cases total
  - Covers: platform admin, API key, workflow access, org scoping, access levels, entity types

### Phase 5: E2E Verification Tests ✅ COMPLETE

- [x] **E2E tests created**
  - File: `api/tests/e2e/api/test_scope_execution.py` (1076 lines)
  - TestComprehensiveSdkScoping: 2 tests
  - TestExplicitScopeOverride: 1 test
  - Verifies tables.query(), tables.count(), config.get(), config.set(), knowledge.search(), knowledge.store()

---

## Critical Finding

**The test infrastructure is 100% complete and comprehensive, but the core implementation (Phase 1) has NOT been updated.**

The consumer code at lines 514-520 still uses the old fallback logic that only applies workflow's org when the caller has no org.

**Next Step:** Implement Phase 1 to make the code match the test specifications.

---

## Files

| File | Phase | Status |
|------|-------|--------|
| `api/src/jobs/consumers/workflow_execution.py` | 1 | ❌ Needs update |
| `api/tests/e2e/api/test_scope_execution.py` | 2, 5 | ✅ Complete |
| `api/tests/integration/engine/test_sdk_scoping.py` | 3 | ✅ Complete |
| `api/tests/unit/services/test_execution_auth.py` | 4 | ✅ Complete |
