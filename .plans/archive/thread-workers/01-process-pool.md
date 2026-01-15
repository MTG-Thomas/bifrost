# Phase 1: ProcessPoolManager

Create the core process pool manager that spawns, monitors, and routes work to long-lived worker processes.

## Overview

The ProcessPoolManager:
- Runs in the consumer process
- Spawns `min_workers` processes on startup
- Scales up to `max_workers` under load
- Routes executions to IDLE processes
- Monitors for timeouts and crashes
- Publishes heartbeats for UI visibility

## File: `api/src/services/execution/process_pool.py`

## Data Structures

### ProcessState Enum
```python
class ProcessState(Enum):
    IDLE = "idle"       # Ready to accept work
    BUSY = "busy"       # Currently executing
    KILLED = "killed"   # Process was terminated
```

### ProcessHandle Dataclass
```python
@dataclass
class ProcessHandle:
    id: str                              # e.g., "process-1"
    process: multiprocessing.Process
    pid: int | None
    state: ProcessState
    work_queue: multiprocessing.Queue    # Send execution_id TO process
    result_queue: multiprocessing.Queue  # Receive result FROM process
    started_at: datetime
    current_execution: ExecutionInfo | None
    executions_completed: int
```

### ExecutionInfo Dataclass
```python
@dataclass
class ExecutionInfo:
    execution_id: str
    started_at: datetime
    timeout_seconds: int
```

## Implementation Tasks

### Task 1.1: Create ProcessPoolManager class skeleton
- [ ] Create `api/src/services/execution/process_pool.py`
- [ ] Define `ProcessState` enum
- [ ] Define `ProcessHandle` dataclass
- [ ] Define `ExecutionInfo` dataclass
- [ ] Create `ProcessPoolManager` class with `__init__`

```python
class ProcessPoolManager:
    def __init__(
        self,
        min_workers: int = 2,
        max_workers: int = 10,
        execution_timeout_seconds: int = 300,
        graceful_shutdown_seconds: int = 5,
        recycle_after_executions: int = 0,
        heartbeat_interval_seconds: int = 10,
        registration_ttl_seconds: int = 30,
        on_result: Callable[[dict], Awaitable[None]] | None = None,
    ):
        self.min_workers = min_workers
        self.max_workers = max_workers
        # ... etc
        self.processes: dict[str, ProcessHandle] = {}
        self.worker_id = os.environ.get("HOSTNAME", str(uuid.uuid4()))
        self._shutdown = False
```

### Task 1.2: Implement process spawning
- [ ] `_spawn_process() -> ProcessHandle`
  - Create work_queue and result_queue
  - Use `multiprocessing.get_context('spawn')`
  - Start process with target pointing to simple_worker entry
  - Add to `self.processes` dict
  - Return handle

```python
def _spawn_process(self) -> ProcessHandle:
    ctx = multiprocessing.get_context('spawn')
    work_queue = ctx.Queue()
    result_queue = ctx.Queue()

    process_id = f"process-{len(self.processes) + 1}"

    process = ctx.Process(
        target=run_worker_process,
        args=(work_queue, result_queue, process_id),
        name=process_id,
    )
    process.start()

    handle = ProcessHandle(
        id=process_id,
        process=process,
        pid=process.pid,
        state=ProcessState.IDLE,
        work_queue=work_queue,
        result_queue=result_queue,
        started_at=datetime.now(timezone.utc),
        current_execution=None,
        executions_completed=0,
    )

    self.processes[process_id] = handle
    return handle
```

### Task 1.3: Implement startup and shutdown
- [ ] `async start()`
  - Spawn `min_workers` processes
  - Register in Redis
  - Start background tasks (monitor, result, heartbeat loops)

- [ ] `async stop()`
  - Set `_shutdown = True`
  - Cancel background tasks
  - Terminate all processes gracefully
  - Unregister from Redis

```python
async def start(self) -> None:
    # Spawn initial pool
    for _ in range(self.min_workers):
        self._spawn_process()

    # Register in Redis
    await self._register_worker()

    # Start background loops
    self._monitor_task = asyncio.create_task(self._monitor_loop())
    self._result_task = asyncio.create_task(self._result_loop())
    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    logger.info(f"ProcessPoolManager started with {self.min_workers} workers")

async def stop(self) -> None:
    self._shutdown = True

    # Cancel background tasks
    for task in [self._monitor_task, self._result_task, self._heartbeat_task]:
        if task:
            task.cancel()

    # Terminate processes
    for handle in self.processes.values():
        await self._terminate_process(handle)

    # Unregister
    await self._unregister_worker()

    logger.info("ProcessPoolManager stopped")
```

