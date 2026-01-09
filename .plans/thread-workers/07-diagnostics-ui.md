# Phase 7: Diagnostics UI

## Overview

Create a new Diagnostics page (rename from Logs) with tabs for System Logs, Workers, and Blacklisted Workflows. The Workers tab shows real-time queue and worker state with animated transitions.

## UI Structure

```
Diagnostics
├── Tab: System Logs (existing logs page content)
├── Tab: Workers (new - real-time monitoring)
└── Tab: Blacklisted Workflows (new - blacklist management)
```

## Workers Tab Wireframe

```
┌─────────────────────────────────────────────────────────────────┐
│ Workers                                                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐│
│ │ QUEUE (3 pending)                                    [↻]    ││
│ │ ┌─────────────────────────────────────────────────────────┐ ││
│ │ │ #1  Sync Contacts       Org A        queued 5s ago      │ ││
│ │ │ #2  Generate Report     Org B        queued 12s ago     │ ││
│ │ │ #3  Import Users        Org A        queued 18s ago     │ ││
│ │ └─────────────────────────────────────────────────────────┘ ││
│ └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│                    ↓ (job fades out when picked up)            │
│                                                                 │
│ ▼ Worker: bifrost-worker-7f8d9                                 │
│   Online since: 2 hours ago                                     │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ Process 12345                              [Recycle]     │  │
│   │ Status: ACTIVE  │  Memory: 245 MB  │  Uptime: 1h 23m    │  │
│   │ Jobs Processed: 847                                      │  │
│   │                                                          │  │
│   │ Active Executions:                                       │  │
│   │ ┌──────────────────────────────────────────────────────┐│  │
│   │ │ ● Sync Contacts          Running      45s    [View] ││  │
│   │ │ ● Generate Report        Running      2m 12s [View] ││  │
│   │ └──────────────────────────────────────────────────────┘│  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ Process 12289                              [Recycle]     │  │
│   │ Status: DRAINING  │  Memory: 312 MB  │  Uptime: 45m     │  │
│   │ Waiting for 1 healthy job to complete                    │  │
│   │                                                          │  │
│   │ Active Executions:                                       │  │
│   │ ┌──────────────────────────────────────────────────────┐│  │
│   │ │ ● Import Users           Completing   8m 34s [View] ││  │
│   │ │ ⚠️ Bad Cron Job          STUCK        25m     [View] ││  │
│   │ └──────────────────────────────────────────────────────┘│  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Blacklisted Workflows Tab Wireframe

```
┌─────────────────────────────────────────────────────────────────┐
│ Blacklisted Workflows                                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐│
│ │ Workflow         │ Reason      │ Blacklisted   │ Actions    ││
│ ├──────────────────┼─────────────┼───────────────┼────────────┤│
│ │ Bad Cron Job     │ 12x stuck   │ 5 min ago     │ [Remove]   ││
│ │ Broken Import    │ 5x stuck    │ 2 hours ago   │ [Remove]   ││
│ │ Test Workflow    │ Manual      │ 1 day ago     │ [Remove]   ││
│ └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│ [+ Manually Blacklist Workflow]                                │
│                                                                 │
│ (empty state: "No workflows are currently blacklisted")        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Tasks

### 1. Page Structure
- [ ] Create `client/src/pages/diagnostics/` directory
- [ ] Create `DiagnosticsPage.tsx` with tab navigation
- [ ] Move existing logs page content to `SystemLogsTab.tsx`
- [ ] Rename navigation item from "Logs" to "Diagnostics"

### 2. Workers Tab Component
- [ ] Create `WorkersTab.tsx`
- [ ] Create `QueueSection.tsx` for pending jobs
- [ ] Create `WorkerCard.tsx` for worker display
- [ ] Create `ProcessCard.tsx` for process details
- [ ] Create `ExecutionRow.tsx` for active executions

### 3. WebSocket Integration
- [ ] Subscribe to `platform_workers` group on mount
- [ ] Handle `worker_heartbeat` events
- [ ] Handle `worker_online` / `worker_offline` events
- [ ] Handle `process_state_changed` events
- [ ] Handle `execution_stuck` events

### 4. Animations
- [ ] Job enters queue → fade in at bottom
- [ ] Job picked up → fade out of queue, fade in under worker
- [ ] Job completes → brief checkmark, then fade out
- [ ] Job stuck → red highlight animation, warning icon
- [ ] Process state change → status badge transition

