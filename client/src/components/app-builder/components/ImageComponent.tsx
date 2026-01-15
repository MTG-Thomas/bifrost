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
	const { props } = component as ImageComponent;

	// Props are pre-evaluated by ComponentRegistry
	const src = String(props?.src ?? "");
	const alt = props?.alt ? String(props.alt) : "";

	const style: React.CSSProperties = {};

	if (props?.max_width) {
		style.maxWidth =
			typeof props.max_width === "number"
				? `${props.max_width}px`
				: props.max_width;
	}

	if (props?.max_height) {
		style.maxHeight =
			typeof props.max_height === "number"
				? `${props.max_height}px`
				: props.max_height;
	}

	const objectFitClass = props?.object_fit
		? `object-${props.object_fit}`
		: "object-contain";

	return (
		<img
			src={src}
			alt={alt}
			style={style}
			className={cn(objectFitClass, props?.class_name)}
		/>
	);
}
