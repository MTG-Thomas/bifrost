# Fork-Based Process Pool Design

**Date:** 2026-04-05
**Status:** Draft
**Scope:** Refactor ProcessPoolManager to use fork-based workers from a template process, with cgroup-aware admission control and support for `min_workers=0` (on-demand) mode.

## Problem

Each worker process uses 200-400MB of RAM (Python interpreter + bifrost SDK + dependencies). With `min_workers=2`, a container idles at 400-800MB. Scaling to handle concurrent load multiplies this linearly — 10 workers can consume 2-4GB. This makes horizontal scaling expensive compared to platforms like Azure Functions that amortize interpreter cost across tenants.

The current `multiprocessing.spawn` model creates a fresh Python interpreter per worker, duplicating the entire runtime in each process's physical memory. Workers are long-lived and reused, but memory recycling is reactive (kill after threshold) rather than structural.

## Solution

Replace `multiprocessing.spawn` with `os.fork()` from a dedicated **template process** that pre-loads all shared dependencies. Forked children share the template's memory pages via copy-on-write (COW), reducing per-worker unique memory from 200-400MB to ~10-30MB (workflow code + execution state only).

Additionally, lower the `min_workers` floor from 2 to 0, enabling **on-demand mode** where children execute once and exit — eliminating idle memory entirely.

## Architecture

### Process Hierarchy

```
Consumer Process (main — owns RabbitMQ, DB, Redis, pub/sub)
    │
    │  pipe (fork requests + responses)
    │
    ▼
Template Process (long-lived, single-threaded, no event loop)
    │  Holds in memory: Python interpreter, bifrost SDK, httpx,
    │  Pydantic, SQLAlchemy, redis client libs, virtual import hook,
    │  user pip packages (site-packages on sys.path)
    │
    ├── fork → Child 1 (creates own event loop + Redis conn, executes workflow)
    ├── fork → Child 2
    └── fork → Child N
```

### Responsibilities

| Process | Owns | Does NOT touch |
|---------|------|----------------|
| Consumer | RabbitMQ connection, DB writes, log flushing, pub/sub updates, result callbacks, timeout monitoring, heartbeat | Workflow execution |
| Template | Loaded dependencies in memory, fork-on-request loop | RabbitMQ, DB, Redis, event loops, threads |
| Child | Redis connection (read context + modules), workflow execution, result queue | RabbitMQ, DB, pub/sub |

### Template Process Constraints

The template process must remain **single-threaded with no event loop** at fork time. This avoids the POSIX fork hazard where inherited mutexes from vanished threads cause deadlocks in children.

The template process NEVER:
- Starts an asyncio event loop
- Opens Redis/DB/RabbitMQ connections
- Spawns background threads
- Initializes thread-based logging handlers

## Template Process Lifecycle

### Startup (consumer boots)

1. Consumer spawns template via `multiprocessing.spawn` (one-time, clean interpreter)
2. Template imports all heavy dependencies (single-threaded)
3. Template installs virtual import hook
4. Template calls `install_requirements()` (pip install user packages)
5. Template adds user site-packages to `sys.path`
6. Template signals "ready" to consumer via pipe
7. Template enters blocking loop: wait for commands on pipe

### Fork Request Flow

1. Consumer sends fork request to template via pipe (includes serialized queue pair or FDs)
2. Template receives request, creates `work_queue, result_queue` (mp.Queue pair)
3. Template calls `os.fork()`
4. **Parent (template):** captures child PID, sends PID + queue references back to consumer via pipe, resumes waiting
5. **Child:** closes template's pipe, inherits queue handles from parent memory, enters worker function
6. **Consumer:** receives PID + queue references, creates ProcessHandle, ready to route work

Note: mp.Queue objects are backed by OS pipes/FDs. The template creates them pre-fork so both template (which discards them after sending refs to consumer) and child inherit working handles. The consumer receives pickled queue objects via the control pipe.

### Pip Install (Template Restart)

1. Consumer receives "recycle all" command (same trigger as today)
2. Consumer drains/kills all active children (existing graceful recycle flow)
3. Consumer sends "shutdown" to template via pipe
4. Template exits cleanly
5. Consumer spawns new template (repeats full startup sequence with fresh packages)
6. If `min_workers > 0`, pre-fork that many children from new template
7. Resume normal operation

### Template Crash Recovery

Consumer detects template death via pipe EOF or process exit. Logs error, spawns replacement. In-flight children are unaffected (independent processes). New forks blocked until replacement template is ready.

## ProcessPoolManager Changes

### Interface (unchanged)

- `route_execution(execution_id, context)` — entry point for work
- Result callback flow
- Timeout monitoring (`_check_timeouts`)
- Cancellation via Redis pub/sub (`bifrost:cancel`)
- Heartbeat loop
- `resize()` for runtime min/max changes
- `mark_for_recycle()` for pip install flow

### Internal Changes

| Current | New |
|---------|-----|
| `multiprocessing.spawn` per worker | `os.fork()` from template process |
| `min_workers >= 2` validation | `min_workers >= 0` |
| Worker always loops, reused across executions | Behavior depends on `min_workers` |
| Recycling: kill + spawn (~1-2s) | Recycling: kill + fork (~50ms) |
| `has_sufficient_memory()` defined but unused | Called before every fork as admission gate |
| `recycle_after_executions` / `recycle_memory_mb` thresholds | Still apply when `min_workers > 0`; irrelevant at 0 (child exits) |

### Admission Control in `route_execution()`

```
1. active_children < max_workers?           → if no, NACK to RabbitMQ (requeue)
2. has_sufficient_memory(threshold)?         → if no, NACK to RabbitMQ (requeue)
3. min_workers > 0 AND idle child available? → route to idle child
4. Otherwise                                 → ask template to fork new child, route to it
```

