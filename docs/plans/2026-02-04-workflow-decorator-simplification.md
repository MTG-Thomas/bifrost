# Workflow Decorator Simplification & Schedule Events

## Overview

Simplify the workflow decorator to identity-only parameters, moving all configuration to database-managed settings. Migrate schedules from a workflow column to the event system, enabling one schedule to trigger multiple workflows.

## Goals

1. **Simpler decorator API** - Only identity params in code, config via UI
2. **Schedules as events** - Consistent with webhooks, 1:many trigger capability
3. **Workflow inputs from triggers** - Subscriptions can define input mappings
4. **Backwards compatible** - Existing code works, unknown params warn (not error)
5. **Complete UI** - Users can manage all workflow configuration without touching code

---

## Part 1: Decorator Changes

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

### Files to Modify

| File | Status | Changes |
|------|--------|---------|
| `api/bifrost/decorators.py` | ✅ DONE | Simplified to identity params, added kwargs warning |
| `api/src/sdk/decorators.py` | ✅ DONE | Same changes (duplicate file) |

---

## Part 2: Database Schema Changes

### New Tables

**schedule_sources:**
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

### Files to Modify

| File | Status | Changes |
|------|--------|---------|
| `api/src/models/orm/workflows.py` | ✅ DONE | Added `display_name` column |
| `api/src/models/orm/events.py` | ✅ DONE | Added `ScheduleSource` model, `input_mapping` column |
| `api/alembic/versions/20260205_...` | ✅ DONE | Migration for all schema changes |

---

## Part 3: Backend API Changes

### Workflow Update API

Extend `WorkflowUpdateRequest` to support all editable fields:

```python
class WorkflowUpdateRequest(BaseModel):
    # Existing fields
    organization_id: str | None = None
    access_level: str | None = None
    clear_roles: bool = False

    # New fields for UI management
    display_name: str | None = None
    timeout_seconds: int | None = None
    execution_mode: str | None = None  # "sync" | "async"

    # Economics
    time_saved: int | None = None
    value: float | None = None

    # Tool configuration
    tool_description: str | None = None

    # Data provider configuration
    cache_ttl_seconds: int | None = None

    # Endpoint configuration
    endpoint_enabled: bool | None = None
    allowed_methods: list[str] | None = None
    public_endpoint: bool | None = None
    disable_global_key: bool | None = None
```

### ScheduleSource CRUD API

New endpoints under `/api/events/sources`:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/events/sources` | Create event source with type='schedule' |
| GET | `/api/events/sources/{id}` | Get source with schedule_source joined |
| PATCH | `/api/events/sources/{id}` | Update source and schedule config |
| DELETE | `/api/events/sources/{id}` | Delete source (cascades to schedule_source) |

### Pydantic Models

```python
class ScheduleSourceConfig(BaseModel):
    """Schedule configuration for event source creation/update."""
    cron_expression: str
    timezone: str = "UTC"
    enabled: bool = True

class EventSourceCreate(BaseModel):
    name: str
    source_type: Literal["webhook", "schedule", "internal"]
    organization_id: str | None = None
    # Type-specific config
    webhook_config: WebhookSourceConfig | None = None
    schedule_config: ScheduleSourceConfig | None = None

class EventSubscriptionCreate(BaseModel):
    workflow_id: str
    event_type: str | None = None
    filter_expression: str | None = None
    input_mapping: dict | None = None  # NEW
