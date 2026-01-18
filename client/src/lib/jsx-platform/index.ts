/**
 * JSX Platform APIs
 *
 * This module exports all platform hooks and functions that are injected
 * into the JSX runtime scope. These APIs allow JSX apps to interact with
 * the platform (run workflows, navigate, access user info, etc.).
 *
 * Usage: These are injected into the JSX runtime scope automatically.
 * JSX code can use them without imports:
 *
 * ```jsx
 * // All of these are available in JSX code
 * const { data, isLoading } = useWorkflow('list_clients');
 * const user = useUser();
 * const params = useParams();
 * navigate('/clients');
 * await runWorkflow('save_client', { name: 'Acme' });
 * ```
 */

// Workflow execution
export { runWorkflow } from "./runWorkflow";
export { useWorkflow } from "./useWorkflow";

// Router utilities
export { useParams } from "./useParams";
export { useSearchParams } from "./useSearchParams";
export {
	navigate,
	useNavigate,
	setNavigateRef,
	clearNavigateRef,
} from "./navigate";

// User context
export { useUser } from "./useUser";

// App state
export {
	useAppState,
	jsxAppStateStore,
	resetJsxAppState,
} from "./useAppState";

/**
 * Platform scope object for JSX runtime
 *
 * This object contains all platform APIs that are injected into
 * the JSX runtime scope. The JSX compiler uses this to make
 * these functions/hooks available to user-authored code.
 *
 * Note: React hooks (useState, useEffect, etc.) are added separately
 * by the runtime to ensure they come from the same React instance.
 */
export { createPlatformScope } from "./scope";
