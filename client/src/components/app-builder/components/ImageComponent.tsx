/**
 * Image Component for App Builder
 *
 * Displays an image with optional sizing constraints.
 * Expression evaluation is handled centrally by ComponentRegistry.
 */

import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type ImageComponent = components["schemas"]["ImageComponent"];
import type { RegisteredComponentProps } from "../ComponentRegistry";

export function ImageComponent({ component }: RegisteredComponentProps) {
	// In the unified model, props are at the top level of the component
	const comp = component as ImageComponent;

	// Props are pre-evaluated by ComponentRegistry
	const src = String(comp.src ?? "");
	const alt = comp.alt ? String(comp.alt) : "";

	const style: React.CSSProperties = {};

	if (comp.max_width) {
		style.maxWidth =
			typeof comp.max_width === "number"
				? `${comp.max_width}px`
				: comp.max_width;
	}

	if (comp.max_height) {
		style.maxHeight =
			typeof comp.max_height === "number"
				? `${comp.max_height}px`
				: comp.max_height;
	}

	const objectFitClass = comp.object_fit
		? `object-${comp.object_fit}`
		: "object-contain";

	return (
		<img
			src={src}
			alt={alt}
			style={style}
			className={cn(objectFitClass, comp.class_name)}
		/>
	);
}
