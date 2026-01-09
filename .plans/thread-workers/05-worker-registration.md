# Phase 5: Worker Registration and Heartbeats

## Overview

Workers register with Redis on startup and publish periodic heartbeats. This enables real-time monitoring in the UI and detecting offline workers.

## Registration Flow

```
Worker Container Starts
        │
        ▼
Register in Redis
(worker:{id} hash with TTL)
        │
        ▼
Publish "worker_online" event
        │
        ▼
    ┌─────────────────┐
    │  Heartbeat Loop │◄───────────┐
    │  (every 10s)    │            │
    └─────────────────┘            │
        │                          │
        ▼                          │
Refresh Redis TTL                  │
Publish heartbeat to WebSocket     │
        │                          │
        └──────────────────────────┘

        │ (on shutdown)
        ▼
Delete Redis key
Publish "worker_offline" event
```

## Tasks

### 1. Worker Registration
- [ ] Generate worker_id from container hostname or UUID
- [ ] Create Redis hash `worker:{worker_id}` with metadata
- [ ] Set TTL on Redis key (30s, refreshed by heartbeat)
- [ ] Store: started_at, status, container info

### 2. Heartbeat Loop
- [ ] Add heartbeat task to orchestrator
- [ ] Refresh Redis TTL every 10s
- [ ] Collect process states and execution info
- [ ] Publish to WebSocket (Azure Web PubSub)

### 3. Heartbeat Payload
- [ ] Worker ID and container info
- [ ] List of processes with state, memory, uptime
- [ ] List of active executions per process
- [ ] Queue depth (from existing queue_tracker)

### 4. WebSocket Events
- [ ] `worker_online` - when worker registers
- [ ] `worker_offline` - when worker shuts down
- [ ] `worker_heartbeat` - periodic status update
- [ ] `process_state_changed` - ACTIVE → DRAINING
- [ ] `execution_stuck` - when execution marked stuck

### 5. Graceful Shutdown
- [ ] Handle SIGTERM in orchestrator
- [ ] Delete Redis registration key
- [ ] Publish worker_offline event
- [ ] Wait for processes to drain (optional timeout)

### 6. Queue Info Integration
- [ ] Use existing `queue_tracker.py` for queue depth
- [ ] Include pending executions in heartbeat
- [ ] Add API endpoint for queue contents

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `api/src/services/execution/orchestrator.py` | MODIFY | Add registration and heartbeat |
| `api/src/core/pubsub.py` | MODIFY | Add worker event types |
| `api/src/config.py` | MODIFY | Add heartbeat interval config |

## Code Structure

### Registration and Heartbeat

