/**
 * `useWorkflow` — the v2 SDK's workflow-execution hook.
 *
 * A standalone_v2 app calls `import { useWorkflow } from "bifrost"` and runs a
 * workflow through the authed transport its `<BifrostProvider>` established —
 * NOT the v1 `globalThis.__bifrost_platform` path (which reaches into platform
 * stores that a standalone app doesn't have). This mirrors how `useTable` reads
 * the provider context: auth/baseUrl/org come from `useBifrostContext()`, so the
 * same code runs in `npm run dev` (cross-origin, bearer token) and deployed.
 *
 * Two shapes, matching the v1 surface:
 *   - `useWorkflow(workflowRef)` → a query-style result you trigger with `run()`.
 *   - `run(input)` POSTs `/api/workflows/execute` (no `sync` flag — the server
 *     always starts the run asynchronously and returns `{ execution_id, status }`
 *     immediately).
 *
 * Streaming transport: after starting the run, `run()` subscribes to the
 * execution over the SDK websocket (`subscribeToExecution`) for live `status`
 * and `log` events. Terminal websocket frames do NOT carry the result (see
 * execution-stream.ts) — on `isTerminal`, `run()` unsubscribes and fetches
 * `GET /api/executions/{id}` once to get the actual `result`/`error_message`.
 * A fast run can finish before the socket even opens, so `run()` also does one
 * immediate `GET` right after subscribing. If the socket goes down
 * (`onSocketDown`), `run()` degrades to polling that same GET every 2s until
 * terminal — no client-side deadline; the server enforces the workflow's own
 * timeout and will eventually flip the execution terminal.
 *
 * Two exceptions bypass the stream entirely and settle inline from the POST
 * response: `is_transient` runs (data-provider-style executions) and runs
 * that are already terminal by the time the POST responds.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { components } from "@/lib/v1";

import { subscribeToExecution, type ExecutionStreamEvent } from "./execution-stream";
import { useBifrostContext } from "./provider";

export interface WorkflowLogEntry {
  level: string;
  message: string;
  timestamp: string;
  sequence?: number;
}

export interface UseWorkflowState<T> {
  /** Last successful result, or null before the first run. */
  data: T | null;
  /** True while a run is in flight. */
  loading: boolean;
  /** Last error, or null. */
  error: Error | null;
  /** Execute the workflow with `input_data`; resolves to the result. */
  run: (input?: Record<string, unknown>) => Promise<T>;
  /** Live log stream for the latest run. Reset to `[]` at the start of each run. */
  logs: WorkflowLogEntry[];
  /** Latest run's status (e.g. "Pending", "Running", "Success"), or null before the first run. */
  status: string | null;
  /** Latest run's execution id, or null before the first run. */
  executionId: string | null;
}

// The generated contract type: `status` is the PascalCase `ExecutionStatus`
// literal union ("Success" | "Failed" | ...), so the compiler enforces the
// exact wire casing in the failed-status check below.
type ExecuteResponse = components["schemas"]["WorkflowExecutionResponse"];

const TERMINAL_STATUSES = new Set([
  "Success",
  "Failed",
  "CompletedWithErrors",
  "Timeout",
  "Cancelled",
]);

const POLL_INTERVAL_MS = 2000;

class ExecutionFetchError extends Error {
  readonly retryable: boolean;

  constructor(status: number, statusText: string) {
    super(`failed to fetch execution: ${status}${statusText ? ` ${statusText}` : ""}`);
    this.name = "ExecutionFetchError";
    // A just-created execution can briefly 404, and transient overload/server
    // failures can recover. Other 4xx responses (notably an expired/forbidden
    // token) will not improve by polling forever.
    this.retryable = status === 404 || status === 408 || status === 429 || status >= 500;
  }
}

interface ExecutionSnapshot {
  status?: string;
  result?: unknown;
  error_message?: string | null;
}

interface ExecutionWait<T> {
  promise: Promise<T>;
  cancel: () => void;
}

function waitForExecution<T>(
  executionId: string,
  fetchExecution: (id: string) => Promise<ExecutionSnapshot>,
  settleFromExecution: (execution: ExecutionSnapshot) => T,
  onStatus: (status: string) => void,
  onLog: (log: WorkflowLogEntry) => void,
): ExecutionWait<T> {
  let settled = false;
  let sawTerminal = false;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let unsubscribe: () => void = () => {};
  let resolvePromise!: (value: T) => void;
  let rejectPromise!: (reason: unknown) => void;

  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });

  const cleanup = () => {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    unsubscribe();
  };
  const resolve = (value: T) => {
    if (settled) return;
    settled = true;
    cleanup();
    resolvePromise(value);
  };
  const reject = (reason: unknown) => {
    if (settled) return;
    settled = true;
    cleanup();
    rejectPromise(reason);
  };
  const settleExecution = (execution: ExecutionSnapshot) => {
    try {
      resolve(settleFromExecution(execution));
    } catch (error) {
      reject(error);
    }
  };
  const startPolling = () => {
    if (!settled && !pollTimer) pollTimer = setInterval(checkOnce, POLL_INTERVAL_MS);
  };
  const checkOnce = async () => {
    if (settled) return;
    try {
      const execution = await fetchExecution(executionId);
      if (settled) return;
      if (execution.status) onStatus(execution.status);
      if (execution.status && TERMINAL_STATUSES.has(execution.status)) {
        settleExecution(execution);
      }
    } catch (fetchError) {
      if (settled) return;
      if (fetchError instanceof ExecutionFetchError && !fetchError.retryable) {
        reject(fetchError);
        return;
      }
      if (sawTerminal) startPolling();
    }
  };
  const handleEvent = (event: ExecutionStreamEvent) => {
    if (settled) return;
    if (event.type === "ready") void checkOnce();
    if (event.type === "log" && event.log) onLog(event.log);
    if (event.type === "status") {
      if (event.status) onStatus(event.status);
      if (event.isTerminal) {
        sawTerminal = true;
        void checkOnce();
      }
    }
  };

  unsubscribe = subscribeToExecution(executionId, handleEvent, startPolling);
  void checkOnce();

  return {
    promise,
    cancel: () => reject(new Error("Workflow run cancelled because its component unmounted")),
  };
}

