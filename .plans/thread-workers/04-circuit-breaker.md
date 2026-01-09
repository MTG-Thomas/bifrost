# Phase 4: Circuit Breaker and Workflow Blacklist

## Overview

Prevent bad workflows from endlessly poisoning processes by blacklisting workflow IDs after repeated stuck executions. This blocks ALL execution paths (schedules, webhooks, API calls, manual runs).

## Circuit Breaker Flow

```
Execution marked STUCK
        │
        ▼
Record stuck in Redis
(workflow_id + timestamp)
        │
        ▼
Count stuck in window
(default: 60 minutes)
        │
        ▼
    Count >= threshold?
    (default: 5)
        │
    ┌───┴───┐
    │       │
   Yes     No
    │       │
    ▼       ▼
Blacklist  Done
workflow
    │
    ▼
Notify platform admins
```

## Tasks

### 1. Database Schema
- [ ] Create migration for `workflow_blacklist` table
- [ ] Add SQLAlchemy model `WorkflowBlacklist`
- [ ] Add unique constraint on `workflow_id`
- [ ] Add foreign key to `workflows` table

### 2. Circuit Breaker Service
- [ ] Create `api/src/services/execution/circuit_breaker.py`
- [ ] Implement `record_stuck(workflow_id)` - track in Redis
- [ ] Implement `check_threshold()` - count stuck in window
- [ ] Implement `blacklist_workflow()` - add to blacklist table
- [ ] Clear Redis counter when removing from blacklist

### 3. System Config
- [ ] Add `stuck_circuit_breaker_threshold` to systemconfig (default: 5)
- [ ] Add `stuck_circuit_breaker_window_minutes` to systemconfig (default: 60)
- [ ] Create getter functions for config values

### 4. Blacklist Enforcement
- [ ] Add `is_workflow_blacklisted()` check function
- [ ] Check blacklist in `workflow_execution.py` consumer (before accepting job)
- [ ] Check blacklist in `executions.py` router (before API accepts request)
- [ ] Return clear error: "Workflow is blacklisted"

### 5. Platform Admin Notification
- [ ] Use existing notification service for platform admins
- [ ] Notification on auto-blacklist with workflow name
- [ ] Include link to blacklist management page

### 6. Blacklist Management API
- [ ] `GET /api/platform/blacklist` - list all blacklisted workflows
- [ ] `POST /api/platform/blacklist` - manually blacklist a workflow
- [ ] `DELETE /api/platform/blacklist/{workflow_id}` - remove from blacklist
- [ ] Require platform admin role

### 7. Audit Trail
- [ ] Track `blacklisted_by` user (null for auto)
- [ ] Track `removed_by` user and `removed_at` timestamp
- [ ] Consider soft delete vs hard delete

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `api/alembic/versions/xxx_add_workflow_blacklist.py` | CREATE | Migration |
| `api/src/models/orm/workflow_blacklist.py` | CREATE | SQLAlchemy model |
| `api/src/services/execution/circuit_breaker.py` | CREATE | Circuit breaker logic |
| `api/src/routers/platform/blacklist.py` | CREATE | Blacklist API endpoints |
| `api/src/jobs/consumers/workflow_execution.py` | MODIFY | Check blacklist |
| `api/src/routers/executions.py` | MODIFY | Check blacklist |

## Database Schema

```sql
-- Migration
CREATE TABLE workflow_blacklist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,  -- "auto:stuck:5" or "manual:admin note"
    blacklisted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    blacklisted_by UUID REFERENCES users(id),  -- NULL for auto
    stuck_count INT,  -- For auto-blacklist, count that triggered it
    removed_at TIMESTAMPTZ,  -- NULL if still blacklisted
    removed_by UUID REFERENCES users(id),
    UNIQUE(workflow_id)
);

CREATE INDEX ix_workflow_blacklist_workflow_id ON workflow_blacklist(workflow_id);
CREATE INDEX ix_workflow_blacklist_active ON workflow_blacklist(workflow_id) WHERE removed_at IS NULL;
```

## Code Structure

### Circuit Breaker

