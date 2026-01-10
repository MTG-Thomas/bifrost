# Phase 5: Diagnostics UI

Update the frontend diagnostics page to show the simplified process pool model.

## Overview

Changes to UI:
- Update WorkersTab to show processes (not threads)
- Show IDLE/BUSY state per process
- Keep recycle button
- Remove blacklist tab entirely

## Files to Modify

- `client/src/pages/diagnostics/WorkersTab.tsx` - Main workers view
- `client/src/pages/diagnostics/index.tsx` - Tab container
- `client/src/services/workers.ts` - API client types

## Implementation Tasks

### Task 5.1: Update TypeScript types

**File:** `client/src/services/workers.ts`

```typescript
export interface ProcessInfo {
  pid: number;
  process_id: string;
  state: "idle" | "busy" | "killed";
  memory_mb: number;
  uptime_seconds: number;
  executions_completed: number;
  execution?: ExecutionInfo;
}

export interface ExecutionInfo {
  execution_id: string;
  started_at: string;
  elapsed_seconds: number;
}

export interface WorkerInfo {
  worker_id: string;
  started_at: string;
  hostname: string;
  min_workers: number;
  max_workers: number;
  pool_size: number;
  idle_count: number;
  busy_count: number;
  processes: ProcessInfo[];
}

export interface WorkerHeartbeat {
  type: "worker_heartbeat";
  worker_id: string;
  timestamp: string;
  processes: ProcessInfo[];
  pool_size: number;
  idle_count: number;
  busy_count: number;
}
```

### Task 5.2: Update WorkersTab layout

Show pool overview and process cards:

```tsx
function WorkersTab() {
  const { workers, heartbeat } = useWorkerData();

  return (
    <div className="space-y-6">
      {/* Pool Overview */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Pool Size" value={heartbeat?.pool_size ?? 0} />
        <StatCard label="Idle" value={heartbeat?.idle_count ?? 0} color="green" />
        <StatCard label="Busy" value={heartbeat?.busy_count ?? 0} color="yellow" />
        <StatCard label="Min/Max" value={`${worker?.min_workers}/${worker?.max_workers}`} />
      </div>

      {/* Process Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {heartbeat?.processes.map((process) => (
          <ProcessCard key={process.process_id} process={process} />
        ))}
      </div>
    </div>
  );
}
```

### Task 5.3: Create ProcessCard component

```tsx
interface ProcessCardProps {
  process: ProcessInfo;
  onRecycle?: (pid: number) => void;
}

function ProcessCard({ process, onRecycle }: ProcessCardProps) {
  const stateColors = {
    idle: "bg-green-100 text-green-800",
    busy: "bg-yellow-100 text-yellow-800",
    killed: "bg-red-100 text-red-800",
  };

  return (
    <Card className="p-4">
      <div className="flex justify-between items-start mb-3">
        <div>
          <div className="font-medium">{process.process_id}</div>
          <div className="text-sm text-muted-foreground">PID: {process.pid}</div>
        </div>
        <Badge className={stateColors[process.state]}>
          {process.state.toUpperCase()}
        </Badge>
      </div>

      <div className="space-y-2 text-sm">
        <div className="flex justify-between">
          <span>Memory</span>
          <span>{process.memory_mb.toFixed(1)} MB</span>
        </div>
        <div className="flex justify-between">
          <span>Uptime</span>
          <span>{formatDuration(process.uptime_seconds)}</span>
        </div>
        <div className="flex justify-between">
          <span>Executions</span>
          <span>{process.executions_completed}</span>
        </div>
      </div>

      {/* Current execution (if busy) */}
      {process.execution && (
        <div className="mt-3 p-2 bg-muted rounded">
          <div className="text-xs text-muted-foreground">Running:</div>
          <div className="text-sm font-mono truncate">
            {process.execution.execution_id.slice(0, 8)}...
          </div>
          <div className="text-xs text-muted-foreground">
            {formatDuration(process.execution.elapsed_seconds)}
          </div>
        </div>
      )}

      {/* Recycle button (only for idle) */}
      {process.state === "idle" && onRecycle && (
        <Button
          variant="outline"
          size="sm"
          className="w-full mt-3"
          onClick={() => onRecycle(process.pid)}
        >
          <RefreshCw className="h-4 w-4 mr-2" />
          Recycle
        </Button>
      )}
    </Card>
  );
}
```

### Task 5.4: Remove BlacklistTab

**Delete file:** `client/src/pages/diagnostics/BlacklistTab.tsx`

**Update index.tsx:**

```tsx
// REMOVE import
// import { BlacklistTab } from "./BlacklistTab";

// REMOVE from tabs array
const tabs = [
  { id: "logs", label: "System Logs", component: LogsTab },
  { id: "workers", label: "Workers", component: WorkersTab },
  // REMOVE: { id: "blacklist", label: "Blacklist", component: BlacklistTab },
];
```

### Task 5.5: Keep queue visualization

The queue visualization showing pending executions should remain:

```tsx
function QueueSection() {
  const { queue } = useQueueData();

  return (
    <div className="space-y-2">
      <h3 className="font-medium">Execution Queue</h3>
      <div className="flex flex-wrap gap-2">
        {queue.items.map((item, index) => (
          <Badge key={item.execution_id} variant="outline">
            #{index + 1} {item.execution_id.slice(0, 8)}...
          </Badge>
        ))}
        {queue.items.length === 0 && (
          <span className="text-muted-foreground">No pending executions</span>
        )}
      </div>
    </div>
  );
}
```

### Task 5.6: Update WebSocket subscription

```tsx
function useWorkerData() {
  const [heartbeat, setHeartbeat] = useState<WorkerHeartbeat | null>(null);

  useEffect(() => {
    const unsubscribe = subscribeToChannel("workers", (message) => {
      if (message.type === "worker_heartbeat") {
        setHeartbeat(message.data);
      }
    });

    return unsubscribe;
  }, []);

  return { heartbeat };
}
```

### Task 5.7: Remove blacklist-related API calls

**In workers.ts service file:**

```typescript
// REMOVE these functions:
// export async function getBlacklistedWorkflows() { ... }
// export async function blacklistWorkflow(...) { ... }
// export async function removeFromBlacklist(...) { ... }
```

## Frontend Tests

- [ ] WorkersTab renders process cards
- [ ] ProcessCard shows correct state colors
- [ ] Recycle button only shows for idle processes
- [ ] Queue visualization works
- [ ] WebSocket updates heartbeat state

## Checklist

- [ ] TypeScript types updated
- [ ] WorkersTab shows process pool overview
- [ ] ProcessCard component created
- [ ] State colors (idle=green, busy=yellow)
- [ ] Current execution shown when busy
- [ ] Recycle button functional
- [ ] BlacklistTab deleted
- [ ] Tab container updated
- [ ] Blacklist API calls removed
- [ ] WebSocket subscription works
- [ ] Frontend builds without errors
