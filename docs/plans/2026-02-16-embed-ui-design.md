# Embed UI Design

**Goal:** Add admin UI for managing embed secrets and an integration guide, plus change the embed entry point to redirect to the actual app.

## 1. Backend: Embed Entry Point Redirect

Change `GET /embed/apps/{slug}` from returning JSON to issuing a **302 redirect** to `/apps/{appId}` after setting the `embed_token` cookie. The existing app renderer handles rendering inside the iframe.

- Set `embed_token` cookie (already implemented)
- Set CSP `frame-ancestors *` header (already implemented)
- Redirect to `/apps/{appId}` instead of returning JSON
- The app renderer authenticates via the embed token cookie through the existing auth chain

## 2. Frontend: "Embed" Tab on App Editor

Add an "Embed" tab to `AppCodeEditorPage.tsx`.

### Section A: Embed Secrets Management

Table listing secrets with columns:
- Name
- Status (active/inactive badge)
- Created date
- Actions (toggle active, delete)

"Create Secret" button opens a dialog:
- Name input (required)
- Secret input (optional — auto-generates if blank)
- On submit, show one-time reveal dialog with raw secret + copy button

Delete uses AlertDialog for confirmation.

### Section B: Integration Guide

Copyable code snippets dynamically populated with the app's slug:

- **iframe HTML** — `<iframe src="https://{host}/embed/apps/{slug}?param=value&hmac=..."></iframe>`
- **HMAC computation** — Python and JavaScript examples showing how to sign parameters

## 3. Frontend Service Layer

Create `client/src/services/embed-secrets.ts` with typed CRUD operations against `/api/applications/{appId}/embed-secrets`.

## 4. Auth on Render Routes

Embed tokens flow through `get_current_user_optional` → `get_current_user` → `get_current_active_user`. The app render endpoint should accept them automatically. Verify during implementation and adjust if needed.