### Task 1.4: Implement work routing
- [ ] `async route_execution(execution_id: str, context: dict) -> None`
  - Write context to Redis
  - Find IDLE process (or scale up)
  - Mark process BUSY
  - Send execution_id via queue

```python
async def route_execution(self, execution_id: str, context: dict) -> None:
    # 1. Write context to Redis
    await self._write_context_to_redis(execution_id, context)

    # 2. Find or create idle process
    idle = self._get_idle_process()
    if idle is None:
        if len(self.processes) < self.max_workers:
            idle = self._spawn_process()
        else:
            # Wait for a process (shouldn't happen with proper prefetch)
            idle = await self._wait_for_idle_process()

    # 3. Assign work
    timeout = context.get("timeout_seconds", self.execution_timeout_seconds)
    idle.state = ProcessState.BUSY
    idle.current_execution = ExecutionInfo(
        execution_id=execution_id,
        started_at=datetime.now(timezone.utc),
        timeout_seconds=timeout,
    )

    # 4. Send to process
    idle.work_queue.put_nowait(execution_id)

    logger.info(f"Routed {execution_id} to {idle.id}")

def _get_idle_process(self) -> ProcessHandle | None:
    for handle in self.processes.values():
        if handle.state == ProcessState.IDLE:
            return handle
    return None
```

### Task 1.5: Implement monitor loop
- [ ] `async _monitor_loop()`
  - Run every 1 second
  - Check for timeouts → kill process
  - Check for crashed processes → replace
  - Scale down excess idle processes

```python
async def _monitor_loop(self) -> None:
    while not self._shutdown:
        try:
            await self._check_timeouts()
            await self._check_process_health()
            await self._maybe_scale_down()
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

        await asyncio.sleep(1.0)
```

### Task 1.6: Implement timeout handling
- [ ] `async _check_timeouts()`
  - For each BUSY process, check elapsed time
  - If exceeded: SIGTERM → wait → SIGKILL
  - Report timeout, spawn replacement

```python
async def _check_timeouts(self) -> None:
    for handle in list(self.processes.values()):
        if handle.state != ProcessState.BUSY:
            continue
        if handle.current_execution is None:
            continue

        exec_info = handle.current_execution
        elapsed = (datetime.now(timezone.utc) - exec_info.started_at).total_seconds()

        if elapsed > exec_info.timeout_seconds:
            logger.warning(
                f"Execution {exec_info.execution_id} timed out after {elapsed:.1f}s"
            )

            # Kill process
            await self._kill_process(handle)

            # Report timeout
            await self._report_timeout(exec_info)

            # Remove and replace
            del self.processes[handle.id]
            if len(self.processes) < self.min_workers:
                self._spawn_process()

async def _kill_process(self, handle: ProcessHandle) -> None:
    # SIGTERM
    try:
        os.kill(handle.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    # Wait grace period
    await asyncio.sleep(self.graceful_shutdown_seconds)

    # SIGKILL if still alive
    if handle.process.is_alive():
        try:
            os.kill(handle.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        handle.process.join(timeout=1)

    handle.state = ProcessState.KILLED
```

### Task 1.7: Implement result collection
- [ ] `async _result_loop()`
  - Poll result queues from all processes (non-blocking)
  - Handle completed executions
  - Mark process IDLE or recycle

```python
async def _result_loop(self) -> None:
    while not self._shutdown:
        for handle in list(self.processes.values()):
            try:
                result = handle.result_queue.get_nowait()
                await self._handle_result(handle, result)
            except Empty:
                pass
            except Exception as e:
                logger.error(f"Result loop error for {handle.id}: {e}")

        await asyncio.sleep(0.1)

async def _handle_result(self, handle: ProcessHandle, result: dict) -> None:
    # Clear current execution
    handle.current_execution = None
    handle.executions_completed += 1

    # Check if should recycle
    if self.recycle_after_executions > 0:
        if handle.executions_completed >= self.recycle_after_executions:
            await self._recycle_process(handle)
            return

    # Return to IDLE
    handle.state = ProcessState.IDLE

    # Forward result
    if self.on_result:
        await self.on_result(result)
```

