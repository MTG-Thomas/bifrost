# App Builder Comprehensive Review Checklist

## Purpose

Verify the app builder frontend and backend are fully aligned after the entity unification and workflow-role access changes. Catch integration issues before manual testing.

---

## Methodology

### Review Phases

1. **Parallel Initial Scan** (3 agents)
   - Agent 1: Type alignment scan
   - Agent 2: Workflow execution paths scan
   - Agent 3: Authorization/permissions scan

2. **Sequential Deep Dive**
   - Interactive components (7 components, full logic review)
   - Cross-cutting integration points
   - Backend authorization & contracts

3. **Findings Consolidation**
   - Critical issues (fixed during review)
   - High priority issues (fix before testing)
   - Low priority issues (fix after testing)

### Issue Severity Definitions

| Severity | Definition | Action |
|----------|------------|--------|
| **Critical** | Would cause runtime crash or complete feature failure | Fix immediately |
| **High** | Would cause incorrect behavior, silent failures, or security gaps | Catalog, fix before testing |
| **Low** | Style inconsistency, suboptimal patterns, technical debt | Catalog, fix after testing |

---

## Phase 1: Parallel Initial Scan

### Agent 1: Type Alignment Scan

**Scope:** All 43 frontend files + type definition files

| Check | Files | What to verify |
|-------|-------|----------------|
| No imports from deleted file | All `app-builder/**/*.tsx` | Zero imports from `app-builder-types.ts` |
| Correct type source | All components, hooks, contexts | Types come from `app-builder-helpers.ts` or `v1.d.ts` |
| `-Output` suffix usage | Components reading data | Types with Input/Output variants use `-Output` for read contexts |
| Props interfaces match contracts | 21 component files | Component props align with `app_components.py` Pydantic models |
| Hook return types | `useApplications.ts`, `usePageData.ts`, `useAppBuilderActions.ts` | Return types match API response contracts |
| Context type definitions | `AppContext.tsx` | Context shape matches what components expect |

**Output:** List of type misalignments with file:line references

---

### Agent 2: Workflow Execution Paths Scan

**Scope:** Components that trigger workflows + hooks that execute them

| Check | Files | What to verify |
|-------|-------|----------------|
| Button workflow execution | `ButtonComponent.tsx` | `workflow_id` and `onClick.workflow_id` both handled |
| Form submit workflow | `FormGroupComponent.tsx` | Submit action triggers workflow with form values |
| Page launch workflow | `usePageData.ts`, `AppRenderer.tsx` | `launch_workflow_id` executes on page load |
| Table action workflows | `DataTableComponent.tsx` | Row actions pass correct `{{ row.* }}` context |
| Workflow result access | `AppContext.tsx`, `expression-parser.ts` | `{{ workflow.dataSourceId.fieldName }}` resolves correctly |
| Execution hook | `useWorkflowExecution.ts` | Calls correct API endpoint, handles response shape |
| Status indicators | `WorkflowStatusIndicator.tsx`, `WorkflowLoadingIndicator.tsx` | Display states match execution lifecycle |

**Output:** List of workflow execution gaps or mismatches

---

### Agent 3: Authorization & Permissions Scan

**Scope:** Permission checks in frontend + backend authorization code

| Check | Files | What to verify |
|-------|-------|----------------|
| PermissionGuard usage | `PermissionGuard.tsx`, components using it | Correctly checks `access_level` and role membership |
| App access_level handling | `ApplicationRunner.tsx`, `useApplications.ts` | Respects `authenticated` vs `role_based` |
| Workflow role checks | `useWorkflowExecution.ts` | Execution respects workflow's `access_level` and `workflow_roles` |
| Backend can_execute_workflow | `execution_auth.py` | Uses `access_level` + `WorkflowRole` pattern |
| Backend app authorization | `applications.py` router | `can_access_entity(app, "app")` called correctly |
| Role sync on save | `applications.py`, `forms.py`, `agents.py` | `sync_*_roles_to_workflows()` called after save |
| No legacy references | All backend files | Zero references to deleted `workflow_access` table/service |

**Output:** List of authorization gaps or legacy code remnants

---

## Phase 2: Sequential Deep Dive - Interactive Components

### 2.1 ButtonComponent.tsx

| Aspect | What to verify |
|--------|----------------|
| Props type | Uses `ButtonProps` from generated types (with `-Output` if applicable) |
| Workflow ID sources | Handles both `props.workflow_id` (legacy) and `props.onClick?.workflow_id` (new) |
| Action execution | Calls `executeWorkflow` from `useAppBuilderActions` with correct params |
| Parameter passing | `action_params` evaluated through expression parser before execution |
| Loading state | Disables button / shows spinner during workflow execution |
| Result handling | `onComplete` action (navigate, setVariable, refreshTable) fires correctly |
| Permission check | If button has permission config, wrapped in PermissionGuard |

### 2.2 DataTableComponent.tsx