```python
# In orchestrator.py

class Orchestrator:
    def __init__(self, ...):
        self.worker_id = os.environ.get("HOSTNAME", str(uuid.uuid4()))
        self.heartbeat_interval = settings.worker_heartbeat_interval_seconds

    async def start(self):
        """Start orchestrator with registration."""
        await self._register_worker()
        self._spawn_worker()
        asyncio.create_task(self._monitor_loop())
        asyncio.create_task(self._result_loop())
        asyncio.create_task(self._heartbeat_loop())

    async def _register_worker(self):
        """Register worker in Redis."""
        await self.redis.hset(
            f"worker:{self.worker_id}",
            mapping={
                "started_at": datetime.utcnow().isoformat(),
                "status": "online",
                "hostname": os.environ.get("HOSTNAME", "unknown"),
            }
        )
        await self.redis.expire(f"worker:{self.worker_id}", 30)

        # Publish online event
        await publish_worker_event({
            "type": "worker_online",
            "worker_id": self.worker_id,
            "started_at": datetime.utcnow().isoformat(),
        })

    async def _heartbeat_loop(self):
        """Periodic heartbeat publishing."""
        while not self._shutdown:
            try:
                # Refresh Redis TTL
                await self.redis.expire(f"worker:{self.worker_id}", 30)

                # Build heartbeat payload
                heartbeat = await self._build_heartbeat()

                # Publish to WebSocket
                await publish_worker_heartbeat(heartbeat)

            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")

            await asyncio.sleep(self.heartbeat_interval)

    async def _build_heartbeat(self) -> dict:
        """Build heartbeat payload with all worker state."""
        from .queue_tracker import get_all_pending_executions

        processes = []
        for w in self.workers:
            proc_info = {
                "pid": w.pid,
                "state": w.state,
                "memory_mb": self._get_process_memory(w.pid),
                "uptime_seconds": (datetime.utcnow() - w.started_at).total_seconds(),
                "jobs_processed": w.jobs_processed,
                "executions": [
                    {
                        "execution_id": eid,
                        "workflow_id": info.get("workflow_id"),
                        "workflow_name": info.get("workflow_name"),
                        "status": self._get_execution_status(eid, w),
                        "started_at": info.get("started_at"),
                        "elapsed_seconds": (datetime.utcnow() - info["started_at"]).total_seconds(),
                    }
                    for eid, info in w.current_executions.items()
                ]
            }
            processes.append(proc_info)

        # Get queue info
        queue_items = await get_all_pending_executions()

        return {
            "type": "worker_heartbeat",
            "worker_id": self.worker_id,
            "timestamp": datetime.utcnow().isoformat(),
            "processes": processes,
            "queue": {
                "depth": len(queue_items),
                "items": queue_items[:20],  # Limit for payload size
            }
        }

    def _get_process_memory(self, pid: int) -> float:
        """Get memory usage for a process in MB."""
        try:
            import psutil
            process = psutil.Process(pid)
            return process.memory_info().rss / 1024 / 1024
        except:
            return 0

    def _get_execution_status(self, exec_id: str, worker) -> str:
        """Get execution status (RUNNING, STUCK, COMPLETING)."""
        if exec_id in worker.stuck_executions:
            return "STUCK"
        handle = worker.current_executions.get(exec_id)
        if handle and handle.cancel_requested_at:
            return "COMPLETING"  # Cancel requested, waiting for response
        return "RUNNING"

    async def shutdown(self):
        """Graceful shutdown."""
        self._shutdown = True

        # Deregister from Redis
        await self.redis.delete(f"worker:{self.worker_id}")

        # Publish offline event
        await publish_worker_event({
            "type": "worker_offline",
            "worker_id": self.worker_id,
        })

        # Shutdown workers
        for w in self.workers:
            w.control_queue.put({"action": "shutdown"})
```

### WebSocket Event Publishing

```python
# In pubsub.py

async def publish_worker_event(event: dict):
    """Publish worker lifecycle event to WebSocket."""
    # Use existing Web PubSub infrastructure
    await pubsub_client.send_to_group(
        group="platform_workers",
        message=event,
    )

async def publish_worker_heartbeat(heartbeat: dict):
    """Publish worker heartbeat to WebSocket."""
    await pubsub_client.send_to_group(
        group="platform_workers",
        message=heartbeat,
    )
```

### Queue Tracker Enhancement

```python
# In queue_tracker.py - add function to get queue contents

async def get_all_pending_executions() -> list[dict]:
    """Get all pending executions with metadata."""
    r = await get_redis()

    # Get execution IDs from sorted set
    exec_ids = await r.zrange(QUEUE_KEY, 0, -1)

    items = []
    for exec_id in exec_ids:
        # Get execution context from Redis
        context = await r.get(f"bifrost:exec:{exec_id}:pending")
        if context:
            data = json.loads(context)
            items.append({
                "execution_id": exec_id,
                "workflow_id": data.get("workflow_id"),
                "workflow_name": data.get("name"),
                "organization_name": data.get("organization", {}).get("name"),
                "queued_at": data.get("queued_at"),
            })

    return items
```

## Testing

### Unit Tests
- [ ] `test_worker_registration.py::test_register_creates_redis_key` - key created
- [ ] `test_worker_registration.py::test_register_sets_ttl` - TTL is 30s
- [ ] `test_worker_registration.py::test_heartbeat_refreshes_ttl` - TTL refreshed
- [ ] `test_worker_registration.py::test_shutdown_deletes_key` - key deleted

### Integration Tests
- [ ] Worker appears in Redis on startup
- [ ] Worker disappears from Redis after TTL (if crashed)
- [ ] Heartbeat contains accurate process info
- [ ] WebSocket receives heartbeat events
