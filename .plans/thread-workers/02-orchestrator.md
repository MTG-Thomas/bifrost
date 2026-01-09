# Phase 2: Orchestrator (Main Process)

## Overview

The orchestrator is the main process that spawns and monitors worker processes. It ensures exactly one ACTIVE process exists at all times and handles spawning replacements when processes drain.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATOR                                │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ RabbitMQ     │  │ Worker       │  │ Redis        │          │
│  │ Consumer     │  │ Manager      │  │ Registration │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         │                 │                  │                   │
│         │    work_queue   │                  │                   │
│         └────────────────►│                  │                   │
│                           │                  │                   │
│                           ▼                  ▼                   │
│              ┌─────────────────────────────────────┐            │
│              │         Worker Processes            │            │
│              │  [ACTIVE]  [DRAINING]  [DRAINING]   │            │
│              └─────────────────────────────────────┘            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Key Responsibilities

1. **Spawn worker processes** - Create new ThreadWorker processes
2. **Route work** - Only send jobs to ACTIVE process
3. **Monitor health** - Detect when processes become DRAINING
4. **Replace processes** - Spawn new ACTIVE when current drains
5. **Handle recycle requests** - API-triggered process recycle
6. **Publish heartbeats** - Real-time status to WebSocket

## Tasks

### 1. Create Orchestrator Class
- [ ] Create `api/src/services/execution/orchestrator.py`
- [ ] Define `WorkerProcess` dataclass with state tracking
- [ ] Implement `Orchestrator` class
- [ ] Add process spawning via `multiprocessing.Process`
- [ ] Set up communication queues (work, result, control)

### 2. Worker Process Management
- [ ] Implement `spawn_worker()` - create new worker process
- [ ] Implement `get_active_worker()` - find ACTIVE process
- [ ] Implement `monitor_workers()` - check process health
- [ ] Handle process exit detection (`process.is_alive()`)
- [ ] Auto-spawn replacement when ACTIVE process starts draining

### 3. Work Routing
- [ ] Implement `route_execution()` - send to ACTIVE worker
- [ ] Queue jobs if no ACTIVE worker (shouldn't happen)
- [ ] Track which worker is handling which execution

### 4. Result Handling
- [ ] Implement `collect_results()` - read from result queue
- [ ] Handle completion results
- [ ] Handle stuck notifications from workers
- [ ] Forward results to RabbitMQ consumer

### 5. Recycle API
- [ ] Implement `recycle_process(pid)` - manual recycle trigger
- [ ] Send control message to specific worker
- [ ] Spawn replacement immediately

### 6. Integration with RabbitMQ Consumer
- [ ] Modify `workflow_execution.py` to use orchestrator
- [ ] Replace direct pool.execute() with orchestrator.route()
- [ ] Handle async result collection

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `api/src/services/execution/orchestrator.py` | CREATE | Main orchestrator class |
| `api/src/services/execution/pool.py` | DEPRECATE | Will be replaced by orchestrator |
| `api/src/jobs/consumers/workflow_execution.py` | MODIFY | Use orchestrator instead of pool |

## Code Structure

```python
# orchestrator.py

from dataclasses import dataclass, field
from datetime import datetime
from multiprocessing import Process, Queue
from typing import Callable
import asyncio
import os

@dataclass
class WorkerProcess:
    process: Process
    pid: int
    state: str  # "active", "draining", "pending_kill"
    work_queue: Queue
    result_queue: Queue
    control_queue: Queue
    started_at: datetime
    jobs_processed: int = 0
    current_executions: dict[str, datetime] = field(default_factory=dict)

class Orchestrator:
    def __init__(
        self,
        thread_count_per_worker: int = 4,
        on_result: Callable | None = None,
    ):
        self.thread_count = thread_count_per_worker
        self.on_result = on_result
        self.workers: list[WorkerProcess] = []
        self._shutdown = False

    async def start(self):
        """Start orchestrator and initial worker."""
        self._spawn_worker()
        asyncio.create_task(self._monitor_loop())
        asyncio.create_task(self._result_loop())

    def _spawn_worker(self) -> WorkerProcess:
        """Spawn a new worker process."""
        work_q = Queue()
        result_q = Queue()
        control_q = Queue()

        from .thread_worker import run_thread_worker

        p = Process(
            target=run_thread_worker,
            args=(work_q, result_q, control_q, self.thread_count),
            name=f"worker-{len(self.workers)}"
        )
        p.start()

        worker = WorkerProcess(
            process=p,
            pid=p.pid,
            state="active",
            work_queue=work_q,
            result_queue=result_q,
            control_queue=control_q,
            started_at=datetime.utcnow(),
        )
        self.workers.append(worker)
        return worker

    def get_active_worker(self) -> WorkerProcess | None:
        """Get the currently ACTIVE worker."""
        for w in self.workers:
            if w.state == "active":
                return w
        return None

    async def route_execution(self, execution_id: str, context: dict):
        """Route execution to active worker."""
        worker = self.get_active_worker()
        if not worker:
            # This shouldn't happen - spawn one
            worker = self._spawn_worker()

        worker.work_queue.put(execution_id)
        worker.current_executions[execution_id] = datetime.utcnow()

    def recycle_process(self, pid: int):
        """Trigger manual recycle of a process."""
        for w in self.workers:
            if w.pid == pid:
                w.state = "pending_kill"
                w.control_queue.put({"action": "recycle"})
                # Spawn replacement immediately
                self._spawn_worker()
                break

    async def _monitor_loop(self):
        """Monitor worker processes."""
        while not self._shutdown:
            for w in list(self.workers):
                # Check if process died
                if not w.process.is_alive():
                    self.workers.remove(w)
                    # If it was ACTIVE, spawn replacement
                    if w.state == "active":
                        self._spawn_worker()

                # Check for state change messages
                # (worker reports it's now draining)

            # Ensure we have an ACTIVE worker
            if not self.get_active_worker():
                self._spawn_worker()

            await asyncio.sleep(1)

    async def _result_loop(self):
        """Collect results from all workers."""
        while not self._shutdown:
            for w in self.workers:
                try:
                    while not w.result_queue.empty():
                        result = w.result_queue.get_nowait()
                        if self.on_result:
                            await self.on_result(result)
                except:
                    pass
            await asyncio.sleep(0.1)
```

## Consumer Integration

```python
# workflow_execution.py changes

class WorkflowExecutionConsumer:
    def __init__(self):
        self.orchestrator = Orchestrator(
            thread_count_per_worker=settings.worker_thread_pool_size,
            on_result=self._handle_result,
        )

    async def start(self):
        await self.orchestrator.start()
        # ... existing RabbitMQ consumer setup

    async def process_message(self, message):
        # ... existing validation, blacklist check ...

        # Route to orchestrator instead of pool
        await self.orchestrator.route_execution(
            execution_id=execution_id,
            context=context_data,
        )

    async def _handle_result(self, result):
        # ... existing result handling (DB update, pub/sub) ...
```

## Testing

### Unit Tests
- [ ] `test_orchestrator.py::test_spawn_worker` - worker process starts
- [ ] `test_orchestrator.py::test_get_active_worker` - returns ACTIVE only
- [ ] `test_orchestrator.py::test_route_execution` - job sent to active worker
- [ ] `test_orchestrator.py::test_recycle_process` - manual recycle triggers drain
- [ ] `test_orchestrator.py::test_auto_replace_draining` - replacement spawned

### Integration Tests
- [ ] Full flow through orchestrator
- [ ] Worker death triggers replacement
- [ ] Multiple workers (one draining, one active)
