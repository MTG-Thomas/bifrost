/**
 * App Builder Helper Functions and Runtime Types
 *
 * This file contains:
 * 1. Runtime helper functions for working with app builder types
 * 2. Frontend-only types not defined in the backend API (e.g., ExpressionContext)
 * 3. Core union types (AppComponent, LayoutContainer) that exclude AppComponentNode
 *    for proper discriminated union behavior
 * 4. All literal types and type aliases for backward compatibility
 *
 * Import from this file for:
 * - Runtime functions: isLayoutContainer, isAppComponent, canHaveChildren, getElementChildren
 * - Constants: CONTAINER_TYPES
 * - Frontend-only types: ExpressionContext, ExpressionUser, WorkflowResult
 * - Core types: AppComponent, LayoutContainer, LayoutElement
 * - Literal types: ComponentType, LayoutType, etc.
 * - Navigation types: NavItem, NavigationConfig, ApplicationDefinition
 */

import type { components } from "./v1";

// =============================================================================
// Literal Types
// =============================================================================

/**
 * Available component types for the app builder
 */
export type ComponentType =
	| "heading"
	| "text"
	| "html"
	| "card"
	| "divider"
	| "spacer"
	| "button"
	| "stat-card"
	| "image"
	| "badge"
	| "progress"
	| "data-table"
	| "tabs"
	| "tab-item"
	| "file-viewer"
	| "modal"
	| "text-input"
	| "number-input"
	| "select"
	| "checkbox"
	| "form-embed"
	| "form-group";

/**
 * Width options for components
 */
export type ComponentWidth = "auto" | "full" | "1/2" | "1/3" | "1/4" | "2/3" | "3/4";

/**
 * Button action types
 */
export type ButtonActionType = "navigate" | "workflow" | "custom" | "submit" | "open-modal";

/**
 * Heading levels
 */
export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;

/**
 * Layout alignment options
 */
export type LayoutAlign = "start" | "center" | "end" | "stretch";

/**
 * Layout justify options
 */
export type LayoutJustify = "start" | "center" | "end" | "between" | "around";

/**
 * Layout max-width options for constraining container width
 */
export type LayoutMaxWidth = "sm" | "md" | "lg" | "xl" | "2xl" | "full" | "none";

/**
 * Layout container types
 */
export type LayoutType = "row" | "column" | "grid";

/**
 * How container children fill available space
 */
export type LayoutDistribute = "natural" | "equal" | "fit";

/**
 * Overflow behavior for scrollable containers
 */
export type LayoutOverflow = "auto" | "scroll" | "hidden" | "visible";

/**
 * Sticky positioning options
 */
export type LayoutSticky = "top" | "bottom";

/**
 * File display mode for file-viewer component
 */
export type FileViewerDisplayMode = "inline" | "modal" | "download";

/**
 * Data source type
 */
export type DataSourceType = "api" | "static" | "computed" | "data-provider" | "workflow";

// =============================================================================
// Re-export types from v1.d.ts for convenience
// =============================================================================

// Types without -Input/-Output variants (use directly)
export type RepeatFor = components["schemas"]["RepeatFor"];
export type OnCompleteAction = components["schemas"]["OnCompleteAction"];
export type TableColumn = components["schemas"]["TableColumn"];
export type SelectOption = components["schemas"]["SelectOption"];
export type PagePermission = components["schemas"]["PagePermission"];

// Core types
export type TableAction = components["schemas"]["TableAction"];
export type TabItemComponent = components["schemas"]["TabItemComponent"];
export type PageDefinition = components["schemas"]["PageDefinition"];
export type DataSourceConfig = components["schemas"]["DataSourceConfig"];