| Aspect | What to verify |
|--------|----------------|
| Props type | Uses `DataTableProps-Output` from generated types |
| Column definitions | `TableColumn` type matches backend contract |
| Row action handling | `TableAction` type with `on_click.workflow_id` correctly structured |
| Row context | `{{ row.fieldName }}` expressions resolved before workflow execution |
| Workflow execution | Row action workflows receive row data as parameters |
| Selection state | Table selection stored in app-builder store, accessible to other components |
| Refresh mechanism | `refreshTable` action from workflow `onComplete` works |

### 2.3 FormGroupComponent.tsx

| Aspect | What to verify |
|--------|----------------|
| Props type | Uses `FormGroupProps-Output` from generated types |
| Field value collection | Gathers all child input values on submit |
| Submit workflow | `props.workflow_id` called with collected field values |
| Validation | Field validation runs before workflow execution |
| Loading state | Submit button disabled during execution |
| Reset behavior | Form resets or preserves values based on config |
| Error display | Workflow errors surfaced to user |

### 2.4 SelectComponent.tsx

| Aspect | What to verify |
|--------|----------------|
| Props type | Uses `SelectProps` from generated types |
| Options source | Static `options` array or dynamic from `{{ workflow.* }}` |
| Option type | `SelectOption` matches backend contract |
| Value binding | Selected value stored in field context, accessible via `{{ fields.fieldId }}` |
| onChange workflow | If configured, triggers workflow on selection change |

### 2.5 ModalComponent.tsx

| Aspect | What to verify |
|--------|----------------|
| Props type | Uses `ModalProps-Output` from generated types |
| Open state | Controlled by `openModal` / `closeModal` actions from store |
| Content rendering | Children rendered through LayoutRenderer |
| Trigger integration | Button `onClick.action = "openModal"` works |
| Close on workflow complete | `onComplete: { action: "closeModal" }` works |

### 2.6 TabsComponent.tsx

| Aspect | What to verify |
|--------|----------------|
| Props type | Uses `TabsProps-Output` from generated types |
| Tab items | `TabItem-Output` type for tab definitions |
| Content rendering | Each tab's content rendered through LayoutRenderer |
| Active tab state | Tab selection persisted appropriately |
| Permission per tab | Individual tabs can have permission restrictions |

### 2.7 FormEmbedComponent.tsx

| Aspect | What to verify |
|--------|----------------|
| Props type | Uses `FormEmbedProps` from generated types |
| Form loading | Fetches form definition by ID |
| Submission handling | Form submit triggers form's configured workflow |
| Result integration | Form submission result accessible in app context |
| Permission check | Embedded form respects its own access_level |

---

## Phase 3: Sequential Deep Dive - Cross-Cutting Integration

### 3.1 Expression Parser Integration

| Expression Pattern | What to verify |
|--------------------|----------------|
| `{{ workflow.getTicket.id }}` | Data source result fields directly accessible under data source ID |
| `{{ workflow.getTicket.summary }}` | No `.result` or `.output` wrapper - direct field access |
| `{{ user.roles }}` | Role array matches backend shape |
| `{{ variables.count }}` | Page variables accessible |
| `{{ params.id }}` | URL params as strings |
| `{{ row.fieldName }}` | Table row context in row actions |
| `{{ fields.inputId }}` | Form field values accessible |

**Files:** `expression-parser.ts`, `AppContext.tsx`, all components using `{{ }}`

### 3.2 Data Source Configuration

| Check | What to verify |
|-------|----------------|
| Page data sources | `data_sources` array processed correctly in `usePageData.ts` |
| Execution on load | Data sources executed on page load, results stored in context |
| Property editor | Data source configuration UI matches `DataSourceConfig-Output` type |
| Workflow ID refs | Data source `workflow_id` references valid workflows |
| Result keying | Results keyed by `data_source_id` in context |
| Refresh triggers | Re-execute correct data sources on refresh |

**Files:** `usePageData.ts`, `AppRenderer.tsx`, `PropertyEditor.tsx`

### 3.3 App Builder Store ↔ Component Sync

| Store Key | Components Using It | What to verify |
|-----------|---------------------|----------------|
| `variables` | All (via expressions) | `setVariable` action updates store, expressions re-evaluate |
| `workflowResults` | All (via expressions) | Workflow completion populates results, components see update |
| `tableSelections` | DataTable, Button | Table selection readable by other components |
| `modalState` | Modal, Button | Open/close modal actions update state correctly |
| `fieldValues` | FormGroup, inputs | Input changes stored, accessible via `{{ fields.* }}` |

### 3.4 Draft/Publish Data Flow

| Scenario | What to verify |
|----------|----------------|
| Editor loads | Fetches draft version pages/components |
| User edits | Changes save to draft version only |
| User publishes | New active version created, runner sees update |
| Runner loads | Fetches active version (not draft) |
| Live updates | WebSocket pushes version change, runner refreshes |

**Files:** `ApplicationEditor.tsx`, `ApplicationRunner.tsx`, `useApplications.ts`, `useAppLiveUpdates.ts`

### 3.5 Backend Contract ↔ Frontend Type Alignment

