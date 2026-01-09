# Phase 3: Stuck Detection and Grace Period

## Overview

Detect when a thread doesn't respond to cancellation within a grace period. Mark it as stuck, report to execution history, and trigger process drain.

## Detection Flow

```
Timeout fires OR Cancel requested
            │
            ▼
    Set cancellation flag
    Start grace period timer (10s default)
            │
            ▼
    ┌───────────────────────┐
    │  Thread still alive?  │◄─────────────────┐
    └───────────────────────┘                  │
            │                                  │
       ┌────┴────┐                             │
       │         │                             │
      Yes       No                             │
       │         │                             │
       │         ▼                             │
       │    Completed normally                 │
       │    (return result)                    │
       │                                       │
       ▼                                       │
  Grace period elapsed?                        │
       │                                       │
   ┌───┴───┐                                   │
   │       │                                   │
  Yes     No ──────────────────────────────────┘
   │
   ▼
STUCK - Mark failed, trigger drain
```

## Tasks

### 1. Cancellation Signal Mechanism
- [ ] Add cancellation flag per execution (`threading.Event`)
- [ ] Pass flag to execution context
- [ ] Set flag on timeout or cancel request
- [ ] Check flag in execution code (cooperative cancellation for async code)

### 2. Grace Period Implementation
- [ ] Add `CANCEL_GRACE_SECONDS` config (default 10)
- [ ] Start grace timer when cancellation requested
- [ ] Monitor thread alive status during grace period
- [ ] Detect completion during grace period (not stuck)

### 3. Stuck Detection in Thread Worker
- [ ] Add `check_executions()` method to ThreadWorker
- [ ] Track `cancel_requested_at` timestamp per execution
- [ ] Calculate grace period expiry
- [ ] Mark execution as stuck when grace period exceeds

### 4. Stuck Handling
- [ ] Move execution_id to `stuck_executions` set
- [ ] Report stuck to orchestrator via result queue
- [ ] Transition process state to DRAINING
- [ ] Stop accepting new work

### 5. Execution History Recording
- [ ] Create execution record with `status=FAILED`
- [ ] Set `error_type="ExecutionStuck"`
- [ ] Set `error_message` explaining the stuck condition
- [ ] Include elapsed time before stuck detection

### 6. Drain Logic
- [ ] Continue monitoring healthy (non-stuck) executions
- [ ] Wait for healthy executions to complete (no timeout)
- [ ] Exit process when only stuck executions remain
- [ ] Stuck threads die with the process

## Files to Modify

| File | Action | Description |
|------|--------|-------------|
| `api/src/services/execution/thread_worker.py` | MODIFY | Add stuck detection logic |
| `api/src/config.py` | MODIFY | Add CANCEL_GRACE_SECONDS |
| `api/src/models/enums.py` | VERIFY | Ensure FAILED status exists |

## Code Structure

