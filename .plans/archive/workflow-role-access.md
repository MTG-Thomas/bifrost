# Direct Workflow-Role Access Control

> **Supersedes**: `.plans/app-builder-workflow-allowlist.md` (do not implement that plan)

## Prerequisites

### Drop `organization_id` from Roles

Roles currently have an `organization_id` column that shouldn't exist. Roles should be **globally defined** - the org scoping happens at the entity level (forms, apps, agents, workflows), not on roles themselves.

**Current state** (`api/src/models/orm/users.py`):
```python
class Role(Base):
    # ...
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None
    )
```

**Required changes:**
1. Create migration to drop `organization_id` column from `roles` table
2. Drop index `ix_roles_organization_id`
3. Update `Role` ORM model - remove `organization_id` field
4. Update any queries that filter roles by org (should be none, but verify)
5. Update role creation endpoints if they accept `organization_id`
6. Update Pydantic contracts (`RoleCreate`, `RolePublic`) to remove `organization_id`

**Files to check/update:**
- `api/src/models/orm/users.py` - ORM model
- `api/src/models/contracts/users.py` - Pydantic contracts
- `api/src/routers/roles.py` - API endpoints
- `api/src/services/authorization.py` - Any org-scoped role queries

This must be done **before** implementing workflow-role access to ensure the model is clean.

---

## Problem Statement

Current workflow authorization requires traversing entity graphs at runtime:
- User → Roles → Forms/Agents/Apps → Workflows

This is expensive to compute and fragile - every new place workflows can be referenced (form fields, app components, agent tools) requires updating the extraction logic in `workflow_access_service.py`.

### Current Implementation (To Be Replaced)

The existing system uses:

1. **`workflow_access` table** (`api/src/models/orm/workflow_access.py`)
   - Stores: `(workflow_id, entity_type, entity_id, access_level, organization_id)`
   - Populated at save/publish time by extracting workflow IDs from forms/apps

2. **`workflow_access_service.py`** (`api/src/services/workflow_access_service.py`)
   - `extract_form_workflows()` - extracts from `workflow_id`, `launch_workflow_id`, field `data_provider_id`
   - `extract_app_workflows()` - recursive JSON extraction from pages/components
   - `sync_workflow_access()` - deletes and re-inserts entries

3. **`ExecutionAuthService`** (`api/src/services/execution_auth.py`)
   - `can_execute_workflow()` - checks `workflow_access` + `form_roles`/`app_roles` intersection
   - Complex query with subqueries for role-based access

**Problems:**
- JSON extraction is fragile - must update for every new place workflows are referenced
- Authorization check still requires entity→role traversal
- No direct visibility into "which roles can execute this workflow?"

## Solution: Follow Existing Authorization Pattern

Workflows should use the **exact same authorization pattern** as forms, apps, and agents - already implemented in `AuthorizationService.can_access_entity()`.

### The Pattern (from `authorization.py`)

```python
async def can_access_entity(self, entity, entity_type) -> bool:
    # 1. Platform admins can access anything
    if self.context.is_platform_admin:
        return True

    # 2. Org scoping - entity org must match user org, or be global
    if entity_org is not None and entity_org != self.context.org_id:
        return False

    # 3. Access level check
    if access_level == "authenticated":
        return True

    if access_level == "role_based":
        return await self._check_role_access(entity.id, entity_type)
```

### What We Add

1. **`access_level` column on workflows** - `authenticated` or `role_based` (same as forms/apps/agents)
2. **`workflow_roles` junction table** - same pattern as `form_roles`, `app_roles`, `agent_roles`
3. **Add `"workflow"` to `role_configs`** in `_check_role_access()`:
   ```python
   role_configs = {
       "form": (FormRole, FormRole.form_id),
       "app": (AppRole, AppRole.app_id),
       "agent": (AgentRole, AgentRole.agent_id),
       "workflow": (WorkflowRole, WorkflowRole.workflow_id),  # NEW
   }
   ```
4. **Add `can_access_workflow()` helper** - calls `can_access_entity(workflow, "workflow")`
5. **Replace `ExecutionAuthService`** with `AuthorizationService.can_access_workflow()`

