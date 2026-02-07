/**
 * Platform hook: useWorkflowQuery
 *
 * Declarative data fetching hook. Executes automatically on mount,
 * re-executes when params change. Thin wrapper around useWorkflowMutation.
 */

import { useEffect, useRef } from "react";
import {
	useWorkflowMutation,
	type UseWorkflowMutationResult,
} from "./useWorkflowMutation";
import type { ExecutionStatus, StreamingLog } from "@/stores/executionStreamStore";

export interface UseWorkflowQueryOptions {
	enabled?: boolean;
}

export interface UseWorkflowQueryResult<T> {
	data: T | null;
	isLoading: boolean;
	isError: boolean;
	error: string | null;
	logs: StreamingLog[];
	refetch: () => Promise<T>;
	executionId: string | null;
	status: ExecutionStatus | null;
}

export function useWorkflowQuery<T = unknown>(
	workflowId: string,
	params?: Record<string, unknown>,
	options?: UseWorkflowQueryOptions,
): UseWorkflowQueryResult<T> {
	const { enabled = true } = options ?? {};
	const mutation: UseWorkflowMutationResult<T> =
		useWorkflowMutation<T>(workflowId);
	const paramsKey = JSON.stringify(params ?? {});
	const hasExecutedRef = useRef(false);
	const paramsKeyRef = useRef(paramsKey);

	useEffect(() => {
		if (!enabled) {
			hasExecutedRef.current = false;
			return;
		}

		// Execute on mount or when params change
		if (!hasExecutedRef.current || paramsKeyRef.current !== paramsKey) {
			hasExecutedRef.current = true;
			paramsKeyRef.current = paramsKey;
			mutation.execute(params).catch(() => {
				// errors surface via reactive state
			});
		}
	}, [enabled, paramsKey]); // eslint-disable-line react-hooks/exhaustive-deps

	return {
		data: mutation.data,
		isLoading: mutation.isLoading,
		isError: mutation.isError,
		error: mutation.error,
		logs: mutation.logs,
		refetch: () => mutation.execute(params),
		executionId: mutation.executionId,
		status: mutation.status,
	};
}
