# App Builder Type Unification Plan

## Status: BACKEND COMPLETE - Frontend Migration Pending

## Goal

Create a **single source of truth** for App Builder types in Python Pydantic models. Frontend will import from auto-generated `v1.d.ts` instead of maintaining separate `app-builder-types.ts`.

**Success = deleting `app-builder-types.ts` and frontend still compiles.**

---

## Completed Tasks

### Task 1: Create Typed Component Props Models ✅ COMPLETE

**File:** `api/src/models/contracts/app_components.py` (1,547 lines)

All 22 component types + shared types ported to Python Pydantic models:
- HeadingProps, TextProps, HtmlProps, CardProps, DividerProps, SpacerProps
- ButtonProps, StatCardProps, ImageProps, BadgeProps, ProgressProps
- DataTableProps, TabsProps, FileViewerProps, ModalProps
- TextInputProps, NumberInputProps, SelectProps, CheckboxProps
- FormEmbedProps, FormGroupProps
- Shared types: RepeatFor, OnCompleteAction, TableColumn, TableAction, SelectOption, TabItem
- Layout types: LayoutContainer with all layout variants
- Top-level: PageDefinition, DataSourceConfig, NavigationConfig

### Task 2: Update applications.py ✅ COMPLETE

Types properly exported through `contracts/__init__.py`.

### Task 4: Create Round-Trip Tests ✅ COMPLETE

**File:** `api/tests/unit/contracts/test_app_roundtrip.py`
- **42/42 tests passing**
- Discriminated union validation
- Snake_case serialization
- Round-trip JSON → Python → JSON preservation
- All 20+ component types tested

---

## Pending Tasks

### Task 3: Update App Indexer - ⚠️ PARTIAL

**File:** `api/src/services/file_storage/indexers/app.py`

- [x] Access control preserved on UPDATE/CREATE
- [ ] **Pydantic validation NOT implemented**
  - Current: Line 63 parses JSON to dict, validates fields manually
  - Missing: `ApplicationDefinition.model_validate(app_data)` for full schema validation
  - Action: Add ValidationError handling after JSON parse

### Task 5: Migrate Frontend to Generated Types ❌ NOT COMPLETE

**This is the main remaining work.**

- [ ] Run `npm run generate:types` to ensure app_components models in v1.d.ts
- [ ] Migrate all frontend imports from `@/lib/app-builder-types` to `@/lib/v1`
- [ ] Delete `client/src/lib/app-builder-types.ts`
- [ ] Verify `npm run tsc` passes

**Current state:**
- `client/src/lib/app-builder-types.ts` still exists (1,097 lines)
- 20+ component files still import from app-builder-types.ts:
  - AppContext.tsx
  - ComponentRegistry.tsx, LayoutRenderer.tsx, AppShell.tsx
  - All component implementations (Button, Text, Modal, etc.)
  - PermissionGuard.tsx, FormEmbedComponent.tsx, etc.

### Task 7: Clean Up Frontend Navigation Types ❌ NOT STARTED

- [ ] Remove duplicate NavigationItem from `app-builder-helpers.ts`
- Blocked by Task 5

---

## Success Criteria

- [x] All 22 component types have typed props models in Pydantic
- [x] Discriminated union validates correct props per component type
- [x] Round-trip tests pass for all component types
- [x] Access control preserved on app update, reset on create
- [ ] **app-builder-types.ts is DELETED**
- [ ] **All frontend imports updated to use @/lib/v1**
- [ ] **npm run tsc passes with zero errors**
- [ ] App builder works in browser (manual verification)
- [ ] App Indexer uses Pydantic validation

---

## Execution Order

1. ✅ Backend Pydantic models (Task 1)
2. ✅ Round-trip tests (Task 4)
3. ⚠️ App Indexer Pydantic validation (Task 3) - partial
4. ❌ Frontend migration (Task 5) - **NEXT PRIORITY**
5. ❌ Frontend cleanup (Task 7)
6. ❌ Manual browser testing

**Estimated remaining work:** ~2-3 hours to migrate 20+ frontend files