### UI Convenience Layer

The `WorkflowSelectorDialog` is just a convenience that auto-assigns the parent entity's roles to selected workflows. The actual authorization is handled identically to forms/apps/agents.

### The Mental Model

- **Roles on forms/apps/agents** = "who can see/access this thing"
- **Roles on workflows** = "who can execute this thing"
- These are **separate concerns** that the UI helps keep aligned

---

## Data Model

### New Table: `workflow_roles`

```sql
CREATE TABLE workflow_roles (
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    assigned_by VARCHAR(255),
    assigned_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (workflow_id, role_id)
);

CREATE INDEX ix_workflow_roles_role_id ON workflow_roles(role_id);
```

### Workflow Model Changes

Add `access_level` to workflows table:

```sql
ALTER TABLE workflows ADD COLUMN access_level VARCHAR(20) DEFAULT 'role_based';
-- Values: 'authenticated' (any logged-in user), 'role_based' (must have assigned role)
```

### ORM Model: `WorkflowRole`

```python
class WorkflowRole(Base):
    """Workflow-Role association table."""
    __tablename__ = "workflow_roles"

    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("workflows.id"), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    assigned_by: Mapped[str] = mapped_column(String(255))
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    workflow: Mapped["Workflow"] = relationship(back_populates="workflow_roles")
    role: Mapped["Role"] = relationship()
```

### Workflow Model Updates

```python
class Workflow(Base):
    # ... existing fields ...

    # New: Access level (authenticated = any user, role_based = check roles)
    access_level: Mapped[str] = mapped_column(String(20), default="role_based")

    # New: Relationship to roles
    workflow_roles: Mapped[list["WorkflowRole"]] = relationship(back_populates="workflow")
    roles: Mapped[list["Role"]] = relationship(
        secondary="workflow_roles",
        viewonly=True,
    )
```

---

## Auto-Assignment on Save

When a form/app/agent is saved, we automatically assign the entity's roles to all referenced workflows. This is **additive** - we never remove roles automatically.

### Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ SAVE TIME (Form/App/Agent Editor)                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Extract all workflow IDs referenced by the entity                        │
│ 2. Get the entity's assigned roles                                          │
│ 3. For each workflow, add the entity's roles (upsert, no duplicates)        │
│ 4. Show validation warnings for any mismatches                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Validation Warnings

Surface helpful warnings at save time:

> "Workflow X will now be accessible to roles [A, B, C] based on this form's configuration"

Or when there's a mismatch:

> "Form has role D, but workflow X doesn't have role D assigned. Add role D to workflow X?"

---

## Existing Workflow Selector (To Be Enhanced)

There's already a unified `WorkflowSelector` component at `client/src/components/forms/WorkflowSelector.tsx`:

**Current features:**
- Two variants: `select` (simple dropdown) and `combobox` (searchable)
- Org scope filtering via `scope` prop
- Workflow type filtering via `type` prop (`workflow`, `tool`, `data_provider`)
- Global/org badge display
- Single selection only

**Current usage locations:**
| Location | File |
|----------|------|
| Component Property Editor | `client/src/components/app-builder/editor/PropertyEditor.tsx` |
| Action Builder | `client/src/components/app-builder/editor/property-editors/ActionBuilder.tsx` |
| Table Action Builder | `client/src/components/app-builder/editor/property-editors/TableActionBuilder.tsx` |

**What needs to be added:**
1. **Role context display** - Show entity's roles alongside workflow list
2. **Role mismatch warnings** - Indicate which workflows are missing required roles
3. **Auto-assign option** - "Assign roles on select" behavior
4. **Multi-select mode** - For agent tool selection

### Enhanced Component: `WorkflowSelectorDialog`

