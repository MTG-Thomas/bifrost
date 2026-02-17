# HMAC-Authenticated Form Embedding

**Date**: 2026-02-17
**Status**: Approved

## Problem

Bifrost forms need to be embedded in external systems (Halo PSA, Zendesk, etc.) via iframes, just like apps. The app embed system (HMAC verification, embed JWTs, scoped middleware) already exists but only supports apps. Forms are independent entities that need their own embed path.

Additionally, HMAC-signed query params (e.g., `agent_id`, `ticket_id`) should flow into the workflow as implicit inputs — no explicit form field needed.

## Solution

Extend the existing embed infrastructure to forms. New `form_embed_secrets` table, new `/embed/forms/{uuid}` entry point, middleware allowlist updates, and frontend embed detection on the form execution page.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Secret storage | Separate `form_embed_secrets` table | Clean separation from app secrets. Same structure, no polymorphic complexity. |
| Form identifier | UUID (not slug) | Forms don't have slugs. UUIDs are stable and sufficient for embed URLs. |
| Param merge order | `default_launch_params` < `verified_params` < `form_data` | User input wins over HMAC params, which win over form defaults. Workflow gets a flat merged dict. |
| Nav hiding | Reuse `EmbedUser` role detection | Same pattern as app embed — `hasRole("EmbedUser")` hides layout chrome. |
| Post-submit behavior | Stay in iframe (`preventNavigation=true`) | Embedded forms shouldn't navigate to the execution history page. |

## Flow

```
External System (Halo)                    Bifrost
─────────────────────                    ───────
1. Agent opens custom tab
2. Compute HMAC-SHA256(secret,
   "agent_id=42&ticket_id=1001")
3. Load iframe:
   /embed/forms/{uuid}?agent_id=42
   &ticket_id=1001&hmac=abc123...
                                         4. Look up form + active embed secrets
                                         5. Verify HMAC (reuses verify_embed_hmac)
                                         6. Issue 8-hour embed JWT with form_id
                                         7. Redirect to /execute/{uuid}#embed_token=...
                                         8. Frontend detects embed token, hides nav
                                         9. Form renders, user fills it out
                                         10. On submit: server merges verified_params
                                             into workflow input alongside form_data
                                         11. Workflow receives {agent_id, ticket_id,
                                             first_name, last_name, ...}
```

## Data Model

### New table: `form_embed_secrets`

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | PK |
| `form_id` | UUID FK → forms | Which form this secret is for |
| `name` | String | Label (e.g., "Halo Production") |
| `secret_encrypted` | String | Fernet-encrypted shared secret |
| `is_active` | Boolean | Can be disabled without deleting |
| `created_at` | DateTime(timezone=True) | |
| `created_by` | UUID FK → users | Audit trail |

## Embed JWT (form variant)

```json
{
  "sub": "SYSTEM_USER_ID",
  "form_id": "<form UUID>",
  "org_id": "<form's organization UUID>",
  "verified_params": {"agent_id": "42", "ticket_id": "1001"},
  "email": "embed@internal.gobifrost.com",
  "is_superuser": false,
  "embed": true,
  "roles": ["EmbedUser"]
}
```

Key difference from app embed JWT: `form_id` instead of `app_id`.

## Param Merge in `execute_form`

```python
# Extract verified_params from embed JWT (if present)
verified_params = ctx.user.verified_params or {}

merged = {
    **(form.default_launch_params or {}),   # form defaults (lowest)
    **verified_params,                       # HMAC params (middle)
    **request.form_data,                     # user input (highest)
}
```

The workflow receives all three as a flat dict. No explicit form input field needed for HMAC params like `agent_id`.

## API Surface

### Embed Secret Management (authenticated, admin only)

```
POST   /api/forms/{form_id}/embed-secrets     → Create secret (returns raw secret once)
GET    /api/forms/{form_id}/embed-secrets     → List secrets (name, id, active — no raw values)
DELETE /api/forms/{form_id}/embed-secrets/{id} → Delete secret
PATCH  /api/forms/{form_id}/embed-secrets/{id} → Toggle active status
```

### Embed Entry Point (public, HMAC-verified)

```
GET    /embed/forms/{form_uuid}?...&hmac=...  → Verify HMAC, issue embed JWT, redirect
```

## Middleware Changes

Add to `EMBED_ALLOWED_PATTERNS`:

```python
r"^/api/forms/[^/]+$",           # GET form metadata
r"^/api/forms/[^/]+/execute$",   # POST execute form
r"^/api/forms/[^/]+/startup$",   # POST launch workflow
r"^/api/forms/[^/]+/upload$",    # POST file upload
```

## Frontend Changes

- **ProtectedRoute** for `/execute/:formId`: Allow `EmbedUser` role (currently requires `OrgUser`)
- **RunForm page**: Detect `hasRole("EmbedUser")`, render without layout/nav
- **FormRenderer**: Pass `preventNavigation={true}` when embedded

## What We're NOT Building

- No form slugs (UUID is sufficient for embed URLs)
- No user mapping (external agent_id stays as a workflow param)
- No embed-specific form analytics beyond existing execution logging
- No JWT refresh flow (re-open the tab for a fresh HMAC check + new token)
- No embed settings UI in the form builder (future work — manage secrets via API or admin panel)
