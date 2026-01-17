/**
 * Divider Component for App Builder
 *
 * Horizontal or vertical divider for visual separation.
 */

import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type DividerComponent = components["schemas"]["DividerComponent"];
import type { RegisteredComponentProps } from "../ComponentRegistry";

/**
 * Divider Component
 *
 * Renders a horizontal or vertical divider line.
 *
 * @example
 * // Definition
 * {
 *   id: "section-divider",
 *   type: "divider",
 *   props: {
 *     orientation: "horizontal"
 *   }
 * }
 */
export function DividerComponent({ component }: RegisteredComponentProps) {
	// In the unified model, props are at the top level of the component
	const comp = component as DividerComponent;
	const orientation = comp.orientation || "horizontal";

	if (orientation === "vertical") {
		return (
			<div
				className={cn(
					"mx-2 h-full w-px shrink-0 bg-border",
					comp.class_name,
				)}
				role="separator"
				aria-orientation="vertical"
			/>
		);
	}

	return (
		<div
			className={cn(
				"my-4 h-px w-full shrink-0 bg-border",
				comp.class_name,
			)}
			role="separator"
			aria-orientation="horizontal"
		/>
	);
}

export default DividerComponent;
