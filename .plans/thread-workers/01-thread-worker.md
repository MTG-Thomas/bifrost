# Phase 1: Thread Pool Worker Process

## Overview

Create a worker process that uses a thread pool instead of spawning a new process per execution. The worker stays alive and processes multiple executions via threads.

## Current Behavior

**File:** `api/src/services/execution/worker.py`

```python
def run_in_worker(execution_id: str):
    """Called when process is spawned - runs ONE execution then exits."""
    asyncio.run(worker_main(execution_id))
    # Process exits after this
```

Each execution spawns a fresh Python process (~300MB), runs the workflow, then exits.

## New Behavior

Worker process stays alive, spawns threads for each execution:

```python
class ThreadWorker:
    """Long-lived worker process with thread pool."""

    def __init__(self, thread_count: int = 4):
        self.thread_count = thread_count
        self.executor = ThreadPoolExecutor(max_workers=thread_count)
        self.active_executions: dict[str, ExecutionHandle] = {}
        self.state = ProcessState.ACTIVE
        self.stuck_executions: set[str] = set()

    async def run(self):
        """Main loop - receive work from orchestrator, dispatch to threads."""
        while self.state != ProcessState.EXITING:
            if self.state == ProcessState.ACTIVE:
                # Accept new work
                execution_id = await self.receive_work()
                if execution_id:
                    self.dispatch_to_thread(execution_id)

            # Monitor running executions
            await self.check_executions()
            await asyncio.sleep(0.25)

    def dispatch_to_thread(self, execution_id: str):
        """Run execution in thread pool."""
        future = self.executor.submit(
            self._run_execution_sync,
            execution_id
        )
        self.active_executions[execution_id] = ExecutionHandle(
            execution_id=execution_id,
            future=future,
            started_at=datetime.utcnow(),
        )
```

## Tasks

### 1. Create Thread Worker Class
- [ ] Create `api/src/services/execution/thread_worker.py`
- [ ] Define `ProcessState` enum: `ACTIVE`, `DRAINING`, `PENDING_KILL`, `EXITING`
- [ ] Define `ExecutionHandle` dataclass with execution tracking
- [ ] Implement `ThreadWorker` class with thread pool
- [ ] Add `dispatch_to_thread()` method
- [ ] Add `check_executions()` monitoring method

### 2. Execution in Thread
- [ ] Implement `_run_execution_sync()` - synchronous wrapper for async execution
- [ ] Handle thread-local event loop creation (`asyncio.run()` per execution)
- [ ] Ensure proper cleanup on thread completion
- [ ] Handle exceptions within threads

### 3. Communication with Orchestrator
- [ ] Implement `receive_work()` - get execution_id from orchestrator (multiprocessing.Queue)
- [ ] Implement `report_result()` - send result back to orchestrator
- [ ] Implement `report_stuck()` - notify orchestrator of stuck execution
- [ ] Handle graceful shutdown signal from orchestrator

### 4. State Management
- [ ] Track active executions with start time
- [ ] Track stuck executions separately
- [ ] Implement state transitions (ACTIVE → DRAINING → EXITING)
- [ ] Stop accepting work when DRAINING

### 5. Entry Point
- [ ] Create `run_thread_worker()` function as multiprocessing target
- [ ] Initialize logging with worker/process ID
- [ ] Set up signal handlers for SIGTERM
- [ ] Install virtual import hook (same as current worker)

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `api/src/services/execution/thread_worker.py` | CREATE | New thread-based worker |
| `api/src/services/execution/worker.py` | MODIFY | Keep for reference, deprecate later |

## Code Structure

```python
# thread_worker.py

from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from multiprocessing import Queue
import asyncio
import threading

class ProcessState(Enum):
    ACTIVE = "active"           # Accepting and processing work
    DRAINING = "draining"       # Not accepting, waiting for healthy jobs
    PENDING_KILL = "pending_kill"  # Manual recycle requested
    EXITING = "exiting"         # About to exit

@dataclass
class ExecutionHandle:
    execution_id: str
    future: Future
    started_at: datetime
    thread_id: int | None = None
    cancel_requested_at: datetime | None = None

class ThreadWorker:
    def __init__(
        self,
        work_queue: Queue,
        result_queue: Queue,
        control_queue: Queue,
        thread_count: int = 4,
    ):
        self.work_queue = work_queue
        self.result_queue = result_queue
        self.control_queue = control_queue
        self.thread_count = thread_count
        self.executor = ThreadPoolExecutor(max_workers=thread_count)
        self.active_executions: dict[str, ExecutionHandle] = {}
        self.stuck_executions: set[str] = set()
        self.state = ProcessState.ACTIVE
        self.jobs_processed = 0

    async def run(self):
        """Main worker loop."""
        while self.state != ProcessState.EXITING:
            # Check for control messages (recycle, shutdown)
            self._check_control_messages()

            # Accept new work if ACTIVE
            if self.state == ProcessState.ACTIVE:
                self._accept_work()

            # Monitor running executions
            await self._check_executions()

            # Check if we should exit (draining complete)
            if self.state in (ProcessState.DRAINING, ProcessState.PENDING_KILL):
                if self._only_stuck_remaining():
                    self.state = ProcessState.EXITING

            await asyncio.sleep(0.25)

        # Cleanup
        self.executor.shutdown(wait=False)
```

## Testing

### Unit Tests
- [ ] `test_thread_worker.py::test_dispatch_execution` - execution runs in thread
- [ ] `test_thread_worker.py::test_multiple_concurrent` - multiple executions in parallel
- [ ] `test_thread_worker.py::test_state_transitions` - ACTIVE → DRAINING → EXITING
- [ ] `test_thread_worker.py::test_stop_accepting_when_draining` - no new work in DRAINING

### Integration Tests
- [ ] Thread worker processes real execution context
- [ ] Results returned via queue to orchestrator
