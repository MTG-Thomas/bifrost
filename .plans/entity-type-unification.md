# Entity Type Unification Plan

## Status: IN PROGRESS

## Goal

Unify application, form, and agent models to follow a consistent pattern:
- **One output model** (`*Public`) for all API responses including export
- **Input models** (`*Create`, `*Update`) for mutations
- **No separate Export/Import models** - file sync handles import via indexers
- **Generated TypeScript types** from OpenAPI - delete manual `app-builder-types.ts`

---

## Architectural Decisions

### Model Pattern (follows Forms/Agents)

```
Entity:
  - EntityCreate   → input for creating
  - EntityUpdate   → input for updating
  - EntityPublic   → output for ALL responses (list, get, export)
  - NO EntityExport/EntityImport
```

### Import via File Sync (NOT dedicated endpoints)

Forms and Agents use file-based sync:
1. Write JSON file to workspace
2. Indexer parses and creates/updates entity
3. No `/import` endpoint needed

**Applications should follow the same pattern.** The current `/api/applications/import` endpoint is architecturally inconsistent and should be removed.

### TypeScript Types

Frontend types come from generated `v1.d.ts`, not manual definitions:
- Delete `client/src/lib/app-builder-types.ts` (1,097 lines of manual types)
- Import from `@/lib/app-builder-helpers.ts` which re-exports from `v1.d.ts`
- Types with `-Input`/`-Output` variants use `-Output` for read contexts

---

## Completed Work

### Backend Model Changes

1. **Added optional export fields to `ApplicationPublic`** (`api/src/models/contracts/applications.py:105-157`)
   - `pages: list[PageDefinition] | None`
   - `permissions` (optional)
   - For list views: None (not loaded)
   - For export: populated with full content

2. **Fixed type re-exports in `app-builder-helpers.ts`** (`client/src/lib/app-builder-helpers.ts:116-152`)
   - Types with `-Input`/`-Output` variants now use `-Output` suffix
   - Example: `PageDefinition-Output`, `TableAction-Output`, `CardProps-Output`

3. **Fixed `useApplications.ts`** (`client/src/hooks/useApplications.ts:26`)
   - `PageDefinition` now uses `PageDefinition-Output`

### Files Modified

- `api/src/models/contracts/applications.py` - Added export fields to ApplicationPublic
- `api/src/models/contracts/__init__.py` - Removed ApplicationExport, ApplicationPortable
- `api/src/routers/applications.py` - Removed ApplicationExport import (but endpoint not yet updated)
- `client/src/lib/app-builder-helpers.ts` - Fixed -Output suffix for all types
- `client/src/hooks/useApplications.ts` - Fixed PageDefinition type

---

## Remaining Work

### Priority 0: Remove Unused Global Fields

**Why:** `global_data_sources` and `global_variables` are unused. Variables come from workflow execution results, not pre-defined globals.

**Files to modify:**

1. `api/src/models/orm/applications.py`
   - Remove `global_data_sources` column
   - Remove `global_variables` column

2. `api/src/models/contracts/applications.py`
   - Remove from `ApplicationPublic`
   - Remove from any create/update models if present

3. `api/src/services/file_storage/indexers/app.py`
   - Remove from `_serialize_app_to_json()`
   - Remove from `index_app()` (stop reading/writing)

4. `api/src/services/git_serialization.py`
   - Remove from `serialize_app_to_dict()`

5. Create migration to drop columns

---

### Priority 1: Remove `/import` Endpoint

**Why:** Applications should use file sync like forms/agents, not a dedicated import endpoint.

**Files to modify:**

1. `api/src/routers/applications.py`
   - Delete the `import_application` endpoint (~lines 714-760)
   - Delete `ApplicationImport` from imports

2. `api/src/models/contracts/applications.py`
   - Delete `ApplicationImport` class (~lines 512-539)
   - Delete the explanatory comment block above it

3. `api/src/models/contracts/__init__.py`
   - Remove `ApplicationImport` from imports and `__all__`

4. `client/src/hooks/useApplications.ts`
   - Delete `useImportApplication` hook
   - Delete `importApplication` function
   - Delete `ApplicationImport` type alias
   - Keep `ApplicationPortable` type if needed, or remove if unused

5. Verify app indexer handles full import flow:
   - Check `api/src/services/file_storage/indexers/app.py`
   - Ensure `index_app` can create new apps from JSON files (not just update existing)

### Priority 1.5: Import Field Handling

**Why:** Portable apps shouldn't assume org structure, role IDs, or access policies of the target environment.

**Update `AppIndexer.index_app()` to ignore instance-specific fields on import:**

