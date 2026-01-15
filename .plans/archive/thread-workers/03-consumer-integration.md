# Phase 3: Consumer Integration

Update the workflow execution consumer to use the new ProcessPoolManager instead of the thread-worker orchestrator.

## Overview

Changes to `api/src/jobs/consumers/workflow_execution.py`:
- Replace orchestrator with ProcessPoolManager
- Simplify result handling (no stuck/draining types)
- Remove feature flag conditional
- Keep SDK prewarm, context writing, finalization logic

## Files to Modify

- `api/src/jobs/consumers/workflow_execution.py` - Main consumer
- `api/src/config.py` - New settings

## Implementation Tasks

### Task 3.1: Update config.py with new settings

```python
# In Settings class

# Process Pool Configuration
min_workers: int = Field(
    default=2,
    description="Minimum worker processes (warm pool)"
)
max_workers: int = Field(
    default=10,
    description="Maximum worker processes for scaling"
)
execution_timeout_seconds: int = Field(
    default=300,
    description="Default execution timeout (5 minutes)"
)
graceful_shutdown_seconds: int = Field(
    default=5,
    description="Seconds after SIGTERM before SIGKILL"
)
recycle_after_executions: int = Field(
    default=0,
    description="Recycle process after N executions (0 = never)"
)

# REMOVE these settings:
# use_thread_workers: bool
# worker_thread_pool_size: int
# cancel_grace_seconds: int
```

### Task 3.2: Replace orchestrator initialization

**Before:**
```python
class WorkflowExecutionConsumer:
    def __init__(self):
        self._use_thread_workers = settings.use_thread_workers
        if self._use_thread_workers:
            self._orchestrator = Orchestrator(...)
```

**After:**
```python
from api.src.services.execution.process_pool import ProcessPoolManager

class WorkflowExecutionConsumer:
    def __init__(self):
        self._pool = ProcessPoolManager(
            min_workers=settings.min_workers,
            max_workers=settings.max_workers,
            execution_timeout_seconds=settings.execution_timeout_seconds,
            graceful_shutdown_seconds=settings.graceful_shutdown_seconds,
            recycle_after_executions=settings.recycle_after_executions,
            on_result=self._handle_result,
        )
```

### Task 3.3: Update startup/shutdown

**Before:**
```python
async def start(self):
    if self._use_thread_workers:
        await self._orchestrator.start()
    # ...

async def stop(self):
    if self._use_thread_workers:
        await self._orchestrator.stop()
```

**After:**
```python
async def start(self):
    await self._pool.start()
    # ... rest of consumer startup

async def stop(self):
    await self._pool.stop()
    # ... rest of consumer shutdown
```

### Task 3.4: Update message processing

**Before:**
```python
async def process_message(self, message):
    # ... validation, context building ...

    if self._use_thread_workers:
        await self._orchestrator.route_execution(execution_id, context)
    else:
        await self._execute_with_process_pool(execution_id, context)
```

**After:**
```python
async def process_message(self, message):
    # ... validation, context building ...

    # Route to process pool (no feature flag)
    await self._pool.route_execution(execution_id, context)
```

### Task 3.5: Simplify result handling

**Before:**
```python
async def _handle_orchestrator_result(self, msg: dict):
    msg_type = msg.get("type")

    if msg_type == "result":
        await self._process_execution_result(msg)
    elif msg_type == "stuck":
        await self._process_stuck_execution(msg)
    elif msg_type == "draining":
        logger.info(f"Worker draining: {msg}")
```

**After:**
```python
async def _handle_result(self, result: dict) -> None:
    """Handle result from process pool."""
    execution_id = result.get("execution_id")

    if result.get("success"):
        await self._process_success(execution_id, result)
    else:
        await self._process_failure(execution_id, result)

async def _process_success(self, execution_id: str, result: dict):
    # Update database
    await update_execution(
        execution_id=execution_id,
        status=ExecutionStatus.SUCCESS,
        result=result.get("result"),
        duration_ms=result.get("duration_ms"),
        execution_model="process",
    )

    # Flush logs and SDK writes
    await flush_pending_changes(execution_id)
    await flush_logs_to_postgres(execution_id)

    # Publish WebSocket update
    await publish_execution_status(execution_id, "SUCCESS")

    # Cleanup Redis
    await cleanup_execution_cache(execution_id)

async def _process_failure(self, execution_id: str, result: dict):
    error_type = result.get("error_type", "ExecutionError")

    # Determine status based on error type
    if error_type == "TimeoutError":
        status = ExecutionStatus.TIMEOUT
    else:
        status = ExecutionStatus.FAILED

    await update_execution(
        execution_id=execution_id,
        status=status,
        error_message=result.get("error"),
        error_type=error_type,
        duration_ms=result.get("duration_ms"),
        execution_model="process",
    )

    # Flush what we have
    await flush_pending_changes(execution_id)
    await flush_logs_to_postgres(execution_id)

    # Publish WebSocket update
    await publish_execution_status(execution_id, status.value)

    # Cleanup Redis
    await cleanup_execution_cache(execution_id)
```

### Task 3.6: Keep SDK prewarm logic

The SDK cache prewarm should still happen in the consumer's main loop before routing:

```python
async def process_message(self, message):
    # ... extract execution details ...

    # Prewarm SDK cache (runs in consumer's event loop)
    if workflow_id:
        await prewarm_sdk_cache(workflow_id, organization_id)

    # Build context
    context = self._build_execution_context(...)

    # Route to pool
    await self._pool.route_execution(execution_id, context)
```

### Task 3.7: Remove blacklist checking

**Remove** this code (circuit breaker is being deleted):

```python
# REMOVE
if workflow_id and not is_script:
    circuit_breaker = get_circuit_breaker()
    if await circuit_breaker.is_workflow_blacklisted(workflow_id):
        await update_execution(
            execution_id=execution_id,
            status=ExecutionStatus.BLOCKED,
            ...
        )
        return
```

### Task 3.8: Update execution_model field

All executions should now be labeled `"process"`:

```python
await update_execution(
    execution_id=execution_id,
    status=...,
    execution_model="process",  # Always "process" now
)
```

Remove `"thread"` and `"blocked"` labels.

## Integration Tests

- [ ] `test_consumer_routes_to_process_pool`
- [ ] `test_successful_execution_updates_db`
- [ ] `test_failed_execution_updates_db`
- [ ] `test_timeout_reported_correctly`
- [ ] `test_sdk_prewarm_called_before_routing`

## Checklist

- [ ] Config updated with new settings
- [ ] Old settings removed
- [ ] ProcessPoolManager initialized in consumer
- [ ] Feature flag conditional removed
- [ ] Result handling simplified
- [ ] Blacklist checking removed
- [ ] SDK prewarm preserved
- [ ] Finalization logic preserved
- [ ] Integration tests passing
