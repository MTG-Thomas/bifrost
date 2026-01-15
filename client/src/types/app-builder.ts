/**
 * Frontend-specific types for the App Builder.
 *
 * These types are NOT generated from the API - they exist only in the frontend
 * for runtime expression evaluation, navigation, and UI state management.
 *
 * For API types (PageDefinition, LayoutContainer, components, etc.),
 * import from "@/lib/v1" directly.
 */

import type { components } from "@/lib/v1";

// Re-export commonly used API types for convenience
export type PageDefinition = components["schemas"]["PageDefinition"];
export type LayoutContainer = components["schemas"]["LayoutContainer"];
export type RepeatFor = components["schemas"]["RepeatFor"];
export type OnCompleteAction = components["schemas"]["OnCompleteAction"];

// =============================================================================
// Expression Context Types
// =============================================================================

/**
 * User information available in expression context
 */
export interface ExpressionUser {
	id: string;
	name: string;
	email: string;
	role: string;
}

/**
 * Workflow execution result stored in context
 */
export interface WorkflowResult {
	/** The execution ID */
	executionId: string;
	/** The workflow ID */
	workflowId?: string;
	/** The workflow name */
	workflowName?: string;
	/** Execution status */
	status: "pending" | "running" | "completed" | "failed";
	/** Result data from the workflow */
	result?: unknown;
	/** Error message if failed */
	error?: string;
}

/**
 * Context for expression evaluation (e.g., {{ user.name }}, {{ workflow.result }})
 */
export interface ExpressionContext {
	/** Current user information */
	user?: ExpressionUser;
	/** Page-level variables */
	variables: Record<string, unknown>;
	/** Field values from form inputs (accessed via {{ field.* }}) */
	field?: Record<string, unknown>;
	/** Workflow execution results keyed by dataSourceId */
	workflow?: Record<string, WorkflowResult>;
	/** Current row context for table row click handlers */
	row?: Record<string, unknown>;
	/** Route parameters from URL */
	params?: Record<string, string>;
	/** Whether any data source is currently loading */
	isDataLoading?: boolean;
	/** Navigation function for button actions */
	navigate?: (path: string) => void;
	/** Workflow trigger function */
	triggerWorkflow?: (
		workflowId: string,
		params?: Record<string, unknown>,
		onComplete?: OnCompleteAction[],
		onError?: OnCompleteAction[],
	) => void;
	/** Submit form to workflow */
	submitForm?: (
		workflowId: string,
		additionalParams?: Record<string, unknown>,
		onComplete?: OnCompleteAction[],
		onError?: OnCompleteAction[],
	) => void;
	/** Custom action handler */
	onCustomAction?: (actionId: string, params?: Record<string, unknown>) => void;
	/** Set field value function */
	setFieldValue?: (fieldId: string, value: unknown) => void;
	/** Refresh a data table by its data source key */
	refreshTable?: (dataSourceKey: string) => void;
	/** Set a page variable */
	setVariable?: (key: string, value: unknown) => void;
	/** Currently executing workflow IDs/names for loading states */
	activeWorkflows?: Set<string>;
	/** Open a modal by its component ID */
	openModal?: (modalId: string) => void;
	/** Close a modal by its component ID */
	closeModal?: (modalId: string) => void;
}

// =============================================================================
// Navigation Types
// =============================================================================

/**
 * Navigation item for sidebar/navbar
 */
export interface NavItem {
	/** Item identifier (usually page ID) */
	id: string;
	/** Display label */
	label: string;
	/** Icon name (lucide icon) */
	icon?: string;
	/** Navigation path */
	path?: string;
	/** Visibility expression */
	visible?: string;
	/** Order in navigation */
	order?: number;
	/** Whether this is a section header (group) */
	isSection?: boolean;
	/** Child items for section groups */
	children?: NavItem[];
}

/**
 * Navigation configuration for the application
 */
export interface NavigationConfig {
	/** Sidebar navigation items */
	sidebar?: NavItem[];
	/** Whether to show the sidebar */
	showSidebar?: boolean;
	/** Whether to show the header */
	showHeader?: boolean;
	/** Custom logo URL */
	logoUrl?: string;
	/** Brand color (hex) */
	brandColor?: string;
}

// =============================================================================
// Permission Types
// =============================================================================

/**
 * Permission rule for app access control
 */
export interface PermissionRule {
	/** Role that has this permission */
	role: string;
	/** Permission level */
	level: "view" | "edit" | "admin";
}

/**
 * Permission configuration for an application
 */
export interface PermissionConfig {
	/** Whether the app is public (no auth required) */
	public?: boolean;
	/** Default permission level for authenticated users */
	defaultLevel?: "none" | "view" | "edit" | "admin";
	/** Role-based permission rules */
	rules?: PermissionRule[];
}

// =============================================================================
// Application Definition (Frontend)
// =============================================================================

/**
 * Full application definition for the frontend runtime.
 * Extends API types with frontend-specific navigation and permissions.
 */
export interface ApplicationDefinition {
	id: string;
	name: string;
	description?: string;
	version: string;
	pages: PageDefinition[];
	navigation?: NavigationConfig;
	permissions?: PermissionConfig;
	globalVariables?: Record<string, unknown>;
	styles?: string;
}
