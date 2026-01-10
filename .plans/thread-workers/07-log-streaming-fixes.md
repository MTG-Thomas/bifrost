# Phase 7: Log Streaming & UI State Bug Fixes

## Status: INVESTIGATION COMPLETE - Ready for Implementation

## Overview

Investigation of two UI/UX bugs discovered during the process pool implementation work.

---

## Bug 1: Logs Not Showing After Execution Completes

### Symptom
When a job completes, the UI reloads but shows no logs (even though logs streamed correctly during execution).

### Root Cause Analysis

**Current Flow (with timing issues):**

1. **Execution Phase**:
   - Worker executes code and streams logs via `publish_execution_log_sync()` in `pubsub.py`
   - Logs are published to Redis pub/sub channel `execution:{execution_id}`
   - WebSocket service receives logs and appends them to `executionStreamStore`
   - Client displays logs in real-time during execution

2. **Completion Phase** (TIMING ISSUE):
   - Consumer receives result from orchestrator in `workflow_execution.py:_process_execution_result()`
   - **Line 219**: Consumer publishes `publish_execution_update()` with completion status
   - **Lines 248-254**: Consumer calls `flush_logs_to_postgres()` AFTER publishing status
   - **BUG**: No guarantee logs are flushed BEFORE status update is published

3. **UI Refresh Phase** (RACE CONDITION):
   - Client receives execution_update event with complete status
   - `useExecutionStream.ts:113-114` calls `onComplete()` callback
   - `ExecutionDetails.tsx:238-244` invalidates React Query cache to refetch execution
   - Backend endpoint `executions.py:get_execution()` fetches from PostgreSQL
   - **BUG OCCURS**: If `flush_logs_to_postgres()` hasn't completed, API returns empty logs array

### Code Reference

`api/src/jobs/consumers/workflow_execution.py` lines 218-254:
```python
# Line 219: Publish status FIRST
await publish_execution_update(
    execution_id,
    status.value,
    {"result": result_data.get("result"), "durationMs": duration_ms},
)

# ... then other operations ...

# Line 250: Flush logs AFTER status was already published
logs_count = await flush_logs_to_postgres(execution_id)
```

### Proposed Fix

**Reorder operations to flush logs BEFORE publishing completion status:**

```python
# 1. Update database with execution result (keep as is)
await update_execution(...)

# 2. Flush logs FIRST (before any status notifications)
try:
    from bifrost._logging import flush_logs_to_postgres
    logs_count = await flush_logs_to_postgres(execution_id)
except Exception as e:
    logger.warning(f"Failed to flush logs: {e}")

# 3. Flush pending SDK changes
try:
    from bifrost._sync import flush_pending_changes
    changes_count = await flush_pending_changes(execution_id)
except Exception as e:
    logger.warning(f"Failed to flush pending changes: {e}")

# 4. NOW publish status updates (after everything is in DB)
await publish_execution_update(...)
await publish_history_update(...)

# 5. Cleanup and sync result
...
```

### Files to Modify
- `api/src/jobs/consumers/workflow_execution.py` - Reorder flush/publish operations

---

## Bug 2: Stale "Waiting for Execution" Message on Page Refresh

### Symptom
When refreshing a page for a FINISHED workflow execution, you see "Waiting for execution to start... The execution is being prepared. This page will update automatically." for several seconds before showing the actual result.

### Root Cause Analysis

**Current Flow (with state mismatch):**

1. **Direct Navigation to Completed Execution**:
   - User refreshes page or navigates directly to `/executions/{id}` for a FINISHED execution
   - `ExecutionDetails.tsx:115` checks `hasNavigationState` (from React Router location.state)
   - Direct navigation = no `location.state`, so `hasNavigationState = false`

2. **Initial State Setup** (PROBLEMATIC):
   - `ExecutionDetails.tsx:128-136`: Fallback timer logic
   - `ExecutionDetails.tsx:148`: `shouldFetchExecution = hasReceivedUpdate || fetchFallbackEnabled`
   - Initial state: `hasReceivedUpdate = false`, `fetchFallbackEnabled = false`
   - **So initially, fetch is NOT enabled!**