```

### Input Mapping Processing

Template syntax for `input_mapping`:
- Static values: `{"report_type": "daily"}`
- Event payload: `{"user_email": "{{ payload.user.email }}"}`
- Schedule context: `{"as_of_date": "{{ scheduled_time }}"}`

Available variables:
- `scheduled_time` - ISO timestamp when schedule fired
- `cron_expression` - The cron expression that triggered
- `payload` - Full event payload (webhooks)
- `headers` - Request headers (webhooks)

### Files to Modify

| File | Status | Changes |
|------|--------|---------|
| `api/src/models/contracts/workflows.py` | ✅ DONE | Extended WorkflowUpdateRequest, added display_name to response |
| `api/src/models/contracts/events.py` | ✅ DONE | Added ScheduleSourceConfig, ScheduleSourceResponse, updated EventSourceCreate/Response |
| `api/src/routers/workflows.py` | ✅ DONE | Handle new update fields in PATCH endpoint |
| `api/src/routers/events.py` | ✅ DONE | Handle schedule source creation, updates, and response building |
| `api/src/repositories/events.py` | ✅ DONE | Added joinedload for schedule_source in queries |
| `api/src/services/events/processor.py` | ✅ DONE | Apply input_mapping template processing when queuing executions |
| `api/src/jobs/schedulers/cron_scheduler.py` | ✅ DONE | Added process_schedule_sources() to query ScheduleSources alongside existing |

---

## Part 4: Frontend - Workflow Configuration UI

### Enhanced WorkflowEditDialog

The existing `WorkflowEditDialog.tsx` handles organization scope and access control only. Extend it to be a comprehensive workflow settings dialog with tabs or sections:

**Tab 1: General**
- Display name (editable, defaults to code name)
- Description (read-only from code)
- Category (editable)
- Tags (editable)

**Tab 2: Execution**
- Timeout (seconds) - number input with reasonable bounds
- Execution mode (sync/async) - select dropdown

**Tab 3: Economics** (collapsed by default)
- Time saved (minutes per execution)
- Value (monetary or custom unit)

**Tab 4: Tool Configuration** (only visible when type='tool')
- Tool description (for LLM - textarea)

**Tab 5: Data Provider Configuration** (only visible when type='data_provider')
- Cache TTL (seconds)

**Tab 6: Access Control** (existing)
- Organization scope
- Access level (authenticated/role_based)
- Role assignments

**Tab 7: HTTP Endpoint** (pull in existing HttpTriggerDialog)
- Endpoint enabled toggle
- Allowed methods checkboxes
- Public endpoint toggle
- API key management

### Files to Modify

| File | Status | Changes |
|------|--------|---------|
| `client/src/components/workflows/WorkflowEditDialog.tsx` | ✅ DONE | Expanded to tabbed interface with all settings |
| `client/src/hooks/useWorkflows.ts` | ✅ DONE | Updated mutation to handle all WorkflowUpdateRequest fields |
| `client/src/lib/v1.d.ts` | ✅ DONE | Regenerated types with new fields |

### Mockup: WorkflowEditDialog

```
┌─────────────────────────────────────────────────────────────┐
│ Edit Workflow Settings                                    X │
│ Configure settings for "sync_users"                         │
├─────────────────────────────────────────────────────────────┤
│ [General] [Execution] [Economics] [Access] [Endpoint]       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Display Name                                                │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ User Sync                                               │ │
│ └─────────────────────────────────────────────────────────┘ │
│ User-facing name (leave empty to use code name)             │
│                                                             │
│ Category                                                    │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ sync                                            ▼       │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ Tags                                                        │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ [users] [m365] [+]                                      │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                              [Cancel]  [Save Changes]       │
└─────────────────────────────────────────────────────────────┘
```

---

## Part 5: Frontend - Schedule Event Sources

### Schedules Page Enhancement

The existing `Schedules.tsx` page shows workflows with schedules. Enhance it to:
1. Show schedule event sources (new model)
2. Allow creating schedule sources
3. Manage subscriptions per schedule

### New Components

**CreateScheduleSourceDialog.tsx**
- Name field
- Cron expression with CronTester component (already exists!)
- Timezone select
- Workflow subscriptions (multi-select or add later)

**ScheduleSourceDetail.tsx** (or integrate into Events page)
- View schedule configuration
- Edit cron/timezone
- Enable/disable toggle
- Manage subscriptions with input mapping

### Files to Modify/Create

| File | Status | Changes |
|------|--------|---------|
| `client/src/pages/Schedules.tsx` | ✅ DONE | Added "Create Schedule" button, schedule event sources table |
| `client/src/components/events/CreateEventSourceDialog.tsx` | ✅ DONE | Enabled schedule type with cron input, timezone, validation, presets |
| `client/src/components/events/EditEventSourceDialog.tsx` | ✅ DONE | Added schedule editing (cron, timezone, enabled toggle) |
| `client/src/components/events/EventSourceDetail.tsx` | ✅ DONE | Added schedule badges (cron, timezone, enabled/disabled) |
| `client/src/components/events/CreateSubscriptionDialog.tsx` | ✅ DONE | Added input_mapping with WorkflowParametersForm |
| `client/src/components/workflows/WorkflowParametersForm.tsx` | ✅ DONE | Added renderAsDiv prop for nested form usage |
| `client/src/services/events.ts` | ✅ DONE | Already had all needed hooks (reused existing) |

### Input Mapping UI

For subscription input mapping, **reuse the existing `WorkflowParametersForm` component**. This is the same form used in:
- Execute workflow dialog
- Code editor workflow execution
- Form builder test launch

When creating/editing a subscription, show the target workflow's parameter form. Users fill in values that will be passed when the schedule triggers. For dynamic values, users type template expressions (e.g., `{{ scheduled_time }}`) into string fields.

No new component needed - just use `WorkflowParametersForm` in controlled mode with `values` and `onChange` props.

### Mockup: CreateScheduleSourceDialog

```
┌─────────────────────────────────────────────────────────────┐
│ Create Schedule                                           X │
│ Schedule workflows to run automatically                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Name                                                        │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Daily User Sync                                         │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ Schedule                                                    │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 0 9 * * *                                               │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ ✓ Valid: Every day at 9:00 AM                          │ │
│ │                                                         │ │
│ │ Next 5 runs:                                            │ │
│ │ • Feb 6, 2026 9:00 AM (in 23 hours)                    │ │
│ │ • Feb 7, 2026 9:00 AM (in 2 days)                      │ │
│ │ • Feb 8, 2026 9:00 AM (in 3 days)                      │ │
│ │ • Feb 9, 2026 9:00 AM (in 4 days)                      │ │
│ │ • Feb 10, 2026 9:00 AM (in 5 days)                     │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ Timezone                                                    │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ America/New_York                                ▼       │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ Quick Presets: [5 min] [Hourly] [Daily 9AM] [Weekly Mon]   │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                   [Cancel]  [Create]        │
└─────────────────────────────────────────────────────────────┘
```

### Mockup: Subscription with Input Mapping

When adding a subscription, show the workflow's parameters using the existing form:

```
┌─────────────────────────────────────────────────────────────┐
│ Add Subscription                                          X │
│ Configure which workflow runs when this schedule fires      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Workflow                                                    │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ generate_report                                 ▼       │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ ── Parameters ──────────────────────────────────────────── │
│                                                             │
│ report_type *                                               │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ daily                                           ▼       │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ include_inactive                                    [ ]     │
│ Include inactive users in the report                        │
│                                                             │
│ as_of_date                                                  │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ {{ scheduled_time }}                                    │ │
│ └─────────────────────────────────────────────────────────┘ │
│ Default: current date                                       │
│                                                             │
│ ℹ️ Use {{ scheduled_time }} for the schedule trigger time  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                   [Cancel]  [Add]           │
└─────────────────────────────────────────────────────────────┘
```

This reuses `WorkflowParametersForm` - the same component used everywhere else for workflow execution.

---

## Part 6: Scheduler Changes

### Current Flow (workflows.schedule)
1. Scheduler queries workflows with non-null schedule
2. Validates cron expression
3. Enqueues workflow execution directly

### New Flow (EventSource + ScheduleSource)
1. Scheduler queries EventSource where type='schedule' with joined ScheduleSource
2. For each enabled schedule, evaluates cron_expression against current time
3. When schedule fires, creates Event record
4. For each EventSubscription linked to this source:
   - Creates EventDelivery (status: PENDING)
   - Applies input_mapping to build workflow inputs
   - Queues workflow execution via existing infrastructure

### Migration Strategy

**Phase 1 (this plan):**
- Build new ScheduleSource infrastructure
- Keep existing workflows.schedule working
- UI creates schedules via new event system

**Phase 2 (future):**
- Migration script to convert workflows.schedule to ScheduleSources
- Deprecation warnings for workflows.schedule
- Remove old scheduler path

### Files to Modify

| File | Status | Changes |
|------|--------|---------|
| `api/src/jobs/schedulers/cron_scheduler.py` | ✅ DONE | Added process_schedule_sources() alongside existing |
| `api/src/services/events/processor.py` | ✅ DONE | Added _process_input_mapping() and _render_template() for template processing |

---

## Implementation Order

### Phase 1: Backend Foundation (Batch 1) ✅ COMPLETE
1. ✅ Simplify `api/bifrost/decorators.py`
2. ✅ Add `display_name` to workflows table
3. ✅ Create `ScheduleSource` model
4. ✅ Add `input_mapping` to EventSubscription
5. ✅ Create migration
6. ✅ Simplify `api/src/sdk/decorators.py` (same changes)

### Phase 2: Backend APIs (Batch 2) ✅ COMPLETE
7. ✅ Extend WorkflowUpdateRequest with all editable fields
8. ✅ Add display_name to workflow response models
9. ✅ Add ScheduleSource Pydantic models
10. ✅ Create schedule source CRUD in events router
11. ✅ Implement input_mapping processing in event processor
12. ✅ Update cron_scheduler to query ScheduleSources

### Phase 3: Frontend - Workflow Settings (Batch 3) ✅ COMPLETE
13. ✅ Extend WorkflowEditDialog to tabbed interface
14. ✅ Add General tab (display_name, category, tags)
15. ✅ Add Execution tab (timeout, mode)
16. ✅ Add Economics tab (time_saved, value)
17. ✅ Add Tool/DataProvider config tabs (conditional)
18. ✅ Integrate endpoint configuration

### Phase 4: Frontend - Schedule Management (Batch 4) ✅ COMPLETE
19. ✅ Enable schedule type in CreateEventSourceDialog (cron input, timezone, presets, validation)
20. ✅ Add schedule info to EventSourceDetail (cron/timezone/enabled badges)
21. ✅ Enhance Schedules page with event sources section and "Create Schedule" button
22. ✅ Add input_mapping support to CreateSubscriptionDialog (WorkflowParametersForm)
23. ✅ Add schedule editing to EditEventSourceDialog (cron, timezone, enabled toggle)

### Phase 5: Testing & Cleanup (Batch 5) ✅ COMPLETE
24. ✅ Unit tests for decorator changes (21 tests pass)
25. ✅ Unit tests for input_mapping template processing (16 new tests)
26. ✅ E2E tests for schedule source CRUD (7 new tests)
27. ✅ E2E tests for schedule source with subscriptions and input_mapping

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
| Workflow settings UI | Org/access only | Full configuration (7 tabs) |
| Schedule management | Manual cron column | Dedicated schedule sources |
