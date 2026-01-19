# Org-Grouped File Tree Design

**Date:** 2026-01-18

## Overview

Group workspace files by organization in the file tree, with drag-drop support to move entities between organizations.

## Current State

- FileTree is modular with `FileOperations` interface for different backends
- `orgScopedIconResolver` exists (uses `metadata.isOrgContainer`)
- Entities (workflows, forms, agents) have `organization_id` in DB
- File list API returns `entity_type` and `entity_id` but NOT `organization_id`
- Entity Management page can already update entity organizations

## Design

### Tree Structure

```
🌐 Global
   └── 📁 workflows/
      └── 🔄 send_email.py
   └── 📁 lib/
      └── 📄 utils.py
   └── 📁 config/
      └── 📄 settings.json
🏢 Customer A
   └── 📁 workflows/
      └── 🔄 process_order.py
🏢 Customer B
   └── 📁 workflows/
      └── 🔄 billing.py
```

- **All files live under org containers** - "Global" is `organization_id = null`
- **Org containers** are collapsible, show entities scoped to that org
- **Path structure preserved** within each org container
- **Only orgs with content** are shown (no empty containers)

### Drag-Drop Behavior

| Item Type | Draggable | Drop Target |
|-----------|-----------|-------------|
| Org container | No | Yes (receives entities) |
| Entity (workflow/form/agent) | Yes | - |
| Module file (.py without decorator) | No | - |

**Cross-org move flow:**
1. User drags entity onto different org container
2. Confirmation dialog: "Move {filename} to {org name}?"
3. On confirm: PATCH entity's `organization_id`
4. Refresh tree, show toast

### Backend Changes

**Add `organization_id` to `FileMetadata`:**

```python
class FileMetadata(BaseModel):
    # ... existing fields ...
    organization_id: str | None = Field(
        default=None,
        description="Organization ID for scoped entities, null for global/modules"
    )
```

**Query approach for `/api/files/editor/list`:**

1. List files as usual (returns `entity_type`, `entity_id`)
2. Collect entity IDs grouped by type
3. Batch query each entity table:
   - `SELECT id, organization_id FROM workflows WHERE id IN (...)`
   - `SELECT id, organization_id FROM forms WHERE id IN (...)`
   - `SELECT id, organization_id FROM agents WHERE id IN (...)`
4. Merge org IDs into response

### Frontend Changes

**New adapter: `client/src/services/orgScopedFileOperations.ts`**

Wraps `fileService` and transforms flat list into org-grouped structure:

```typescript
function createOrgScopedFileOperations(
  orgs: Organization[]
): FileOperations
```

**Path convention:** Org containers use `org:{id}` or `org:global` as virtual path prefix.

**Key behaviors:**

| Method | Behavior |
|--------|----------|
| `list("")` | Returns org containers (only those with content) |
| `list("org:{id}")` | Lists files within that org, normal folder structure |
| `rename()` | Detects cross-org moves, calls entity update API |
| Other ops | Delegate to `fileService` after stripping org prefix |

**FileTree.tsx changes (minimal):**

- Check `metadata.isOrgContainer` to hide rename/delete options
- Disable drag on org containers (can still receive drops)
- Show confirmation dialog on cross-org entity drops

### Entity Update APIs

Already exist, no changes needed:

- `PATCH /api/workflows/{id}` with `{ organization_id: "..." }`
- `PATCH /api/forms/{id}` with `{ organization_id: "..." }`
- `PATCH /api/agents/{id}` with `{ organization_id: "..." }`

## File Summary

### New Files
- `client/src/services/orgScopedFileOperations.ts` - Adapter for org grouping

### Modified Files
- `api/src/models/contracts/editor.py` - Add `organization_id` to `FileMetadata`
- `api/src/routers/files.py` - Fetch org IDs in list endpoint
- `client/src/components/file-tree/FileTree.tsx` - Org container handling
- `client/src/components/editor/WorkspaceFileTree.tsx` - Use org-scoped adapter

## Out of Scope

- App code editor (uses separate `appCodeOperations` adapter, unaffected)
- Org selection dialog on entity creation (removed - entities default to `role_based` with no roles)
- `.app.json` virtual files (being removed separately)
