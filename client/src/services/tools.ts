/**
 * Tools Service
 *
 * Provides access to the unified tools endpoint.
 * Returns both system tools and workflow tools.
 */

import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types for convenience
export type ToolInfo = components["schemas"]["ToolInfo"];
export type ToolsResponse = components["schemas"]["ToolsResponse"];

/**
 * Fetch all available tools
 * @param type - Filter by tool type: 'system' or 'workflow'
 */
export async function getTools(
	type?: "system" | "workflow",
): Promise<ToolsResponse> {
	const { data, error } = await apiClient.GET("/api/tools", {
		params: { query: type ? { type } : {} },
	});

	if (error) {
		throw new Error("Failed to fetch tools");
	}

	return data;
}

/**
 * Fetch system tools only
 */
export async function getSystemTools(): Promise<ToolsResponse> {
	const { data, error } = await apiClient.GET("/api/tools/system");

	if (error) {
		throw new Error("Failed to fetch system tools");
	}

	return data;
}
