# Process Pools Dashboard Redesign

## Context

The Process Pools tab in Diagnostics was built around a persistent-process model with configurable min/max workers. With the move to fork-based execution, persistent processes no longer exist — each pool is a single container that forks on demand. The current UI has:

- A config dialog for min/max workers (no longer relevant)
- Large expandable cards per pool (doesn't scale to 100 containers)
- No container-level memory visibility
- No historical resource data
- Full-width layout that wastes space

This redesign removes dead configuration UI, adds container memory monitoring with persisted history, and restructures the layout for clarity and scalability.

## Design

### Layout

Centered container, max-width ~900px. Three-layer vertical hierarchy:

1. **Header strip** — Title, live indicator, queue badge, container/fork counts, refresh button
2. **Aggregate memory chart** — Stacked area chart showing total memory across all containers over time
3. **Container table** — Dense table with expandable rows for fork details

### Aggregate Memory Chart

A stacked area chart (recharts `AreaChart`) showing memory usage across all containers over time. Each container is a colored layer in the stack.

- **Time range selector**: 1h, 6h, 24h, 7d (buttons above chart, right-aligned)
- **85% pressure threshold**: Dashed red horizontal reference line, matching the existing `memory_pressure_threshold` setting used for fork admission control
- **Y-axis**: 0 to max cgroup limit across containers (in GB)
- **X-axis**: Time, formatted based on range (HH:MM for 1h/6h, day+time for 24h/7d)
- **Legend**: Below chart, shows container name + current memory for each, color-coded
- **Live updates**: While page is open, WebSocket heartbeats append data points to the chart between API fetches

Follows existing recharts patterns from `ResourceTrendChart.tsx` — uses `ResponsiveContainer`, `Card` wrapper, loading skeleton.

### Container Table

Replaces the current `PoolCard` accordion. One row per container/worker.

**Columns:**
| Column | Content |
|--------|---------|
| Container | Color dot (matches chart) + worker_id hostname |
| Status | Badge: online/offline |
| Forks | Total count + busy count in parentheses |
| Memory | Inline progress bar + "current / max GB" text (cgroup data) |
| Uptime | Relative time since container started |
| Expand | Chevron indicator |

Click a row to expand and show the fork sub-table.

### Fork Sub-Table

Rendered inside an expanded container row. Dense table (not cards) — handles dozens of forks per container.

**Columns:**
| Column | Content |
|--------|---------|
| PID | OS process ID (monospace) |
| State | idle (green) / busy (yellow) / killed (red) |
| Memory | Mini progress bar + MB value (RSS from psutil) |
| Jobs Done | `executions_completed` count |
| Current Execution | Workflow name + elapsed time if busy, dash if idle |
| Uptime | Relative time since fork started |

**Actions:** "Recycle All" button at bottom of expanded section (existing functionality, relocated).

### Removed

- `PoolConfigForm.tsx` — deleted entirely (no min/max workers config)
- `WorkerCard.tsx` — replaced by container table rows
- `ProcessCard.tsx` — replaced by fork sub-table rows
- Configure button and worker count display from header
- 4-card stats grid (pools/processes/idle/busy) — replaced by inline counts in header

## Backend Changes

### New Table: `worker_metrics`

SQLAlchemy model `WorkerMetric` in `api/src/models/worker_metric.py`. Stores periodic resource snapshots for the aggregate chart.

```
id              BIGINT PRIMARY KEY (auto-increment)
worker_id       VARCHAR NOT NULL
timestamp       TIMESTAMPTZ NOT NULL
memory_current  BIGINT NOT NULL      -- cgroup memory.current (bytes)
memory_max      BIGINT NOT NULL      -- cgroup memory.max (bytes)
fork_count      INTEGER NOT NULL
busy_count      INTEGER NOT NULL
idle_count      INTEGER NOT NULL
```

Index on `(timestamp)` for range queries. Index on `(worker_id, timestamp)` for per-container lookups.

### Sampling

The heartbeat fires every ~10s. Sample one snapshot per minute per container (write on every 6th heartbeat). At 3 containers this produces ~4,300 rows/day.

### Retention

Automatic cleanup of rows older than 7 days, run by the existing scheduler. Caps table at ~30k rows for typical deployments.

### New API Endpoint

`GET /api/platform/workers/metrics?range=1h`

**Parameters:**
- `range`: `1h`, `6h`, `24h`, `7d`

**Response:** Array of time-series data points, each containing timestamp, worker_id, memory_current, memory_max, fork_count, busy_count, idle_count.

**Downsampling:**
- 1h: raw data (~60 points per container)
- 6h/24h: averaged to 1 point per 5 minutes
- 7d: averaged to 1 point per 30 minutes

### Heartbeat Change

Include `memory_current_bytes` and `memory_max_bytes` from `get_cgroup_memory()` in the heartbeat payload. This function already exists in `memory_monitor.py` and is called for admission control — it just needs to be added to `_build_heartbeat()` in `process_pool.py`.

## Frontend Changes

### New Components

- `MemoryChart.tsx` — Stacked area chart using recharts. Fetches from metrics API, supplements with WebSocket heartbeat data. Time range selector. Threshold reference line.
- `ContainerTable.tsx` — Table component replacing pool cards. Expandable rows. Inline memory bars per container.
- `ForkTable.tsx` — Sub-table rendered inside expanded container rows. Dense process list.

### Modified Components

- `WorkersTab.tsx` — Rebuilt. New layout: header, MemoryChart, ContainerTable. Removes config dialog state, stats grid, PoolCard references.

### Removed Components

- `PoolConfigForm.tsx`
- `WorkerCard.tsx`
- `ProcessCard.tsx`

### New Service Function

`getWorkerMetrics(range: string)` in `client/src/services/workers.ts` — calls `GET /api/platform/workers/metrics`.

### Chart Data Flow

1. On mount + range change: fetch historical data from metrics API
2. While page is open: append live points from WebSocket heartbeats
3. Range selector re-fetches from API

## Critical Files

| File | Action |
|------|--------|
| `api/src/models/contracts/platform.py` | Add metrics response models |
| `api/src/routers/platform/workers.py` | Add metrics endpoint |
| `api/src/services/execution/process_pool.py` | Add cgroup data to heartbeat, add sampling logic |
| `api/src/services/execution/memory_monitor.py` | Already has `get_cgroup_memory()` — no changes needed |
| `api/alembic/` | New migration for `worker_metrics` table |
| `client/src/pages/diagnostics/components/WorkersTab.tsx` | Rebuild |
| `client/src/pages/diagnostics/components/MemoryChart.tsx` | New |
| `client/src/pages/diagnostics/components/ContainerTable.tsx` | New |
| `client/src/pages/diagnostics/components/ForkTable.tsx` | New |
| `client/src/pages/diagnostics/components/PoolConfigForm.tsx` | Delete |
| `client/src/pages/diagnostics/components/WorkerCard.tsx` | Delete |
| `client/src/pages/diagnostics/components/ProcessCard.tsx` | Delete |
| `client/src/services/workers.ts` | Add `getWorkerMetrics()` |
| `client/src/components/charts/ResourceTrendChart.tsx` | Reference for recharts patterns |

## Verification

1. **Backend:** Run `./test.sh` — existing pool tests should pass. Write unit test for metrics sampling and downsampling logic. Write E2E test for metrics endpoint.
2. **Type generation:** `cd client && npm run generate:types` after API changes.
3. **Frontend:** `cd client && npm run tsc && npm run lint` — type check and lint pass.
4. **Backend lint:** `cd api && pyright && ruff check .`
5. **Visual:** Open http://localhost:3000, navigate to Diagnostics > Process Pools. Verify chart renders with historical data, container table is expandable, fork sub-table shows process details.
6. **Responsiveness:** Verify centered layout at various viewport widths. Content should not stretch beyond ~900px.
