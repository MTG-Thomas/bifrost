---
planStatus:
  planId: plan_1769109703986_jd6qe
  title: ""
  status: draft
  planType: feature
  priority: medium
  progress: 0
  owner: ""
  stakeholders: []
  tags: []
  created: "2026-01-22"
  updated: "2026-01-22T19:21:43.986Z"
---
# Execution Details Loading Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify execution details page loading to always fetch immediately and merge API logs with streaming logs by sequence number.

**Architecture:** Remove navigation-state-based deferral logic. Fetch execution data immediately on mount, subscribe to WebSocket in parallel, close WebSocket when execution is complete. Deduplicate logs using sequence numbers.

**Tech Stack:** React, TypeScript, Zustand, React Query, WebSocket

---

## Background

The current execution details page has a confusing loading flow:

1. When navigating from an execution trigger, the page shows "Waiting for execution to start" for up to 5 seconds
2. This happens even for **completed** executions clicked from the history list
3. Logs don't load during refresh while in this waiting state
4. The logic depends on `location.state` to determine navigation source, which is fragile

The root cause: the page tries to avoid a race condition (execution in Redis but not PostgreSQL yet) by deferring API fetches until WebSocket confirms the execution exists. But this creates a worse UX for the common case of viewing existing executions.

## Solution

Simplify to a parallel-fetch model that doesn't care how you navigated to the page:

```
Page Mount
    │
    ├──────────────────────────┐
    ▼                          ▼
useExecution(executionId)    Subscribe to WebSocket
    │                          │
    ▼                          ▼
Show "Loading execution..."  WebSocket connects
    │                          │
    ▼                          │
API returns execution         │
    │                          │
    ▼                          │
Render page with all data     │
    │                          │
    ▼                          ▼
Is status complete? ──Yes──► Close WebSocket
    │
    No
    │
    ▼
Keep WebSocket open, merge incoming logs by sequence
```

---

### Task 1: Add id and sequence to ExecutionLogPublic API model

**Files:**
- Modify: `api/src/models/contracts/executions.py:20-25`
- Modify: `api/src/routers/executions.py:176-184` and `api/src/routers/executions.py:320-328`

**Step 1: Add id and sequence fields to ExecutionLogPublic**

In `api/src/models/contracts/executions.py`, add both fields to the model:

```python
class ExecutionLogPublic(BaseModel):
    """Single log entry from workflow execution (API response model)"""
    id: int  # Unique log ID for exact deduplication
    timestamp: str
    level: str  # debug, info, warning, error
    message: str
    data: dict[str, Any] | None = None
    sequence: int  # For ordering and range-based deduplication
```

**Step 2: Include id and sequence when serializing logs**

In `api/src/routers/executions.py`, update both places where logs are serialized (around lines 176-184 and 320-328):

```python
logs = [
    ExecutionLogPublic(
        id=log.id,
        timestamp=log.timestamp.isoformat() if log.timestamp else "",
        level=log.level or "info",
        message=log.message or "",
        data=log.log_metadata,
        sequence=log.sequence,
    )
    for log in log_entries
]
```

**Step 3: Run type checking**

Run: `cd api && pyright`
Expected: PASS

**Step 4: Commit**

```bash
git add api/src/models/contracts/executions.py api/src/routers/executions.py
git commit -m "feat(api): add id and sequence to ExecutionLogPublic for log deduplication"
```

---

### Task 2: Regenerate TypeScript types

**Files:**
- Regenerate: `client/src/lib/v1.d.ts`

**Step 1: Regenerate types from API**

Run: `cd client && npm run generate:types`
Expected: Types regenerated with sequence field on ExecutionLogPublic

**Step 2: Verify sequence field exists**

Run: `grep -A6 "ExecutionLogPublic" client/src/lib/v1.d.ts`
Expected: Should show `id: number` and `sequence: number` in the type

**Step 3: Commit**

```bash
git add client/src/lib/v1.d.ts
git commit -m "chore(client): regenerate types with log id and sequence fields"
```

---

### Task 3: Update StreamingLog type in executionStreamStore

**Files:**
- Modify: `client/src/stores/executionStreamStore.ts:4-9`

**Step 1: Add id to StreamingLog interface**

```typescript
export interface StreamingLog {
	id?: number;  // Unique log ID for deduplication with API logs
	level: string;
	message: string;
	timestamp: string;
	sequence?: number; // Sequence number for client-side reordering
}
```

**Step 2: Run type checking**

