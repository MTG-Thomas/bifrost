/**
 * React Query client configuration
 */

import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
	defaultOptions: {
		queries: {
			// Don't refetch on window focus by default
			refetchOnWindowFocus: false,
			// Disable retries for all queries
			retry: false,
			// Consider data fresh for 30 seconds to prevent excessive refetching
			staleTime: 30000,
			// Only refetch if data is stale (not on every mount)
			refetchOnMount: true,
		},
		mutations: {
			// IMPORTANT: Disable retries for ALL mutations globally
			// Mutations are typically NOT idempotent (create, update, delete, execute operations)
			// Retrying failed mutations can cause:
			// - Duplicate workflow executions
			// - Duplicate records created
			// - Unintended side effects
			// If a specific mutation needs retries, it should opt-in explicitly
			retry: false,
		},
	},
});
