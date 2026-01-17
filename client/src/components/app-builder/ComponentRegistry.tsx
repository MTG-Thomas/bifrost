/**
 * Component Registry for App Builder
 *
 * Registry pattern for mapping component types to React components.
 * Allows dynamic component rendering based on type definitions.
 */

import type { ComponentType as ReactComponentType } from "react";
import type { components } from "@/lib/v1";
import type { ExpressionContext } from "@/types/app-builder";
import { evaluateComponentProps } from "@/lib/expression-parser";
import { Skeleton } from "@/components/ui/skeleton";

// Type aliases for generated API types
type AppComponent =
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

type ComponentType =
	| "row"
	| "column"
	| "grid"
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
 * Props passed to each registered component
 */
export interface RegisteredComponentProps {
	/** The component definition */
	component: AppComponent;
	/** Expression context for evaluating expressions */
	context: ExpressionContext;
}

/**
 * Type for registered React components
 */
type RegisteredComponent = ReactComponentType<RegisteredComponentProps>;

/**
 * Internal registry storage
 */
const componentRegistry = new Map<ComponentType, RegisteredComponent>();

/**
 * Register a component for a specific type
 *
 * @param type - The component type identifier
 * @param component - The React component to render for this type
 *
 * @example
 * registerComponent("heading", HeadingComponent);
 * registerComponent("button", ButtonComponent);
 */
export function registerComponent(
	type: ComponentType,
	component: RegisteredComponent,
): void {
	componentRegistry.set(type, component);
}

/**
 * Get a registered component by type
 *
 * @param type - The component type to look up
 * @returns The registered React component, or undefined if not found
 *
 * @example
 * const HeadingComponent = getComponent("heading");
 * if (HeadingComponent) {
 *   return <HeadingComponent component={def} context={ctx} />;
 * }
 */
export function getComponent(
	type: ComponentType,
): RegisteredComponent | undefined {
	return componentRegistry.get(type);
}

/**
 * Check if a component type is registered
 *
 * @param type - The component type to check
 * @returns True if the component type is registered
 */
export function hasComponent(type: ComponentType): boolean {
	return componentRegistry.has(type);
}

/**
 * Unregister a component type
 *
 * @param type - The component type to unregister
 * @returns True if the component was unregistered, false if it wasn't registered
 */
export function unregisterComponent(type: ComponentType): boolean {
	return componentRegistry.delete(type);
}

/**
 * Get all registered component types
 *
 * @returns Array of all registered component types
 */
export function getRegisteredTypes(): ComponentType[] {
	return Array.from(componentRegistry.keys());
}

/**
 * Clear all registered components
 * Useful for testing or hot module reloading
 */
export function clearRegistry(): void {
	componentRegistry.clear();
}

/**
 * Fallback component for unregistered types
 */
export function UnknownComponent({ component }: RegisteredComponentProps) {
	return (
		<div className="rounded border border-dashed border-yellow-500 bg-yellow-50 p-4 text-sm text-yellow-700 dark:border-yellow-400 dark:bg-yellow-900/20 dark:text-yellow-300">
			<span className="font-medium">Unknown component type:</span>{" "}
			<code className="rounded bg-yellow-100 px-1 dark:bg-yellow-800">
				{component.type}
			</code>
		</div>
	);
}

/**
 * Component-specific loading skeleton
 * Returns a skeleton that approximates the shape of the component type
 */
function ComponentSkeleton({
	type,
}: {
	type: ComponentType;
}): React.ReactElement {
	switch (type) {
		case "stat-card":
			return (
				<div className="rounded-lg border p-6 space-y-3">
					<Skeleton className="h-4 w-24" />
					<Skeleton className="h-8 w-16" />
					<Skeleton className="h-3 w-20" />
				</div>
			);
		case "card":
			return (
				<div className="rounded-lg border p-6 space-y-4">
					<Skeleton className="h-5 w-1/3" />
					<Skeleton className="h-4 w-full" />
					<Skeleton className="h-4 w-2/3" />
				</div>
			);
		case "heading":
			return <Skeleton className="h-8 w-1/2" />;
		case "text":
			return (
				<div className="space-y-2">
					<Skeleton className="h-4 w-full" />
					<Skeleton className="h-4 w-4/5" />
				</div>
			);
		case "button":
			return <Skeleton className="h-10 w-24 rounded-md" />;
		case "image":
			return <Skeleton className="h-48 w-full rounded-lg" />;
		case "badge":
			return <Skeleton className="h-6 w-16 rounded-full" />;
		case "progress":
			return <Skeleton className="h-2 w-full rounded-full" />;
		case "data-table":
			return (
				<div className="space-y-3">
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-10 w-full" />
				</div>
			);
		default:
			return <Skeleton className="h-24 w-full rounded-lg" />;
	}
}

/**
 * Check if any of the specified workflows are currently active
 */
function isWorkflowLoading(
	loading_workflows: string[] | null | undefined,
	activeWorkflows: Set<string> | undefined,
): boolean {
	if (
		!loading_workflows ||
		loading_workflows.length === 0 ||
		!activeWorkflows
	) {
		return false;
	}
	return loading_workflows.some((wfId) => activeWorkflows.has(wfId));
}

/**
 * Render a component from the registry
 *
 * Props are automatically evaluated before being passed to the component,
 * so components receive pre-evaluated values and don't need to call
 * evaluateExpression() for most props.
 *
 * Note: Some props are intentionally NOT evaluated here because they
 * require special handling (e.g., rowActions need row context, onClick
 * handlers are evaluated at runtime). See NON_EVALUABLE_PROPS in
 * expression-parser.ts for the full list.
 *
 * If the component has `loading_workflows` specified and any of those
 * workflows are currently executing, a loading skeleton is shown instead.
 *
 * @param component - The component definition
 * @param context - The expression context
 * @returns The rendered React element
 */
export function renderRegisteredComponent(
	component: AppComponent,
	context: ExpressionContext,
): React.ReactElement {
	// Check if component should show loading skeleton
	if (
		isWorkflowLoading(component.loading_workflows, context.activeWorkflows)
	) {
		return <ComponentSkeleton key={component.id} type={component.type} />;
	}

	const Component = getComponent(component.type) || UnknownComponent;

	// Automatically evaluate props before passing to component
	// In the unified model, props are at the top level of the component object
	// (not nested in a `props` wrapper), so we evaluate the entire component
	const evaluatedComponent = evaluateComponentProps(
		component as unknown as Record<string, unknown>,
		context,
	) as AppComponent;

	return (
		<Component
			key={component.id}
			component={evaluatedComponent}
			context={context}
		/>
	);
}
