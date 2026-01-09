# Cross-Organization Workflow Validation & App Workflow Allowlists

## Problem Statement

1. `cross_org_validation.py` exists but is **never used** - forms, agents, and apps can reference workflows from any organization
2. Apps store workflow IDs in scattered JSON (data_sources, component props) with zero validation
3. Changing a workflow's scope can silently orphan references in forms/apps/agents
4. No tests exist for cross-organization workflow access scenarios

## Solution Overview

1. **Integrate cross-org validation** into forms, agents, and apps at mutation time
2. **Create `app_workflows` table** as explicit allowlist (not JSON)
3. **Create `app_page_workflows` table** for per-page restriction to subset
4. **Validate JSON workflow refs** against allowlist at save time
5. **Block workflow scope changes** that would orphan references
6. **Simplify execution authorization** - use allowlist tables instead of JSON extraction

---

## Current Authorization Model (To Be Replaced)

### How It Works Today

1. **`workflow_access` table** - Precomputed authorization lookup
   - Stores: `(workflow_id, entity_type, entity_id, access_level, organization_id)`
   - Populated at **publish time** by extracting workflow IDs from JSON

2. **JSON Extraction at Publish** (`sync_app_workflow_access()`)
   - Recursively searches `page.data_sources`, `component.props`, `loading_workflows`
   - Fragile: easy to miss new JSON fields where workflows are stored
   - No validation: any workflow ID in JSON gets synced

3. **Execution Check** (`ExecutionAuthService.can_execute_workflow()`)
   - Checks `workflow_access` table for user's org + access level
   - Fast O(1) indexed lookup

### Problems with Current Model

- **No upfront validation** - Any workflow ID can be added to JSON
- **Extraction is fragile** - Must update extraction logic for each new JSON field
- **No explicit allowlist** - No way to restrict which workflows an app can use
- **Cross-org not enforced** - Org A's app can reference Org B's workflows

---

## New Authorization Model

### Design Principles

1. **Explicit allowlist** - `app_workflows` is the source of truth for what an app CAN use
2. **Allowlist drives authorization** - Sync `workflow_access` FROM `app_workflows` (not JSON extraction)
3. **Validation at edit time** - Reject JSON refs to workflows not in allowlist
4. **Simplified publish** - No more recursive JSON extraction

### Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ CONFIGURATION TIME (App Settings)                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ Admin adds workflows to app_workflows table via UI/API                      │
│ Cross-org validation: Can only add workflows from same org or global        │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ EDIT TIME (Page/Component Editing)                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ UI dropdowns filter to only show workflows in app_workflows                 │
│ Backend validation rejects save if workflow_id not in allowlist             │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ PUBLISH TIME                                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│ OLD: Extract workflow IDs from JSON, sync to workflow_access                │
│ NEW: Sync workflow_access directly FROM app_workflows table                 │
│      (No JSON extraction needed - allowlist IS the list)                    │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ EXECUTION TIME (Unchanged)                                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│ ExecutionAuthService.can_execute_workflow() checks workflow_access table    │
│ Fast O(1) lookup with org scoping and role-based access                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Changes to `sync_app_workflow_access()`

**Before:**
```python
async def sync_app_workflow_access(db, app):
    # Extract from JSON (fragile)
    workflow_ids = extract_app_workflows(app)  # Recursive JSON search
    await sync_workflow_access(db, "app", app.id, workflow_ids, ...)
```

**After:**
```python
async def sync_app_workflow_access(db, app):
    # Read from allowlist table (explicit)
    result = await db.execute(
        select(AppWorkflow.workflow_id).where(AppWorkflow.application_id == app.id)
    )
    workflow_ids = [row.workflow_id for row in result.all()]
    await sync_workflow_access(db, "app", app.id, workflow_ids, ...)
```

### Benefits

