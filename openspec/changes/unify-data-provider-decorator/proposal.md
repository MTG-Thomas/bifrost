# Proposal: Consolidate Executables into Unified Workflows Table

## Summary

Consolidate the `data_providers` table into the `workflows` table using a `type` discriminator field. Refactor decorators to use a shared `ExecutableMetadata` base class. Enable unified execution via `/workflows/execute` and update the Workflows UI with type filter toggles.

## Motivation

Currently, the system has two separate tables for executable user code:

- `workflows` table (with `is_tool: bool` for AI tools)
- `data_providers` table (separate)

This leads to:
1. **Duplicated infrastructure** - separate ORM models, repositories, routers, discovery
2. **Inconsistent metadata** - data providers missing fields like `tags`, `timeout_seconds`
3. **Fragmented UI** - data providers not visible on Workflows page
4. **Complex foreign keys** - `FormField.data_provider_id` points to separate table
5. **Separate MCP tools** - no unified execution experience

## Goals

1. **Single table** - consolidate into `workflows` with `type: Literal["workflow", "tool", "data_provider"]`
2. **Shared base class** - `ExecutableMetadata` for all decorator types
3. **Unified execution** - `/workflows/execute` handles all types (superuser-only)
4. **Unified UI** - Workflows page with filter toggles
5. **MCP simplification** - `execute_workflow` handles all types
6. **Preserve forms/apps** - keep `data_provider_id` column name, update FK target

## Architecture

### Database Schema Change

**Before:**
```
workflows (is_tool: bool)
data_providers (separate table)
form_fields.data_provider_id → FK to data_providers.id
```

**After:**
```
workflows (type: "workflow" | "tool" | "data_provider", cache_ttl_seconds: int)
form_fields.data_provider_id → FK to workflows.id
```

### Column Changes to `workflows` Table

| Change | Column | Details |
|--------|--------|---------|
| ADD | `type` | VARCHAR(20), default 'workflow', values: workflow/tool/data_provider |
| ADD | `cache_ttl_seconds` | INTEGER, default 300 (for data providers) |
| DROP | `is_tool` | Replaced by `type='tool'` |

### Class Hierarchy (Runtime Dataclasses)

```
ExecutableMetadata (base)
├── id, name, description, category, tags
├── timeout_seconds, parameters, source_file_path
└── type: Literal["workflow", "tool", "data_provider"]

WorkflowMetadata(ExecutableMetadata)
├── execution_mode, retry_policy, schedule
├── endpoint_enabled, allowed_methods, disable_global_key, public_endpoint
├── tool_description (when type='tool')
└── time_saved, value

DataProviderMetadata(ExecutableMetadata)
├── timeout_seconds = 300 (override)
└── cache_ttl_seconds
```

### Migration Strategy

1. Add `type` and `cache_ttl_seconds` columns to `workflows`
2. Migrate `is_tool=true` → `type='tool'`, `is_tool=false` → `type='workflow'`
3. Copy `data_providers` rows into `workflows` with `type='data_provider'`
4. Update `form_fields.data_provider_id` FK to reference `workflows.id`
5. Drop `is_tool` column
6. Drop `data_providers` table (in separate migration for safety)

## Permission Model

### Execute Endpoint (`/api/workflows/execute`)
- **Superuser-only** for all types (workflow, tool, data_provider)
- Used for testing in code editor and workflows UI
- Workflow `type` determines result formatting

### Invoke Endpoint (`/api/data-providers/{id}/invoke`)
- **Unchanged** - granular role-based access
- Used by forms and app builder
- Queries `workflows` table with `type='data_provider'` filter

### Forms/App Builder
- **Minimal changes** - FK target changes but column name preserved
- Field configuration continues using invoke endpoint
- App builder data sources unchanged

## UI Changes

### Workflows Page

Add filter toggles:
```
[All] [Workflows] [Tools] [Data Providers]
```

- Filter by `type` field (no separate data provider fetch)
- "Data Provider" badge (teal color, Database icon)
- Execute button works for all types
- Same execution page handles all types

## Implementation Overview

### Phase 1: Base Class & Models
- Create `ExecutableMetadata` base dataclass
- Add `type` field to Pydantic models

### Phase 2: Database Migration
- Add columns, migrate data, update FK, drop old table

### Phase 3: Repository Updates
- Query by `type` instead of separate table/`is_tool`

### Phase 4: Decorator Updates
- Set `type` field in all decorators

### Phase 5: Router Updates
- Data provider router queries workflows with type filter
- Workflows router uses `type` field

### Phase 6: Execute Endpoint
- Handle all types via `workflow_id`
- Format result based on `type`

### Phase 7: MCP Tools
- `execute_workflow` handles all types

### Phase 8: Frontend
- Add type filter toggles
- Single API response for all executables

## Files to Modify

### Backend
| File | Changes |
|------|---------|
| `api/src/models/orm/workflows.py` | Add `type`, `cache_ttl_seconds`; remove `is_tool`; delete `DataProvider` |
| `api/src/models/orm/forms.py` | Update FK constraint target |
| `api/src/services/execution/module_loader.py` | Add `ExecutableMetadata` base |
| `api/src/models/contracts/workflows.py` | Add `type` field |
| `api/src/sdk/decorators.py` | Set `type` in all decorators |
| `api/bifrost/decorators.py` | Mirror SDK changes |
| `api/src/repositories/workflows.py` | Add type-based queries |
| `api/src/repositories/data_providers.py` | Query workflows table |
| `api/src/routers/workflows.py` | Use `type` field |
| `api/src/routers/data_providers.py` | Query workflows with type filter |
| `api/src/services/file_storage_service.py` | Upsert data providers to workflows |
| `api/src/services/mcp/server.py` | Update execute tool |
| `api/alembic/versions/...` | Migration scripts |

### Frontend
| File | Changes |
|------|---------|
| `client/src/pages/Workflows.tsx` | Add type filter toggles |
| `client/src/hooks/useWorkflows.ts` | Simplify (no separate DP fetch) |
| `client/src/lib/v1.d.ts` | Regenerate types |

### Unchanged
| File | Reason |
|------|--------|
| `client/src/components/forms/FormRenderer.tsx` | Uses invoke endpoint |
| `client/src/components/forms/FieldConfigDialog.tsx` | Uses invoke endpoint |

## Success Criteria

1. Single `workflows` table with `type` discriminator
2. `ExecutableMetadata` base class working
3. All decorators set appropriate `type`
4. Data providers executable via `/api/workflows/execute`
5. Workflows UI shows all types with filter toggles
6. Forms/app builder continue working (invoke endpoint)
7. `data_providers` table dropped
8. All tests pass
