/**
 * Usage Reports API service using openapi-react-query pattern
 *
 * Provides hooks for fetching AI usage and resource consumption data.
 * Organization filtering is handled via the X-Organization-Id header, which is
 * automatically injected by the API client based on the org switcher selection.
 */

import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types for convenience
export type UsageReportResponse = components["schemas"]["UsageReportResponse"];
export type UsageReportSummary = components["schemas"]["UsageReportSummary"];
export type UsageTrend = components["schemas"]["UsageTrend"];
export type WorkflowUsage = components["schemas"]["WorkflowUsage"];
export type ConversationUsage = components["schemas"]["ConversationUsage"];
export type OrganizationUsage = components["schemas"]["OrganizationUsage"];

export type UsageSource = "executions" | "chat" | "all";

/**
 * Hook to fetch usage report for a date range.
 * Organization filtering is controlled by the org switcher (X-Organization-Id header).
 *
 * @param startDate - Start date in YYYY-MM-DD format
 * @param endDate - End date in YYYY-MM-DD format
 * @param source - Filter by source: executions, chat, or all
 */
export function useUsageReport(
	startDate: string,
	endDate: string,
	source: UsageSource = "all",
) {
	return $api.useQuery(
		"get",
		"/api/reports/usage",
		{
			params: {
				query: {
					start_date: startDate,
					end_date: endDate,
					source,
				},
			},
		},
		{
			enabled: !!startDate && !!endDate,
		},
	);
}
