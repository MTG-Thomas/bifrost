/**
 * Runtime utility functions for the App Builder.
 *
 * These are pure functions for working with the app builder layout tree.
 * For types, import from "@/types/app-builder" or "@/lib/v1".
 */

import type { components } from "./v1";

// Type aliases for cleaner code
type LayoutContainer = components["schemas"]["LayoutContainer"];
type AppComponent =
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
	| components["schemas"]["FileViewerComponent"]
	| components["schemas"]["ModalComponent"]
	| components["schemas"]["TextInputComponent"]
	| components["schemas"]["NumberInputComponent"]
	| components["schemas"]["SelectComponent"]
	| components["schemas"]["CheckboxComponent"]
	| components["schemas"]["FormEmbedComponent"]
	| components["schemas"]["FormGroupComponent"];

type LayoutElement = LayoutContainer | AppComponent;

/**
 * Layout types that can contain children
 */
export const CONTAINER_TYPES = ["row", "column", "grid", "card", "modal"] as const;

/**
 * Type guard to check if an element is a LayoutContainer
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
 * Get children from an element (handles both direct children and props.children)
 */
export function getElementChildren(element: LayoutElement): LayoutElement[] {
	// Layout containers have direct children
	if (isLayoutContainer(element)) {
		return (element.children || []) as LayoutElement[];
	}

	// Components may have children in props (e.g., Card, Modal)
	if ("props" in element && element.props) {
		const props = element.props as {
			children?: LayoutElement[];
			content?: LayoutContainer;
		};

		// Check for props.children first (cards)
		if (Array.isArray(props.children)) {
			return props.children;
		}

		// Check for props.content (modals)
		if (props.content) {
			return [props.content];
		}
	}

	return [];
}