| Aspect | Old Model | New Model |
|--------|-----------|-----------|
| Workflow discovery | JSON extraction (fragile) | Explicit allowlist table |
| Validation | None | At edit time + cross-org |
| UI filtering | None (shows all) | Filters to allowlist |
| New JSON fields | Must update extraction | No changes needed |
| Debugging | Hunt through JSON | Query allowlist table |

---

## Implementation Checklist

### Phase 1: Database Schema
- [ ] Create migration: `cd api && ../venv/bin/alembic revision -m "add_app_workflow_allowlists"`
- [ ] Create `api/src/models/orm/app_workflows.py` with `AppWorkflow` and `AppPageWorkflow` ORM models
- [ ] Export new models in `api/src/models/orm/__init__.py`
- [ ] Run migration and verify tables created

### Phase 2: Backend API - App Workflow Allowlist
- [ ] Add `allowed_workflow_ids` field to `ApplicationUpdateRequest` in `api/src/models/contracts/applications.py`
- [ ] Add `allowed_workflows` response field to `ApplicationPublic` model
- [ ] Update `api/src/routers/applications.py` PATCH handler to sync allowlist
- [ ] Add `allowed_workflow_ids` field to `AppPageUpdate` model
- [ ] Add `allowed_workflows` response field to page models
- [ ] Update `api/src/routers/app_pages.py` PATCH handler to sync page restrictions

### Phase 2b: Frontend UI for Allowlist Management
- [ ] Create `client/src/components/forms/MultiWorkflowSelector.tsx` (checkbox pattern like role selection)
- [ ] Add Workflow Allowlist Card to `client/src/pages/ApplicationEditor.tsx` Settings tab (~line 1173)
- [ ] Add page-level workflow restriction UI to `client/src/components/app-builder/editor/PropertyEditor.tsx` (~line 500)
- [ ] Update `client/src/hooks/useApplications.ts` mutation to include `allowed_workflow_ids`
- [ ] Regenerate types: `cd client && npm run generate:types`

### Phase 3: Cross-Org Validation Integration
- [ ] Update `_validate_form_references()` in `api/src/routers/forms.py` to call `validate_workflow_reference()`
- [ ] Update `_validate_agent_references()` in `api/src/routers/agents.py` to call `validate_workflow_reference()`
- [ ] Create `api/src/services/app_workflow_validation.py` with `validate_page_workflow_references()`
- [ ] Integrate validation into `api/src/routers/app_pages.py` create/update endpoints
- [ ] Integrate validation into `api/src/routers/app_components.py` create/update endpoints

### Phase 3b: Authorization Model Changes
- [ ] Update `sync_app_workflow_access()` in `api/src/services/workflow_access_service.py`:
  - [ ] Change to read from `app_workflows` table instead of JSON extraction
  - [ ] Remove call to `extract_app_workflows()` (no longer needed)
- [ ] Deprecate/remove `extract_app_workflows()` function (keep for reference temporarily)
- [ ] Update `api/src/routers/applications.py` publish endpoint to use new sync logic
- [ ] Verify `ExecutionAuthService.can_execute_workflow()` still works (should be unchanged)
- [ ] Test execution authorization with new model

### Phase 4: Workflow Scope Change Protection
- [ ] Create `api/src/services/workflow_impact_service.py` with `WorkflowImpactService`
- [ ] Update `api/src/routers/workflows.py` PATCH endpoint to check for orphans before scope change
- [ ] Return 409 Conflict with impact report when scope change would break references

### Phase 5: MCP Tools Updates
- [ ] Update `api/src/services/mcp_server/tools/apps.py`:
  - [ ] Add `allowed_workflow_ids` param to `create_app`
  - [ ] Add `allowed_workflow_ids` param to `update_app`
  - [ ] Include `allowed_workflows` in `get_app` response
- [ ] Update `api/src/services/mcp_server/tools/pages.py`:
  - [ ] Add `allowed_workflow_ids` param to `create_page`
  - [ ] Validate `launch_workflow_id` against app allowlist
- [ ] Update `api/src/services/mcp_server/tools/components.py`:
  - [ ] Validate workflow refs in props against page/app allowlist

