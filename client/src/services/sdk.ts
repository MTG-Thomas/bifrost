/**
 * SDK Service
 *
 * API methods for developer context management.
 * Enables local SDK development workflow.
 *
 * Note: These endpoints are not in the OpenAPI spec, so we use authFetch
 * instead of the typed apiClient.
 */

import { authFetch } from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export interface DeveloperContext {
	user: {
		id: string;
		email: string;
		name: string;
	};
	organization: {
		id: string;
		name: string;
	} | null;
	default_parameters: Record<string, unknown>;
	track_executions: boolean;
}

export interface UpdateContextRequest {
	default_org_id?: string | null;
	default_parameters?: Record<string, unknown>;
	track_executions?: boolean;
}

// =============================================================================
// Developer Context
// =============================================================================

export async function getContext(): Promise<DeveloperContext> {
	const response = await authFetch("/api/cli/context");
	if (!response.ok) {
		throw new Error(`Failed to get context: ${response.statusText}`);
	}
	return response.json();
}

export async function updateContext(
	data: UpdateContextRequest,
): Promise<DeveloperContext> {
	const response = await authFetch("/api/cli/context", {
		method: "PUT",
		body: JSON.stringify(data),
	});
	if (!response.ok) {
		throw new Error(`Failed to update context: ${response.statusText}`);
	}
	return response.json();
}

// =============================================================================
// SDK Download
// =============================================================================

export function getSdkDownloadUrl(): string {
	return "/api/cli/download";
}

// =============================================================================
// Service Export
// =============================================================================

export const sdkService = {
	getContext,
	updateContext,
	getSdkDownloadUrl,
};