A modal dialog wrapper around the existing selector, adding role context:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Select Workflow                                                        [X]  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────┐  ┌─────────────────────────────────────────────┐  │
│  │ ENTITY ROLES        │  │ WORKFLOWS                                   │  │
│  │                     │  │                                             │  │
│  │ ☑ Admin             │  │ ┌─────────────────────────────────────────┐ │  │
│  │ ☑ Manager           │  │ │ 📄 Process Order                       │ │  │
│  │ ☐ Viewer            │  │ │    Roles: Admin, Manager ✓             │ │  │
│  │                     │  │ └─────────────────────────────────────────┘ │  │
│  │ ─────────────────── │  │ ┌─────────────────────────────────────────┐ │  │
│  │ Pre-selected from   │  │ │ 📄 Send Notification                   │ │  │
│  │ parent entity       │  │ │    Roles: Admin ⚠️ Missing: Manager    │ │  │
│  │                     │  │ └─────────────────────────────────────────┘ │  │
│  │                     │  │ ┌─────────────────────────────────────────┐ │  │
│  │                     │  │ │ 📄 Generate Report                     │ │  │
│  │                     │  │ │    Roles: None ⚠️ Not accessible       │ │  │
│  │                     │  │ └─────────────────────────────────────────┘ │  │
│  └─────────────────────┘  └─────────────────────────────────────────────┘  │
│                                                                             │
│  ⚠️ 2 workflows have role mismatches. Auto-assign roles on save?           │
│                                                                             │
│                                    [Cancel]  [Select & Assign Roles]        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Features

1. **Left panel**: Shows roles from the parent entity (read-only, for context)
2. **Right panel**: Searchable/filterable workflow list with role status indicators
3. **Inline warnings**: Shows which workflows are missing roles
4. **Auto-assign option**: "Select & Assign Roles" button adds missing roles to workflows
5. **Single selection mode**: For fields like `workflow_id`, `launch_workflow_id`
6. **Multi-selection mode**: For fields like `tool_ids` (agent tools)

### Props

```typescript
interface WorkflowSelectorDialogProps {
  // What roles the parent entity has (for context display)
  entityRoles: Role[];

  // Filter workflows by type
  workflowType?: 'workflow' | 'tool' | 'data_provider';

  // Single vs multi-select
  mode: 'single' | 'multi';

  // Current selection
  selectedWorkflowIds: string[];

  // Callbacks
  onSelect: (workflowIds: string[], assignRoles: boolean) => void;
  onCancel: () => void;

  // Optional: org scope filter
  organizationId?: string | null;
}
```

---

## Places Requiring the Enhanced Selector

All places where workflows are selected need the new dialog with role context. Some already use `WorkflowSelector`, others use custom implementations.

### Forms

| Location | File | Field(s) | Mode | Current State |
|----------|------|----------|------|---------------|
| Form Editor | `client/src/components/forms/FormEditor.tsx` | `workflow_id`, `launch_workflow_id` | single | Needs selector |
| Form Field Editor | `client/src/components/forms/FieldEditor.tsx` | `data_provider_id` | single | Needs selector |

### Agents

| Location | File | Field(s) | Mode | Current State |
|----------|------|----------|------|---------------|
| Agent Editor | `client/src/components/agents/AgentEditor.tsx` | `tool_ids` | **multi** | Has separate tools section - needs integration |

**Note:** Agent tools have a dedicated section (not just a dropdown). The dialog needs to accommodate this - possibly a "Manage Tools" button that opens the multi-select dialog.

### Apps (App Builder)

| Location | File | Field(s) | Mode | Current State |
|----------|------|----------|------|---------------|
| App Settings | `client/src/pages/ApplicationEditor.tsx` | `launch_workflow_id` | single | Needs selector |
| Page Settings | Page properties panel | `launch_workflow_id`, `data_sources[].workflowId` | single | Needs selector |
| Property Editor | `client/src/components/app-builder/editor/PropertyEditor.tsx` | Various workflow props | single | **Already uses WorkflowSelector** |
| Action Builder | `client/src/components/app-builder/editor/property-editors/ActionBuilder.tsx` | `onClick.workflowId` | single | **Already uses WorkflowSelector** |
| Table Action Builder | `client/src/components/app-builder/editor/property-editors/TableActionBuilder.tsx` | Row/header actions | single | **Already uses WorkflowSelector** |

### Implementation Approach

Since several places already use `WorkflowSelector`, we have two options:

