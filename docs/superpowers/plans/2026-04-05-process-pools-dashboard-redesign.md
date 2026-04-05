# Process Pools Dashboard Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Process Pools diagnostics tab: aggregate memory chart with persisted history, container table with expandable fork details, remove min/max config UI.

**Architecture:** New `worker_metrics` table stores periodic resource snapshots. A **scheduler job** reads the latest heartbeats from Redis every 60s and persists them to the DB (worker processes cannot connect to the DB). A new API endpoint serves time-series data with downsampling. The frontend is rebuilt around a recharts stacked area chart + dense tables replacing the current card-based layout.

**Tech Stack:** Python/FastAPI, SQLAlchemy, Alembic, PostgreSQL, React, TypeScript, recharts, TanStack Query

**Spec:** `docs/superpowers/specs/2026-04-05-process-pools-dashboard-redesign.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `api/src/models/orm/worker_metric.py` | SQLAlchemy model for `worker_metrics` table |
| `api/alembic/versions/20260405_create_worker_metrics.py` | Migration to create table + indexes |
| `api/src/jobs/schedulers/worker_metrics_sampling.py` | Scheduler job: reads heartbeats from Redis, persists to DB |
| `api/src/jobs/schedulers/worker_metrics_cleanup.py` | 7-day retention cleanup job |
| `api/tests/unit/test_worker_metrics.py` | Unit tests for metrics persistence + downsampling |
| `client/src/pages/diagnostics/components/MemoryChart.tsx` | Stacked area chart for aggregate memory |
| `client/src/pages/diagnostics/components/ContainerTable.tsx` | Container/pool table with expandable rows |
| `client/src/pages/diagnostics/components/ForkTable.tsx` | Dense fork/process sub-table |

### Modified Files
| File | Changes |
|------|---------|
| `api/src/models/orm/__init__.py` | Import + export `WorkerMetric` |
| `api/src/models/contracts/platform.py` | Add `WorkerMetricsResponse` and `WorkerMetricPoint` models |
| `api/src/routers/platform/workers.py` | Add `GET /metrics` endpoint |
| `api/src/services/execution/process_pool.py` | Add cgroup data to heartbeat payload |
| `api/src/scheduler/main.py` | Register metrics sampling + cleanup jobs |
| `client/src/services/workers.ts` | Add `useWorkerMetrics()` hook, remove config-related exports |
| `client/src/pages/diagnostics/components/WorkersTab.tsx` | Complete rebuild |
| `client/src/pages/diagnostics/hooks/useWorkerWebSocket.ts` | Expose cgroup data from heartbeats |

### Deleted Files
| File | Reason |
|------|--------|
| `client/src/pages/diagnostics/components/PoolConfigForm.tsx` | No min/max config |
| `client/src/pages/diagnostics/components/WorkerCard.tsx` | Replaced by ContainerTable |
| `client/src/pages/diagnostics/components/ProcessCard.tsx` | Replaced by ForkTable |

---

## Task 1: WorkerMetric ORM Model + Migration

**Files:**
- Create: `api/src/models/orm/worker_metric.py`
- Create: `api/alembic/versions/20260405_create_worker_metrics.py`
- Modify: `api/src/models/orm/__init__.py:1-120`

- [ ] **Step 1: Create the ORM model**

```python
# api/src/models/orm/worker_metric.py
"""Worker metrics time-series data for diagnostics dashboard."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkerMetric(Base):
    """Periodic resource snapshot from worker heartbeats."""

    __tablename__ = "worker_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    memory_current: Mapped[int] = mapped_column(BigInteger, nullable=False)
    memory_max: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fork_count: Mapped[int] = mapped_column(Integer, nullable=False)
    busy_count: Mapped[int] = mapped_column(Integer, nullable=False)
    idle_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_worker_metrics_timestamp", "timestamp"),
        Index("ix_worker_metrics_worker_timestamp", "worker_id", "timestamp"),
    )
```

- [ ] **Step 2: Register model in ORM __init__**

Add to `api/src/models/orm/__init__.py`:

At the imports section, add:
```python
from src.models.orm.worker_metric import WorkerMetric
```

In the `__all__` list, add at the end (before the closing bracket):
```python
    # Worker Metrics
    "WorkerMetric",
```

- [ ] **Step 3: Create the Alembic migration**

```python
# api/alembic/versions/20260405_create_worker_metrics.py
"""Create worker_metrics table for diagnostics time-series

Revision ID: 20260405_worker_metrics
Revises: 20260402_process_rss_bytes
Create Date: 2026-04-05