Run: `cd client && npm run tsc`
Expected: PASS

**Step 3: Commit**

```bash
git add client/src/stores/executionStreamStore.ts
git commit -m "feat(client): add id to StreamingLog type for deduplication"
```

---

### Task 4: Update ExecutionLogEntry type in ExecutionDetails

**Files:**
- Modify: `client/src/pages/ExecutionDetails.tsx:87-93`

**Step 1: Add id and sequence to ExecutionLogEntry interface**

```typescript
// Type for execution log entry
interface ExecutionLogEntry {
	id?: number;  // Unique log ID for exact deduplication
	level?: string;
	message?: string;
	timestamp?: string;
	data?: Record<string, unknown>;
	sequence?: number;  // For ordering and range-based deduplication
}
```

**Step 2: Run type checking**

Run: `cd client && npm run tsc`
Expected: PASS

**Step 3: Commit**

```bash
git add client/src/pages/ExecutionDetails.tsx
git commit -m "feat(client): add id and sequence to ExecutionLogEntry type"
```

---

### Task 5: Remove navigation state deferral logic

**Files:**
- Modify: `client/src/pages/ExecutionDetails.tsx:113-152`

**Step 1: Remove hasNavigationState and fallback timer**

Remove or simplify the following code block (lines 113-152). Replace with:

```typescript
// Removed: hasNavigationState, fetchFallbackEnabled, shouldFetchExecution logic
// We now always fetch immediately and let React Query handle retries

// Get streaming logs from store
// Use stable selector to avoid infinite loops
const streamState = useExecutionStreamStore((state) =>
	executionId ? state.streams[executionId] : undefined,
);
const streamingLogs = streamState?.streamingLogs ?? [];
```

**Step 2: Update useExecution call to always fetch**

Change line 179-182 from:
```typescript
} = useExecution(
	shouldFetchExecution ? executionId : undefined,
	signalrEnabled,
);
```

To:
```typescript
} = useExecution(executionId, signalrEnabled);
```

**Step 3: Remove useLocation import if no longer needed**

Check if `location` is used elsewhere. If only for `hasNavigationState`, remove the `useLocation` hook usage.

**Step 4: Run type checking**

Run: `cd client && npm run tsc`
Expected: PASS (may have unused variable warnings to fix)

**Step 5: Commit**

```bash
git add client/src/pages/ExecutionDetails.tsx
git commit -m "refactor(client): remove navigation state deferral, always fetch immediately"
```

---

### Task 6: Simplify WebSocket enable logic

**Files:**
- Modify: `client/src/pages/ExecutionDetails.tsx:210-230`

**Step 1: Replace WebSocket enable logic**

The current logic checks `hasNavigationState` to decide when to enable WebSocket. Change to:

```typescript
// Enable WebSocket streaming for non-complete executions
// Start immediately - if execution is complete, we'll close after first status check
const [signalrEnabled, setSignalrEnabled] = useState(true);

// Disable streaming when execution is complete (from API or stream)
useEffect(() => {
	if (isComplete || streamState?.isComplete) {
		setSignalrEnabled(false);
	}
}, [isComplete, streamState?.isComplete]);
```

Remove the old effect that checks `hasNavigationState` and `executionStatus` to set `signalrEnabled`.

**Step 2: Run type checking**

Run: `cd client && npm run tsc`
Expected: PASS

**Step 3: Commit**

```bash
git add client/src/pages/ExecutionDetails.tsx
git commit -m "refactor(client): simplify WebSocket enable to close on complete"
```

---

### Task 6: Remove "Waiting for execution to start" UI

**Files:**
- Modify: `client/src/pages/ExecutionDetails.tsx:573-611`

**Step 1: Remove the waiting state block**

Delete the entire block that shows "Waiting for execution to start..." (lines 573-611). This condition `if (!execution && !error && hasNavigationState)` is no longer valid.

**Step 2: Update loading state message**

The existing loading state (lines 562-571) shows "Loading execution details..." - this is correct and should remain.

**Step 3: Run type checking**

Run: `cd client && npm run tsc`
Expected: PASS

**Step 4: Commit**

```bash
git add client/src/pages/ExecutionDetails.tsx
git commit -m "refactor(client): remove 'Waiting for execution' state, use loading state"
```

---

### Task 7: Implement log deduplication by ID

**Files:**
- Modify: `client/src/pages/ExecutionDetails.tsx:945-970`

**Step 1: Create deduplication helper**

Add a helper function near the top of the component (after type definitions):

