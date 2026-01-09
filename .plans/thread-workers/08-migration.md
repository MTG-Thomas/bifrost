# Phase 8: Migration and Rollout Strategy

## Overview

Safely roll out the thread-based worker model using feature flags and gradual deployment.

## Feature Flag

```python
# config.py
USE_THREAD_WORKERS: bool = False  # Default off
```

When `USE_THREAD_WORKERS=false`:
- Use existing process-per-execution model (pool.py)
- No orchestrator, no thread workers
- Current behavior unchanged

When `USE_THREAD_WORKERS=true`:
- Use new thread pool model (orchestrator.py + thread_worker.py)
- Stuck detection, drain, recycle
- Worker registration and heartbeats

## Rollout Stages

### Stage 1: Development (Local)
- [ ] Complete all implementation phases
- [ ] Run full test suite
- [ ] Manual testing with debug.sh
- [ ] Test stuck detection with `while True: pass` workflow
- [ ] Test manual recycle via UI
- [ ] Verify metrics/memory usage

### Stage 2: Staging Deployment
- [ ] Deploy with `USE_THREAD_WORKERS=false` (baseline)
- [ ] Monitor for 24 hours
- [ ] Enable `USE_THREAD_WORKERS=true`
- [ ] Run integration test suite
- [ ] Manual testing of all features
- [ ] Monitor memory, CPU, execution latency
- [ ] Test circuit breaker with intentionally bad workflow

### Stage 3: Production - Canary
- [ ] Deploy to production with flag off
- [ ] Enable flag for single worker instance
- [ ] Monitor for 48-72 hours
- [ ] Check execution success rates
- [ ] Check memory usage vs baseline
- [ ] Check stuck detection accuracy

### Stage 4: Production - Full Rollout
- [ ] Enable flag for all workers
- [ ] Monitor dashboards
- [ ] Keep old code path available (flag off = rollback)
- [ ] Document operational procedures

### Stage 5: Cleanup
- [ ] Remove feature flag
- [ ] Remove old pool.py code
- [ ] Update documentation
- [ ] Archive this plan

## Monitoring Checklist

### Memory
- [ ] Per-worker container memory (docker stats)
- [ ] Memory trend over time (should be stable, not growing)
- [ ] Memory at different concurrency levels

### Executions
- [ ] Execution success rate (should be >= baseline)
- [ ] Execution latency (queue time + execution time)
- [ ] Stuck execution count
- [ ] Blacklisted workflow count

### Process Health
- [ ] Process recycle frequency
- [ ] Average process uptime
- [ ] Drain duration (how long from DRAINING to exit)

### Alerts to Set Up
- [ ] High stuck execution rate (>1% of executions)
- [ ] Worker offline for >5 minutes
- [ ] Circuit breaker trips
- [ ] Process stuck in DRAINING for >1 hour

## Rollback Plan

If issues detected:

1. **Immediate**: Set `USE_THREAD_WORKERS=false` in config
2. **Restart workers**: Workers will use old process-per-execution model
3. **Investigate**: Check logs, stuck executions, memory patterns
4. **Fix**: Address issues found
5. **Re-attempt**: Go back to Stage 2

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_THREAD_WORKERS` | `false` | Feature flag |
| `WORKER_THREAD_POOL_SIZE` | `4` | Threads per process |
| `CANCEL_GRACE_SECONDS` | `10` | Grace period for cancellation |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `10` | Heartbeat frequency |

### System Config (DB)

| Key | Default | Description |
|-----|---------|-------------|
| `stuck_circuit_breaker_threshold` | `5` | Stuck count to trigger blacklist |
| `stuck_circuit_breaker_window_minutes` | `60` | Time window for counting |

## Documentation Updates

- [ ] Update README with new worker architecture
- [ ] Document diagnostics page in admin guide
- [ ] Document circuit breaker behavior
- [ ] Document manual recycle procedure
- [ ] Add troubleshooting guide for stuck executions

## Testing Checklist (Pre-Rollout)

### Unit Tests
- [ ] All new unit tests pass
- [ ] Existing unit tests still pass
- [ ] Coverage meets threshold

### Integration Tests
- [ ] Full execution flow works
- [ ] Stuck detection triggers correctly
- [ ] Circuit breaker trips at threshold
- [ ] Manual recycle works
- [ ] WebSocket events received by UI

### E2E Tests
- [ ] Queue visualization updates in real-time
- [ ] Worker monitoring shows accurate state
- [ ] Recycle button works end-to-end
- [ ] Blacklist management works

### Performance Tests
- [ ] Memory usage under load
- [ ] Execution throughput
- [ ] Startup time (process warm-up)

## Files Summary

### New Files (10)
```
api/src/services/execution/orchestrator.py
api/src/services/execution/thread_worker.py
api/src/services/execution/circuit_breaker.py
api/src/routers/platform/workers.py
api/src/routers/platform/blacklist.py
api/src/models/orm/workflow_blacklist.py
api/alembic/versions/xxx_add_workflow_blacklist.py
client/src/pages/diagnostics/DiagnosticsPage.tsx
client/src/pages/diagnostics/components/WorkersTab.tsx
client/src/pages/diagnostics/components/BlacklistTab.tsx
(+ supporting components)
```

### Modified Files (8)
```
api/src/services/execution/pool.py (deprecate)
api/src/services/execution/worker.py (reference)
api/src/jobs/consumers/workflow_execution.py
api/src/routers/executions.py (blacklist check)
api/src/core/pubsub.py
api/src/config.py
client/src/App.tsx
Navigation components
```

## Success Criteria

- [ ] Memory per execution reduced by >50%
- [ ] No increase in execution failures
- [ ] Stuck executions properly detected and recorded
- [ ] Circuit breaker prevents runaway bad workflows
- [ ] Platform admins can monitor and manage workers via UI
- [ ] Manual recycle works for stuck processes