**Option A: Enhance existing `WorkflowSelector`**
- Add optional `entityRoles` prop
- Add optional `onSelectWithRoles` callback
- Show warning badges inline when roles provided
- Pros: Minimal changes to existing integrations
- Cons: Component gets more complex

**Option B: Create wrapper `WorkflowSelectorDialog`**
- New modal component that wraps `WorkflowSelector`
- Shows role context in left panel
- Existing simple usages stay unchanged
- Pros: Cleaner separation, existing code untouched
- Cons: Two components to maintain

**Recommendation: Option B** - Create the dialog wrapper, update integrations one by one

### Implementation Pattern

Each editor integrates the dialog the same way:

```typescript
const [workflowSelectorOpen, setWorkflowSelectorOpen] = useState(false);
const [targetField, setTargetField] = useState<string | null>(null);

const handleWorkflowSelect = (workflowIds: string[], assignRoles: boolean) => {
  // 1. Update the field with selected workflow(s)
  updateField(targetField, workflowIds);

  // 2. If assignRoles=true, the backend will auto-assign on save
  // (handled by the save mutation, not here)

  setWorkflowSelectorOpen(false);
};

// In render:
<WorkflowSelectorDialog
  entityRoles={form.roles}  // or agent.roles, app.roles
  workflowType="workflow"   // or "tool", "data_provider"
  mode="single"
  selectedWorkflowIds={form.workflow_id ? [form.workflow_id] : []}
  onSelect={handleWorkflowSelect}
  onCancel={() => setWorkflowSelectorOpen(false)}
/>
```

---

## Backend Changes

### 1. New Service: `WorkflowRoleService`

```python
class WorkflowRoleService:
    async def get_workflow_roles(self, workflow_id: UUID) -> list[Role]:
        """Get all roles assigned to a workflow."""

    async def assign_roles_to_workflow(
        self,
        workflow_id: UUID,
        role_ids: list[UUID],
        assigned_by: str
    ) -> None:
        """Add roles to a workflow (upsert, additive)."""

    async def remove_roles_from_workflow(
        self,
        workflow_id: UUID,
        role_ids: list[UUID]
    ) -> None:
        """Remove specific roles from a workflow."""

    async def sync_entity_roles_to_workflows(
        self,
        entity_type: str,  # 'form', 'agent', 'app'
        entity_id: UUID,
        workflow_ids: list[UUID],
        role_ids: list[UUID],
        assigned_by: str
    ) -> None:
        """Bulk assign entity's roles to all its workflows."""
```

### 2. Update Authorization Service

Replace complex entity traversal with simple lookup:

```python
async def can_execute_workflow(
    self,
    user_id: UUID,
    workflow_id: UUID
) -> bool:
    """Check if user can execute workflow."""
    workflow = await self.get_workflow(workflow_id)

    # Check access level first
    if workflow.access_level == "authenticated":
        return True  # Any authenticated user can execute

    # Role-based: check intersection
    user_role_ids = await self.get_user_role_ids(user_id)
    workflow_role_ids = await self.get_workflow_role_ids(workflow_id)

    return bool(set(user_role_ids) & set(workflow_role_ids))
```

### 3. Update Save Handlers

Each entity save handler calls the sync service:

```python
# In forms.py PATCH handler
async def update_form(form_id: UUID, data: FormUpdate):
    # ... existing update logic ...

    # Auto-assign roles to referenced workflows
    workflow_ids = extract_form_workflow_ids(form)  # workflow_id, launch_workflow_id, field data_providers
    role_ids = [fr.role_id for fr in form.form_roles]

    await workflow_role_service.sync_entity_roles_to_workflows(
        entity_type="form",
        entity_id=form.id,
        workflow_ids=workflow_ids,
        role_ids=role_ids,
        assigned_by=current_user.email
    )
```

### 4. New API Endpoints

```
GET  /api/workflows/{workflow_id}/roles     → List roles assigned to workflow
POST /api/workflows/{workflow_id}/roles     → Assign roles to workflow
DELETE /api/workflows/{workflow_id}/roles/{role_id}  → Remove role from workflow
```

---

## Redis Caching

### Cache Keys

