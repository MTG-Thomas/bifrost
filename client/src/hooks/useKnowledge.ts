/**
 * Hook for knowledge namespace management
 *
 * Used for selecting knowledge sources in agent configuration.
 */

import { useQuery } from "@tanstack/react-query";

export interface KnowledgeNamespaceInfo {
	namespace: string;
	scopes: {
		global: number;
		org: number;
		total: number;
	};
}

/**
 * Fetch knowledge namespaces from the CLI API
 */
async function fetchKnowledgeNamespaces(): Promise<KnowledgeNamespaceInfo[]> {
	const response = await fetch("/api/cli/knowledge/namespaces", {
		method: "GET",
		headers: {
			"Content-Type": "application/json",
		},
		credentials: "include",
	});

	if (!response.ok) {
		if (response.status === 404) {
			// No knowledge namespaces exist yet
			return [];
		}
		throw new Error(`Failed to fetch namespaces: ${response.status}`);
	}

	return response.json();
}

/**
 * Hook to fetch available knowledge namespaces
 *
 * Returns list of namespace info with document counts.
 * Used in AgentDialog for selecting knowledge sources.
 */
export function useKnowledgeNamespaces() {
	return useQuery({
		queryKey: ["knowledge", "namespaces"],
		queryFn: fetchKnowledgeNamespaces,
		staleTime: 60 * 1000, // Cache for 1 minute
		retry: false,
	});
}