// Full component types (unified model - props at top level)
export type RowComponent = components["schemas"]["RowComponent"];
export type ColumnComponent = components["schemas"]["ColumnComponent"];
export type GridComponent = components["schemas"]["GridComponent"];
export type HeadingComponent = components["schemas"]["HeadingComponent"];
export type TextComponent = components["schemas"]["TextComponent"];
export type HtmlComponent = components["schemas"]["HtmlComponent"];
export type CardComponent = components["schemas"]["CardComponent"];
export type DividerComponent = components["schemas"]["DividerComponent"];
export type SpacerComponent = components["schemas"]["SpacerComponent"];
export type ButtonComponent = components["schemas"]["ButtonComponent"];
export type StatCardComponent = components["schemas"]["StatCardComponent"];
export type ImageComponent = components["schemas"]["ImageComponent"];
export type BadgeComponent = components["schemas"]["BadgeComponent"];
export type ProgressComponent = components["schemas"]["ProgressComponent"];
export type DataTableComponent = components["schemas"]["DataTableComponent"];
export type TabsComponent = components["schemas"]["TabsComponent"];
export type FileViewerComponent = components["schemas"]["FileViewerComponent"];
export type ModalComponent = components["schemas"]["ModalComponent"];
export type TextInputComponent = components["schemas"]["TextInputComponent"];
export type NumberInputComponent = components["schemas"]["NumberInputComponent"];
export type SelectComponent = components["schemas"]["SelectComponent"];
export type CheckboxComponent = components["schemas"]["CheckboxComponent"];
export type FormEmbedComponent = components["schemas"]["FormEmbedComponent"];
export type FormGroupComponent = components["schemas"]["FormGroupComponent"];

// Legacy aliases for backwards compatibility
export type HeadingComponentProps = HeadingComponent;
export type TextComponentProps = TextComponent;
export type HtmlComponentProps = HtmlComponent;
export type CardComponentProps = CardComponent;
export type DividerComponentProps = DividerComponent;
export type SpacerComponentProps = SpacerComponent;
export type ButtonComponentProps = ButtonComponent;
export type StatCardComponentProps = StatCardComponent;
export type ImageComponentProps = ImageComponent;
export type BadgeComponentProps = BadgeComponent;
export type ProgressComponentProps = ProgressComponent;
export type DataTableComponentProps = DataTableComponent;
export type TabsComponentProps = TabsComponent;
export type FileViewerComponentProps = FileViewerComponent;
export type ModalComponentProps = ModalComponent;
export type TextInputComponentProps = TextInputComponent;
export type NumberInputComponentProps = NumberInputComponent;
export type SelectComponentProps = SelectComponent;
export type CheckboxComponentProps = CheckboxComponent;
export type FormEmbedComponentProps = FormEmbedComponent;
export type FormGroupComponentProps = FormGroupComponent;

// =============================================================================
// Navigation and Permission Types (re-exported from generated API types)
// =============================================================================

// Navigation types (use -Output suffix for reading from API)
export type NavItem = components["schemas"]["NavItem-Output"];
export type NavigationConfig = components["schemas"]["NavigationConfig-Output"];

// Permission types
export type PermissionRule = components["schemas"]["PermissionRule"];
export type PermissionConfig = components["schemas"]["PermissionConfig"];

// Application type
export type ApplicationPublic = components["schemas"]["ApplicationPublic"];

/**
 * Full application definition for frontend runtime.
 * Combines API types with resolved pages for rendering.
 */
export interface ApplicationDefinition {
	/** Application identifier */
	id: string;
	/** Application name */
	name: string;
	/** Application description */
	description?: string;
	/** Application version */
	version: string;
	/** Pages in the application */
	pages: PageDefinition[];
	/** Navigation configuration (uses snake_case fields from API) */
	navigation?: NavigationConfig;
	/** Permission configuration (uses snake_case fields from API) */
	permissions?: PermissionConfig;
	/** Global variables available to all pages */
	globalVariables?: Record<string, unknown>;
	/** App-level CSS styles (global for entire application) */
	styles?: string;
}

/**
 * Base props shared by all app components
 * @deprecated Use individual component types directly
 */
export interface BaseComponentProps {
	/** Unique component identifier */
	id: string;
	/** Component type */
	type: ComponentType;
	/** Optional width constraint */
	width?: ComponentWidth;
	/** Visibility expression (e.g., "{{ user.role == 'admin' }}") */
	visible?: string;
	/** Workflow IDs/names that trigger loading skeleton when executing */
	loadingWorkflows?: string[];
	/** Grid column span (for components inside grid layouts) */
	gridSpan?: number;
	/** Repeat this component for each item in an array */
	repeatFor?: RepeatFor;
	/** Additional CSS classes */
	className?: string;
	/** Inline CSS styles (camelCase properties) */
	style?: React.CSSProperties;
}

// =============================================================================
// Core Union Types
// =============================================================================

/**
 * Layout container types (row, column, grid).
 * In the unified model, these are now individual component types.
 */
export type LayoutContainer =
	| components["schemas"]["RowComponent"]
	| components["schemas"]["ColumnComponent"]
	| components["schemas"]["GridComponent"];

