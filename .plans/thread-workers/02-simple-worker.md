# Phase 2: Simple Worker Process

Create the simplified worker process that runs one execution at a time and returns results.

## Overview

The simple worker:
- Runs in a spawned process (not the main consumer)
- Waits for execution_id via queue
- Reads context from Redis
- Executes the workflow
- Writes result to Redis and result_queue
- Loops back to wait for next execution

## File: `api/src/services/execution/simple_worker.py`

## Design Principles

1. **Simple loop** - No threading, no complex state
2. **Reuse existing engine** - Call `execute()` from `engine.py`
3. **Handle SIGTERM gracefully** - Allow current execution to complete or exit
4. **Report errors** - Always return a result (success or failure)

## Implementation Tasks

### Task 2.1: Create worker entry point

```python
def run_worker_process(
    work_queue: Queue,
    result_queue: Queue,
    worker_id: str,
) -> None:
    """
    Entry point for worker process.
    Simple loop: wait for execution_id → execute → return result.
    """
    # Setup signal handler for graceful shutdown
    shutdown_requested = False

    def handle_sigterm(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.info(f"Worker {worker_id} received SIGTERM, will exit after current work")

    signal.signal(signal.SIGTERM, handle_sigterm)

    logger.info(f"Worker {worker_id} started")

    while not shutdown_requested:
        try:
            # Block waiting for work (with timeout to check shutdown flag)
            try:
                execution_id = work_queue.get(timeout=1.0)
            except Empty:
                continue

            # Execute and return result
            result = _execute_sync(execution_id, worker_id)
            result_queue.put(result)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.exception(f"Worker {worker_id} error: {e}")
            # Try to report error if we have an execution_id
            if 'execution_id' in dir():
                result_queue.put({
                    "execution_id": execution_id,
                    "success": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                })

    logger.info(f"Worker {worker_id} exiting")
```

### Task 2.2: Implement sync execution wrapper

```python
def _execute_sync(execution_id: str, worker_id: str) -> dict:
    """
    Synchronous wrapper that runs async execution.
    Creates event loop for this execution.
    """
    try:
        result = asyncio.run(_execute_async(execution_id, worker_id))
        return result
    except Exception as e:
        logger.exception(f"Execution {execution_id} failed: {e}")
        return {
            "execution_id": execution_id,
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
```

### Task 2.3: Implement async execution

```python
async def _execute_async(execution_id: str, worker_id: str) -> dict:
    """
    Read context from Redis, execute workflow, return result.
    """
    start_time = datetime.now(timezone.utc)

    # 1. Read context from Redis
    context = await _read_context_from_redis(execution_id)
    if context is None:
        return {
            "execution_id": execution_id,
            "success": False,
            "error": "Execution context not found in Redis",
            "error_type": "ContextNotFound",
        }

    # 2. Build execution request
    request = _build_execution_request(execution_id, context)

    # 3. Execute using existing engine
    try:
        result = await execute(request)

        return {
            "execution_id": execution_id,
            "success": result.success,
            "result": result.result,
            "error": result.error,
            "error_type": result.error_type if not result.success else None,
            "duration_ms": result.duration_ms,
            "logs": result.logs,
            "variables": result.variables,
            "worker_id": worker_id,
        }

    except Exception as e:
        duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        return {
            "execution_id": execution_id,
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "duration_ms": duration_ms,
            "worker_id": worker_id,
        }
```

### Task 2.4: Implement Redis context reading

```python
async def _read_context_from_redis(execution_id: str) -> dict | None:
    """Read execution context from Redis."""
    from api.src.core.redis_client import get_redis_client

    redis = await get_redis_client()
    key = f"bifrost:exec:{execution_id}:context"

    data = await redis.get(key)
    if data is None:
        return None

    return json.loads(data)
```

### Task 2.5: Build execution request from context

```python
def _build_execution_request(execution_id: str, context: dict) -> ExecutionRequest:
    """Convert Redis context to ExecutionRequest."""
    from api.src.services.execution.engine import ExecutionRequest

    return ExecutionRequest(
        execution_id=execution_id,
        workflow_id=context.get("workflow_id"),
        workflow_code=context.get("workflow_code"),
        function_name=context.get("function_name"),
        file_path=context.get("file_path"),
        caller=context.get("caller"),
        organization=context.get("organization"),
        parameters=context.get("parameters", {}),
        timeout_seconds=context.get("timeout_seconds", 300),
        # ... other fields from context
    )
```

### Task 2.6: Resource metrics capture (optional)

```python
def _capture_resource_metrics() -> dict:
    """Capture resource usage for diagnostics."""
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "peak_memory_mb": usage.ru_maxrss / 1024 / 1024,  # Convert to MB
        "user_time_seconds": usage.ru_utime,
        "system_time_seconds": usage.ru_stime,
    }
```

## Integration with Existing Code

### Reuse from `engine.py`
- `execute()` function - core execution logic
- `ExecutionRequest` - request dataclass
- Variable capture, log streaming, etc.

### Reuse from `worker.py`
- Signal handling pattern
- Resource metrics capture
- Result formatting

### Keep Virtual Import Hook
The virtual import hook should still be installed at process start:

```python
def run_worker_process(...):
    # Install virtual import hook FIRST
    from api.src.services.execution.virtual_import import install_virtual_import_hook
    install_virtual_import_hook()

    # ... rest of worker code
```

## Unit Tests

- [ ] `test_worker_executes_and_returns_result`
- [ ] `test_worker_handles_missing_context`
- [ ] `test_worker_handles_execution_error`
- [ ] `test_worker_responds_to_sigterm`
- [ ] `test_worker_loops_for_multiple_executions`

## Checklist

- [ ] Worker entry point created
- [ ] SIGTERM handling for graceful shutdown
- [ ] Context reading from Redis
- [ ] Integration with engine.execute()
- [ ] Result formatting and return
- [ ] Error handling for all failure modes
- [ ] Virtual import hook installed
- [ ] Unit tests passing
