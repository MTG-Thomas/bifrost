/**
 * Text Component for App Builder
 *
 * Renders paragraph text with optional label. Expression evaluation
 * is handled centrally by ComponentRegistry.
 */

import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type TextComponent = components["schemas"]["TextComponent"];
import type { RegisteredComponentProps } from "../ComponentRegistry";

/**
 * Text Component
 *
 * Renders a paragraph of text with optional label.
 * Supports expressions in both label and text content.
 *
 * @example
 * // Definition
 * {
 *   id: "user-email",
 *   type: "text",
 *   props: {
 *     label: "Email Address",
 *     text: "{{ user.email }}"
 *   }
 * }
 */
export function TextComponent({ component }: RegisteredComponentProps) {
	const { props } = component as TextComponent;
	// Props are pre-evaluated by ComponentRegistry
	const text = String(props?.text ?? "");
	const label = props?.label ? String(props.label) : undefined;

	return (
		<div className={cn("space-y-1", props?.class_name)}>
			{label && (
				<p className="text-sm font-medium text-muted-foreground">
					{label}
				</p>
			)}
			<p className="leading-7">{text}</p>
		</div>
	);
}

export default TextComponent;
