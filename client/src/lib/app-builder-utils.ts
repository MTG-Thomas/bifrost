/**
 * Runtime utility functions for the App Builder.
 *
 * These are pure functions for working with the app builder layout tree.
 * For types, import from "@/types/app-builder" or "@/lib/v1".
 */

import type { components } from "./v1";

// Type aliases for cleaner code - unified model where all components are AppComponent
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

// LayoutContainer is a subset of AppComponent that represents the row/column/grid types
type LayoutContainer =
	| components["schemas"]["RowComponent"]
	| components["schemas"]["ColumnComponent"]
	| components["schemas"]["GridComponent"];

// LayoutElement is the same as AppComponent in the unified model
type LayoutElement = AppComponent;

/**
 * Layout types that can contain children
 */
export const CONTAINER_TYPES = ["row", "column", "grid", "card", "modal", "tabs", "tab-item", "form-group"] as const;

/**
 * Type guard to check if an element is a LayoutContainer (row, column, grid)
 */
export function isLayoutContainer(element: LayoutElement): element is LayoutContainer {
	return (
		element.type === "row" || element.type === "column" || element.type === "grid"
	);
}

/**
 * Type guard to check if an element is an AppComponent
 */
export function isAppComponent(element: LayoutElement): element is AppComponent {
	return !isLayoutContainer(element);
}

/**
 * Check if an element type can have children
 */
export function canHaveChildren(element: LayoutElement): boolean {
	return CONTAINER_TYPES.includes(element.type as (typeof CONTAINER_TYPES)[number]);
}

/**
 * Get children from an element.
 * In the unified model, container components have children directly on the component.
 */
export function getElementChildren(element: LayoutElement): LayoutElement[] {
	// All container types (row, column, grid, card, modal, tabs, tab-item, form-group)
	// have children directly on the element in the unified model
	if ("children" in element && Array.isArray(element.children)) {
		return element.children as LayoutElement[];
	}

	return [];
}
