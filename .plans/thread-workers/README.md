# Thread-Based Workflow Execution with Process Recycling

Replace process-per-execution with thread pools inside recyclable processes. When threads get stuck, the process drains and exits while a new process takes over. Includes real-time worker monitoring UI and circuit breaker for repeatedly stuck workflows.

## Summary

- **Threads for efficiency** - No 300MB spawn overhead per execution
- **Process recycling for stuck jobs** - When threads won't die, process drains and exits
- **Never kills healthy jobs** - Waits indefinitely for non-stuck work to complete
- **Circuit breaker** - Auto-blacklists workflows that keep getting stuck
- **Real-time monitoring UI** - Queue → Workers → Executions funnel visualization
- **Execution history integration** - Stuck jobs recorded with `error_type: "ExecutionStuck"`

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    WORKER CONTAINER                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                 ORCHESTRATOR (Main Process)                 │ │
│  │  - Spawns/monitors worker processes                        │ │
│  │  - Ensures exactly 1 ACTIVE process at all times           │ │
│  │  - Registers with Redis on startup                         │ │
│  │  - Publishes heartbeats via WebSocket                      │ │
│  └────────────────────────────────────────────────────────────┘ │
│           │                              │                       │
│           ▼                              ▼                       │
│  ┌─────────────────────┐      ┌─────────────────────┐          │
│  │  Worker Process 1   │      │  Worker Process 2   │          │
│  │  State: DRAINING    │      │  State: ACTIVE      │          │
│  │  ┌─────┐ ┌─────┐    │      │  ┌─────┐ ┌─────┐    │          │
│  │  │ T1  │ │ T2  │    │      │  │ T1  │ │ T2  │    │          │
│  │  │STUCK│ │finish│   │      │  │ ok  │ │ ok  │    │          │
│  │  └─────┘ └─────┘    │      │  └─────┘ └─────┘    │          │
│  └─────────────────────┘      └─────────────────────┘          │
│           │                                                      │
│           ▼                                                      │
│     T2 completes → Process 1 exits (stuck T1 dies with it)      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Plan Files

| Phase | File | Description | Tasks |
|-------|------|-------------|-------|
| 1 | [01-thread-worker.md](./01-thread-worker.md) | Thread pool worker process | 5 sections, ~15 tasks |
| 2 | [02-orchestrator.md](./02-orchestrator.md) | Main process that manages worker processes | 6 sections, ~18 tasks |
| 3 | [03-stuck-detection.md](./03-stuck-detection.md) | Grace period and stuck thread handling | 6 sections, ~16 tasks |
| 4 | [04-circuit-breaker.md](./04-circuit-breaker.md) | Workflow blacklist and auto-disable | 7 sections, ~20 tasks |
| 5 | [05-worker-registration.md](./05-worker-registration.md) | Redis registration and heartbeats | 6 sections, ~15 tasks |
| 6 | [06-api-endpoints.md](./06-api-endpoints.md) | Platform admin API for workers | 6 sections, ~14 tasks |
| 7 | [07-diagnostics-ui.md](./07-diagnostics-ui.md) | Frontend diagnostics page with tabs | 7 sections, ~22 tasks |
| 8 | [08-migration.md](./08-migration.md) | Feature flag and rollout strategy | 5 stages, ~25 tasks |

**Total: ~145 tasks across 8 phases**

## Configuration

### Environment Variables
```bash
WORKER_THREAD_POOL_SIZE=4           # Threads per process
CANCEL_GRACE_SECONDS=10             # Grace period after cancel
WORKER_HEARTBEAT_INTERVAL_SECONDS=10
USE_THREAD_WORKERS=false            # Feature flag
```

### System Config (DB)
```sql
stuck_circuit_breaker_threshold = 5
stuck_circuit_breaker_window_minutes = 60
```

## Testing Strategy

### Unit Tests
- Thread worker executes and returns results
- Stuck detection triggers after grace period
- Circuit breaker trips after threshold
- Process state transitions (ACTIVE → DRAINING → exit)

### Integration Tests
- Full flow: enqueue → thread execution → completion
- Stuck workflow triggers process drain
- Blacklisted workflow rejected at entry point
- Manual recycle via API

### E2E Tests
- Worker monitoring UI shows real-time updates
- Job flows from queue to worker visually
- Recycle button drains process
- Blacklist management works