### 5. Recycle Action
- [ ] Add Recycle button to ProcessCard
- [ ] Confirmation dialog before recycle
- [ ] Call `POST /api/platform/workers/{id}/processes/{pid}/recycle`
- [ ] Show loading state while processing
- [ ] Toast notification on success/failure

### 6. Blacklisted Workflows Tab
- [ ] Create `BlacklistTab.tsx`
- [ ] Fetch blacklisted workflows from API
- [ ] Display table with workflow info
- [ ] Remove button with confirmation
- [ ] Manual blacklist dialog

### 7. Navigation Updates
- [ ] Update sidebar/navigation to show "Diagnostics" instead of "Logs"
- [ ] Update routes in App.tsx
- [ ] Add redirect from old /logs path

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `client/src/pages/diagnostics/DiagnosticsPage.tsx` | CREATE | Main page with tabs |
| `client/src/pages/diagnostics/components/SystemLogsTab.tsx` | CREATE | Moved from logs page |
| `client/src/pages/diagnostics/components/WorkersTab.tsx` | CREATE | Workers monitoring |
| `client/src/pages/diagnostics/components/QueueSection.tsx` | CREATE | Queue display |
| `client/src/pages/diagnostics/components/WorkerCard.tsx` | CREATE | Worker display |
| `client/src/pages/diagnostics/components/ProcessCard.tsx` | CREATE | Process display |
| `client/src/pages/diagnostics/components/ExecutionRow.tsx` | CREATE | Execution row |
| `client/src/pages/diagnostics/components/BlacklistTab.tsx` | CREATE | Blacklist management |
| `client/src/pages/diagnostics/hooks/useWorkerWebSocket.ts` | CREATE | WebSocket hook |
| `client/src/services/workers.ts` | CREATE | API client for workers |
| `client/src/App.tsx` | MODIFY | Update routes |
| Navigation component | MODIFY | Rename Logs to Diagnostics |

## Code Structure

### Workers Tab

```tsx
// WorkersTab.tsx

import { useWorkerWebSocket } from '../hooks/useWorkerWebSocket';
import { QueueSection } from './QueueSection';
import { WorkerCard } from './WorkerCard';

export function WorkersTab() {
  const { workers, queue, isConnected } = useWorkerWebSocket();

  return (
    <div className="space-y-6">
      {/* Connection status */}
      {!isConnected && (
        <Alert variant="warning">
          Connecting to worker updates...
        </Alert>
      )}

      {/* Queue Section */}
      <QueueSection items={queue} />

      {/* Workers */}
      <div className="space-y-4">
        {workers.length === 0 ? (
          <EmptyState message="No workers connected" />
        ) : (
          workers.map(worker => (
            <WorkerCard key={worker.worker_id} worker={worker} />
          ))
        )}
      </div>
    </div>
  );
}
```

### WebSocket Hook

```tsx
// useWorkerWebSocket.ts

import { useEffect, useState, useCallback } from 'react';
import { useWebPubSub } from '@/hooks/useWebPubSub';

interface Worker {
  worker_id: string;
  started_at: string;
  processes: Process[];
}

interface Process {
  pid: number;
  state: 'active' | 'draining' | 'pending_kill';
  memory_mb: number;
  uptime_seconds: number;
  jobs_processed: number;
  executions: Execution[];
}

interface Execution {
  execution_id: string;
  workflow_name: string;
  status: 'RUNNING' | 'STUCK' | 'COMPLETING';
  elapsed_seconds: number;
}

export function useWorkerWebSocket() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);

  const handleMessage = useCallback((message: any) => {
    switch (message.type) {
      case 'worker_heartbeat':
        // Update worker state
        setWorkers(prev => {
          const idx = prev.findIndex(w => w.worker_id === message.worker_id);
          if (idx >= 0) {
            const updated = [...prev];
            updated[idx] = { ...updated[idx], ...message };
            return updated;
          }
          return [...prev, message];
        });
        // Update queue
        if (message.queue) {
          setQueue(message.queue.items);
        }
        break;

      case 'worker_online':
        // Add new worker
        setWorkers(prev => [...prev, { worker_id: message.worker_id, processes: [] }]);
        break;

      case 'worker_offline':
        // Remove worker
        setWorkers(prev => prev.filter(w => w.worker_id !== message.worker_id));
        break;

      case 'execution_stuck':
        // Flash animation on execution
        // Handled by individual ExecutionRow component
        break;
    }
  }, []);

  const { isConnected } = useWebPubSub({
    group: 'platform_workers',
    onMessage: handleMessage,
  });

  return { workers, queue, isConnected };
}
```

