# Phase 6: Cleanup

Remove old thread-worker code and related files.

## Overview

Delete files that are no longer needed after the process pool migration:
- Thread worker implementation
- Orchestrator
- Circuit breaker / blacklist
- Related migrations
- Old tests

## Files to Delete

### Backend - Execution

| File | Reason |
|------|--------|
| `api/src/services/execution/thread_worker.py` | Replaced by simple_worker.py |
| `api/src/services/execution/orchestrator.py` | Replaced by process_pool.py |
| `api/src/services/execution/circuit_breaker.py` | Feature removed |

### Backend - Models

| File | Reason |
|------|--------|
| `api/src/models/orm/workflow_blacklist.py` | Feature removed |

### Backend - Routers

| File | Reason |
|------|--------|
| `api/src/routers/platform/blacklist.py` | Feature removed |

### Backend - Tests

| File | Reason |
|------|--------|
| `api/tests/unit/execution/test_thread_worker.py` | Testing deleted code |
| `api/tests/unit/execution/test_thread_worker_config.py` | Testing deleted code |
| `api/tests/unit/execution/test_circuit_breaker.py` | Testing deleted code |
| `api/tests/unit/services/test_orchestrator.py` | Testing deleted code |
| `api/tests/integration/platform/test_blacklist_enforcement.py` | Testing deleted feature |
| `api/tests/integration/platform/test_thread_worker_integration.py` | Testing deleted code |

### Frontend

| File | Reason |
|------|--------|
| `client/src/pages/diagnostics/BlacklistTab.tsx` | Feature removed |

### Migrations

Review and handle these migration files:
- `api/alembic/versions/20260109_add_workflow_blacklist.py`
- Any other blacklist-related migrations

**Options:**
1. Delete if never deployed to production
2. Create a "down" migration to drop the table if deployed
3. Leave as no-op if schema was applied but table is empty

## Implementation Tasks

### Task 6.1: Delete thread worker files

```bash
rm api/src/services/execution/thread_worker.py
rm api/src/services/execution/orchestrator.py
rm api/src/services/execution/circuit_breaker.py
```

### Task 6.2: Delete blacklist model

```bash
rm api/src/models/orm/workflow_blacklist.py
```

**Update `api/src/models/orm/__init__.py`:**

```python
# REMOVE this import:
# from .workflow_blacklist import WorkflowBlacklist
```

### Task 6.3: Delete blacklist router

```bash
rm api/src/routers/platform/blacklist.py
```

**Update `api/src/routers/platform/__init__.py`:**

```python
# REMOVE:
# from .blacklist import router as blacklist_router
# platform_router.include_router(blacklist_router, ...)
```

### Task 6.4: Delete old tests

```bash
rm api/tests/unit/execution/test_thread_worker.py
rm api/tests/unit/execution/test_thread_worker_config.py
rm api/tests/unit/execution/test_circuit_breaker.py
rm api/tests/unit/services/test_orchestrator.py
rm api/tests/integration/platform/test_blacklist_enforcement.py
rm api/tests/integration/platform/test_thread_worker_integration.py
```

### Task 6.5: Delete frontend blacklist tab

```bash
rm client/src/pages/diagnostics/BlacklistTab.tsx
```

### Task 6.6: Handle migrations

**Option A: Never deployed** - Delete the migration file:
```bash
rm api/alembic/versions/20260109_add_workflow_blacklist.py
```

**Option B: Deployed** - Create down migration:
```python
# api/alembic/versions/YYYYMMDD_drop_workflow_blacklist.py

def upgrade():
    op.drop_table("workflow_blacklist")

def downgrade():
    # Recreate table if needed
    pass
```

### Task 6.7: Update model imports

**In `api/src/models/orm/__init__.py`:**

```python
# Remove WorkflowBlacklist from __all__ if present
```

**In `api/src/models/contracts/__init__.py`:**

```python
# Remove any blacklist-related contract imports
```

### Task 6.8: Clean up config.py

Remove old settings that are no longer used:

```python
# REMOVE:
# use_thread_workers: bool
# worker_thread_pool_size: int
# cancel_grace_seconds: int
```

### Task 6.9: Update consumer imports

In `api/src/jobs/consumers/workflow_execution.py`:

```python
# REMOVE:
# from api.src.services.execution.orchestrator import Orchestrator
# from api.src.services.execution.circuit_breaker import get_circuit_breaker

# ADD:
from api.src.services.execution.process_pool import ProcessPoolManager
```

### Task 6.10: Clean up enums

In `api/src/models/enums.py`:

```python
# If ExecutionStatus.BLOCKED was only for blacklist, consider removing
# Or keep if it's used for other blocking scenarios
```

### Task 6.11: Verify no dangling references

Run grep to check for any remaining references:

```bash
grep -r "thread_worker" api/src/
grep -r "orchestrator" api/src/ --include="*.py" | grep -v "process_pool"
grep -r "circuit_breaker" api/src/
grep -r "blacklist" api/src/ --include="*.py"
grep -r "BlacklistTab" client/src/
```

### Task 6.12: Run full test suite

```bash
./test.sh
cd client && npm run tsc && npm run lint
cd api && pyright && ruff check .
```

## Checklist

- [ ] thread_worker.py deleted
- [ ] orchestrator.py deleted
- [ ] circuit_breaker.py deleted
- [ ] workflow_blacklist.py deleted
- [ ] blacklist router deleted
- [ ] Old tests deleted
- [ ] BlacklistTab.tsx deleted
- [ ] Migrations handled
- [ ] Model imports updated
- [ ] Config cleaned up
- [ ] Consumer imports updated
- [ ] No dangling references
- [ ] All tests pass
- [ ] Type checking passes
- [ ] Linting passes
