/**
 * GitHub Integration hooks using openapi-react-query pattern
 *
 * Exports both hooks for React components and standalone async functions
 * for imperative usage outside of React hooks
 */

import { $api, apiClient, authFetch } from "@/lib/api-client";
import { useQueryClient } from "@tanstack/react-query";
import type { components } from "@/lib/v1";

// =============================================================================
// Types - Auto-generated from OpenAPI spec
// =============================================================================

export type GitHubConnectRequest = components["schemas"]["GitHubConfigRequest"];
export type GitHubConfigResponse =
	components["schemas"]["GitHubConfigResponse"];
export type GitHubRepoInfo = components["schemas"]["GitHubRepoInfo"];
export type GitHubBranchInfo = components["schemas"]["GitHubBranchInfo"];
export type WorkspaceAnalysisResponse =
	components["schemas"]["WorkspaceAnalysisResponse"];
export type CreateRepoRequest = components["schemas"]["CreateRepoRequest"];
export type CreateRepoResponse = components["schemas"]["CreateRepoResponse"];
export type GitStatusResponse =
	components["schemas"]["GitRefreshStatusResponse"];
export type PullRequest = components["schemas"]["PullFromGitHubRequest"];
export type PushRequest = components["schemas"]["PushToGitHubRequest"];
export type FileChange = components["schemas"]["FileChange"];
export type CommitInfo = components["schemas"]["CommitInfo"];
export type ConflictInfo = components["schemas"]["ConflictInfo"];
export type CommitHistoryResponse =
	components["schemas"]["CommitHistoryResponse"];
export type DiscardCommitsResponse =
	components["schemas"]["DiscardUnpushedCommitsResponse"];
export type UnresolvedRefInfo = components["schemas"]["UnresolvedRefInfo"];
export type RefResolution = components["schemas"]["RefResolution"];
export type ResolveRefsRequest = components["schemas"]["ResolveRefsRequest"];
export type ResolveRefsResponse = components["schemas"]["ResolveRefsResponse"];

// Sync Preview/Execute Types - defined locally until types are regenerated
export type SyncActionType = "add" | "modify" | "delete";

export interface SyncAction {
	path: string;
	action: SyncActionType;
	sha?: string | null;
}

export interface SyncConflictInfo {
	path: string;
	local_content?: string | null;
	remote_content?: string | null;
	local_sha: string;
	remote_sha: string;
}

export interface WorkflowReference {
	type: string;
	id: string;
	name: string;
}

export interface OrphanInfo {
	workflow_id: string;
	workflow_name: string;
	function_name: string;
	last_path: string;
	used_by: WorkflowReference[];
}

export interface SyncPreviewResponse {
	to_pull: SyncAction[];
	to_push: SyncAction[];
	conflicts: SyncConflictInfo[];
	will_orphan: OrphanInfo[];
	is_empty: boolean;
}

export interface SyncExecuteRequest {
	conflict_resolutions: Record<string, "keep_local" | "keep_remote">;
	confirm_orphans: boolean;
}

export interface SyncExecuteResponse {
	success: boolean;
	job_id?: string | null;
	status: string;
	// These fields are populated via WebSocket completion, not initial response
	pulled: number;
	pushed: number;
	orphaned_workflows: string[];
	commit_sha?: string | null;
	error?: string | null;
}

// =============================================================================
// Query Hooks
// =============================================================================

/**
 * Get current Git status
 */
export function useGitStatus() {
	return $api.useQuery("get", "/api/github/status", {}, {});
}

/**
 * Get current GitHub configuration
 */
export function useGitHubConfig() {
	return $api.useQuery("get", "/api/github/config", {}, {});
}

/**
 * List repositories accessible with saved token
 * Only runs when enabled is true (defaults to true)
 */
export function useGitHubRepositories(enabled: boolean = true) {
	return $api.useQuery("get", "/api/github/repositories", {}, { enabled });
}

/**
 * Get list of local changes
 */
export function useGitChanges() {
	return $api.useQuery("get", "/api/github/changes", {}, {});
}

/**
 * Get merge conflicts
 */
export function useGitConflicts() {
	return $api.useQuery("get", "/api/github/conflicts", {}, {});
}

/**
 * Get commit history with pagination
 * Query parameters are passed via the query key to enable proper caching
 */
export function useGitCommits(limit: number = 20, offset: number = 0) {
	return $api.useQuery(
		"get",
		"/api/github/commits",
		{
			params: {
				query: { limit, offset },
			},
		},
		{},
	);
}

/**
 * List branches for a repository
 */