### Task 1.8: Implement heartbeats and registration
- [ ] `async _register_worker()` - Redis key with TTL
- [ ] `async _unregister_worker()` - Delete key, publish offline event
- [ ] `async _heartbeat_loop()` - Refresh TTL, publish status
- [ ] `_build_heartbeat() -> dict` - Process states for UI

```python
async def _heartbeat_loop(self) -> None:
    while not self._shutdown:
        try:
            # Refresh registration
            await self._refresh_registration()

            # Build and publish heartbeat
            heartbeat = self._build_heartbeat()
            await publish_worker_heartbeat(heartbeat)
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")

        await asyncio.sleep(self.heartbeat_interval_seconds)

def _build_heartbeat(self) -> dict:
    processes = []
    for p in self.processes.values():
        info = {
            "pid": p.pid,
            "process_id": p.id,
            "state": p.state.value,
            "memory_mb": self._get_process_memory(p.pid),
            "uptime_seconds": (datetime.now(timezone.utc) - p.started_at).total_seconds(),
            "executions_completed": p.executions_completed,
        }
        if p.current_execution:
            info["execution"] = {
                "execution_id": p.current_execution.execution_id,
                "started_at": p.current_execution.started_at.isoformat(),
                "elapsed_seconds": (
                    datetime.now(timezone.utc) - p.current_execution.started_at
                ).total_seconds(),
            }
        processes.append(info)

    return {
        "type": "worker_heartbeat",
        "worker_id": self.worker_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "processes": processes,
        "pool_size": len(self.processes),
        "idle_count": len([p for p in self.processes.values() if p.state == ProcessState.IDLE]),
        "busy_count": len([p for p in self.processes.values() if p.state == ProcessState.BUSY]),
    }
```

### Task 1.9: Implement manual recycle
- [ ] `async recycle_process(pid: int | None = None) -> bool`
  - Find process by PID (or pick idle one)
  - Only recycle IDLE processes
  - Terminate and spawn replacement

```python
async def recycle_process(self, pid: int | None = None) -> bool:
    target: ProcessHandle | None = None

    if pid is not None:
        for p in self.processes.values():
            if p.pid == pid:
                target = p
                break
    else:
        # Find any idle process
        target = self._get_idle_process()

    if target is None:
        return False

    if target.state == ProcessState.BUSY:
        logger.warning(f"Cannot recycle busy process {target.id}")
        return False

    await self._terminate_process(target)
    del self.processes[target.id]
    self._spawn_process()

    return True
```

### Task 1.10: Implement scaling logic
- [ ] `async _maybe_scale_down()` - Remove excess idle processes

```python
async def _maybe_scale_down(self) -> None:
    idle_processes = [
        p for p in self.processes.values()
        if p.state == ProcessState.IDLE
    ]

    excess = len(self.processes) - self.min_workers
    if excess <= 0:
        return

    # Remove oldest idle processes
    idle_processes.sort(key=lambda p: p.started_at)
    to_remove = idle_processes[:excess]

    for handle in to_remove:
        logger.info(f"Scaling down: removing idle process {handle.id}")
        await self._terminate_process(handle)
        del self.processes[handle.id]
```

## Unit Tests

- [ ] `test_pool_starts_with_min_workers`
- [ ] `test_route_to_idle_process`
- [ ] `test_scale_up_when_all_busy`
- [ ] `test_scale_down_when_excess_idle`
- [ ] `test_timeout_kills_process`
- [ ] `test_crash_detection_replaces_process`
- [ ] `test_recycle_idle_process`
- [ ] `test_cannot_recycle_busy_process`
- [ ] `test_heartbeat_published`

## Checklist

- [ ] ProcessPoolManager class created
- [ ] Process spawning works
- [ ] Work routing to idle process
- [ ] Timeout handling (SIGTERM → SIGKILL)
- [ ] Crash detection and replacement
- [ ] Result collection loop
- [ ] Heartbeat publishing
- [ ] Manual recycle API
- [ ] Scale up under load
- [ ] Scale down when idle
- [ ] Unit tests passing