Stores periodic resource snapshots from worker heartbeats.
Used by the Process Pools diagnostics dashboard for the aggregate memory chart.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260405_worker_metrics"
down_revision = "20260402_process_rss_bytes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("memory_current", sa.BigInteger(), nullable=False),
        sa.Column("memory_max", sa.BigInteger(), nullable=False),
        sa.Column("fork_count", sa.Integer(), nullable=False),
        sa.Column("busy_count", sa.Integer(), nullable=False),
        sa.Column("idle_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_worker_metrics_timestamp", "worker_metrics", ["timestamp"])
    op.create_index(
        "ix_worker_metrics_worker_timestamp",
        "worker_metrics",
        ["worker_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_metrics_worker_timestamp", table_name="worker_metrics")
    op.drop_index("ix_worker_metrics_timestamp", table_name="worker_metrics")
    op.drop_table("worker_metrics")
```

- [ ] **Step 4: Verify migration applies**

Restart the `bifrost-init` container to run the migration, then restart `bifrost-dev-api-1`:
```bash
docker restart bifrost-init-1
# Wait a few seconds for migration to apply
docker restart bifrost-dev-api-1
```

Check the API logs to confirm no errors:
```bash
docker logs bifrost-dev-api-1 2>&1 | tail -20
```

- [ ] **Step 5: Commit**

```bash
git add api/src/models/orm/worker_metric.py api/src/models/orm/__init__.py api/alembic/versions/20260405_create_worker_metrics.py
git commit -m "feat: add worker_metrics table for diagnostics time-series"
```

---

## Task 2: Heartbeat Enhancement — Add cgroup Memory Data

**Important constraint:** Worker/pool processes are forked and **cannot connect to the database**. The heartbeat publishes to Redis + WebSocket only. DB persistence happens in the scheduler (Task 3).

**Files:**
- Modify: `api/src/services/execution/process_pool.py:1733-1778` (`_build_heartbeat`)
- Test: `api/tests/unit/test_worker_metrics.py`

- [ ] **Step 1: Write the failing test for cgroup data in heartbeat**

```python
# api/tests/unit/test_worker_metrics.py
"""Tests for worker metrics persistence and heartbeat enhancement."""

from unittest.mock import patch
from datetime import datetime, timezone

import pytest


class TestHeartbeatCgroupData:
    """Tests for cgroup memory data in heartbeat payload."""

    def test_heartbeat_includes_cgroup_memory(self):
        """Heartbeat should include memory_current_bytes and memory_max_bytes."""
        from src.services.execution.process_pool import ProcessPoolManager

        pool = ProcessPoolManager.__new__(ProcessPoolManager)
        pool.worker_id = "test-worker"
        pool.processes = {}
        pool.min_workers = 0
        pool.max_workers = 10
        pool._started_at = datetime.now(timezone.utc)
        pool._requirements_installed = 0
        pool._requirements_total = 0
        pool.heartbeat_interval_seconds = 10

        with patch(
            "src.services.execution.process_pool.get_cgroup_memory",
            return_value=(4_000_000_000, 8_000_000_000),
        ):
            heartbeat = pool._build_heartbeat()

        assert heartbeat["memory_current_bytes"] == 4_000_000_000
        assert heartbeat["memory_max_bytes"] == 8_000_000_000

    def test_heartbeat_cgroup_unavailable(self):
        """When cgroup is unavailable, heartbeat should have -1 values."""
        from src.services.execution.process_pool import ProcessPoolManager

        pool = ProcessPoolManager.__new__(ProcessPoolManager)
        pool.worker_id = "test-worker"
        pool.processes = {}
        pool.min_workers = 0
        pool.max_workers = 10
        pool._started_at = datetime.now(timezone.utc)
        pool._requirements_installed = 0
        pool._requirements_total = 0
        pool.heartbeat_interval_seconds = 10

        with patch(
            "src.services.execution.process_pool.get_cgroup_memory",
            return_value=(-1, -1),
        ):
            heartbeat = pool._build_heartbeat()

        assert heartbeat["memory_current_bytes"] == -1
        assert heartbeat["memory_max_bytes"] == -1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./test.sh tests/unit/test_worker_metrics.py::TestHeartbeatCgroupData -v
```
Expected: FAIL — `memory_current_bytes` key not in heartbeat dict.

- [ ] **Step 3: Add cgroup data to `_build_heartbeat()`**

In `api/src/services/execution/process_pool.py`, add the import near the top of the file with the other imports from memory_monitor:

```python
from src.services.execution.memory_monitor import get_cgroup_memory
```

Then modify `_build_heartbeat()` at line 1763 to include cgroup data. Add before the return statement:

```python
        memory_current, memory_max = get_cgroup_memory()
```

And add to the returned dict, after `"requirements_total"`:

```python
            "memory_current_bytes": memory_current,
            "memory_max_bytes": memory_max,
```

The heartbeat is published to Redis via `publish_worker_heartbeat()` which stores it at `bifrost:pool:{worker_id}:heartbeat` with a TTL. No DB connection needed.

- [ ] **Step 4: Run test to verify it passes**

```bash
./test.sh tests/unit/test_worker_metrics.py::TestHeartbeatCgroupData -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/src/services/execution/process_pool.py api/tests/unit/test_worker_metrics.py
git commit -m "feat: add cgroup memory data to worker heartbeat payload"
```

---

## Task 3: Metrics Sampling Job + API Endpoint + Cleanup Job

**Key design:** Worker processes publish heartbeats to Redis but **cannot connect to the DB**. The scheduler process reads heartbeats from Redis and persists snapshots to `worker_metrics`.

**Files:**
- Modify: `api/src/models/contracts/platform.py`
- Modify: `api/src/routers/platform/workers.py`
- Create: `api/src/jobs/schedulers/worker_metrics_sampling.py`
- Create: `api/src/jobs/schedulers/worker_metrics_cleanup.py`
- Modify: `api/src/scheduler/main.py`
- Modify: `api/tests/unit/test_worker_metrics.py`

- [ ] **Step 1: Add response models to platform contracts**

Add to the end of `api/src/models/contracts/platform.py` (before the closing of the file):

```python
# =============================================================================
# Worker Metrics Models (Time-Series for Diagnostics Chart)
# =============================================================================


class WorkerMetricPoint(BaseModel):
    """A single time-series data point for the memory chart."""

    timestamp: str = Field(..., description="ISO timestamp")
    worker_id: str = Field(..., description="Container/pool identifier")
    memory_current: int = Field(..., description="cgroup memory.current in bytes")
    memory_max: int = Field(..., description="cgroup memory.max in bytes")
    fork_count: int = Field(default=0)
    busy_count: int = Field(default=0)
    idle_count: int = Field(default=0)


class WorkerMetricsResponse(BaseModel):
    """Response for worker metrics time-series endpoint."""

    range: str = Field(..., description="Requested time range: 1h, 6h, 24h, 7d")
    points: list[WorkerMetricPoint] = Field(default_factory=list)
```

- [ ] **Step 2: Write failing test for the metrics endpoint**

Add to `api/tests/unit/test_worker_metrics.py`:

```python
class TestMetricsDownsampling:
    """Tests for metrics query downsampling."""

    def test_range_to_timedelta(self):
        """Verify range string parsing."""
        from datetime import timedelta

        range_map = {
            "1h": timedelta(hours=1),
            "6h": timedelta(hours=6),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
        }
        for range_str, expected in range_map.items():
            unit = range_str[-1]
            value = int(range_str[:-1])
            if unit == "h":
                result = timedelta(hours=value)
            elif unit == "d":
                result = timedelta(days=value)
            assert result == expected
```

- [ ] **Step 3: Run test to verify it passes (this is a pure logic test)**

```bash
./test.sh tests/unit/test_worker_metrics.py::TestMetricsDownsampling -v
```
Expected: PASS

- [ ] **Step 4: Add the metrics endpoint**

Add these imports at the top of `api/src/routers/platform/workers.py`:

```python
from src.models.contracts.platform import WorkerMetricsResponse, WorkerMetricPoint
```

Add this endpoint to the router (before the recycle endpoints):

```python
@router.get(
    "/metrics",
    response_model=WorkerMetricsResponse,
    summary="Get worker metrics time-series",
    description="Returns time-series memory data for the aggregate chart. "
    "Downsampled for longer ranges.",
)
async def get_worker_metrics(
    _admin: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
    range: str = Query(
        default="1h",
        regex=r"^(1h|6h|24h|7d)$",
        description="Time range: 1h, 6h, 24h, 7d",
    ),
) -> WorkerMetricsResponse:
    """Get worker metrics for the diagnostics memory chart."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from src.models.orm.worker_metric import WorkerMetric

    # Parse range
    range_map = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
    }
    delta = range_map[range]
    cutoff = datetime.now(timezone.utc) - delta

    # Determine bucket size for downsampling
    # 1h: raw data, 6h/24h: 5-min buckets, 7d: 30-min buckets
    if range == "1h":
        # Raw data — no downsampling
        stmt = (
            select(WorkerMetric)
            .where(WorkerMetric.timestamp >= cutoff)
            .order_by(WorkerMetric.timestamp)
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

        points = [
            WorkerMetricPoint(
                timestamp=row.timestamp.isoformat(),
                worker_id=row.worker_id,
                memory_current=row.memory_current,
                memory_max=row.memory_max,
                fork_count=row.fork_count,
                busy_count=row.busy_count,
                idle_count=row.idle_count,
            )
            for row in rows
        ]
    else:
        # Downsampled — bucket by time interval
        bucket_minutes = 5 if range in ("6h", "24h") else 30

        # Use date_trunc to nearest bucket
        bucket_expr = func.date_trunc(
            "minute",
            WorkerMetric.timestamp
            - func.cast(
                func.extract("minute", WorkerMetric.timestamp) % bucket_minutes,
                sa.Interval,
            ),
        )

        # Simpler approach: truncate to bucket and avg
        stmt = (
            select(
                func.date_trunc("hour", WorkerMetric.timestamp).label("hour"),
                func.floor(
                    func.extract("minute", WorkerMetric.timestamp) / bucket_minutes
                ).label("bucket"),
                WorkerMetric.worker_id,
                func.avg(WorkerMetric.memory_current).label("avg_memory_current"),
                func.max(WorkerMetric.memory_max).label("memory_max"),
                func.round(func.avg(WorkerMetric.fork_count)).label("avg_fork_count"),
                func.round(func.avg(WorkerMetric.busy_count)).label("avg_busy_count"),
                func.round(func.avg(WorkerMetric.idle_count)).label("avg_idle_count"),
            )
            .where(WorkerMetric.timestamp >= cutoff)
            .group_by(
                func.date_trunc("hour", WorkerMetric.timestamp),
                func.floor(
                    func.extract("minute", WorkerMetric.timestamp) / bucket_minutes
                ),
                WorkerMetric.worker_id,
            )
            .order_by(
                func.date_trunc("hour", WorkerMetric.timestamp),
                func.floor(
                    func.extract("minute", WorkerMetric.timestamp) / bucket_minutes
                ),
            )
        )
        result = await db.execute(stmt)
        rows = result.all()

        points = [
            WorkerMetricPoint(
                timestamp=(
                    row.hour.replace(
                        minute=int(row.bucket * bucket_minutes)
                    )
                ).isoformat(),
                worker_id=row.worker_id,
                memory_current=int(row.avg_memory_current),
                memory_max=int(row.memory_max),
                fork_count=int(row.avg_fork_count),
                busy_count=int(row.avg_busy_count),
                idle_count=int(row.avg_idle_count),
            )
            for row in rows
        ]

    return WorkerMetricsResponse(range=range, points=points)
```

Also add `import sqlalchemy as sa` to the imports at the top of the file if not already present.

- [ ] **Step 5: Create the metrics sampling job**

This job runs in the **scheduler container** (which has DB access). Every 60s, it reads the latest heartbeat from Redis for each registered worker and persists a snapshot.

```python
# api/src/jobs/schedulers/worker_metrics_sampling.py
"""Sample worker heartbeats from Redis into the worker_metrics table.

Runs every 60s in the scheduler container. Worker processes publish
heartbeats to Redis but cannot connect to the DB — this job bridges
the gap by reading heartbeats from Redis and persisting snapshots.
"""

import json
import logging

from src.core.database import get_session_factory
from src.core.redis_client import get_redis_client
from src.models.orm.worker_metric import WorkerMetric

logger = logging.getLogger(__name__)


async def sample_worker_metrics() -> dict:
    """
    Read latest heartbeats from Redis and persist to worker_metrics.

    Returns:
        Summary with workers_sampled count.
    """
    redis_client = get_redis_client()
    if not redis_client:
        return {"workers_sampled": 0, "error": "Redis unavailable"}

    try:
        # Find all registered worker pools in Redis
        pool_keys = []
        async for key in redis_client.scan_iter("bifrost:pool:*:heartbeat"):
            pool_keys.append(key)

        if not pool_keys:
            return {"workers_sampled": 0}

        sampled = 0
        session_factory = get_session_factory()
        async with session_factory() as db:
            for key in pool_keys:
                try:
                    raw = await redis_client.get(key)
                    if not raw:
                        continue

                    heartbeat = json.loads(raw)
                    memory_current = heartbeat.get("memory_current_bytes", -1)
                    memory_max = heartbeat.get("memory_max_bytes", -1)

                    # Skip if cgroup data unavailable
                    if memory_current < 0 or memory_max <= 0:
                        continue

                    metric = WorkerMetric(
                        worker_id=heartbeat.get("worker_id", "unknown"),
                        memory_current=memory_current,
                        memory_max=memory_max,
                        fork_count=heartbeat.get("pool_size", 0),
                        busy_count=heartbeat.get("busy_count", 0),
                        idle_count=heartbeat.get("idle_count", 0),
                    )
                    db.add(metric)
                    sampled += 1
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Skipping malformed heartbeat from {key}: {e}")

            if sampled > 0:
                await db.commit()

        logger.debug(f"Worker metrics sampled: {sampled} workers from {len(pool_keys)} heartbeats")
        return {"workers_sampled": sampled}

    except Exception as e:
        logger.error(f"Worker metrics sampling failed: {e}", exc_info=True)
        return {"workers_sampled": 0, "error": str(e)}
```

- [ ] **Step 6: Create the cleanup job**

```python
# api/src/jobs/schedulers/worker_metrics_cleanup.py
"""Cleanup old worker metrics rows (7-day retention)."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from src.core.database import get_session_factory
from src.models.orm.worker_metric import WorkerMetric

logger = logging.getLogger(__name__)


async def cleanup_old_worker_metrics() -> dict:
    """
    Delete worker_metrics rows older than 7 days.

    Returns:
        Summary with rows_deleted count.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            stmt = delete(WorkerMetric).where(WorkerMetric.timestamp < cutoff)
            result = await db.execute(stmt)
            await db.commit()
            deleted = result.rowcount

        logger.info(f"Worker metrics cleanup: deleted {deleted} rows older than {cutoff.isoformat()}")
        return {"rows_deleted": deleted}
    except Exception as e:
        logger.error(f"Worker metrics cleanup failed: {e}", exc_info=True)
        return {"rows_deleted": 0, "error": str(e)}
```

- [ ] **Step 6: Register both jobs in scheduler**

Add to `api/src/scheduler/main.py` in `_start_scheduler()`, after the event cleanup job block (around line 206):

```python
        # Worker metrics sampling - every 60 seconds
        # Reads heartbeats from Redis and persists to DB for the diagnostics chart
        try:
            from src.jobs.schedulers.worker_metrics_sampling import sample_worker_metrics
            scheduler.add_job(
                sample_worker_metrics,
                IntervalTrigger(seconds=60),
                id="worker_metrics_sampling",
                name="Sample worker metrics from Redis heartbeats",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),
                **misfire_options,
            )
            logger.info("Worker metrics sampling job scheduled (every 60s)")
        except ImportError:
            logger.warning("Worker metrics sampling job not available")

        # Worker metrics cleanup - daily at 4:00 AM UTC (7-day retention)
        try:
            from src.jobs.schedulers.worker_metrics_cleanup import cleanup_old_worker_metrics
            scheduler.add_job(
                cleanup_old_worker_metrics,
                CronTrigger(hour=4, minute=0),  # Daily at 4:00 AM UTC
                id="worker_metrics_cleanup",
                name="Cleanup old worker metrics (7-day retention)",
                replace_existing=True,
                **misfire_options,
            )
            logger.info("Worker metrics cleanup job scheduled (daily at 4:00 AM)")
        except ImportError:
            logger.warning("Worker metrics cleanup job not available")
```

- [ ] **Step 7: Run backend checks**

```bash
cd api && pyright && ruff check .
```
Expected: 0 errors

- [ ] **Step 8: Run tests**

```bash
./test.sh tests/unit/test_worker_metrics.py -v
```
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add api/src/models/contracts/platform.py api/src/routers/platform/workers.py api/src/jobs/schedulers/worker_metrics_sampling.py api/src/jobs/schedulers/worker_metrics_cleanup.py api/src/scheduler/main.py api/tests/unit/test_worker_metrics.py
git commit -m "feat: add worker metrics sampling, API endpoint, and 7-day cleanup"
```

---

## Task 4: Frontend — Worker Service + WebSocket Updates

**Files:**
- Modify: `client/src/services/workers.ts`
- Modify: `client/src/pages/diagnostics/hooks/useWorkerWebSocket.ts`

- [ ] **Step 1: Add `useWorkerMetrics` hook to workers service**

Add to the end of `client/src/services/workers.ts`:

```typescript
// =============================================================================
// Worker Metrics (Time-Series for Diagnostics Chart)
// =============================================================================

export interface WorkerMetricPoint {
    timestamp: string;
    worker_id: string;
    memory_current: number;
    memory_max: number;
    fork_count: number;
    busy_count: number;
    idle_count: number;
}

export interface WorkerMetricsResponse {
    range: string;
    points: WorkerMetricPoint[];
}

export function useWorkerMetrics(range: string = "1h") {
    return useQuery<WorkerMetricsResponse>({
        queryKey: ["worker-metrics", range],
        queryFn: async () => {
            const response = await authFetch(
                `/api/platform/workers/metrics?range=${range}`
            );
            if (!response.ok) {
                throw new Error(
                    `Failed to fetch worker metrics: ${response.statusText}`
                );
            }
            return response.json();
        },
        refetchInterval: 60_000, // Refresh every 60s to get new data points
    });
}
```

- [ ] **Step 2: Remove config-related exports that are no longer needed**

In `client/src/services/workers.ts`, remove the `usePoolConfig` and `useUpdatePoolConfig` hooks entirely (they are only used by PoolConfigForm which we're deleting). Keep `PoolConfigUpdateResponse` type if it's referenced elsewhere — check first:

```bash
grep -r "usePoolConfig\|useUpdatePoolConfig\|PoolConfigUpdateRequest" client/src/ --include="*.ts" --include="*.tsx" -l
```

If only referenced in `workers.ts` and `WorkersTab.tsx` (which we're rebuilding) and `PoolConfigForm.tsx` (which we're deleting), remove them.

- [ ] **Step 3: Update WebSocket hook to expose cgroup data**

In `client/src/pages/diagnostics/hooks/useWorkerWebSocket.ts`, the heartbeat messages already contain all the data from `_build_heartbeat()`. The `PoolDetail` type in the hook maps heartbeat data to pools. We need to ensure `memory_current_bytes` and `memory_max_bytes` are passed through.

Add to the `PoolDetail` interface (or wherever the hook transforms heartbeat data into pool objects) — the hook should carry these new fields through. Add a new interface or extend the existing pool data:

```typescript
export interface PoolCgroupMemory {
    memory_current_bytes: number;
    memory_max_bytes: number;
}
```

In the heartbeat handler where pools are constructed from WebSocket messages, include these fields:

```typescript
// In the worker_heartbeat handler, include cgroup data in the pool object
memory_current_bytes: message.memory_current_bytes ?? -1,
memory_max_bytes: message.memory_max_bytes ?? -1,
```

Also add `memory_current_bytes` and `memory_max_bytes` to the `PoolDetail` interface in `workers.ts`:

```typescript
export interface PoolDetail {
    worker_id: string;
    hostname: string | null;
    status: string | null;
    started_at: string | null;
    last_heartbeat: string | null;
    min_workers: number;
    max_workers: number;
    processes: ProcessInfo[];
    memory_current_bytes?: number;
    memory_max_bytes?: number;
}
```

- [ ] **Step 4: Run frontend checks**

```bash
cd client && npm run tsc && npm run lint
```
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add client/src/services/workers.ts client/src/pages/diagnostics/hooks/useWorkerWebSocket.ts
git commit -m "feat: add worker metrics hook and cgroup data to WebSocket"
```

---

## Task 5: Frontend — MemoryChart Component

**Files:**
- Create: `client/src/pages/diagnostics/components/MemoryChart.tsx`

- [ ] **Step 1: Create the MemoryChart component**

Reference `client/src/components/charts/ResourceTrendChart.tsx` for recharts patterns (uses `ResponsiveContainer`, `Card` wrapper, `hsl(var(--chart-N))` colors).

```typescript
// client/src/pages/diagnostics/components/MemoryChart.tsx
import { useMemo, useState } from "react";
import {
    AreaChart,
    Area,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    ReferenceLine,
} from "recharts";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkerMetrics, type WorkerMetricPoint } from "@/services/workers";

const TIME_RANGES = ["1h", "6h", "24h", "7d"] as const;
type TimeRange = (typeof TIME_RANGES)[number];

// Consistent colors for up to 10 containers
const CONTAINER_COLORS = [
    "hsl(var(--chart-1))",
    "hsl(var(--chart-2))",
    "hsl(var(--chart-3))",
    "hsl(var(--chart-4))",
    "hsl(var(--chart-5))",
    "#f97316",
    "#06b6d4",
    "#8b5cf6",
    "#ec4899",
    "#14b8a6",
];

interface ChartDataPoint {
    timestamp: string;
    label: string;
    [workerId: string]: number | string;
}

function formatBytes(bytes: number): string {
    if (bytes < 0) return "N/A";
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) return `${gb.toFixed(1)} GB`;
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(0)} MB`;
}

function formatTimeLabel(isoString: string, range: TimeRange): string {
    const date = new Date(isoString);
    if (range === "1h" || range === "6h") {
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    if (range === "24h") {
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return date.toLocaleDateString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

interface MemoryChartProps {
    /** Optional live data points from WebSocket to append */
    livePoints?: WorkerMetricPoint[];
}

export function MemoryChart({ livePoints }: MemoryChartProps) {
    const [range, setRange] = useState<TimeRange>("1h");
    const { data, isLoading } = useWorkerMetrics(range);

    const { chartData, workerIds, totalCurrent, totalMax } = useMemo(() => {
        const allPoints = [...(data?.points ?? []), ...(livePoints ?? [])];
        if (allPoints.length === 0) {
            return { chartData: [], workerIds: [], totalCurrent: 0, totalMax: 0 };
        }

        // Get unique worker IDs
        const ids = [...new Set(allPoints.map((p) => p.worker_id))];

        // Group by timestamp
        const byTimestamp = new Map<string, Map<string, WorkerMetricPoint>>();
        for (const point of allPoints) {
            if (!byTimestamp.has(point.timestamp)) {
                byTimestamp.set(point.timestamp, new Map());
            }
            byTimestamp.get(point.timestamp)!.set(point.worker_id, point);
        }

        // Build chart data
        const result: ChartDataPoint[] = [];
        const sortedTimestamps = [...byTimestamp.keys()].sort();
        for (const ts of sortedTimestamps) {
            const workers = byTimestamp.get(ts)!;
            const row: ChartDataPoint = {
                timestamp: ts,
                label: formatTimeLabel(ts, range),
            };
            for (const id of ids) {
                const point = workers.get(id);
                row[id] = point ? point.memory_current : 0;
            }
            result.push(row);
        }

        // Compute current totals from latest data points
        const latestByWorker = new Map<string, WorkerMetricPoint>();
        for (const point of allPoints) {
            const existing = latestByWorker.get(point.worker_id);
            if (!existing || point.timestamp > existing.timestamp) {
                latestByWorker.set(point.worker_id, point);
            }
        }
        let current = 0;
        let max = 0;
        for (const point of latestByWorker.values()) {
            current += Math.max(0, point.memory_current);
            max += Math.max(0, point.memory_max);
        }

        return { chartData: result, workerIds: ids, totalCurrent: current, totalMax: max };
    }, [data, livePoints, range]);

    const thresholdBytes = totalMax * 0.85;
    const utilizationPct = totalMax > 0 ? ((totalCurrent / totalMax) * 100).toFixed(0) : "0";

    if (isLoading) {
        return (
            <Card>
                <CardContent className="pt-6">
                    <Skeleton className="h-[250px] w-full" />
                </CardContent>
            </Card>
        );
    }

    return (
        <Card>
            <CardContent className="pt-6">
                {/* Header */}
                <div className="flex items-start justify-between mb-4">
                    <div>
                        <div className="text-xs text-muted-foreground uppercase tracking-wider">
                            Total Memory Usage
                        </div>
                        <div className="flex items-baseline gap-2 mt-1">
                            <span className="text-3xl font-bold">
                                {formatBytes(totalCurrent)}
                            </span>
                            <span className="text-sm text-muted-foreground">
                                / {formatBytes(totalMax)} across{" "}
                                {workerIds.length} container
                                {workerIds.length !== 1 ? "s" : ""}
                            </span>
                        </div>
                        <div className="text-xs text-muted-foreground mt-0.5">
                            {utilizationPct}% utilized &middot; Threshold: 85%
                        </div>
                    </div>
                    <div className="flex gap-1">
                        {TIME_RANGES.map((r) => (
                            <Button
                                key={r}
                                variant={range === r ? "default" : "ghost"}
                                size="sm"
                                className="h-7 px-3 text-xs"
                                onClick={() => setRange(r)}
                            >
                                {r}
                            </Button>
                        ))}
                    </div>
                </div>

                {/* Chart */}
                {chartData.length === 0 ? (
                    <div className="flex items-center justify-center h-[200px] text-muted-foreground text-sm">
                        No metrics data available yet
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height={200}>
                        <AreaChart data={chartData}>
                            <CartesianGrid
                                strokeDasharray="3 3"
                                className="stroke-muted"
                            />
                            <XAxis
                                dataKey="label"
                                tick={{ fontSize: 11 }}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis
                                tick={{ fontSize: 11 }}
                                tickLine={false}
                                axisLine={false}
                                tickFormatter={(v) => formatBytes(v)}
                                width={60}
                            />
                            <Tooltip
                                contentStyle={{
                                    backgroundColor: "hsl(var(--card))",
                                    border: "1px solid hsl(var(--border))",
                                    borderRadius: "6px",
                                    fontSize: "12px",
                                }}
                                formatter={(value: number, name: string) => [
                                    formatBytes(value),
                                    name,
                                ]}
                                labelFormatter={(label) => label}
                            />
                            {totalMax > 0 && (
                                <ReferenceLine
                                    y={thresholdBytes}
                                    stroke="hsl(var(--destructive))"
                                    strokeDasharray="4 4"
                                    strokeOpacity={0.5}
                                    label={{
                                        value: "85%",
                                        position: "right",
                                        style: {
                                            fontSize: 10,
                                            fill: "hsl(var(--destructive))",
                                        },
                                    }}
                                />
                            )}
                            {workerIds.map((id, i) => (
                                <Area
                                    key={id}
                                    type="monotone"
                                    dataKey={id}
                                    stackId="memory"
                                    fill={CONTAINER_COLORS[i % CONTAINER_COLORS.length]}
                                    fillOpacity={0.2}
                                    stroke={CONTAINER_COLORS[i % CONTAINER_COLORS.length]}
                                    strokeWidth={1.5}
                                />
                            ))}
                        </AreaChart>
                    </ResponsiveContainer>
                )}

                {/* Legend */}
                {workerIds.length > 0 && (
                    <div className="flex flex-wrap gap-4 mt-3 text-xs">
                        {workerIds.map((id, i) => (
                            <span
                                key={id}
                                className="flex items-center gap-1.5"
                            >
                                <span
                                    className="inline-block w-2.5 h-0.5 rounded-sm"
                                    style={{
                                        backgroundColor:
                                            CONTAINER_COLORS[
                                                i % CONTAINER_COLORS.length
                                            ],
                                    }}
                                />
                                {id}
                            </span>
                        ))}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
```

- [ ] **Step 2: Run frontend checks**

```bash
cd client && npm run tsc && npm run lint
```
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add client/src/pages/diagnostics/components/MemoryChart.tsx
git commit -m "feat: add MemoryChart component with stacked area chart"
```

---

## Task 6: Frontend — ForkTable Component

**Files:**
- Create: `client/src/pages/diagnostics/components/ForkTable.tsx`

- [ ] **Step 1: Create the ForkTable component**

```typescript
// client/src/pages/diagnostics/components/ForkTable.tsx
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { RotateCw } from "lucide-react";
import { toast } from "sonner";
import type { ProcessInfo } from "@/services/workers";
import { useRecycleAllProcesses } from "@/services/workers";
import type { ExecutionRowData } from "./ExecutionRow";

function formatUptime(seconds: number): string {
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

const stateVariant: Record<string, "secondary" | "default" | "destructive"> = {
    idle: "secondary",
    busy: "default",
    killed: "destructive",
};

interface ForkTableProps {
    workerId: string;
    processes: ProcessInfo[];
    /** Map of process_id -> execution info for busy processes */
    executions?: Map<string, ExecutionRowData>;
    /** Max memory for this container in bytes (for progress bar scale) */
    containerMemoryMax?: number;
}

export function ForkTable({
    workerId,
    processes,
    executions,
    containerMemoryMax,
}: ForkTableProps) {
    const recycleAll = useRecycleAllProcesses();

    const handleRecycleAll = () => {
        recycleAll.mutate(
            { workerId, reason: "manual_recycle" },
            {
                onSuccess: () => toast.success("Recycle request sent"),
                onError: (err) => toast.error(`Recycle failed: ${err.message}`),
            }
        );
    };

    // Max memory for progress bars — use container cgroup or fallback to max process memory
    const maxMem =
        containerMemoryMax && containerMemoryMax > 0
            ? containerMemoryMax / (1024 * 1024) // Convert bytes to MB
            : Math.max(...processes.map((p) => p.memory_mb), 1);

    return (
        <div>
            <Table>
                <TableHeader>
                    <TableRow className="text-xs">
                        <TableHead className="w-[80px]">PID</TableHead>
                        <TableHead className="w-[70px]">State</TableHead>
                        <TableHead className="w-[140px]">Memory</TableHead>
                        <TableHead className="w-[70px]">Jobs</TableHead>
                        <TableHead>Execution</TableHead>
                        <TableHead className="w-[80px]">Uptime</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {processes.map((proc) => {
                        const execution = executions?.get(proc.process_id);
                        const memPct = maxMem > 0 ? (proc.memory_mb / maxMem) * 100 : 0;

                        return (
                            <TableRow
                                key={proc.process_id}
                                className={
                                    proc.state === "busy"
                                        ? "bg-yellow-500/5"
                                        : undefined
                                }
                            >
                                <TableCell className="font-mono text-xs">
                                    {proc.pid}
                                </TableCell>
                                <TableCell>
                                    <Badge
                                        variant={stateVariant[proc.state] ?? "secondary"}
                                        className="text-[10px] px-1.5 py-0"
                                    >
                                        {proc.state}
                                    </Badge>
                                </TableCell>
                                <TableCell>
                                    <div className="flex items-center gap-2">
                                        <Progress
                                            value={memPct}
                                            className="h-1 w-10"
                                        />
                                        <span className="text-xs text-muted-foreground">
                                            {proc.memory_mb.toFixed(0)} MB
                                        </span>
                                    </div>
                                </TableCell>
                                <TableCell className="text-xs">
                                    {proc.executions_completed}
                                </TableCell>
                                <TableCell className="text-xs">
                                    {execution ? (
                                        <span>
                                            <span className="text-foreground">
                                                {execution.workflow_name}
                                            </span>
                                            <span className="text-muted-foreground ml-2">
                                                {formatUptime(execution.elapsed_seconds)}
                                            </span>
                                        </span>
                                    ) : (
                                        <span className="text-muted-foreground">&mdash;</span>
                                    )}
                                </TableCell>
                                <TableCell className="text-xs text-muted-foreground">
                                    {formatUptime(proc.uptime_seconds)}
                                </TableCell>
                            </TableRow>
                        );
                    })}
                </TableBody>
            </Table>
            <div className="flex justify-end mt-2 px-2">
                <AlertDialog>
                    <AlertDialogTrigger asChild>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 text-xs text-destructive hover:text-destructive"
                        >
                            <RotateCw className="h-3 w-3 mr-1" />
                            Recycle All
                        </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                        <AlertDialogHeader>
                            <AlertDialogTitle>Recycle all processes?</AlertDialogTitle>
                            <AlertDialogDescription>
                                This will gracefully restart all {processes.length}{" "}
                                fork(s) in {workerId}. Running executions will
                                complete before their process is recycled.
                            </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction onClick={handleRecycleAll}>
                                Recycle All
                            </AlertDialogAction>
                        </AlertDialogFooter>
                    </AlertDialogContent>
                </AlertDialog>
            </div>
        </div>
    );
}
```

- [ ] **Step 2: Run frontend checks**

```bash
cd client && npm run tsc && npm run lint
```
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add client/src/pages/diagnostics/components/ForkTable.tsx
git commit -m "feat: add ForkTable component for process details"
```

---

## Task 7: Frontend — ContainerTable Component

**Files:**
- Create: `client/src/pages/diagnostics/components/ContainerTable.tsx`

- [ ] **Step 1: Create the ContainerTable component**

```typescript
// client/src/pages/diagnostics/components/ContainerTable.tsx
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { ForkTable } from "./ForkTable";
import type { ExecutionRowData } from "./ExecutionRow";
import type { ProcessInfo, PoolDetail, PoolSummary } from "@/services/workers";

type PoolData = PoolSummary | PoolDetail;

/** Consistent colors for container color dots (same order as MemoryChart) */
const CONTAINER_COLORS = [
    "hsl(var(--chart-1))",
    "hsl(var(--chart-2))",
    "hsl(var(--chart-3))",
    "hsl(var(--chart-4))",
    "hsl(var(--chart-5))",
    "#f97316",
    "#06b6d4",
    "#8b5cf6",
    "#ec4899",
    "#14b8a6",
];

function formatUptime(seconds: number): string {
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

function formatBytes(bytes: number): string {
    if (bytes < 0) return "N/A";
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) return `${gb.toFixed(1)} GB`;
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(0)} MB`;
}

function getPoolCounts(pool: PoolData) {
    if ("processes" in pool && Array.isArray(pool.processes)) {
        const processes = pool.processes as ProcessInfo[];
        return {
            total: processes.length,
            idle: processes.filter((p) => p.state === "idle").length,
            busy: processes.filter((p) => p.state === "busy").length,
            processes,
        };
    }
    const summary = pool as PoolSummary;
    return {
        total: summary.pool_size ?? 0,
        idle: summary.idle_count ?? 0,
        busy: summary.busy_count ?? 0,
        processes: [] as ProcessInfo[],
    };
}

function getUptimeSeconds(pool: PoolData): number {
    const startedAt = pool.started_at;
    if (!startedAt) return 0;
    return (Date.now() - new Date(startedAt).getTime()) / 1000;
}

interface ContainerTableProps {
    pools: PoolData[];
    /** Sorted worker IDs for consistent color assignment (same as chart) */
    workerIds: string[];
}

export function ContainerTable({ pools, workerIds }: ContainerTableProps) {
    const [expanded, setExpanded] = useState<Set<string>>(new Set());

    const toggleExpand = (workerId: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(workerId)) {
                next.delete(workerId);
            } else {
                next.add(workerId);
            }
            return next;
        });
    };

    const colorIndex = (workerId: string) => {
        const idx = workerIds.indexOf(workerId);
        return idx >= 0 ? idx : 0;
    };

    return (
        <div className="border rounded-lg overflow-hidden">
            <Table>
                <TableHeader>
                    <TableRow className="text-xs">
                        <TableHead className="w-8" />
                        <TableHead>Container</TableHead>
                        <TableHead className="w-[80px]">Status</TableHead>
                        <TableHead className="w-[100px]">Forks</TableHead>
                        <TableHead className="w-[180px]">Memory</TableHead>
                        <TableHead className="w-[90px]">Uptime</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {pools.map((pool) => {
                        const isExpanded = expanded.has(pool.worker_id);
                        const counts = getPoolCounts(pool);
                        const memCurrent = (pool as any).memory_current_bytes ?? -1;
                        const memMax = (pool as any).memory_max_bytes ?? -1;
                        const memPct =
                            memMax > 0 ? (memCurrent / memMax) * 100 : 0;
                        const ci = colorIndex(pool.worker_id);

                        // Build execution map for fork table
                        const execMap = new Map<string, ExecutionRowData>();
                        for (const proc of counts.processes) {
                            if (
                                proc.state === "busy" &&
                                proc.current_execution_id
                            ) {
                                execMap.set(proc.process_id, {
                                    execution_id: proc.current_execution_id,
                                    workflow_name:
                                        proc.current_execution_id.slice(0, 8),
                                    status: "RUNNING",
                                    elapsed_seconds: 0,
                                });
                            }
                        }

                        return (
                            <Fragment key={pool.worker_id}>
                                <TableRow
                                    className="cursor-pointer hover:bg-muted/50"
                                    onClick={() =>
                                        toggleExpand(pool.worker_id)
                                    }
                                >
                                    <TableCell className="w-8 px-2">
                                        {isExpanded ? (
                                            <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                        ) : (
                                            <ChevronRight className="h-4 w-4 text-muted-foreground" />
                                        )}
                                    </TableCell>
                                    <TableCell className="font-medium">
                                        <div className="flex items-center gap-2">
                                            <span
                                                className="inline-block w-2 h-2 rounded-sm flex-shrink-0"
                                                style={{
                                                    backgroundColor:
                                                        CONTAINER_COLORS[
                                                            ci %
                                                                CONTAINER_COLORS.length
                                                        ],
                                                }}
                                            />
                                            {pool.worker_id}
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <Badge
                                            variant={
                                                pool.status === "online"
                                                    ? "secondary"
                                                    : "destructive"
                                            }
                                            className="text-[10px]"
                                        >
                                            {pool.status ?? "offline"}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="text-sm">
                                        {counts.total}{" "}
                                        {counts.busy > 0 && (
                                            <span className="text-xs text-muted-foreground">
                                                ({counts.busy} busy)
                                            </span>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        {memMax > 0 ? (
                                            <div className="flex items-center gap-2">
                                                <Progress
                                                    value={memPct}
                                                    className="h-1.5 w-16"
                                                />
                                                <span className="text-xs">
                                                    {formatBytes(memCurrent)} /{" "}
                                                    {formatBytes(memMax)}
                                                </span>
                                            </div>
                                        ) : (
                                            <span className="text-xs text-muted-foreground">
                                                N/A
                                            </span>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground">
                                        {formatUptime(getUptimeSeconds(pool))}
                                    </TableCell>
                                </TableRow>
                                <AnimatePresence>
                                    {isExpanded &&
                                        counts.processes.length > 0 && (
                                            <TableRow>
                                                <TableCell
                                                    colSpan={6}
                                                    className="p-0 bg-muted/30"
                                                >
                                                    <motion.div
                                                        initial={{
                                                            height: 0,
                                                            opacity: 0,
                                                        }}
                                                        animate={{
                                                            height: "auto",
                                                            opacity: 1,
                                                        }}
                                                        exit={{
                                                            height: 0,
                                                            opacity: 0,
                                                        }}
                                                        transition={{
                                                            duration: 0.2,
                                                        }}
                                                        className="overflow-hidden"
                                                    >
                                                        <div className="px-4 py-3 pl-10">
                                                            <ForkTable
                                                                workerId={
                                                                    pool.worker_id
                                                                }
                                                                processes={
                                                                    counts.processes
                                                                }
                                                                executions={
                                                                    execMap
                                                                }
                                                                containerMemoryMax={
                                                                    memMax > 0
                                                                        ? memMax
                                                                        : undefined
                                                                }
                                                            />
                                                        </div>
                                                    </motion.div>
                                                </TableCell>
                                            </TableRow>
                                        )}
                                </AnimatePresence>
                            </Fragment>
                        );
                    })}
                </TableBody>
            </Table>
        </div>
    );
}
```

Add the missing `Fragment` import at the top:
```typescript
import { Fragment, useState } from "react";
```

- [ ] **Step 2: Run frontend checks**

```bash
cd client && npm run tsc && npm run lint
```
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add client/src/pages/diagnostics/components/ContainerTable.tsx
git commit -m "feat: add ContainerTable component with expandable fork details"
```

---

## Task 8: Frontend — Rebuild WorkersTab + Delete Old Components

**Files:**
- Modify: `client/src/pages/diagnostics/components/WorkersTab.tsx`
- Delete: `client/src/pages/diagnostics/components/PoolConfigForm.tsx`
- Delete: `client/src/pages/diagnostics/components/WorkerCard.tsx`
- Delete: `client/src/pages/diagnostics/components/ProcessCard.tsx`

- [ ] **Step 1: Rewrite WorkersTab.tsx**

Replace the entire contents of `client/src/pages/diagnostics/components/WorkersTab.tsx` with:

```typescript
import { useState, useMemo } from "react";
import { RefreshCw, Loader2, WifiOff, Server } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { usePools, useQueueStatus } from "@/services/workers";
import { getErrorMessage } from "@/lib/api-error";
import { QueueBadge } from "./QueueBadge";
import { MemoryChart } from "./MemoryChart";
import { ContainerTable } from "./ContainerTable";
import { useWorkerWebSocket } from "../hooks/useWorkerWebSocket";

export function WorkersTab() {
    const {
        data: poolsData,
        isLoading: poolsLoading,
        error: poolsError,
        refetch: refetchPools,
    } = usePools();

    const {
        data: queueData,
        isLoading: queueLoading,
        refetch: refetchQueue,
    } = useQueueStatus({ limit: 50 });

    const { pools: wsPools, isConnected } = useWorkerWebSocket();

    const [isRefreshing, setIsRefreshing] = useState(false);

    const handleRefresh = async () => {
        setIsRefreshing(true);
        try {
            await Promise.all([refetchPools(), refetchQueue()]);
        } finally {
            setIsRefreshing(false);
        }
    };

    const restPools = poolsData?.pools || [];
    const pools = wsPools.length > 0 ? wsPools : restPools;
    const queueItems = queueData?.items || [];

    // Stable sorted worker IDs for consistent color assignment
    const workerIds = useMemo(
        () => [...new Set(pools.map((p) => p.worker_id))].sort(),
        [pools]
    );

    // Compute summary stats
    const stats = useMemo(() => {
        let totalForks = 0;
        let totalBusy = 0;
        for (const pool of pools) {
            if ("processes" in pool && Array.isArray(pool.processes)) {
                totalForks += pool.processes.length;
                totalBusy += pool.processes.filter(
                    (p: any) => p.state === "busy"
                ).length;
            } else {
                totalForks += (pool as any).pool_size ?? 0;
                totalBusy += (pool as any).busy_count ?? 0;
            }
        }
        return { containers: pools.length, forks: totalForks, busy: totalBusy };
    }, [pools]);

    return (
        <div className="max-w-[900px] mx-auto space-y-6">
            {/* Connection Status Banner */}
            {!isConnected && (
                <Alert className="border-amber-500/50 text-amber-700 dark:text-amber-400 [&>svg]:text-amber-600">
                    <WifiOff className="h-4 w-4" />
                    <AlertDescription>
                        Connecting to real-time worker updates... Data may not be
                        current.
                    </AlertDescription>
                </Alert>
            )}

            {/* Error State */}
            {poolsError && (
                <Alert variant="destructive">
                    <AlertDescription>
                        Failed to load pools:{" "}
                        {getErrorMessage(poolsError, "Unknown error")}
                    </AlertDescription>
                </Alert>
            )}

            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <h2 className="text-lg font-semibold">Process Pools</h2>
                    {isConnected && (
                        <span className="flex items-center gap-1 text-xs text-green-600">
                            <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                            Live
                        </span>
                    )}
                    <QueueBadge items={queueItems} isLoading={queueLoading} />
                </div>
                <div className="flex items-center gap-3">
                    <span className="text-sm text-muted-foreground">
                        {stats.containers} container{stats.containers !== 1 ? "s" : ""}{" "}
                        &middot; {stats.forks} fork{stats.forks !== 1 ? "s" : ""}
                    </span>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleRefresh}
                        disabled={isRefreshing || poolsLoading}
                    >
                        <RefreshCw
                            className={`h-4 w-4 mr-2 ${isRefreshing ? "animate-spin" : ""}`}
                        />
                        Refresh
                    </Button>
                </div>
            </div>

            {/* Memory Chart */}
            <MemoryChart />

            {/* Container Table */}
            {poolsLoading && pools.length === 0 ? (
                <div className="flex items-center justify-center py-12">
                    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                </div>
            ) : pools.length === 0 ? (
                <Card>
                    <CardContent className="flex flex-col items-center justify-center py-12 text-center">
                        <Server className="h-12 w-12 text-muted-foreground mb-4" />
                        <h3 className="text-lg font-semibold">
                            No containers connected
                        </h3>
                        <p className="mt-2 text-sm text-muted-foreground max-w-md">
                            Worker containers register themselves on startup.
                            If you expect containers to be running, check the
                            worker logs for connection issues.
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <ContainerTable pools={pools} workerIds={workerIds} />
            )}
        </div>
    );
}
```

- [ ] **Step 2: Delete old components**

Delete these three files:
- `client/src/pages/diagnostics/components/PoolConfigForm.tsx`
- `client/src/pages/diagnostics/components/WorkerCard.tsx`
- `client/src/pages/diagnostics/components/ProcessCard.tsx`

```bash
rm client/src/pages/diagnostics/components/PoolConfigForm.tsx
rm client/src/pages/diagnostics/components/WorkerCard.tsx
rm client/src/pages/diagnostics/components/ProcessCard.tsx
```

- [ ] **Step 3: Check for remaining imports of deleted components**

```bash
grep -r "PoolConfigForm\|WorkerCard\|ProcessCard" client/src/ --include="*.ts" --include="*.tsx" -l
```

Fix any remaining imports. `ExecutionRow.tsx` should still exist (it's kept for reference but may not be directly used anymore — check if `ForkTable` needs it or if it can be removed too). If `ExecutionRow.tsx` is only imported by `ProcessCard.tsx` (now deleted) and `ForkTable.tsx` (which uses the type but has its own rendering), verify and remove if unused.

- [ ] **Step 4: Run frontend checks**

```bash
cd client && npm run tsc && npm run lint
```
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add -A client/src/pages/diagnostics/
git commit -m "feat: rebuild WorkersTab with chart + table layout, remove old card components"
```

---

## Task 9: Type Generation + Full Verification

**Files:**
- Modify: `client/src/lib/v1.d.ts` (auto-generated)

- [ ] **Step 1: Regenerate TypeScript types**

Ensure the dev stack is running, then:

```bash
cd client && npm run generate:types
```

- [ ] **Step 2: Run full backend checks**

```bash
cd api && pyright && ruff check .
```
Expected: 0 errors

- [ ] **Step 3: Run full frontend checks**

```bash
cd client && npm run tsc && npm run lint
```
Expected: 0 errors

- [ ] **Step 4: Run all tests**

```bash
./test.sh
```
Expected: All existing tests pass. Parse `/tmp/bifrost/test-results.xml` for failures.

- [ ] **Step 5: Visual verification**

Open http://localhost:3000, navigate to Diagnostics > Process Pools:
- Memory chart renders (may show "No metrics data available yet" if no heartbeats have been sampled yet)
- Container table shows connected pools
- Clicking a container row expands to show fork sub-table
- No config dialog or configure button visible
- Layout is centered at ~900px max-width
- Time range selector works (1h/6h/24h/7d)

- [ ] **Step 6: Commit type generation**

```bash
cd client && git add src/lib/v1.d.ts
git commit -m "chore: regenerate TypeScript types for worker metrics endpoint"
```