```typescript
// Deduplicate logs by ID
// API logs are the baseline, streaming logs are filtered to exclude any with IDs already in API logs
function mergeLogsWithDedup(
	apiLogs: ExecutionLogEntry[],
	streamingLogs: StreamingLog[]
): ExecutionLogEntry[] {
	if (streamingLogs.length === 0) return apiLogs;
	if (apiLogs.length === 0) return streamingLogs as ExecutionLogEntry[];

	// Build Set of API log IDs for O(1) lookup
	const apiLogIds = new Set(
		apiLogs.map((log) => log.id).filter((id): id is number => id !== undefined)
	);

	// Filter streaming logs to only those not already in API response
	const newStreamingLogs = streamingLogs.filter(
		(log) => log.id === undefined || !apiLogIds.has(log.id)
	);

	return [...apiLogs, ...newStreamingLogs];
}
```

**Step 2: Update log rendering to use deduplication**

In the logs display section (around line 952-959), change:

```typescript
// Combine API logs with real-time streaming logs
const existingLogs = (logsData as ExecutionLogEntry[]) || [];
const logsToDisplay = [...existingLogs, ...streamingLogs];
```

To:

```typescript
// Merge API logs with streaming logs, deduplicating by sequence
const existingLogs = (logsData as ExecutionLogEntry[]) || [];
const logsToDisplay = mergeLogsWithDedup(existingLogs, streamingLogs);
```

**Step 3: Run type checking**

Run: `cd client && npm run tsc`
Expected: PASS

**Step 4: Commit**

```bash
git add client/src/pages/ExecutionDetails.tsx
git commit -m "feat(client): deduplicate logs by ID when merging API and stream"
```

---

### Task 8: Clean up unused imports and variables

**Files:**
- Modify: `client/src/pages/ExecutionDetails.tsx`

**Step 1: Remove unused imports**

After all changes, check for unused imports. Likely to remove:
- `useLocation` from react-router-dom (if no longer used)

**Step 2: Run linting**

Run: `cd client && npm run lint`
Expected: PASS (fix any remaining issues)

**Step 3: Run type checking**

Run: `cd client && npm run tsc`
Expected: PASS

**Step 4: Commit**

```bash
git add client/src/pages/ExecutionDetails.tsx
git commit -m "chore(client): clean up unused imports after loading redesign"
```

---

### Task 9: Update handleRerunExecution navigation

**Files:**
- Modify: `client/src/pages/ExecutionDetails.tsx:476-487`

**Step 1: Simplify rerun navigation**

The rerun handler passes `location.state` to avoid the 404 race condition. Since we now handle this with React Query retries, simplify:

```typescript
// Navigate to the new execution
if (result?.execution_id) {
	navigate(`/history/${result.execution_id}`);
}
```

Remove the `state` object since it's no longer needed for the deferral logic.

**Step 2: Run type checking**

Run: `cd client && npm run tsc`
Expected: PASS

**Step 3: Commit**

```bash
git add client/src/pages/ExecutionDetails.tsx
git commit -m "refactor(client): simplify rerun navigation, remove state passing"
```

---

### Task 10: Manual testing

**Step 1: Test completed execution via direct link**

1. Open a completed execution by pasting URL directly
2. Expected: Shows "Loading execution details..." briefly, then renders immediately
3. Expected: No "Waiting for execution to start" message

**Step 2: Test completed execution from history list**

1. Navigate to history, click a completed execution
2. Expected: Same as above - immediate load

**Step 3: Test running execution**

1. Trigger a workflow, navigate to execution
2. Expected: Shows "Loading execution..." then renders with live logs
3. Expected: WebSocket stays connected, logs stream in
4. Expected: When complete, WebSocket closes

**Step 4: Test page refresh on running execution**

1. While execution is running, refresh the page
2. Expected: API logs load immediately
3. Expected: New logs stream in without duplicates

**Step 5: Verify no log duplication**

1. Watch logs during a running execution
2. Refresh page mid-execution
3. Expected: No duplicate log entries appear

---

## Testing Scenarios Summary

1. **Direct link to completed execution** - Should load immediately, no WebSocket connection lingers
2. **Direct link to running execution** - Should load immediately, WebSocket stays open, logs stream in
3. **Navigate from history to completed** - Same as #1
4. **Navigate from "Run" button** - Should show "Loading execution...", then render when API returns
5. **Refresh while execution is running** - Should load all existing logs, then stream new ones
6. **Execution completes while viewing** - WebSocket should close, final state from API