```python
# circuit_breaker.py

import redis.asyncio as redis
from datetime import datetime, timedelta

class CircuitBreaker:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def get_config(self) -> tuple[int, int]:
        """Get threshold and window from system config."""
        # Read from systemconfig table (cached)
        threshold = await get_system_config("stuck_circuit_breaker_threshold", default=5)
        window = await get_system_config("stuck_circuit_breaker_window_minutes", default=60)
        return int(threshold), int(window)

    async def record_stuck(self, workflow_id: str, execution_id: str):
        """Record a stuck execution for circuit breaker tracking."""
        threshold, window_minutes = await self.get_config()

        # Key with timestamp for TTL-based expiry
        key = f"stuck:{workflow_id}:{datetime.utcnow().timestamp()}"
        await self.redis.setex(key, window_minutes * 60, execution_id)

        # Check threshold
        pattern = f"stuck:{workflow_id}:*"
        keys = await self.redis.keys(pattern)
        count = len(keys)

        if count >= threshold:
            await self.blacklist_workflow(workflow_id, count)

    async def blacklist_workflow(self, workflow_id: str, stuck_count: int):
        """Add workflow to blacklist."""
        from src.models.orm.workflow_blacklist import WorkflowBlacklist
        from src.services.notifications import notify_platform_admins

        # Check if already blacklisted
        existing = await get_blacklist_entry(workflow_id)
        if existing and existing.removed_at is None:
            return  # Already blacklisted

        # Get workflow name for notification
        workflow = await get_workflow(workflow_id)
        workflow_name = workflow.name if workflow else "Unknown"

        # Add to blacklist
        await create_blacklist_entry(
            workflow_id=workflow_id,
            reason=f"auto:stuck:{stuck_count}",
            stuck_count=stuck_count,
        )

        # Notify platform admins
        await notify_platform_admins(
            title="Workflow Auto-Blacklisted",
            message=(
                f"Workflow '{workflow_name}' was blacklisted after {stuck_count} "
                f"stuck executions. All execution paths are blocked until manually re-enabled."
            ),
            severity="warning",
            link="/platform/diagnostics?tab=blacklist",
        )

    async def clear_stuck_counter(self, workflow_id: str):
        """Clear stuck counter when removing from blacklist."""
        pattern = f"stuck:{workflow_id}:*"
        keys = await self.redis.keys(pattern)
        if keys:
            await self.redis.delete(*keys)
```

### Blacklist Check

```python
# In workflow_execution.py consumer

async def process_message(self, message):
    workflow_id = context.get("workflow_id")

    # Check blacklist FIRST
    if await is_workflow_blacklisted(workflow_id):
        # Don't even start - reject immediately
        await create_execution_record(
            execution_id=execution_id,
            status=ExecutionStatus.FAILED,
            error_type="WorkflowBlacklisted",
            error_message="Workflow is blacklisted due to repeated stuck executions.",
        )
        # Ack message so it doesn't retry
        return

    # ... proceed with execution
```

### Blacklist API

```python
# routers/platform/blacklist.py

from fastapi import APIRouter, Depends
from src.core.auth import require_platform_admin

router = APIRouter(prefix="/api/platform/blacklist", tags=["Platform Admin"])

@router.get("")
async def list_blacklisted_workflows(
    admin = Depends(require_platform_admin),
):
    """List all blacklisted workflows."""
    return await get_all_blacklisted_workflows()

@router.post("")
async def blacklist_workflow(
    request: BlacklistWorkflowRequest,
    admin = Depends(require_platform_admin),
):
    """Manually blacklist a workflow."""
    await create_blacklist_entry(
        workflow_id=request.workflow_id,
        reason=f"manual:{request.reason}",
        blacklisted_by=admin.user_id,
    )
    return {"success": True}

@router.delete("/{workflow_id}")
async def remove_from_blacklist(
    workflow_id: str,
    admin = Depends(require_platform_admin),
):
    """Remove workflow from blacklist."""
    await remove_blacklist_entry(
        workflow_id=workflow_id,
        removed_by=admin.user_id,
    )
    # Clear stuck counter for fresh start
    await circuit_breaker.clear_stuck_counter(workflow_id)
    return {"success": True}
```

## Testing

### Unit Tests
- [ ] `test_circuit_breaker.py::test_record_stuck_increments` - counter increases
- [ ] `test_circuit_breaker.py::test_threshold_triggers_blacklist` - blacklist at threshold
- [ ] `test_circuit_breaker.py::test_below_threshold_no_blacklist` - no blacklist below threshold
- [ ] `test_circuit_breaker.py::test_window_expiry` - old stuck entries expire
- [ ] `test_circuit_breaker.py::test_already_blacklisted_no_duplicate` - idempotent

### Integration Tests
- [ ] Workflow gets blacklisted after 5 stuck executions
- [ ] Blacklisted workflow rejected at API
- [ ] Blacklisted workflow rejected at consumer
- [ ] Platform admin notification sent
- [ ] Remove from blacklist clears counter
- [ ] Manual blacklist works
