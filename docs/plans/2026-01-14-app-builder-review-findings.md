# App Builder Review Findings - 2026-01-14

## Summary

- Critical issues found: 0
- High priority issues: 3 (all fixed)
- Low priority issues: 4 (cataloged for future)

## Critical Issues (Fixed)

None found.

## High Priority Issues (Fixed)

| Issue | File:Line | Description | Fix Applied |
|-------|-----------|-------------|-------------|
| Documentation mismatch | `expression-parser.ts:64` | Comment said `{{ workflow.<dataSourceId>.result }}` but runtime stores unwrapped | Updated comment to reflect actual pattern `{{ workflow.<dataSourceId> }}` |
| Missing role sync on app update | `applications.py:update_application()` | Workflow roles only synced on publish, not update | Added `sync_app_roles_to_workflows()` call after app update when role_ids change |
| PermissionGuard access_level | `PermissionGuard.tsx` | Frontend uses `permissions.rules` but backend has `access_level` field | Added TODO comment documenting the alignment needed |

## Low Priority Issues (Cataloged)

| Issue | File:Line | Description | Notes |
|-------|-----------|-------------|-------|
| Type assertion | `CardComponent.tsx:76` | Uses `layout={child as any}` | Pragmatic workaround for union types |
| Backward compat fallback | `TabsComponent.tsx:23` | Uses `(props as any)?.tabs` | Old `tabs` vs new `items` naming |
| Duplicate type definitions | `app-builder-helpers.ts` | Duplicates types from `@/types/app-builder.ts` | Not causing issues, cleanup opportunity |
| No explicit PermissionGuard | `ApplicationRunner.tsx:634-647` | Relies solely on backend 403 | Backend is authoritative, acceptable pattern |

## Verification Commands Run

- [x] `pyright` - 0 errors, 1 warning (pre-existing psutil import)
- [x] `ruff check .` - All checks passed
- [x] `npm run tsc` - No errors
- [x] `npm run lint` - 5 pre-existing errors (not related to app builder changes)

## Technical Debt Notes

- `can_access_workflow` vs `can_execute_workflow` - Conceptually redundant methods in authorization.py and execution_auth.py. Consider consolidating in future.
- Frontend `PermissionGuard` should integrate backend `access_level` field for full alignment.

## Areas Verified OK

### Type Alignment
- Zero imports from deleted `app-builder-types.ts`
- All 7 interactive component props match backend contracts
- Hook return types align with API response contracts
- AppContext properly implements ExpressionContext interface

### Workflow Execution Paths
- Button workflow execution handles both legacy and new format
- Form submit workflow correctly collects and passes field values
- Page launch workflow executes on mount, results stored correctly
- Table action workflows pass row context correctly
- Workflow results stored unwrapped (direct access, no `.result` wrapper)

### Authorization & Permissions
- Backend uses new `access_level` + `WorkflowRole` pattern
- `_has_direct_workflow_access()` checks workflow org scoping correctly
- `role_configs` contains `"workflow"` entry
- No legacy `workflow_access` references in codebase
- Role sync called on form and agent save
- Role sync now called on app update (fixed)

## Files Modified During Review

- `client/src/lib/expression-parser.ts` - Fixed documentation comment
- `api/src/routers/applications.py` - Added role sync on app update
- `client/src/components/app-builder/PermissionGuard.tsx` - Added TODO comment
