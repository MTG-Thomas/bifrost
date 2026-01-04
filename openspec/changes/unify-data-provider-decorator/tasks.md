# Tasks: Consolidate Data Providers into Workflows Table

## Overview

Consolidate the `data_providers` table into the `workflows` table with a `type` discriminator field. Update decorators to use shared `ExecutableMetadata` base class. Enable unified execution and update Workflows UI with type filters.

---

## Phase 1: Base Class & Models

### Task 1.1: Create ExecutableMetadata Base Dataclass
**File**: `api/src/services/execution/module_loader.py`

Create `ExecutableMetadata` dataclass with common fields:
- `id`, `name`, `description`, `category`, `tags`
- `timeout_seconds`, `parameters`, `source_file_path`, `function`
- `type: Literal["workflow", "tool", "data_provider"]`

Refactor `WorkflowMetadata` to extend it.
Refactor `DataProviderMetadata` to extend it with `cache_ttl_seconds`.

**Validation**: Existing tests pass, pyright passes.

---

### Task 1.2: Update Pydantic Models
**File**: `api/src/models/contracts/workflows.py`

Add `ExecutableType` enum: `workflow`, `tool`, `data_provider`
Add `type` field to `WorkflowMetadata` model.
Add `cache_ttl_seconds` field.

**Validation**: API models work, pyright passes.

---

## Phase 2: Database Migration

### Task 2.1: Create Migration - Add Columns
**File**: `api/alembic/versions/YYYYMMDD_consolidate_data_providers.py`

Part 1 - Add columns and migrate data:
```python
# Add new columns
op.add_column('workflows', sa.Column('type', sa.String(20), server_default='workflow'))
op.add_column('workflows', sa.Column('cache_ttl_seconds', sa.Integer(), server_default='300'))

# Migrate is_tool to type
op.execute("UPDATE workflows SET type = 'tool' WHERE is_tool = true")
op.execute("UPDATE workflows SET type = 'workflow' WHERE is_tool = false OR is_tool IS NULL")

# Copy data_providers into workflows
op.execute("""
    INSERT INTO workflows (id, org_id, name, function_name, description, file_path,
                           module_path, is_active, last_seen_at, type, cache_ttl_seconds,
                           category, parameters_schema, tags, created_at, updated_at)
    SELECT id, org_id, name, function_name, description, file_path, module_path,
           is_active, last_seen_at, 'data_provider', 300, 'General',
           '[]'::jsonb, '[]'::jsonb, created_at, updated_at
    FROM data_providers
""")

# Update FormField FK
op.drop_constraint('form_fields_data_provider_id_fkey', 'form_fields', type_='foreignkey')
op.create_foreign_key('form_fields_data_provider_id_fkey', 'form_fields', 'workflows',
                      ['data_provider_id'], ['id'], ondelete='SET NULL')

# Create index on type
op.create_index('ix_workflows_type', 'workflows', ['type'])

# Drop is_tool column and index
op.drop_index('ix_workflows_is_tool', 'workflows')
op.drop_column('workflows', 'is_tool')
```

**Validation**: Migration applies and rolls back.

---

### Task 2.2: Create Migration - Drop data_providers Table
**File**: `api/alembic/versions/YYYYMMDD_drop_data_providers.py`

Separate migration to drop `data_providers` table (for safety):
```python
op.drop_table('data_providers')
```

**Validation**: Migration applies after 2.1 completes.

---

### Task 2.3: Update Workflow ORM
**File**: `api/src/models/orm/workflows.py`

- Add `type: Mapped[str]` column with index
- Add `cache_ttl_seconds: Mapped[int]` column
- Remove `is_tool: Mapped[bool]` column
- Delete `DataProvider` class entirely

**Validation**: ORM matches migration, pyright passes.

---

## Phase 3: Repository Updates

### Task 3.1: Update WorkflowRepository
**File**: `api/src/repositories/workflows.py`

- Add `get_by_type(type: str)` method
- Add `get_data_providers()` convenience method
- Add `get_tools()` convenience method
- Update any queries using `is_tool` to use `type` field

**Validation**: Repository methods work correctly.

---

### Task 3.2: Update DataProviderRepository
**File**: `api/src/repositories/data_providers.py`

Change all queries to use `Workflow` model with `type='data_provider'` filter:
- `get_by_name()` - query workflows table
- `get_all_active()` - query workflows table
- `search()` - query workflows table

**Validation**: Data provider queries return correct results.

---

## Phase 4: Decorator Updates

### Task 4.1: Refactor SDK Decorators
**File**: `api/src/sdk/decorators.py`

- Add `_create_executable_metadata()` helper function
- Update `workflow()` to set `type='workflow'`
- Update `tool()` to set `type='tool'` (currently delegates to workflow with is_tool=True)
- Update `data_provider()` to set `type='data_provider'` and accept `tags`, `timeout_seconds`

**Validation**: Decorators set correct type, existing tests pass.

---

### Task 4.2: Update Standalone SDK Decorators
**File**: `api/bifrost/decorators.py`

Mirror changes from Task 4.1.

**Validation**: Consistent with SDK version.

---

## Phase 5: Discovery & Router Updates

### Task 5.1: Update File Storage Discovery
**File**: `api/src/services/file_storage_service.py`

