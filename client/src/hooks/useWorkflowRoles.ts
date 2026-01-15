/**
 * React Query hooks for workflow role management
 * Uses openapi-react-query for type-safe API calls
 *
 * These hooks manage the roles assigned to workflows, enabling role-based
 * access control for workflow execution.
 */

import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

/**
 * Response type for workflow roles endpoint
 * Note: Will use generated types once API types are regenerated
 */
interface WorkflowRolesResponse {
	role_ids: string[];
}

/**
 * Request type for assigning roles to a workflow
 */
interface AssignRolesToWorkflowRequest {
	role_ids: string[];
}

/**
 * Fetch roles assigned to a specific workflow.
 * Returns an array of role IDs.
 *
 * @param workflowId - The ID of the workflow to fetch roles for
 * @param options.enabled - Whether to enable the query (default: true when workflowId is provided)
 */
export function useWorkflowRoles(
	workflowId: string | undefined,
	_options?: { enabled?: boolean },
) {
	const queryClient = useQueryClient();

	// Use manual query since endpoint may not be in generated types yet
	const queryKey = ["workflow-roles", workflowId] as const;

	// Return a query-like object with manual fetching
	// This will be replaced with $api.useQuery once types are generated
	return {
		queryKey,
		data: queryClient.getQueryData<WorkflowRolesResponse>(queryKey),
		isLoading: false,
		isError: false,
		error: null,
		refetch: async () => {
			if (!workflowId) return { data: undefined };
			try {
				const response = await fetch(`/api/workflows/${workflowId}/roles`, {
					credentials: "include",
				});
				if (!response.ok) {
					throw new Error(`Failed to fetch workflow roles: ${response.status}`);
				}
				const data = (await response.json()) as WorkflowRolesResponse;
				queryClient.setQueryData(queryKey, data);
				return { data };
			} catch (error) {
				console.error("Failed to fetch workflow roles:", error);
				return { data: undefined };
			}
		},
	};
}

/**
 * Fetch roles for multiple workflows at once.
 * Returns a map of workflow ID to role IDs.
 *
 * @param workflowIds - Array of workflow IDs to fetch roles for
 */
export async function fetchWorkflowRolesBatch(
	workflowIds: string[],
): Promise<Map<string, string[]>> {
	const roleMap = new Map<string, string[]>();

	// Fetch roles for each workflow in parallel
	await Promise.all(
		workflowIds.map(async (workflowId) => {
			try {
				const response = await fetch(`/api/workflows/${workflowId}/roles`, {
					credentials: "include",
				});
				if (response.ok) {
					const data = (await response.json()) as WorkflowRolesResponse;
					roleMap.set(workflowId, data.role_ids);
				} else {
					roleMap.set(workflowId, []);
				}
			} catch {
				roleMap.set(workflowId, []);
			}
		}),
	);

	return roleMap;
}

/**
 * Mutation hook for assigning roles to a workflow.
 * This is an additive operation - roles that are already assigned will be skipped.
 */
export function useAssignRolesToWorkflow() {
	const queryClient = useQueryClient();

	return {
		mutateAsync: async (
			workflowId: string,
			roleIds: string[],
		): Promise<void> => {
			if (!workflowId || roleIds.length === 0) return;

			const response = await fetch(`/api/workflows/${workflowId}/roles`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				credentials: "include",
				body: JSON.stringify({ role_ids: roleIds } as AssignRolesToWorkflowRequest),
			});

			if (!response.ok && response.status !== 204) {
				const error = await response.json().catch(() => ({}));
				throw new Error(
					error.detail || `Failed to assign roles to workflow: ${response.status}`,
				);
			}

			// Invalidate workflow roles cache
			queryClient.invalidateQueries({
				queryKey: ["workflow-roles", workflowId],
			});
		},
		isLoading: false,
	};
}

/**
 * Mutation hook for removing a role from a workflow.
 */
export function useRemoveRoleFromWorkflow() {
	const queryClient = useQueryClient();

	return {
		mutateAsync: async (workflowId: string, roleId: string): Promise<void> => {
			const response = await fetch(
				`/api/workflows/${workflowId}/roles/${roleId}`,
				{
					method: "DELETE",
					credentials: "include",
				},
			);

			if (!response.ok && response.status !== 204) {
				const error = await response.json().catch(() => ({}));
				throw new Error(
					error.detail || `Failed to remove role from workflow: ${response.status}`,
				);
			}

			// Invalidate workflow roles cache
			queryClient.invalidateQueries({
				queryKey: ["workflow-roles", workflowId],
			});

			toast.success("Role removed", {
				description: "Role has been removed from the workflow",
			});
		},
		isLoading: false,
	};
}

/**
 * Bulk assign roles from an entity (form/app/agent) to multiple workflows.
 * Used during save operations to auto-assign entity roles to referenced workflows.
 *
 * @param workflowIds - Array of workflow IDs to assign roles to
 * @param roleIds - Array of role IDs to assign
 */
export async function bulkAssignRolesToWorkflows(
	workflowIds: string[],
	roleIds: string[],
): Promise<{ success: number; failed: number }> {
	let success = 0;
	let failed = 0;

	await Promise.all(
		workflowIds.map(async (workflowId) => {
			try {
				const response = await fetch(`/api/workflows/${workflowId}/roles`, {
					method: "POST",
					headers: {
						"Content-Type": "application/json",
					},
					credentials: "include",
					body: JSON.stringify({ role_ids: roleIds }),
				});

				if (response.ok || response.status === 204) {
					success++;
				} else {
					failed++;
				}
			} catch {
				failed++;
			}
		}),
	);

	return { success, failed };
}
