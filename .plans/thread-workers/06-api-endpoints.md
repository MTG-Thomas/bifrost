# Phase 6: Platform Admin API Endpoints

## Overview

Create API endpoints for platform admins to view workers, processes, queue status, and trigger manual recycles.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/platform/workers` | List all workers |
| GET | `/api/platform/workers/{worker_id}` | Get single worker details |
| POST | `/api/platform/workers/{worker_id}/processes/{pid}/recycle` | Recycle a process |
| GET | `/api/platform/queue` | Get queue contents |
| GET | `/api/platform/stuck-history` | Get stuck workflow stats |

## Tasks

### 1. Workers List Endpoint
- [ ] Create `api/src/routers/platform/workers.py`
- [ ] Implement `GET /api/platform/workers`
- [ ] Read worker registrations from Redis
- [ ] Include process and execution counts
- [ ] Require platform admin role

### 2. Worker Detail Endpoint
- [ ] Implement `GET /api/platform/workers/{worker_id}`
- [ ] Return full worker state from latest heartbeat
- [ ] Include all processes and active executions
- [ ] Return 404 if worker not found

### 3. Process Recycle Endpoint
- [ ] Implement `POST /api/platform/workers/{worker_id}/processes/{pid}/recycle`
- [ ] Publish recycle command to worker via Redis pub/sub
- [ ] Return immediately (async operation)
- [ ] Accept optional reason in body

### 4. Queue Endpoint
- [ ] Implement `GET /api/platform/queue`
- [ ] Use queue_tracker to get pending executions
- [ ] Include workflow name, org, queue time
- [ ] Support pagination

### 5. Stuck History Endpoint
- [ ] Implement `GET /api/platform/stuck-history`
- [ ] Query executions table for `error_type='ExecutionStuck'`
- [ ] Aggregate by workflow_id
- [ ] Return count and last stuck timestamp
- [ ] Support time window filter (?hours=24)

### 6. Register Router
- [ ] Add router to main app
- [ ] Set up platform admin authentication

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `api/src/routers/platform/workers.py` | CREATE | Worker management endpoints |
| `api/src/routers/platform/__init__.py` | CREATE | Platform router module |
| `api/src/main.py` | MODIFY | Register platform router |

## Code Structure

```python
# routers/platform/workers.py

from fastapi import APIRouter, Depends, HTTPException
from src.core.auth import require_platform_admin
from src.core.redis_client import get_redis
import json

router = APIRouter(prefix="/api/platform/workers", tags=["Platform Admin - Workers"])


@router.get("")
async def list_workers(
    admin = Depends(require_platform_admin),
):
    """
    List all registered workers.

    Returns workers from Redis with their current state.
    """
    redis = await get_redis()

    # Find all worker keys
    worker_keys = await redis.keys("worker:*")

    workers = []
    for key in worker_keys:
        worker_id = key.split(":", 1)[1]
        data = await redis.hgetall(key)

        # Get latest heartbeat from cache (if available)
        heartbeat_key = f"worker:{worker_id}:heartbeat"
        heartbeat = await redis.get(heartbeat_key)

        worker_info = {
            "worker_id": worker_id,
            "started_at": data.get("started_at"),
            "status": data.get("status"),
            "hostname": data.get("hostname"),
        }

        if heartbeat:
            hb = json.loads(heartbeat)
            worker_info["processes"] = len(hb.get("processes", []))
            worker_info["active_executions"] = sum(
                len(p.get("executions", [])) for p in hb.get("processes", [])
            )
            worker_info["last_heartbeat"] = hb.get("timestamp")

        workers.append(worker_info)

    return {"workers": workers}


@router.get("/{worker_id}")
async def get_worker(
    worker_id: str,
    admin = Depends(require_platform_admin),
):
    """
    Get detailed worker information.

    Returns the latest heartbeat data for the worker.
    """
    redis = await get_redis()

    # Check worker exists
    exists = await redis.exists(f"worker:{worker_id}")
    if not exists:
        raise HTTPException(status_code=404, detail="Worker not found")

    # Get registration data
    data = await redis.hgetall(f"worker:{worker_id}")

    # Get latest heartbeat
    heartbeat_key = f"worker:{worker_id}:heartbeat"
    heartbeat = await redis.get(heartbeat_key)

    result = {
        "worker_id": worker_id,
        "started_at": data.get("started_at"),
        "status": data.get("status"),
        "hostname": data.get("hostname"),
    }

    if heartbeat:
        hb = json.loads(heartbeat)
        result["processes"] = hb.get("processes", [])
        result["queue"] = hb.get("queue", {})
        result["last_heartbeat"] = hb.get("timestamp")

    return result


