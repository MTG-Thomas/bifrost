# Long-Lived Process Pool for Workflow Execution

Replace the complex thread-worker model with a simpler long-lived process pool. Each process handles ONE execution at a time, is reused after completion, and can be monitored/recycled via the UI.

## Status: PIVOT IN PROGRESS

**Previous approach (thread-workers) is being replaced.** The threading model added complexity without solving core issues.

### New Strategy
- **Long-lived worker PROCESSES** (not threads)
- **One execution per process** at a time (no concurrent executions within a process)
- **Process reuse** after completion (no spawn overhead)
- **Simple timeout handling** (SIGTERM → SIGKILL, no stuck detection state machines)
- **Configurable pool sizing** (warm pool + max scaling)
- **Keep observability** (Redis registration, heartbeats, UI monitoring, manual recycle)

### What's Being Removed
- Threading (`ThreadPoolExecutor`)
- Drain states (DRAINING, PENDING_KILL)
- Circuit breaker / auto-blacklisting
- Stuck detection with grace periods
- Blacklist table and UI

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    PROCESS POOL MANAGER                         │
│  (runs in consumer process)                                     │
│                                                                 │
│  - Spawns/monitors worker processes                             │
│  - Routes executions to IDLE processes                          │
│  - Scales pool between min_workers and max_workers              │
│  - Handles timeouts (SIGTERM → wait → SIGKILL)                  │
│  - Publishes heartbeats to Redis/WebSocket                      │
└─────────────────────────────────────────────────────────────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
 ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐
 │ Process 1 │  │ Process 2 │  │ Process 3 │  │ Process 4 │
 │   IDLE    │  │   BUSY    │  │   IDLE    │  │ (scaled)  │
 │           │  │ exec-abc  │  │           │  │           │
 └───────────┘  └───────────┘  └───────────┘  └───────────┘
```

## Process State Model (Simplified)

```
IDLE ──(work assigned)──► BUSY ──(completed)──► IDLE
                           │
                           └──(timeout/crash)──► [process replaced]
```

Only 3 states: `IDLE`, `BUSY`, `KILLED` (no DRAINING, no PENDING_KILL)

## Plan Files

| Phase | File | Description | Status |
|-------|------|-------------|--------|
| 1 | [01-process-pool.md](./01-process-pool.md) | ProcessPoolManager class | ✅ DONE |
| 2 | [02-simple-worker.md](./02-simple-worker.md) | Simplified worker process | ✅ DONE |
| 3 | [03-consumer-integration.md](./03-consumer-integration.md) | Update consumer to use pool | 🔄 IN PROGRESS |
| 4 | [04-api-endpoints.md](./04-api-endpoints.md) | Update platform API | ⬜ TODO |
| 5 | [05-diagnostics-ui.md](./05-diagnostics-ui.md) | Update monitoring UI | ⬜ TODO |
| 6 | [06-cleanup.md](./06-cleanup.md) | Remove old code | ⬜ TODO |
| 7 | [07-log-streaming-fixes.md](./07-log-streaming-fixes.md) | Fix log flush timing & stale UI state | 🔄 IN PROGRESS |

## Configuration

### New Environment Variables
```bash
MIN_WORKERS=2                       # Warm pool (always running)
MAX_WORKERS=10                      # Max processes for scaling
EXECUTION_TIMEOUT_SECONDS=300       # Default 5 minutes
GRACEFUL_SHUTDOWN_SECONDS=5         # Wait after SIGTERM before SIGKILL
RECYCLE_AFTER_EXECUTIONS=0          # 0 = never recycle
WORKER_HEARTBEAT_INTERVAL_SECONDS=10
```

### Removed
```bash
USE_THREAD_WORKERS                  # No longer needed (no feature flag)
WORKER_THREAD_POOL_SIZE             # No threading
CANCEL_GRACE_SECONDS                # No stuck detection
```

## Key Behaviors

### Work Routing
1. Find IDLE process → assign work
2. No IDLE + under max → spawn new process
3. At max capacity → wait for process to finish

### Scaling
- **Up:** On demand when all processes busy (up to max_workers)
- **Down:** In monitor loop, terminate excess idle (down to min_workers)

### Timeout Handling
```
elapsed > timeout_seconds?
  → Send SIGTERM
  → Wait graceful_shutdown_seconds
  → If still alive: SIGKILL
  → Report timeout error
  → Spawn replacement process
```

### Crash Handling
```
process.is_alive() == False while BUSY?
  → Report crash error for execution
  → Remove from pool
  → Spawn replacement if below min_workers
```

## Files Overview

### CREATE
- `api/src/services/execution/process_pool.py` - New pool manager
- `api/src/services/execution/simple_worker.py` - Simplified worker

### MODIFY
- `api/src/jobs/consumers/workflow_execution.py` - Use new pool
- `api/src/config.py` - New settings
- `api/src/routers/platform/workers.py` - Simpler schema
- `client/src/pages/diagnostics/WorkersTab.tsx` - Process view

### DELETE
- `api/src/services/execution/thread_worker.py`
- `api/src/services/execution/orchestrator.py`
- `api/src/services/execution/circuit_breaker.py`
- `api/src/models/orm/workflow_blacklist.py`
- `api/src/routers/platform/blacklist.py`
- `client/src/pages/diagnostics/BlacklistTab.tsx`
- Related migration files

## Testing Strategy

### Unit Tests
- Process pool spawns min_workers on start
- Work routed to idle process
- Scaling up when all busy
- Scaling down when excess idle
- Timeout kills process and spawns replacement
- Crash detected and process replaced

### Integration Tests
- Full flow: enqueue → process execution → completion
- Timeout workflow killed after configured seconds
- Manual recycle via API
- Heartbeat shows correct process states

### E2E Tests
- Worker monitoring UI shows real-time updates
- Job flows from queue to process visually
- Recycle button terminates and replaces process
