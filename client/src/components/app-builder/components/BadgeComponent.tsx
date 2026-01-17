/**
 * Badge Component for App Builder
 *
 * Displays a badge with configurable variant. Expression evaluation
 * is handled centrally by ComponentRegistry.
 */

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { components } from "@/lib/v1";
import type { RegisteredComponentProps } from "../ComponentRegistry";

type BadgeComponent = components["schemas"]["BadgeComponent"];

export function BadgeComponent({ component }: RegisteredComponentProps) {
	// In the unified model, props are at the top level of the component
	const comp = component as BadgeComponent;

	// Props are pre-evaluated by ComponentRegistry
	const text = String(comp.text ?? "");

	return (
		<Badge
			variant={comp.variant || "default"}
			className={cn(comp.class_name)}
		>
			{text}
		</Badge>
	);
}