@router.post("/{worker_id}/processes/{pid}/recycle")
async def recycle_process(
    worker_id: str,
    pid: int,
    request: RecycleRequest = None,
    admin = Depends(require_platform_admin),
):
    """
    Trigger manual recycle of a worker process.

    The process will stop accepting new work, wait for healthy jobs
    to complete, then exit. A new process will be spawned to replace it.
    """
    redis = await get_redis()

    # Check worker exists
    exists = await redis.exists(f"worker:{worker_id}")
    if not exists:
        raise HTTPException(status_code=404, detail="Worker not found")

    # Publish recycle command via Redis pub/sub
    await redis.publish(
        f"worker:{worker_id}:commands",
        json.dumps({
            "action": "recycle_process",
            "pid": pid,
            "reason": request.reason if request else "manual_recycle",
            "requested_by": admin.user_id,
        })
    )

    return {
        "success": True,
        "message": f"Recycle request sent for process {pid}",
    }


# routers/platform/queue.py

@router.get("/queue")
async def get_queue(
    admin = Depends(require_platform_admin),
    limit: int = 50,
    offset: int = 0,
):
    """
    Get pending executions in the queue.

    Returns executions waiting to be picked up by a worker.
    """
    from src.services.execution.queue_tracker import get_all_pending_executions

    all_items = await get_all_pending_executions()

    return {
        "total": len(all_items),
        "items": all_items[offset:offset + limit],
    }


@router.get("/stuck-history")
async def get_stuck_history(
    admin = Depends(require_platform_admin),
    hours: int = 24,
):
    """
    Get aggregated stuck workflow statistics.

    Returns workflows that have had stuck executions in the time window,
    grouped by workflow with count and last occurrence.
    """
    from sqlalchemy import func, desc
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # Query executions table
    query = (
        select(
            Execution.workflow_id,
            Workflow.name.label("workflow_name"),
            func.count(Execution.id).label("stuck_count"),
            func.max(Execution.created_at).label("last_stuck_at"),
        )
        .join(Workflow, Execution.workflow_id == Workflow.id)
        .where(Execution.error_type == "ExecutionStuck")
        .where(Execution.created_at >= cutoff)
        .group_by(Execution.workflow_id, Workflow.name)
        .order_by(desc("stuck_count"))
    )

    results = await db.execute(query)

    return {
        "hours": hours,
        "workflows": [
            {
                "workflow_id": str(row.workflow_id),
                "workflow_name": row.workflow_name,
                "stuck_count": row.stuck_count,
                "last_stuck_at": row.last_stuck_at.isoformat(),
            }
            for row in results
        ]
    }


# Request/Response models

class RecycleRequest(BaseModel):
    reason: str | None = None
```

### Command Listener in Orchestrator

```python
# In orchestrator.py

async def _command_listener(self):
    """Listen for commands from API via Redis pub/sub."""
    pubsub = self.redis.pubsub()
    await pubsub.subscribe(f"worker:{self.worker_id}:commands")

    async for message in pubsub.listen():
        if message["type"] == "message":
            try:
                cmd = json.loads(message["data"])
                await self._handle_command(cmd)
            except Exception as e:
                logger.error(f"Failed to handle command: {e}")

async def _handle_command(self, cmd: dict):
    """Handle command from API."""
    action = cmd.get("action")

    if action == "recycle_process":
        pid = cmd.get("pid")
        reason = cmd.get("reason", "manual_recycle")
        self.recycle_process(pid, reason)

    elif action == "shutdown":
        await self.shutdown()
```

## Testing

### Unit Tests
- [ ] `test_workers_api.py::test_list_workers` - returns workers from Redis
- [ ] `test_workers_api.py::test_get_worker_not_found` - 404 for missing worker
- [ ] `test_workers_api.py::test_recycle_publishes_command` - Redis pub/sub triggered
- [ ] `test_workers_api.py::test_queue_returns_pending` - queue contents returned
- [ ] `test_workers_api.py::test_stuck_history_aggregates` - correct aggregation

### Integration Tests
- [ ] Full recycle flow via API
- [ ] API reflects worker state changes
- [ ] Stuck history shows recent stuck executions