/**
 * Union type of all app components.
 * In the unified model, all components including layout containers are here.
 */
export type AppComponent =
	| components["schemas"]["RowComponent"]
	| components["schemas"]["ColumnComponent"]
	| components["schemas"]["GridComponent"]
	| components["schemas"]["HeadingComponent"]
	| components["schemas"]["TextComponent"]
	| components["schemas"]["HtmlComponent"]
	| components["schemas"]["CardComponent"]
	| components["schemas"]["DividerComponent"]
	| components["schemas"]["SpacerComponent"]
	| components["schemas"]["ButtonComponent"]
	| components["schemas"]["StatCardComponent"]
	| components["schemas"]["ImageComponent"]
	| components["schemas"]["BadgeComponent"]
	| components["schemas"]["ProgressComponent"]
	| components["schemas"]["DataTableComponent"]
	| components["schemas"]["TabsComponent"]
	| components["schemas"]["TabItemComponent"]
	| components["schemas"]["FileViewerComponent"]
	| components["schemas"]["ModalComponent"]
	| components["schemas"]["TextInputComponent"]
	| components["schemas"]["NumberInputComponent"]
	| components["schemas"]["SelectComponent"]
	| components["schemas"]["CheckboxComponent"]
	| components["schemas"]["FormEmbedComponent"]
	| components["schemas"]["FormGroupComponent"];

/**
 * User information for expression context
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
 * Context for expression evaluation
 */
export interface ExpressionContext {
	/** Current user information */
	user?: ExpressionUser;
	/** Page-level variables */
	variables: Record<string, unknown>;
	/** Field values from form inputs (accessed via {{ field.* }}) */
	field?: Record<string, unknown>;
	/**
	 * Workflow execution results keyed by dataSourceId.
	 * Access via {{ workflow.<dataSourceId>.result }}
	 */
	workflow?: Record<string, WorkflowResult>;
	/** Current row context for table row click handlers (accessed via {{ row.* }}) */
	row?: Record<string, unknown>;
	/** Route parameters from URL (accessed via {{ params.id }}) */
	params?: Record<string, string>;
	/** Whether any data source is currently loading */
	isDataLoading?: boolean;
	/** Navigation function for button actions */
	navigate?: (path: string) => void;
	/** Workflow trigger function - returns Promise with result for onComplete/onError handling */
	triggerWorkflow?: (
		workflowId: string,
		params?: Record<string, unknown>,
		onComplete?: components["schemas"]["OnCompleteAction"][],
		onError?: components["schemas"]["OnCompleteAction"][],
	) => void;
	/** Submit form to workflow - collects all field values and triggers workflow */
	submitForm?: (
		workflowId: string,
		additionalParams?: Record<string, unknown>,
		onComplete?: components["schemas"]["OnCompleteAction"][],
		onError?: components["schemas"]["OnCompleteAction"][],
	) => void;
	/** Custom action handler */
	onCustomAction?: (
		actionId: string,
		params?: Record<string, unknown>,
	) => void;
	/** Set field value function (used by input components) */
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

/**
 * Types that can contain children (layout containers + container components)
 * These are all component types that have a `children` field in the unified model.
 */
export const CONTAINER_TYPES = [
	"row",
	"column",
	"grid",
	"card",
	"modal",
	"tabs",
	"tab-item",
	"form-group",
] as const;

/**
 * Element type for the layout tree.
 * In the unified model, this is the same as AppComponent.
 */
export type LayoutElement = AppComponent;

/**
 * Type guard to check if an element is a LayoutContainer (row, column, grid)
 */
export function isLayoutContainer(
	element: AppComponent,
): element is LayoutContainer {
	return (
		element.type === "row" ||
		element.type === "column" ||
		element.type === "grid"
	);
}

/**
 * Check if an element type can have children
 */
export function canHaveChildren(element: AppComponent): boolean {
	return CONTAINER_TYPES.includes(
		element.type as (typeof CONTAINER_TYPES)[number],
	);
}

/**
 * Get children from an element.
 * In the unified model, container components have children directly on the component.
 */
export function getElementChildren(element: AppComponent): AppComponent[] {
	// All container types (row, column, grid, card, modal, tabs, tab-item, form-group)
	// have children directly on the element
	if ("children" in element && Array.isArray(element.children)) {
		return element.children as AppComponent[];
	}
	return [];
}