Update data provider discovery to upsert into `workflows` table:
- Set `type='data_provider'`
- Include all metadata fields (tags, timeout_seconds, cache_ttl_seconds)
- Remove references to `DataProvider` ORM model

**Validation**: Discovery populates workflows table with type='data_provider'.

---

### Task 5.2: Update Data Provider Router
**File**: `api/src/routers/data_providers.py`

- Query `Workflow` model with `type='data_provider'` filter
- Update `list_data_providers()` to use workflows table
- Update `invoke_data_provider()` to use workflows table
- Update access control checks to use workflows table

**Validation**: API endpoints return same data, invoke works.

---

### Task 5.3: Update Workflows Router
**File**: `api/src/routers/workflows.py`

- Replace `is_tool` checks with `type` field checks
- Add optional `type` query parameter to list endpoint
- Update any tool-specific logic to use `type='tool'`

**Validation**: Workflows API works correctly.

---

## Phase 6: Execute Endpoint

### Task 6.1: Update Execute Endpoint
**File**: `api/src/routers/workflows.py`

Handle all types via `workflow_id`:
- Check workflow's `type` field
- For `type='data_provider'`, call `run_data_provider()`
- For `type='workflow'` or `type='tool'`, call `run_workflow()`
- Set `result_type` based on workflow's `type`

**Validation**: Execute works for all types.

---

### Task 6.2: Add Return Type Formatting
**File**: `api/src/services/execution/service.py`

For `type='data_provider'` results:
- Normalize to options format (list of {value, label})
- Handle various return types (list[dict], list[str], DataProviderResult)

**Validation**: Data provider results formatted correctly.

---

## Phase 7: MCP Tool Updates

### Task 7.1: Update execute_workflow Tool
**File**: `api/src/services/mcp/server.py`

Update `_execute_workflow_impl()` to handle all types:
- Check workflow's `type` field from database
- Execute appropriately (run_data_provider vs run_workflow)
- Format response based on type

**Validation**: MCP execute tool handles all types.

---

### Task 7.2: Update list_workflows Tool
**File**: `api/src/services/mcp/server.py`

Add optional `type` parameter to filter results.

**Validation**: Can filter MCP list by type.

---

## Phase 8: Frontend - Workflows UI

### Task 8.1: Add Type Filter Toggles
**File**: `client/src/pages/Workflows.tsx`

Add filter toggle buttons: `[All] [Workflows] [Tools] [Data Providers]`

State management:
```typescript
type FilterType = "all" | "workflow" | "tool" | "data_provider";
const [typeFilter, setTypeFilter] = useState<FilterType>("all");
```

**Validation**: Filter toggles render, state updates on click.

---

### Task 8.2: Update Data Display
**File**: `client/src/pages/Workflows.tsx`

- Remove separate data provider fetching (now in same response)
- Filter combined list by `type` field
- Show appropriate badges: "Tool", "Data Provider", "Endpoint"
- Show `cache_ttl_seconds` for data providers

**Validation**: All types display correctly with filtering.

---

### Task 8.3: Update useWorkflows Hook
**File**: `client/src/hooks/useWorkflows.ts`

- Remove separate `useDataProviders` query
- Add `type` to workflow response type
- Simplify to single unified query

**Validation**: Hook returns all types.

---

### Task 8.4: Regenerate TypeScript Types
**Command**: `cd client && npm run generate:types`

Regenerate after backend changes to include:
- `type` field on WorkflowMetadata
- `cache_ttl_seconds` field

**Validation**: Types match backend, no type errors.

---

## Phase 9: Testing & Cleanup

### Task 9.1: Update Unit Tests
**Files**: `api/tests/unit/`

- Update tests using `is_tool` to use `type`
- Update tests for DataProvider to use Workflow
- Add tests for new type-based filtering

---

### Task 9.2: Update Integration Tests
**Files**: `api/tests/integration/`

- Update data provider tests to query workflows table
- Add tests for migration correctness

---

### Task 9.3: Verify Forms/App Builder
**Files**: `api/tests/e2e/api/test_forms.py`

Run existing tests to verify:
- Form field data provider invocation works
- `data_provider_id` FK references work
- No regressions

---

### Task 9.4: Cleanup Unused Code
**Files**: Multiple

- Remove `DataProvider` ORM model
- Remove unused data provider imports
- Clean up any remaining `is_tool` references

---

## Dependency Graph

```
Phase 1 (Models):
1.1 → 1.2

Phase 2 (Database):
2.1 → 2.2 → 2.3

Phase 3 (Repositories) - after 2.3:
3.1 → 3.2

Phase 4 (Decorators) - after 1.1:
4.1 → 4.2

Phase 5 (Discovery/Routers) - after 2.3, 3.2, 4.1:
5.1 → 5.2 → 5.3

Phase 6 (Execute) - after 5.3:
6.1 → 6.2

Phase 7 (MCP) - after 6.1:
7.1 → 7.2

Phase 8 (Frontend) - after 6.1, 8.4 after all backend:
8.1 → 8.2 → 8.3 → 8.4

Phase 9 (Testing) - after all phases:
9.1 → 9.2 → 9.3 → 9.4
```

## Parallel Work

- **1.1, 2.1** can start in parallel
- **4.1, 4.2** can run in parallel
- **8.1-8.3** can start after 6.1 (execute endpoint ready)
- **8.4** after all backend changes
- **9.x** after their dependencies complete