export function useGitHubBranches(repoFullName?: string) {
	return $api.useQuery(
		"get",
		"/api/github/branches",
		{
			params: {
				query: { repo: repoFullName || "" },
			},
		},
		{
			enabled: !!repoFullName,
		},
	);
}

// =============================================================================
// Mutation Hooks
// =============================================================================

/**
 * Refresh Git status - uses GitHub API to get complete Git status
 */
export function useRefreshGitStatus() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/refresh", {
		onSuccess: () => {
			// Invalidate status cache after refresh
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
		},
	});
}

/**
 * Validate GitHub token and list repositories
 */
export function useValidateGitHubToken() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/validate", {
		onSuccess: () => {
			// Invalidate repositories cache after validation
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/repositories"],
			});
		},
	});
}

/**
 * Configure GitHub integration
 */
export function useConfigureGitHub() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/configure", {
		onSuccess: () => {
			// Invalidate related queries after configuration
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/config"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
		},
	});
}

/**
 * Analyze workspace before configuration
 */
export function useAnalyzeWorkspace() {
	return $api.useMutation("post", "/api/github/analyze-workspace", {});
}

/**
 * Create a new GitHub repository
 */
export function useCreateGitHubRepository() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/create-repository", {
		onSuccess: () => {
			// Invalidate repositories list after creation
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/repositories"],
			});
		},
	});
}

/**
 * Disconnect GitHub integration
 */
export function useDisconnectGitHub() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/disconnect", {
		onSuccess: () => {
			// Clear all GitHub-related caches
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/config"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/repositories"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/changes"],
			});
		},
	});
}

/**
 * Initialize Git repository with remote
 */
export function useInitRepo() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/init", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
		},
	});
}

/**
 * Commit local changes
 */
export function useCommitChanges() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/commit", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/changes"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/commits"],
			});
		},
	});
}

/**
 * Discard all unpushed commits
 */
export function useDiscardUnpushedCommits() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/discard-unpushed", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/commits"],
			});
		},
	});
}

/**
 * Discard a specific commit and all newer commits
 */
export function useDiscardCommit() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/discard-commit", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/commits"],
			});
		},
	});
}

/**
 * Abort current merge operation
 */
export function useAbortMerge() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/abort-merge", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/conflicts"],
			});
		},
	});
}

/**
 * Resolve unresolved workflow references after pull
 */
export function useResolveWorkflowRefs() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/github/resolve-refs", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/status"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/github/changes"],
			});
		},
	});
}

/**
 * Get sync preview - shows what will be pulled/pushed and any conflicts
 * Uses authFetch for CSRF protection since endpoint isn't in OpenAPI spec yet
 */
export function useSyncPreview() {
	return {
		mutateAsync: async (): Promise<SyncPreviewResponse> => {
			const response = await authFetch("/api/github/sync", {
				method: "GET",
			});
			if (!response.ok) {
				const error = await response.json().catch(() => ({}));
				throw new Error(error.detail || "Failed to get sync preview");
			}
			return response.json();
		},
		isPending: false,
	};
}

/**
 * Execute sync with conflict resolutions and orphan confirmation
 * Uses authFetch for CSRF protection since endpoint isn't in OpenAPI spec yet
 *
 * NOTE: This queues a background job and returns immediately with job_id.
 * The client should subscribe to WebSocket channel git:{job_id} AFTER
 * receiving the response to get progress/completion messages.
 * Query invalidation should happen in the UI when WebSocket completion is received.
 */
export function useSyncExecute() {
	return {
		mutateAsync: async (params: {
			body: SyncExecuteRequest;
		}): Promise<SyncExecuteResponse> => {
			const response = await authFetch("/api/github/sync", {
				method: "POST",
				body: JSON.stringify(params.body),
			});
			if (!response.ok) {
				const error = await response.json().catch(() => ({}));
				throw new Error(error.detail || "Failed to queue sync");
			}
			// Returns job_id and status="queued", actual results come via WebSocket
			return response.json();
		},
		isPending: false,
	};
}

// =============================================================================
// Standalone async functions for imperative usage (outside React)
// =============================================================================

/**
 * Validate GitHub token and list repositories (imperative)
 */
export async function validateGitHubToken(token: string) {
	const response = await apiClient.POST("/api/github/validate", {
		body: { token },
	});

	if (response.error) {
		throw new Error("Failed to validate token");
	}

	return response.data;
}

/**
 * List branches for a repository (imperative)
 */
export async function listGitHubBranches(repoFullName: string) {
	const response = await apiClient.GET("/api/github/branches", {
		params: {
			query: { repo: repoFullName },
		},
	});

	if (response.error) {
		throw new Error("Failed to list branches");
	}

	const data = response.data as { branches: GitHubBranchInfo[] };
	return data.branches;
}