/**
 * Run a Bifrost workflow by UUID, portable `path::function` ref, or workflow
 * name from a v2 app. All three resolve identically: the server scopes the
 * ref to THIS install (via the X-Bifrost-App transport), so a name or path
 * ref reaches the install's own workflow first. Prefer `path::function` —
 * it's the ref shape that also runs locally under `bifrost solution start`
 * (name/UUID refs proxy to the deployed copy there). Must be called within a
 * `<BifrostProvider>` (throws otherwise — same contract as `useBifrostContext`).
 */
export function useWorkflow<T = unknown>(workflowRef: string): UseWorkflowState<T> {
  const { authedFetch, appId } = useBifrostContext();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [logs, setLogs] = useState<WorkflowLogEntry[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null);
  // Monotonic run counter: overlapping runs each capture their own seq, and
  // only the LATEST run (seq === seqRef.current) may write hook state. A slow
  // stale run can't overwrite a newer run's data or flip `loading` while the
  // newer run is still in flight. Each caller's promise still settles with its
  // own result/rejection.
  const seqRef = useRef(0);
  const mountedRef = useRef(false);
  const activeCancelsRef = useRef(new Set<() => void>());

  useEffect(() => {
    mountedRef.current = true;
    const activeCancels = activeCancelsRef.current;
    return () => {
      mountedRef.current = false;
      seqRef.current += 1;
      for (const cancel of activeCancels) cancel();
      activeCancels.clear();
    };
  }, []);

  const run = useCallback(
    async (input: Record<string, unknown> = {}): Promise<T> => {
      const seq = ++seqRef.current;
      setLoading(true);
      setError(null);
      setLogs([]);
      setStatus("Pending");
      setExecutionId(null);

      const settleFromExecution = (exec: ExecutionSnapshot): T => {
        if (exec.status === "Success" || exec.status === "CompletedWithErrors") {
          return exec.result as T;
        }
        throw new Error(
          exec.error_message ?? `Workflow failed (status: ${exec.status ?? "unknown"})`,
        );
      };
      const fetchExecution = async (id: string) => {
        const r = await authedFetch(`/api/executions/${id}`);
        if (!r.ok) throw new ExecutionFetchError(r.status, r.statusText);
        return (await r.json()) as ExecutionSnapshot;
      };

      try {
        const resp = await authedFetch("/api/workflows/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workflow_id: workflowRef,
            input_data: input,
            // Scope a path::function ref to THIS install's own workflow (so it
            // can't resolve a sibling install's workflow sharing the path).
            ...(appId ? { app_id: appId } : {}),
          }),
        });
        if (!resp.ok) {
          throw new Error(`workflow execution failed: ${resp.status} ${resp.statusText}`);
        }
        const body = (await resp.json()) as ExecuteResponse & { is_transient?: boolean };
        if (!mountedRef.current) {
          throw new Error("Workflow run cancelled because its component unmounted");
        }
        const execId = body.execution_id;
        if (seq === seqRef.current && execId) setExecutionId(execId);

        // Transient (data-provider-style) and already-terminal responses
        // settle inline — never touch the websocket.
        if (body.is_transient || TERMINAL_STATUSES.has(body.status)) {
          if (seq === seqRef.current) setStatus(body.status);
          const result = settleFromExecution({
            status: body.status,
            result: body.result,
            error_message: body.error,
          });
          if (seq === seqRef.current) setData(result);
          return result;
        }

        const executionWait = waitForExecution(
          execId,
          fetchExecution,
          settleFromExecution,
          (nextStatus) => {
            if (mountedRef.current && seq === seqRef.current) setStatus(nextStatus);
          },
          (log) => {
            if (mountedRef.current && seq === seqRef.current) {
              setLogs((previousLogs) => [...previousLogs, log]);
            }
          },
        );
        activeCancelsRef.current.add(executionWait.cancel);
        let result: T;
        try {
          result = await executionWait.promise;
        } finally {
          activeCancelsRef.current.delete(executionWait.cancel);
        }

        if (mountedRef.current && seq === seqRef.current) setData(result);
        return result;
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        if (mountedRef.current && seq === seqRef.current) setError(err);
        throw err;
      } finally {
        if (mountedRef.current && seq === seqRef.current) setLoading(false);
      }
    },
    [authedFetch, workflowRef, appId],
  );

  return { data, loading, error, run, logs, status, executionId };
}