| Backend Model | Frontend Type | Check |
|---------------|---------------|-------|
| `ApplicationPublic` | `components["schemas"]["ApplicationPublic"]` | All fields present |
| `AppPageResponse` | `components["schemas"]["AppPageResponse"]` | `layout`, `data_sources`, `launch_workflow_id` match |
| `PageDefinition` | `PageDefinition-Output` | Nested structure matches |
| `AppComponentResponse` | `components["schemas"]["AppComponentResponse"]` | Props shape matches |
| `WorkflowExecutionResponse` | Execution result type | Response fields align |

---

## Phase 4: Sequential Deep Dive - Backend Authorization & Contracts

### 4.1 Authorization Code Paths

| File | Check | What to verify |
|------|-------|----------------|
| `authorization.py` | `role_configs` dict | Contains `"workflow": (WorkflowRole, WorkflowRole.workflow_id)` |
| `execution_auth.py` | `can_execute_workflow()` | Uses `access_level` + `WorkflowRole` lookup |
| `execution_auth.py` | `_has_direct_workflow_access()` | Checks workflow org scoping, access_level, role membership |
| `execution_auth.py` | No legacy imports | Zero imports from `workflow_access_service` or `workflow_access` model |
| `applications.py` router | App access checks | `can_access_entity(app, "app")` before returning app data |
| `applications.py` router | Role sync on save | `sync_app_roles_to_workflows()` called after app/page save |

### 4.2 Contract Model Verification

| Model | File | Fields to verify |
|-------|------|------------------|
| `ApplicationPublic` | `contracts/applications.py` | `access_level`, `role_ids` (optional), `pages` (optional for export) |
| `AppPageResponse` | `contracts/applications.py` | `launch_workflow_id`, `data_sources`, `layout`, `permissions` |
| `PageDefinition` | `contracts/app_components.py` | Nested structure matches frontend expectations |
| `WorkflowPublic` | `contracts/workflows.py` | `access_level`, `role_ids` fields present |

### 4.3 No Legacy Code Remnants

| Check | What to verify |
|-------|----------------|
| `workflow_access` table | No ORM model, no references in any file |
| `workflow_access_service.py` | File deleted, no imports anywhere |
| `sync_form_workflow_access` | Replaced with `sync_form_roles_to_workflows` |
| `sync_app_workflow_access` | Replaced with `sync_app_roles_to_workflows` |

### 4.4 Workflow Role Service Integration

| File | Check | What to verify |
|------|-------|----------------|
| `workflow_role_service.py` | Service exists | `sync_entity_roles_to_workflows()` implemented |
| `forms.py` router | Integration | Calls `sync_form_roles_to_workflows()` on save |
| `agents.py` router | Integration | Calls `sync_agent_roles_to_workflows()` on save |
| `applications.py` router | Integration | Calls `sync_app_roles_to_workflows()` on save |
| `workflows.py` router | Role endpoints | `GET/POST/DELETE /api/workflows/{id}/roles` exist |

---

## Findings Report Template

```markdown
# App Builder Review Findings - YYYY-MM-DD

## Summary
- Critical issues found: X (all fixed during review)
- High priority issues: X (fix before manual testing)
- Low priority issues: X (fix after testing)

## Critical Issues (Fixed)
| Issue | File:Line | Description | Fix Applied |
|-------|-----------|-------------|-------------|

## High Priority Issues
| Issue | File:Line | Description | Impact |
|-------|-----------|-------------|--------|

## Low Priority Issues
| Issue | File:Line | Description | Notes |
|-------|-----------|-------------|-------|

## Verification Commands Run
- [ ] `pyright` - result
- [ ] `ruff check .` - result
- [ ] `npm run tsc` - result
- [ ] `npm run lint` - result
- [ ] `./test.sh` - result

## Technical Debt Notes
- `can_access_workflow` vs `can_execute_workflow` - conceptually redundant, consider consolidating
```

---

## Execution Order

1. Complete Phases 1-4 of unification-continuation.md (WorkflowSelectorDialog integrations)
2. Run parallel scan (3 agents)
3. Consolidate parallel findings, fix critical issues
4. Sequential deep dive (interactive components, cross-cutting, backend)
5. Generate findings report
6. Run verification commands
7. Handoff for manual E2E testing

---

## Files in Scope

### Frontend (43 files)
- `client/src/pages/ApplicationEditor.tsx`
- `client/src/pages/ApplicationRunner.tsx`
- `client/src/components/app-builder/**/*.tsx` (all)
- `client/src/hooks/useApplications.ts`
- `client/src/hooks/useAppBuilderActions.ts`
- `client/src/hooks/usePageData.ts`
- `client/src/hooks/useWorkflowExecution.ts`
- `client/src/contexts/AppContext.tsx`
- `client/src/stores/app-builder.store.ts`
- `client/src/lib/app-builder-*.ts`
- `client/src/lib/expression-parser.ts`

### Backend (12 files)
- `api/src/models/orm/applications.py`
- `api/src/models/contracts/applications.py`
- `api/src/models/contracts/app_components.py`
- `api/src/routers/applications.py`
- `api/src/services/app_builder_service.py`
- `api/src/services/authorization.py`
- `api/src/services/execution_auth.py`
- `api/src/services/workflow_role_service.py`
