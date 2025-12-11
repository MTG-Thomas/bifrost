import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types for convenience
export type SystemLog = components["schemas"]["SystemLog"];
export type SystemLogsListResponse =
	components["schemas"]["SystemLogsListResponse"];

export interface GetSystemLogsParams {
	category?: string;
	level?: string;
	startDate?: string;
	endDate?: string;
	limit?: number;
	continuationToken?: string;
}

export function useSystemLogs(params: GetSystemLogsParams = {}) {
	return $api.useQuery("get", "/api/logs", {
		params: {
			query: params as Record<string, string | number | undefined>,
		},
	});
}
