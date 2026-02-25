# SDK-First Skills Refresh

Date: 2026-02-24

## Problem

The bifrost-build and bifrost-setup skills reference `bifrost push --watch` (now `bifrost watch`), rely heavily on MCP tools for operations that `bifrost api` can handle directly, and don't leverage the CLI's authenticated API access for schema documentation.

## Changes

### 1. Add sync execution to REST endpoint

**File:** `api/src/routers/workflows.py`, `api/src/models/contracts/executions.py`

Add optional `sync` field to `WorkflowExecutionRequest` (default: `None`). When `True`, pass `sync=True` to `run_workflow` regardless of the workflow's `execution_mode` setting. When `False` or `None`, use existing behavior (respect `execution_mode`).

This lets `bifrost api POST /api/workflows/{id}/execute '{"workflow_id":"...","input_data":{...},"sync":true}'` block until completion, matching the MCP `execute_workflow` behavior.

### 2. Add REST doc endpoints

**New file:** `api/src/routers/docs.py`

Add lightweight REST endpoints that return the same documentation the MCP schema tools generate:

| Endpoint | Returns | Source |
|----------|---------|--------|
| `GET /api/docs/sdk` | SDK decorator/module docs | Same as `get_sdk_schema` MCP tool |
| `GET /api/docs/apps` | App platform overview | Same as `get_app_schema` MCP tool |
| `GET /api/docs/forms` | Form field type docs | Same as `get_form_schema` MCP tool |
| `GET /api/docs/agents` | Agent structure docs | Same as `get_agent_schema` MCP tool |
| `GET /api/docs/tables` | Table schema docs | Same as `get_table_schema` MCP tool |
| `GET /api/docs/data-providers` | Data provider docs | Same as `get_data_provider_schema` MCP tool |

Each endpoint returns `{"content": "<markdown>"}`. These reuse the existing MCP tool functions (refactored to extract the doc generation from the MCP context wrapper).

Usage: `bifrost api GET /api/docs/sdk > /tmp/bifrost-docs/sdk.md` then grep locally.

### 3. Update bifrost-build skill

Major rewrite of the SDK-first section:

**Watch mode as primary dev loop:**
- Auto-start `bifrost watch` as a background Bash task when entering SDK-first build mode
- Check if already running first (via `ps aux | grep 'bifrost watch'`)
- Document when watch may need attention: manifest validation errors print to its output

**Replace MCP discovery with `bifrost api`:**

| Need | MCP (old) | CLI (new) |
|------|-----------|-----------|
| List workflows | `list_workflows` | `bifrost api GET /api/workflows` |
| Get workflow details | `get_workflow` | `bifrost api GET /api/workflows/{id}` |
| Execute workflow (sync) | `execute_workflow` | `bifrost api POST /api/workflows/{id}/execute '{"workflow_id":"...","sync":true}'` |
| List/get executions | `list_executions`, `get_execution` | `bifrost api GET /api/executions[/{id}]` |
| List forms | `list_forms` | `bifrost api GET /api/forms` |
| List agents | `list_agents` | `bifrost api GET /api/agents` |
| List apps | `list_apps` | `bifrost api GET /api/applications` |
| List integrations | `list_integrations` | `bifrost api GET /api/integrations` |
| List tables | `list_tables` | `bifrost api GET /api/tables` |
| SDK/schema docs | MCP schema tools | Download once: `bifrost api GET /api/docs/sdk`, grep locally |

**MCP tools still used for:**
- `create_form`, `create_app`, `create_agent` — platform-managed artifacts with complex creation logic
- `create_event_source`, `create_event_subscription` — event management
- `search_knowledge` — RAG search
- `patch_content`, `replace_content` — remote file editing (MCP-only mode)

**Remove MCP-only mode?** No — keep as secondary path, but SDK-first is the default when `.bifrost/` exists.

### 4. Update bifrost-setup skill

Minor changes:
- Note that MCP is optional for SDK-first development (CLI covers most needs)
- MCP still recommended for form/app creation and knowledge search

### 5. Update integration docs

**File:** `bifrost-integrations-docs/src/content/docs/how-to-guides/local-dev/ai-coding.md`

- Replace `bifrost push --watch` with `bifrost watch`
- Add `bifrost api` examples

## Implementation Order

1. Add `sync` field to `WorkflowExecutionRequest` and wire it through
2. Extract doc generation functions from MCP tools into shared module
3. Add `api/src/routers/docs.py` with REST doc endpoints
4. Update bifrost-build skill
5. Update bifrost-setup skill
6. Update integration docs
7. Tests: unit test for sync execution, test doc endpoints return content