### Phase 6: Documentation Updates
- [ ] Update `get_app_schema()` in MCP tools to document `allowed_workflows`
- [ ] Update bifrost-docs App Builder guide with workflow allowlist feature
- [ ] Update bifrost-docs API reference

### Phase 7: Tests
- [ ] Create `tests/unit/services/test_cross_org_validation.py`
- [ ] Create `tests/unit/services/test_app_workflow_service.py`
- [ ] Create `tests/unit/services/test_workflow_impact_service.py`
- [ ] Create `tests/integration/test_forms_cross_org.py`
- [ ] Create `tests/integration/test_app_workflows.py`
- [ ] Create `tests/integration/test_workflow_scope_changes.py`

### Final Steps
- [ ] Run `pyright` - verify type checking passes
- [ ] Run `ruff check .` - verify linting passes
- [ ] Run `./test.sh` - verify all tests pass
- [ ] Run `cd client && npm run tsc` - verify frontend type checking
- [ ] Run `cd client && npm run lint` - verify frontend linting

---

## Technical Details

### Database Schema

**`app_workflows`** - App-level workflow allowlist
```sql
CREATE TABLE app_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id UUID NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    added_by VARCHAR(255),
    added_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (application_id, workflow_id)
);
```

**`app_page_workflows`** - Per-page restriction (subset of app allowlist)
```sql
CREATE TABLE app_page_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id UUID NOT NULL REFERENCES app_pages(id) ON DELETE CASCADE,
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    added_by VARCHAR(255),
    added_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (page_id, workflow_id)
);
```

### API Changes

**App-level allowlist** - via existing `PATCH /api/applications/{app_id}`:
```json
{
    "allowed_workflow_ids": ["uuid-1", "uuid-2", ...]
}
```

**Page-level restriction** - via existing `PATCH /api/applications/{app_id}/pages/{page_id}`:
```json
{
    "allowed_workflow_ids": ["uuid-1", ...]
}
```

### Frontend Behavior

App Builder workflow dropdowns automatically filter to show only:
1. Workflows in app's `allowed_workflows` list
2. Further filtered by page's `allowed_workflows` if configured

---

## Files to Modify

| File | Changes |
|------|---------|
| `api/src/services/cross_org_validation.py` | Already done - just import/use it |
| `api/src/services/workflow_access_service.py` | **Key change**: Read from `app_workflows` instead of JSON extraction |
| `api/src/services/execution_auth.py` | Verify unchanged (should still work) |
| `api/src/routers/forms.py` | Add cross-org validation call |
| `api/src/routers/agents.py` | Add cross-org validation call |
| `api/src/routers/workflows.py` | Add scope change protection |
| `api/src/routers/applications.py` | Add `allowed_workflow_ids` to PATCH, update publish |
| `api/src/routers/app_pages.py` | Add `allowed_workflow_ids` + validation |
| `api/src/routers/app_components.py` | Add validation call |
| `api/src/models/orm/app_workflows.py` | New - ORM models |
| `api/src/models/contracts/applications.py` | Add allowlist contracts |
| `api/src/services/app_workflow_validation.py` | New - validation service |
| `api/src/services/workflow_impact_service.py` | New - impact detection |
| `api/src/services/mcp_server/tools/apps.py` | Add allowlist to create/update/get |
| `api/src/services/mcp_server/tools/pages.py` | Validate against allowlist |
| `api/src/services/mcp_server/tools/components.py` | Validate against allowlist |
| `api/alembic/versions/*_add_app_workflow_allowlists.py` | New - migration |
| `client/src/pages/ApplicationEditor.tsx` | Add Workflow Allowlist Card |
| `client/src/components/app-builder/editor/PropertyEditor.tsx` | Add page-level restriction UI |
| `client/src/components/forms/MultiWorkflowSelector.tsx` | New - multi-select picker |
| `client/src/hooks/useApplications.ts` | Update mutation for allowlist |