3. **The Problem**:
   - `shouldFetchExecution` starts as `false` because both conditions are false initially
   - The effect that sets `fetchFallbackEnabled = true` runs after first render
   - This creates a one-render delay where the query is disabled
   - UI shows loading/waiting state during this delay

### Code Reference

`client/src/pages/ExecutionDetails.tsx` lines 125-175:
```typescript
// Line 115: No nav state = direct link/refresh
const hasNavigationState = location.state != null;

// Line 128-136: Fallback timer logic
const [fetchFallbackEnabled, setFetchFallbackEnabled] = useState(false);
useEffect(() => {
    setFetchFallbackEnabled(false); // Reset on ID change
    if (!hasNavigationState) {
        setFetchFallbackEnabled(true); // Enable immediately for direct links
        return;
    }
    // Otherwise, wait 5s...
}, [executionId, hasNavigationState]);

// Line 148: Only fetch if fallback enabled OR stream received update
const shouldFetchExecution = hasReceivedUpdate || fetchFallbackEnabled;

// Line 175: Query is disabled when shouldFetchExecution is false!
useExecution(
    shouldFetchExecution ? executionId : undefined,
    signalrEnabled
);
```

### Proposed Fix

**Enable fetch immediately for direct navigation:**

```typescript
// Direct navigation (refresh/direct link) should fetch immediately
const shouldFetchExecution =
    hasReceivedUpdate ||              // Stream gave us updates
    fetchFallbackEnabled ||           // Fallback timer expired
    !hasNavigationState;              // Direct link/refresh - fetch NOW!

// Set up fallback timer only if we have navigation state
useEffect(() => {
    if (!hasNavigationState) {
        // Direct link - fetch immediately via shouldFetchExecution
        return;
    }
    // Only set fallback timer for navigation state (fresh execution)
    const timer = setTimeout(() => setFetchFallbackEnabled(true), 5000);
    return () => clearTimeout(timer);
}, [executionId, hasNavigationState]);
```

### Files to Modify
- `client/src/pages/ExecutionDetails.tsx` - Fix `shouldFetchExecution` logic

---

## Implementation Plan

### Step 1: Fix Backend Log Flush Ordering
1. Read `workflow_execution.py` and understand current flow
2. Move `flush_logs_to_postgres()` call BEFORE `publish_execution_update()`
3. Move `flush_pending_changes()` call BEFORE status publish
4. Keep error handling for flush operations (non-blocking)

### Step 2: Fix Frontend Fetch Enablement
1. Update `shouldFetchExecution` to include `!hasNavigationState`
2. Simplify the fallback timer logic (only needed for fresh executions)
3. Test direct navigation shows result immediately

### Step 3: Testing
1. Execute a workflow with verbose logging
2. Verify logs appear on completion refresh
3. Complete a workflow, then refresh page
4. Verify "Waiting" message does NOT appear for completed executions

---

## Related Files

### Backend
- `api/src/jobs/consumers/workflow_execution.py` - Consumer result handling
- `api/src/core/pubsub.py` - WebSocket/event publishing
- `api/src/repositories/executions.py` - Log retrieval from PostgreSQL

### Frontend
- `client/src/pages/ExecutionDetails.tsx` - Main execution page
- `client/src/hooks/useExecutionStream.ts` - WebSocket stream handling
- `client/src/stores/executionStreamStore.ts` - Execution state store

---

## Additional Improvements to Consider

### For Bug 1:
- Add a `logsFlushComplete` message type in pubsub that signals logs are ready
- Client could wait for this signal before refetching (more explicit coordination)
- Include a `logsCount` in the completion update so client knows if logs were flushed

### For Bug 2:
- Store the initial page load time and cache the execution status
- If the cached status is already complete, skip WebSocket setup entirely
- Add a check in `useExecution()` to not use fallback polling for completed executions
