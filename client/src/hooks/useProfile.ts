/**
 * React Query hook for user profile
 * Uses caching to avoid duplicate API calls across navigations
 */

import { $api } from "@/lib/api-client";

/**
 * Fetch current user's profile with caching.
 * Profile data (avatar, name) rarely changes, so we cache for 5 minutes.
 */
export function useProfile() {
	return $api.useQuery(
		"get",
		"/api/profile",
		{},
		{
			staleTime: 5 * 60 * 1000, // 5 minutes - profile rarely changes
		},
	);
}