Rejection NACKs the RabbitMQ message with `requeue=True`. Another consumer with headroom picks it up, or the same consumer retries when memory frees after a child exits.

## Cgroup-Based Memory Admission

### Data Source

Read container-aware memory from cgroup v2 files:
- `/sys/fs/cgroup/memory.current` — current container memory usage (bytes)
- `/sys/fs/cgroup/memory.max` — container memory limit (bytes)

### Logic

```python
memory_usage_ratio = memory_current / memory_max
if memory_usage_ratio > threshold:
    return False  # reject fork
```

### Configuration

- `BIFROST_MEMORY_PRESSURE_THRESHOLD` — float, default `0.85`
- Added to existing settings in `config.py`

### Fallback

If cgroup files aren't readable (local dev without Docker, macOS), admission is permissive (allow fork). `max_workers` still acts as hard cap.

### Interaction with `max_workers`

Both constraints apply independently. A fork only happens if active children < `max_workers` AND memory is below threshold.

## Child Process Behavior

### Communication

Queue-based, same as today:
- `work_queue` (in) — receives execution_id from consumer
- `result_queue` (out) — sends result dict back to consumer

Queue pair created by consumer before fork request. Child inherits queue file descriptors through fork.

### On-Demand Mode (`min_workers=0`)

```
fork → create event loop + Redis conn
     → receive execution_id from work_queue
     → read context from Redis
     → execute workflow
     → put result on result_queue
     → exit
```

No loop. No module clearing. No recycling logic. Consumer cleans up ProcessHandle after collecting result.

### Persistent Mode (`min_workers > 0`)

```
fork → create event loop + Redis conn
     → loop:
         receive execution_id from work_queue
         → clear stale modules (existing logic)
         → execute workflow
         → put result on result_queue
         → repeat
```

Same as today's `run_worker_process` loop. Recycling thresholds (`recycle_after_executions`, `recycle_memory_mb`) apply. Consumer replaces recycled children by requesting a new fork from template.

### Worker Entry Point Change

`simple_worker.py` gets a parameter to control lifecycle:
- On-demand (`min_workers=0`): runs single execution, returns
- Persistent (`min_workers > 0`): loops as today

Both paths share the same `_execute_sync()` call. The existing execution engine, virtual import hook, module clearing, and resource metric collection are unchanged.

## Configuration Changes

### Modified Settings

| Setting | Change |
|---------|--------|
| `min_workers` | Floor lowered from 2 to 0 |

### New Settings

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `memory_pressure_threshold` | `BIFROST_MEMORY_PRESSURE_THRESHOLD` | `0.85` | Reject forks above this ratio of memory.current/memory.max |

### Unchanged Settings

All other pool settings remain as-is: `max_workers`, `execution_timeout_seconds`, `graceful_shutdown_seconds`, `recycle_after_executions`, `recycle_memory_mb`, `worker_heartbeat_interval_seconds`, `worker_registration_ttl_seconds`.

## Testing & Benchmarking

### A/B Comparison

No special test harness. Same pool, same code, different config:
- `BIFROST_MIN_WORKERS=0` — on-demand fork mode
- `BIFROST_MIN_WORKERS=2` — persistent fork mode with COW savings

### Metrics to Capture

| Metric | Method | Source |
|--------|--------|--------|
| Fork latency | Timestamp before fork request → child signals ready | Consumer logs |
| Execution overhead | Compare `duration_ms` for identical workflows at min=0 vs min=2 | Execution records (DB) |
| Container memory | `memory.current` from cgroup, sampled during load | Heartbeat data (Redis) |
| Per-child unique memory | `/proc/{pid}/smaps_rollup` → `Private_Dirty` | New metric in heartbeat |
| Concurrent throughput | Executions/second under sustained load | Load test |
| Cold start penalty | First execution latency after container boot | Execution records |

**Key comparison:** Total container memory at N concurrent executions, fork vs spawn. If 10 concurrent executions use ~1.2GB (fork) vs ~3GB (spawn), the case is made.

### New Tests

- **Unit:** Template process startup/shutdown, fork request/response, admission control rejection
- **Unit:** `min_workers=0` child exits after single execution
- **Unit:** Cgroup admission rejects fork under memory pressure (mock cgroup files)
- **E2E:** Execution completes correctly in fork mode
- **E2E:** Pip install triggers template restart, subsequent execution uses new packages

### Existing Tests

All existing E2E execution tests must pass unchanged — the refactor is internal to the pool. External behavior (route execution, get result, timeout, cancel) is identical.

## Migration Path

This is a drop-in replacement. No database migrations, no API changes, no client changes. The pool starts using fork internally, but the consumer interface is unchanged.

1. Deploy with `min_workers=2` (persistent fork mode) — validates fork works correctly with COW savings
2. Benchmark memory at `min_workers=2` fork vs previous spawn baseline
3. Test `min_workers=0` (on-demand) in staging — benchmark latency and memory
4. If on-demand latency is acceptable, deploy with `min_workers=0` to production
5. If latency-sensitive workflows need warm workers, keep `min_workers > 0` for those containers

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Fork inherits locked mutexes from threads | Template is strictly single-threaded, no event loop. Children create all connections post-fork. |
| C extensions (OpenSSL) misbehave after fork | Python 3.12+ re-initializes OpenSSL. Children create fresh TLS connections. |
| Template crash blocks all new forks | Consumer detects crash, spawns replacement. In-flight children unaffected. |
| Cgroup files not available (local dev) | Fallback to permissive. `max_workers` still acts as hard cap. |
| Fork latency (~50ms) impacts app-serving workflows | Benchmark with `min_workers=0` vs `min_workers=2`. If needed, use persistent mode for latency-sensitive containers. |
