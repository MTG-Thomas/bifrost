/**
 * Platform Scope for JSX Runtime
 *
 * Creates the scope object that gets injected into the JSX runtime.
 * All platform APIs are bundled here for easy injection.
 */

import { runWorkflow } from "./runWorkflow";
import { useWorkflow } from "./useWorkflow";
import { useParams } from "./useParams";
import { useSearchParams } from "./useSearchParams";
import { navigate, useNavigate } from "./navigate";
import { useUser } from "./useUser";
import { useAppState } from "./useAppState";

/**
 * Platform scope object containing all platform APIs
 *
 * This is merged with React hooks and UI components to create
 * the full scope available to JSX code.
 *
 * @example
 * ```typescript
 * // In jsx-runtime.ts
 * const scope = {
 *   ...createPlatformScope(),
 *   React,
 *   useState: React.useState,
 *   useEffect: React.useEffect,
 *   ...UIComponents,
 * };
 * ```
 */
export function createPlatformScope(): Record<string, unknown> {
	return {
		// Workflow execution
		runWorkflow,
		useWorkflow,

		// Router utilities
		useParams,
		useSearchParams,
		navigate,
		useNavigate,

		// User context
		useUser,

		// App state
		useAppState,
	};
}
