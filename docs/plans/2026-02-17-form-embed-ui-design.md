# Form Embed UI Design

## Goal

Add embed secret management and integration guide directly into the existing FormInfoDialog as a collapsible section. Users configure embed settings in the same place they configure everything else about a form.

## Location

Collapsible section at the bottom of `FormInfoDialog`, only visible when editing an existing form (`formId` is set).

## Section Layout

### Collapsible Header

Link icon + "Embed Settings" label. Collapsed by default.

### Secrets Management

- **List**: Each secret shows name, created date, Active/Inactive badge, deactivate toggle button, and delete (trash) button. All actions fire immediately via API.
- **Create**: Name input + optional secret value input + "Add" button. On success, shows a one-time reveal alert with the raw secret and a copy button. Warning text: "Copy this secret now. It will not be shown again."
- **Empty state**: Muted text explaining that embed secrets enable HMAC-authenticated iframe embedding.

### Integration Guide

Shown below secrets list. Contains copyable code snippets:

1. **Embed URL**: `${window.location.origin}/embed/forms/${formId}`
2. **iframe HTML**: Basic embed snippet
3. **HMAC Signing — Python**: `hmac` + `hashlib` example
4. **HMAC Signing — JavaScript**: `crypto.createHmac` example

Each snippet has a copy button in the top-right corner.

## Props Change

`FormInfoDialog` receives a new optional prop:
- `formId?: string` — when set, the embed section renders and uses this for API calls

## API Calls

All via `authFetch` (not typed hooks — these endpoints aren't in the OpenAPI spec):

| Action | Method | Endpoint |
|--------|--------|----------|
| List   | GET    | `/api/forms/${formId}/embed-secrets` |
| Create | POST   | `/api/forms/${formId}/embed-secrets` |
| Toggle | PATCH  | `/api/forms/${formId}/embed-secrets/${id}` |
| Delete | DELETE | `/api/forms/${formId}/embed-secrets/${id}` |

## Delete Confirmation

Uses `AlertDialog` for destructive delete confirmation, same pattern as the app embed dialog.

## Components Used

All from existing shadcn/ui: `Collapsible`/`CollapsibleTrigger`/`CollapsibleContent`, `Button`, `Input`, `Label`, `Badge`, `Alert`, `AlertDialog`, icons from `lucide-react` (`Link`, `Plus`, `Trash2`, `Copy`, `Check`, `ChevronDown`, `AlertTriangle`).
