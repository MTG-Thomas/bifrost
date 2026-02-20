# Discard All Changes + Batch Mapping Save

**Date**: 2026-02-19

Two independent UI/UX improvements.

---

## Feature 1: Discard All Changes (Context Menu)

Right-click on the "Changes" section header in SourceControlPanel to get a context menu with "Discard All Changes". Clicking shows a confirmation dialog.

### Behavior

1. Right-click the "Changes" header bar → custom context menu appears at cursor position
2. Single menu item: "Discard All Changes" (with Undo2 icon)
3. Click → confirmation AlertDialog: "Discard All Changes? This will discard all N uncommitted changes. This cannot be undone."
4. Confirm → calls existing `discard` endpoint with all file paths
5. Context menu dismisses on click-outside or Escape

### Components

- `client/src/components/editor/SourceControlPanel.tsx` — ChangesSection gets `onContextMenu`, context menu, and confirmation dialog

### No backend changes needed

The existing `/api/github/discard` endpoint already accepts `paths: string[]`.

---

## Feature 2: Batch Mapping Save

New batch endpoint replaces the N-request-per-mapping `handleSaveAll` loop.

### Backend

**Endpoint**: `POST /api/integrations/{integration_id}/mappings/batch`

**Request model** (`IntegrationMappingBatchRequest`):
```python
class IntegrationMappingBatchItem(BaseModel):
    organization_id: str
    entity_id: str
    entity_name: str | None = None

class IntegrationMappingBatchRequest(BaseModel):
    mappings: list[IntegrationMappingBatchItem]
```

**Response model** (`IntegrationMappingBatchResponse`):
```python
class IntegrationMappingBatchResponse(BaseModel):
    created: int
    updated: int
    errors: list[str]
```

**Logic**: For each item, check if a mapping already exists for `(integration_id, organization_id)`. If yes, update `entity_id`/`entity_name`. If no, create new mapping. All within a single DB transaction.

### Frontend

- New mutation hook for batch endpoint
- `handleSaveAll` collects dirty mappings → single batch call → single toast → single invalidation
- Remove sequential loop

### Components

- `api/shared/models.py` — new request/response models
- `api/src/routers/integrations.py` — new batch handler
- `client/src/pages/IntegrationDetail.tsx` — rewrite `handleSaveAll`
- `client/src/lib/v1.d.ts` — regenerated types
