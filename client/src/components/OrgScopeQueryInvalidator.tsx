import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useScopeStore } from "@/stores/scopeStore";

/**
 * Invalidates all React Query caches when organization scope changes.
 * This ensures API data is refetched with the new X-Organization-Id header.
 *
 * The API client middleware injects X-Organization-Id based on sessionStorage,
 * but React Query caches don't include org ID in their keys. This component
 * bridges that gap by invalidating all queries when the org changes.
 */
export function OrgScopeQueryInvalidator() {
	const queryClient = useQueryClient();
	const orgId = useScopeStore((s) => s.scope.orgId);
	const isFirstMount = useRef(true);

	useEffect(() => {
		// Skip initial mount - don't invalidate on first render
		if (isFirstMount.current) {
			isFirstMount.current = false;
			return;
		}

		// Invalidate all queries when org changes
		queryClient.invalidateQueries();
	}, [orgId, queryClient]);

	return null;
}