```python
# In thread_worker.py

@dataclass
class ExecutionHandle:
    execution_id: str
    future: Future
    started_at: datetime
    timeout_seconds: int
    cancel_event: threading.Event = field(default_factory=threading.Event)
    cancel_requested_at: datetime | None = None

class ThreadWorker:
    async def _check_executions(self):
        """Monitor running executions for timeout/stuck."""
        now = datetime.utcnow()

        for exec_id, handle in list(self.active_executions.items()):
            # Skip if already marked stuck
            if exec_id in self.stuck_executions:
                continue

            # Check if future completed
            if handle.future.done():
                await self._handle_completion(exec_id, handle)
                continue

            elapsed = (now - handle.started_at).total_seconds()

            # Check for timeout
            if elapsed > handle.timeout_seconds and not handle.cancel_requested_at:
                self._request_cancel(exec_id, handle, reason="timeout")
                continue

            # Check for stuck (cancel requested but not responding)
            if handle.cancel_requested_at:
                grace_elapsed = (now - handle.cancel_requested_at).total_seconds()
                if grace_elapsed > self.cancel_grace_seconds:
                    await self._mark_stuck(exec_id, handle)

    def _request_cancel(self, exec_id: str, handle: ExecutionHandle, reason: str):
        """Request cancellation of an execution."""
        handle.cancel_event.set()
        handle.cancel_requested_at = datetime.utcnow()
        logger.info(f"Cancellation requested for {exec_id}: {reason}")

    async def _mark_stuck(self, exec_id: str, handle: ExecutionHandle):
        """Mark an execution as stuck and trigger drain."""
        logger.warning(f"Execution {exec_id} marked as STUCK - did not respond to cancellation")

        self.stuck_executions.add(exec_id)

        # Report to orchestrator
        self.result_queue.put({
            "type": "stuck",
            "execution_id": exec_id,
            "elapsed_seconds": (datetime.utcnow() - handle.started_at).total_seconds(),
        })

        # Trigger drain if this is first stuck execution
        if self.state == ProcessState.ACTIVE:
            self.state = ProcessState.DRAINING
            self.control_queue.put({"action": "draining", "reason": "stuck_execution"})
            logger.info("Process transitioning to DRAINING state")

    def _only_stuck_remaining(self) -> bool:
        """Check if only stuck executions remain."""
        active_non_stuck = [
            eid for eid in self.active_executions
            if eid not in self.stuck_executions
        ]
        return len(active_non_stuck) == 0

    async def _handle_completion(self, exec_id: str, handle: ExecutionHandle):
        """Handle completed execution."""
        try:
            result = handle.future.result(timeout=0)
            self.result_queue.put({
                "type": "completed",
                "execution_id": exec_id,
                "result": result,
            })
        except Exception as e:
            self.result_queue.put({
                "type": "error",
                "execution_id": exec_id,
                "error": str(e),
            })

        del self.active_executions[exec_id]
        self.jobs_processed += 1
```

## Execution-Side Cooperative Cancellation

For async code, check cancellation flag:

```python
# In engine.py or execution context

async def execute_with_cancellation(func, cancel_event: threading.Event):
    """Wrapper that checks for cancellation."""
    # For async code, we can check the event between await points
    if cancel_event.is_set():
        raise asyncio.CancelledError("Execution cancelled")

    result = await func()

    if cancel_event.is_set():
        raise asyncio.CancelledError("Execution cancelled")

    return result
```

**Note:** This only helps for async code. Blocking sync code (like `requests.get()` without timeout) won't check the flag - hence the need for process recycling.

## Result Recording

```python
# In orchestrator._handle_result or consumer

async def handle_stuck_result(self, result: dict):
    """Record stuck execution in database."""
    await create_execution_record(
        execution_id=result["execution_id"],
        status=ExecutionStatus.FAILED,
        error_type="ExecutionStuck",
        error_message=(
            f"Execution did not respond to cancellation within "
            f"{self.cancel_grace_seconds} seconds. Worker process recycled."
        ),
        duration_ms=int(result["elapsed_seconds"] * 1000),
    )

    # Trigger circuit breaker check
    await circuit_breaker.record_stuck(workflow_id)
```

## Testing

### Unit Tests
- [ ] `test_stuck_detection.py::test_cancel_sets_flag` - cancellation flag set on timeout
- [ ] `test_stuck_detection.py::test_grace_period_respected` - not stuck during grace period
- [ ] `test_stuck_detection.py::test_marked_stuck_after_grace` - stuck after grace expires
- [ ] `test_stuck_detection.py::test_completion_during_grace` - not stuck if completes
- [ ] `test_stuck_detection.py::test_drain_triggered` - state becomes DRAINING

### Integration Tests
- [ ] Workflow with `while True: pass` gets marked stuck
- [ ] Workflow with `time.sleep(1000)` gets marked stuck
- [ ] Workflow that catches and ignores cancel gets marked stuck
- [ ] Execution history shows `error_type=ExecutionStuck`