### Queue Section with Animation

```tsx
// QueueSection.tsx

import { AnimatePresence, motion } from 'framer-motion';

export function QueueSection({ items }: { items: QueueItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          QUEUE
          <Badge variant="secondary">{items.length} pending</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-muted-foreground text-sm">No jobs queued</p>
        ) : (
          <AnimatePresence>
            {items.map((item, index) => (
              <motion.div
                key={item.execution_id}
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: 20 }}
                transition={{ duration: 0.2 }}
                className="flex items-center justify-between py-2 border-b last:border-0"
              >
                <div className="flex items-center gap-3">
                  <span className="text-muted-foreground">#{index + 1}</span>
                  <span className="font-medium">{item.workflow_name}</span>
                  <span className="text-sm text-muted-foreground">
                    {item.organization_name}
                  </span>
                </div>
                <span className="text-sm text-muted-foreground">
                  queued {formatRelativeTime(item.queued_at)}
                </span>
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </CardContent>
    </Card>
  );
}
```

### Execution Row with Status

```tsx
// ExecutionRow.tsx

export function ExecutionRow({ execution }: { execution: Execution }) {
  const statusStyles = {
    RUNNING: 'bg-blue-500',
    STUCK: 'bg-red-500 animate-pulse',
    COMPLETING: 'bg-yellow-500',
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      className={cn(
        "flex items-center justify-between py-2 px-3 rounded",
        execution.status === 'STUCK' && "bg-red-50 dark:bg-red-950"
      )}
    >
      <div className="flex items-center gap-2">
        <div className={cn("w-2 h-2 rounded-full", statusStyles[execution.status])} />
        {execution.status === 'STUCK' && <AlertTriangle className="w-4 h-4 text-red-500" />}
        <span className="font-medium">{execution.workflow_name}</span>
      </div>
      <div className="flex items-center gap-4">
        <span className="text-sm text-muted-foreground">
          {execution.status}
        </span>
        <span className="text-sm tabular-nums">
          {formatDuration(execution.elapsed_seconds)}
        </span>
        <Button variant="ghost" size="sm" asChild>
          <Link to={`/executions/${execution.execution_id}`}>View</Link>
        </Button>
      </div>
    </motion.div>
  );
}
```

### Recycle Confirmation

```tsx
// ProcessCard.tsx (recycle button portion)

function RecycleButton({ workerId, pid }: { workerId: string; pid: number }) {
  const [isOpen, setIsOpen] = useState(false);
  const recycleMutation = useMutation({
    mutationFn: () => recycleProcess(workerId, pid),
    onSuccess: () => {
      toast.success('Recycle request sent');
      setIsOpen(false);
    },
    onError: (err) => {
      toast.error('Failed to recycle process');
    },
  });

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setIsOpen(true)}>
        Recycle
      </Button>

      <AlertDialog open={isOpen} onOpenChange={setIsOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Recycle Process?</AlertDialogTitle>
            <AlertDialogDescription>
              This will stop the process from accepting new jobs and wait for
              current jobs to complete. A new process will be spawned to replace it.
              Stuck jobs will be marked as failed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => recycleMutation.mutate()}
              disabled={recycleMutation.isPending}
            >
              {recycleMutation.isPending ? 'Recycling...' : 'Recycle'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
```

## Testing

### Component Tests
- [ ] WorkersTab renders queue and workers
- [ ] QueueSection animates items in/out
- [ ] ProcessCard shows correct state badge
- [ ] ExecutionRow highlights stuck status
- [ ] RecycleButton shows confirmation dialog
- [ ] BlacklistTab displays and manages blacklist

### Integration Tests
- [ ] WebSocket updates reflect in UI
- [ ] Recycle action calls API and updates UI
- [ ] Navigation works between tabs
- [ ] Empty states display correctly