| Field | On Import |
|-------|-----------|
| `organization_id` | Set from import context or `None` (global) |
| `permissions` | Set to `{}` (role IDs don't transfer across instances) |
| `access_level` | Set to `"role_based"` (locked down by default) |
| `created_by` | Set to `"file_sync"` or importing user |
| `created_at`/`updated_at` | Regenerated (ignore if present in JSON) |

**Export stays unchanged** - include everything for simplicity (not sensitive data).

**Rationale:** Safer to start locked down. Admins explicitly configure access after import.

### Priority 2: Fix Export Endpoint

**File:** `api/src/routers/applications.py`

Current state: Endpoint references deleted `ApplicationExport` type.

**Fix:**
```python
@router.get(
    "/{app_id}/export",
    response_model=ApplicationPublic,  # Changed from ApplicationExport
    summary="Export application to JSON",
)
async def export_application(...) -> ApplicationPublic:
    ...
    return ApplicationPublic.model_validate(export_data)
```

### Priority 3: Regenerate Types & Verify

```bash
# From client/ directory (with dev stack running)
npm run generate:types
npm run tsc
npm run lint
```

### Priority 4: Migrate Frontend Files

45 files import from `app-builder-types.ts`. They need to import from `app-builder-helpers.ts` instead.

**Files to migrate** (in `client/src/`):
- `components/app-builder/**/*.tsx` (27 files)
- `contexts/AppContext.tsx`
- `components/app-builder/LayoutRenderer.tsx`
- `components/app-builder/AppRenderer.tsx`
- Various hooks and utility files

**Migration pattern:**
```typescript
// Before
import { ComponentType, LayoutContainer } from '@/lib/app-builder-types';

// After
import { ComponentType, LayoutContainer } from '@/lib/app-builder-helpers';
```

### Priority 5: Delete Manual Types File

Once all imports are migrated:
```bash
rm client/src/lib/app-builder-types.ts
```

### Priority 6: Fix Round-Trip Tests

**Why:** Tests exist but are timing out. Model changes may have broken ref translation.

**Audit existing tests:**
- `tests/unit/services/test_github_sync.py`
- `tests/unit/services/test_github_sync_virtual_files.py`
- `tests/e2e/api/test_portable_refs_sync.py`
- Any app indexer tests

**Fix or extend to ensure coverage of:**
1. Create workflows in DB with known UUIDs
2. Create app with workflow refs in:
   - Page-level `launch_workflow_id`
   - Page `data_sources[].workflow_id`
   - Component props (buttons, forms with `workflow_id`)
3. Serialize app → verify UUIDs become portable refs
4. Delete app from DB
5. Deserialize via indexer → verify portable refs resolve to UUIDs
6. Validate result matches original

**Test should fail if:**
- Any workflow ref not transformed on export
- Any portable ref unresolved on import
- Instance-specific fields (org_id, permissions, etc.) leak through

---

### Priority 7: Audit Workflow Ref Paths

**Why:** Current `_export.workflow_refs` may not cover all locations.

Current tracked paths:
```python
"pages.*.layout..*.props.workflow_id",
"pages.*.data_sources.*.workflow_id",
"pages.*.launch_workflow_id",
```

**Verify coverage of:**
- Button `onClick` actions with workflow execution
- Modal/dialog triggers
- Form submit actions
- Table row actions
- Any new component types added since initial implementation

**Validation:** Round-trip test will expose missing paths - if a UUID survives serialization unchanged, that path isn't tracked.

---

### Priority 8: Final Verification

```bash
# Backend
cd api
pyright
ruff check .

# Frontend
cd client
npm run tsc
npm run lint
npm run build

# Tests
cd ..
./test.sh
```

---

## Type Mapping Reference

Types that exist WITHOUT `-Input`/`-Output` suffix (use directly):
- `RepeatFor`, `OnCompleteAction`, `TableColumn`, `SelectOption`, `PagePermission`
- `HeadingProps`, `TextProps`, `HtmlProps`, `DividerProps`, `SpacerProps`
- `ImageProps`, `BadgeProps`, `ProgressProps`, `FileViewerProps`
- `TextInputProps`, `NumberInputProps`, `SelectProps`, `CheckboxProps`, `FormEmbedProps`

Types that need `-Output` suffix for read contexts:
- `TableAction-Output`, `TabItem-Output`, `PageDefinition-Output`, `DataSourceConfig-Output`
- `CardProps-Output`, `ButtonProps-Output`, `StatCardProps-Output`
- `DataTableProps-Output`, `TabsProps-Output`, `ModalProps-Output`, `FormGroupProps-Output`

---

## Success Criteria

### Model Cleanup
- [ ] `global_data_sources` and `global_variables` columns dropped (migration)
- [ ] No `/api/applications/import` endpoint
- [ ] `ApplicationPublic` is the single output model (with optional `pages` for export)
- [ ] `ApplicationImport` model deleted
- [ ] Export endpoint uses `ApplicationPublic`

### Import Behavior
- [ ] `AppIndexer.index_app()` ignores instance-specific fields
- [ ] Imported apps default to `access_level="role_based"`
- [ ] `permissions` reset to `{}` on import
- [ ] `organization_id` set from context, not JSON

### GitHub Sync
- [ ] Round-trip tests pass (serialize → delete → deserialize → validate)
- [ ] Workflow ref paths audited and complete
- [ ] No unresolved refs on import with valid workflows

### Frontend Types
- [ ] `app-builder-types.ts` deleted
- [ ] All frontend imports use `app-builder-helpers.ts` or `v1.d.ts`

### Quality Gates
- [ ] `npm run tsc` passes with zero errors
- [ ] `npm run build` succeeds
- [ ] `pyright` passes
- [ ] `./test.sh` passes

---

## Related Files

- Supersedes: `.plans/app-builder-unification-exportability.md`
- Supersedes: `.plans/remaining-unification-tasks.md`
- Supersedes: `.plans/forms-agents-type-unification.md` (already complete)
- Reference: `~/.claude/plans/zany-skipping-whisper.md` (unified serialization pattern)