```python
# Workflow roles (fast lookup at execution time)
f"bifrost:workflow:{workflow_id}:roles"  → set of role_ids

# Workflow access level
f"bifrost:workflow:{workflow_id}:access_level"  → "authenticated" | "role_based"
```

### Invalidation

Invalidate on:
- Workflow role assignment changes
- Workflow access level changes
- Role deletion (remove from all workflows)

---

## Implementation Checklist

### Phase 0: Prerequisites - Clean Up Roles Model
- [ ] Create migration to drop `organization_id` from `roles` table
- [ ] Drop index `ix_roles_organization_id`
- [ ] Update `Role` ORM model in `api/src/models/orm/users.py`
- [ ] Update `RoleCreate`, `RolePublic` contracts in `api/src/models/contracts/users.py`
- [ ] Update `api/src/routers/roles.py` - remove org_id from create/update
- [ ] Verify no queries filter roles by org_id
- [ ] Run tests, fix any breakage

### Phase 1: Database Schema
- [ ] Create migration for `workflow_roles` junction table
- [ ] Add `access_level` column to `workflows` table (default: `role_based`)
- [ ] Create ORM model `WorkflowRole` in `api/src/models/orm/workflow_roles.py`
- [ ] Update `Workflow` model with `access_level` field and `roles` relationship
- [ ] Export new model in `api/src/models/orm/__init__.py`

### Phase 2: Authorization Service Updates
- [ ] Add `"workflow": (WorkflowRole, WorkflowRole.workflow_id)` to `role_configs` in `authorization.py`
- [ ] Add `can_access_workflow()` method to `AuthorizationService`
- [ ] Update `ExecutionAuthService.can_execute_workflow()` to use new pattern (or replace entirely)
- [ ] Add workflow role API endpoints:
  - `GET /api/workflows/{id}/roles`
  - `POST /api/workflows/{id}/roles`
  - `DELETE /api/workflows/{id}/roles/{role_id}`

### Phase 3: Auto-Assignment on Entity Save
- [ ] Create `WorkflowRoleService` with `sync_entity_roles_to_workflows()` method
- [ ] Add role sync to form save handler (`api/src/routers/forms.py`)
- [ ] Add role sync to agent save handler (`api/src/routers/agents.py`)
- [ ] Add role sync to app/page/component save handlers

### Phase 4: Frontend - Enhanced Workflow Selector
- [ ] Create `WorkflowSelectorDialog` component wrapping existing `WorkflowSelector`
- [ ] Add `entityRoles` prop for role context display
- [ ] Add role mismatch warning badges on workflows
- [ ] Add "Select & Assign Roles" button behavior
- [ ] Add multi-select mode for agent tools
- [ ] Integrate into Form Editor
- [ ] Integrate into Form Field Editor
- [ ] Integrate into Agent Editor (tools section)
- [ ] Integrate into App Settings
- [ ] Integrate into Page Settings
- [ ] Update existing PropertyEditor/ActionBuilder usages

### Phase 5: Cache & Performance
- [ ] Add Redis caching for workflow roles
- [ ] Add cache invalidation on role assignment changes
- [ ] Update cache warming for SDK execution context

### Phase 6: Cleanup Legacy Code
- [ ] Remove `sync_form_workflow_access()` calls from `forms.py`
- [ ] Remove `sync_app_workflow_access()` calls from `applications.py`
- [ ] Delete `api/src/services/workflow_access_service.py`
- [ ] Delete `api/src/models/orm/workflow_access.py`
- [ ] Remove `WorkflowAccess` from `api/src/models/orm/__init__.py`
- [ ] Create migration to drop `workflow_access` table
- [ ] Delete `api/tests/unit/services/test_workflow_access_service.py`
- [ ] Update `api/tests/unit/services/test_execution_auth.py`
- [ ] Archive `.plans/app-builder-workflow-allowlist.md`

---

## What Gets Removed (Deprecation Plan)

This section explicitly lists code/tables to remove to avoid leaving legacy artifacts.

### Database Tables to Drop

| Table | File | Migration |
|-------|------|-----------|
| `workflow_access` | `api/src/models/orm/workflow_access.py` | Create migration to drop table |

### Files to Delete

