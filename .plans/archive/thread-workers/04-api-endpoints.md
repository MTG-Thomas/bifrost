# Phase 4: API Endpoints

Update the platform API endpoints to work with the simplified process pool model.

## Overview

Changes to API:
- Simplify worker info schema (no thread details)
- Keep recycle endpoint
- Update heartbeat payload structure
- Remove blacklist endpoints

## Files to Modify

- `api/src/routers/platform/workers.py` - Worker endpoints
- `api/src/models/contracts/platform.py` - API schemas

## Implementation Tasks

### Task 4.1: Update worker info schema

**Before:**
```python
class ProcessInfo(BaseModel):
    pid: int
    worker_id: str
    state: str  # active, draining, pending_kill
    memory_mb: float
    uptime_seconds: float
    jobs_processed: int
    executions: list[ExecutionInfo]  # Multiple per process

class WorkerInfo(BaseModel):
    worker_id: str
    started_at: datetime
    hostname: str
    thread_count: int
    processes: list[ProcessInfo]
```

**After:**
```python
class ProcessInfo(BaseModel):
    pid: int
    process_id: str
    state: str  # idle, busy, killed
    memory_mb: float
    uptime_seconds: float
    executions_completed: int
    execution: ExecutionInfo | None  # Current execution (if busy)

class ExecutionInfo(BaseModel):
    execution_id: str
    started_at: datetime
    elapsed_seconds: float

class WorkerInfo(BaseModel):
    worker_id: str
    started_at: datetime
    hostname: str
    min_workers: int
    max_workers: int
    pool_size: int
    idle_count: int
    busy_count: int
    processes: list[ProcessInfo]
```

### Task 4.2: Update GET /api/platform/workers

```python
@router.get("/workers")
async def get_workers() -> list[WorkerInfo]:
    """Get all registered workers and their process pools."""
    redis = await get_redis_client()

    # Scan for worker keys
    workers = []
    async for key in redis.scan_iter(match="worker:*"):
        worker_data = await redis.hgetall(key)
        if worker_data:
            workers.append(WorkerInfo(
                worker_id=key.split(":")[1],
                started_at=datetime.fromisoformat(worker_data["started_at"]),
                hostname=worker_data.get("hostname", "unknown"),
                min_workers=int(worker_data.get("min_workers", 2)),
                max_workers=int(worker_data.get("max_workers", 10)),
                pool_size=int(worker_data.get("pool_size", 0)),
                idle_count=int(worker_data.get("idle_count", 0)),
                busy_count=int(worker_data.get("busy_count", 0)),
                processes=[],  # Populated from heartbeat
            ))

    return workers
```

### Task 4.3: Keep recycle endpoint (simplified)

```python
@router.post("/workers/{worker_id}/recycle")
async def recycle_worker_process(
    worker_id: str,
    pid: int | None = None,
) -> dict:
    """
    Recycle a worker process.

    If pid is provided, recycle that specific process.
    Otherwise, recycle any idle process.
    """
    # Publish recycle command to Redis
    redis = await get_redis_client()
    await redis.publish(
        f"worker:{worker_id}:commands",
        json.dumps({
            "type": "recycle",
            "pid": pid,
        })
    )

    return {"status": "recycle_requested", "worker_id": worker_id, "pid": pid}
```

### Task 4.4: Update queue endpoint (keep as-is)

The queue tracking endpoint should remain mostly unchanged:

```python
@router.get("/queue")
async def get_execution_queue() -> QueueStatus:
    """Get current execution queue status."""
    # Keep existing implementation from queue_tracker.py
    pass
```

### Task 4.5: Remove blacklist endpoints

**Delete these endpoints:**

```python
# DELETE
@router.get("/blacklist")
async def get_blacklisted_workflows():
    ...

@router.post("/blacklist")
async def blacklist_workflow():
    ...

@router.delete("/blacklist/{workflow_id}")
async def remove_from_blacklist():
    ...
```

### Task 4.6: Update router registration

In `api/src/routers/platform/__init__.py`:

```python
from .workers import router as workers_router
# REMOVE: from .blacklist import router as blacklist_router

platform_router.include_router(workers_router, prefix="/workers", tags=["workers"])
# REMOVE: platform_router.include_router(blacklist_router, prefix="/blacklist", tags=["blacklist"])
```

### Task 4.7: Update heartbeat WebSocket payload

The WebSocket heartbeat should match the new schema:

```python
# In pubsub.py or wherever heartbeats are published

async def publish_worker_heartbeat(heartbeat: dict) -> None:
    """Publish worker heartbeat via WebSocket and Redis."""
    # WebSocket broadcast
    await broadcast_to_channel(
        channel="workers",
        message={
            "type": "worker_heartbeat",
            "data": heartbeat,
        }
    )

    # Redis publish for other consumers
    redis = await get_redis_client()
    await redis.publish("bifrost:workers:heartbeat", json.dumps(heartbeat))
```

## API Tests

- [ ] `test_get_workers_returns_pool_info`
- [ ] `test_recycle_endpoint_publishes_command`
- [ ] `test_queue_endpoint_returns_status`
- [ ] `test_heartbeat_schema_matches`

## Checklist

- [ ] Worker info schema simplified
- [ ] ProcessInfo updated for single execution
- [ ] GET /workers returns pool info
- [ ] Recycle endpoint works
- [ ] Blacklist endpoints removed
- [ ] Router registration updated
- [ ] Heartbeat schema matches frontend expectations
- [ ] API tests passing
