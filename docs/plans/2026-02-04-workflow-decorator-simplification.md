# Workflow Decorator Simplification & Schedule Events

## Overview

Simplify the workflow decorator to identity-only parameters, moving all configuration to database-managed settings. Migrate schedules from a workflow column to the event system, enabling one schedule to trigger multiple workflows.

## Goals

1. **Simpler decorator API** - Only identity params in code, config via UI
2. **Schedules as events** - Consistent with webhooks, 1:many trigger capability
3. **Workflow inputs from triggers** - Subscriptions can define input mappings
4. **Backwards compatible** - Existing code works, unknown params warn (not error)

---

## Decorator Changes

### Allowed Parameters

All three decorators accept only identity parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Overrides function name (stable identifier for git matching) |
| `description` | string | Overrides docstring |
| `category` | string | Hint, overridable in UI |
| `tags` | string[] | Hint, overridable in UI |
| `is_tool` | bool | Marks as AI agent tool (`@workflow` only) |

### Examples

```python
# Minimal - uses function name + docstring
@workflow
async def sync_users():
    """Sync users from external system"""
    ...

# With explicit identity
@workflow(name="User Sync", category="sync", tags=["users"])
async def sync_users():
    """Sync users from external system"""
    ...

# Tool shorthand
@tool
async def search_users(query: str):
    """Search for users by name"""
    ...

# Data provider
@data_provider(category="m365")
async def get_m365_users():
    """Returns M365 users for the organization."""
    ...
```

### Removed Parameters (Ignored with Warning)

These parameters are silently ignored with a logged warning:

**From `@workflow`:**
- `schedule`, `timeout_seconds`, `retry_policy`, `execution_mode`
- `endpoint_enabled`, `allowed_methods`, `public_endpoint`, `disable_global_key`
- `tool_description`, `time_saved`, `value`

**From `@data_provider`:**
- `timeout_seconds`, `cache_ttl_seconds`

Warning format: `"Unknown @workflow parameters ignored: schedule, timeout_seconds"`

---

## Schedule Event Sources

### New ScheduleSource Table

```
EventSource (type='schedule')
    └── ScheduleSource
            ├── cron_expression (string, required)
            ├── timezone (string, default 'UTC')
            └── enabled (bool, default true)
    └── EventSubscription → Workflow A
    └── EventSubscription → Workflow B
```

### Schedule Execution Flow

1. Scheduler queries `EventSource` where `type='schedule'` with joined `ScheduleSource`
2. For each enabled schedule, evaluates `cron_expression` against current time
3. When schedule fires, creates `Event` record:
   ```json
   {
     "scheduled_time": "2026-02-04T09:00:00Z",
     "cron_expression": "0 9 * * *"
   }
   ```
4. For each `EventSubscription` linked to this source:
   - Creates `EventDelivery` (status: PENDING)
   - Applies `input_mapping` to build workflow inputs
   - Queues workflow execution
   - Updates delivery status based on result

### Input Mapping

`EventSubscription` gets a new `input_mapping` column (JSON):

```json
{
  "report_type": "daily",
  "include_inactive": false,
  "as_of_date": "{{ scheduled_time }}"
}
```

- Static values passed directly
- Template expressions pull from event payload
- For webhooks: `{{ payload.user.email }}`
- For schedules: `{{ scheduled_time }}`, `{{ cron_expression }}`

---

## Endpoint Configuration

Stays on workflow table, managed via API/UI only (not decorator):

| Column | Type | Description |
|--------|------|-------------|
| `endpoint_enabled` | bool | Expose as HTTP endpoint |
| `allowed_methods` | string[] | HTTP methods (default: POST) |
| `public_endpoint` | bool | Skip authentication |
| `execution_mode` | string | sync/async (only for endpoints) |
| `disable_global_key` | bool | Require workflow-specific API key |

## Other DB-Only Configuration

These remain on workflow table, managed via UI:

| Column | Type | Description |
|--------|------|-------------|
| `display_name` | string | User-facing name (defaults to `name`) |
| `timeout_seconds` | int | Max execution time |
| `retry_policy` | JSON | Retry configuration |
| `cache_ttl_seconds` | int | For data providers |
| `time_saved` | int | ROI metric |
| `value` | float | ROI metric |
| `tool_description` | string | LLM description for tools |

---

## Database Changes

### New Tables

**ScheduleSource:**
```sql
CREATE TABLE schedule_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_source_id UUID NOT NULL REFERENCES event_sources(id) ON DELETE CASCADE,
    cron_expression VARCHAR NOT NULL,
    timezone VARCHAR NOT NULL DEFAULT 'UTC',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_source_id)
);
```

### Modified Tables

**workflows:**
```sql
ALTER TABLE workflows ADD COLUMN display_name VARCHAR;
-- display_name defaults to name when NULL
```

**event_subscriptions:**
```sql
ALTER TABLE event_subscriptions ADD COLUMN input_mapping JSONB;
```

---

## Migration & Backwards Compatibility

### Decorator Parsing

1. Define allowed params in decorator function signature
2. Accept `**kwargs` for unknown params
3. If kwargs non-empty, log warning with param names
4. Continue processing with allowed params only

### Existing Workflows

- Decorator params ignored, config columns retain existing values
- No automatic migration of `schedule` column to ScheduleSource
- Users recreate schedules via UI

### Scheduler Changes

1. Stop querying `workflows.schedule` column
2. Query `EventSource` where `type='schedule'` with joined `ScheduleSource`
3. Use existing event delivery infrastructure

### Future Cleanup

After migration period:
- Remove `schedule` column from workflows table
- Remove deprecated params from decorator dataclasses

---

## Files to Modify

| File | Changes |
|------|---------|
| `api/bifrost/decorators.py` | Simplify params, add unknown param warning |
| `api/src/sdk/decorators.py` | Same changes (duplicate file) |
| `api/src/models/orm/workflows.py` | Add `display_name` column |
| `api/src/models/orm/events.py` | Add `ScheduleSource` model |
| `api/src/models/orm/events.py` | Add `input_mapping` to `EventSubscription` |
| `api/src/models/contracts/events.py` | Add Pydantic models for ScheduleSource |
| `api/src/models/contracts/workflows.py` | Add `display_name` to response models |
| `api/src/routers/events.py` | CRUD for schedule sources |
| `api/src/jobs/schedulers/cron_scheduler.py` | Query ScheduleSources, create events |
| `api/src/services/events/processor.py` | Handle input_mapping application |
| `api/alembic/versions/` | Migration for schema changes |
| `client/` | UI for schedule sources, input mapping, display name |

---

## Summary

| Area | Before | After |
|------|--------|-------|
| `@workflow` params | 18+ params | 5 params (identity only) |
| Schedule storage | `workflows.schedule` column | `ScheduleSource` via events |
| Schedule → workflow | 1:1 | 1:many (via subscriptions) |
| Endpoint config | Decorator | DB-only, managed via UI |
| Workflow inputs | N/A | `input_mapping` on subscription |
| Display name | Same as `name` | Separate, editable in UI |