| File | Reason |
|------|--------|
| `api/src/models/orm/workflow_access.py` | Table no longer needed |
| `api/src/services/workflow_access_service.py` | Extraction logic replaced by direct role assignment |
| `api/tests/unit/services/test_workflow_access_service.py` | Tests for deleted service |

### Code to Remove/Update

| File | Changes |
|------|---------|
| `api/src/models/orm/__init__.py` | Remove `WorkflowAccess` export |
| `api/src/services/execution_auth.py` | Replace `_has_workflow_access()` with direct `workflow_roles` lookup |
| `api/src/routers/forms.py` | Remove `sync_form_workflow_access()` call, add role sync call |
| `api/src/routers/applications.py` | Remove `sync_app_workflow_access()` call, add role sync call |
| `api/tests/unit/services/test_execution_auth.py` | Update tests for new auth model |

### Plans to Archive

| Plan File | Status |
|-----------|--------|
| `.plans/app-builder-workflow-allowlist.md` | Superseded - do not implement |

**Note:** The `app_workflows` and `app_page_workflows` tables mentioned in the old plan were **never created** - they were only proposed. No database cleanup needed for those.

---

## What Remains Unchanged

These items from the existing plan are still valid and should be implemented separately:

1. **Cross-org validation** - "Can this entity reference this workflow?" remains a separate concern
2. **`source_scope` query param** on `GET /api/workflows` - Still useful for UI filtering

---

## Open Questions

1. **Default access level for new workflows**: Should it be `role_based` (secure by default) or `authenticated` (easier onboarding)?
   - Recommendation: `role_based` - explicit is better

2. **Bulk role management UI**: Do we need a dedicated page to manage workflow roles across many workflows at once?
   - Recommendation: Defer - the selector dialog handles most cases

3. **Role removal policy**: When a role is removed from a form, should we prompt to remove it from the workflow too?
   - Recommendation: No auto-removal, but could show "cleanup" suggestions in a future iteration

---

## Migration Strategy

### Data Migration

When deploying, we need to migrate existing `workflow_access` data to `workflow_roles`:

```sql
-- For each unique (workflow_id, organization_id) in workflow_access,
-- we need to find the roles that should have access and insert into workflow_roles.
-- This requires joining through entity_type/entity_id to form_roles/app_roles.

-- Step 1: Insert workflow roles from forms
INSERT INTO workflow_roles (workflow_id, role_id, assigned_by, assigned_at)
SELECT DISTINCT
    wa.workflow_id,
    fr.role_id,
    'migration',
    NOW()
FROM workflow_access wa
JOIN form_roles fr ON fr.form_id = wa.entity_id
WHERE wa.entity_type = 'form'
ON CONFLICT (workflow_id, role_id) DO NOTHING;

-- Step 2: Insert workflow roles from apps
INSERT INTO workflow_roles (workflow_id, role_id, assigned_by, assigned_at)
SELECT DISTINCT
    wa.workflow_id,
    ar.role_id,
    'migration',
    NOW()
FROM workflow_access wa
JOIN app_roles ar ON ar.app_id = wa.entity_id
WHERE wa.entity_type = 'app'
ON CONFLICT (workflow_id, role_id) DO NOTHING;

-- Step 3: Set access_level on workflows based on workflow_access
-- (Workflows used by "authenticated" entities get authenticated access)
UPDATE workflows w
SET access_level = 'authenticated'
WHERE EXISTS (
    SELECT 1 FROM workflow_access wa
    WHERE wa.workflow_id = w.id
    AND wa.access_level = 'authenticated'
);
```

### Rollout Order

1. **Phase 1: Add new schema** (backward compatible)
   - Create `workflow_roles` table
   - Add `access_level` column to `workflows`
   - Deploy new `WorkflowRoleService`
   - Keep old auth path working

2. **Phase 2: Dual-write**
   - Update save handlers to write to both old and new tables
   - Run data migration for existing data
   - Verify new auth path works

3. **Phase 3: Switch auth**
   - Update `ExecutionAuthService` to use new model
   - Monitor for issues

4. **Phase 4: Cleanup**
   - Remove old service/table
   - Delete deprecated code
