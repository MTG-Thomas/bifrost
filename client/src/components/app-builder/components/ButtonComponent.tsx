/**
 * Button Component for App Builder
 *
 * Action button supporting navigation, workflow triggers, and custom actions.
 */

import { useCallback } from "react";
import { cn } from "@/lib/utils";
import type { ButtonComponentProps } from "@/lib/app-builder-types";
import { evaluateExpression } from "@/lib/expression-parser";
import type { RegisteredComponentProps } from "../ComponentRegistry";
import { Button } from "@/components/ui/button";
import { getIcon } from "@/lib/icons";

/**
 * Button Component
 *
 * Renders an action button with various action types.
 *
 * @example
 * // Navigation button
 * {
 *   id: "go-home",
 *   type: "button",
 *   props: {
 *     label: "Go Home",
 *     actionType: "navigate",
 *     navigateTo: "/home"
 *   }
 * }
 *
 * @example
 * // Workflow trigger button
 * {
 *   id: "run-workflow",
 *   type: "button",
 *   props: {
 *     label: "Run Analysis",
 *     actionType: "workflow",
 *     workflowId: "analysis-workflow",
 *     actionParams: { mode: "full" }
 *   }
 * }
 *
 * @example
 * // Custom action button
 * {
 *   id: "custom-action",
 *   type: "button",
 *   props: {
 *     label: "Do Something",
 *     actionType: "custom",
 *     customActionId: "my-action"
 *   }
 * }
 */
export function ButtonComponent({
	component,
	context,
}: RegisteredComponentProps) {
	const { props } = component as ButtonComponentProps;
	// Support both 'label' and 'text' for button text
	const labelValue = props?.label ?? (props as Record<string, unknown>)?.text ?? "";
	const label = String(evaluateExpression(labelValue as string, context) ?? "");

	// Evaluate disabled state - can be boolean or expression string
	const isDisabled = (() => {
		if (props?.disabled === undefined || props?.disabled === null) {
			return false;
		}
		if (typeof props.disabled === "boolean") {
			return props.disabled;
		}
		// It's a string - evaluate as expression
		return Boolean(evaluateExpression(props.disabled, context));
	})();

	const handleClick = useCallback(() => {
		// Support both old format (actionType at top level) and new format (onClick object)
		const onClick = (props as Record<string, unknown>)?.onClick as {
			type?: string;
			navigateTo?: string;
			workflowId?: string;
			actionParams?: Record<string, unknown>;
			onComplete?: unknown[];
		} | undefined;

		const actionType = props?.actionType || onClick?.type;
		const navigateTo = props?.navigateTo || onClick?.navigateTo;
		const workflowId = props?.workflowId || onClick?.workflowId;
		const actionParams = props?.actionParams || onClick?.actionParams;
		const onComplete = props?.onComplete || onClick?.onComplete;

		switch (actionType) {
			case "navigate":
				if (navigateTo && context.navigate) {
					// Evaluate navigation path in case it contains expressions
					const path = String(
						evaluateExpression(navigateTo, context) ?? navigateTo,
					);
					context.navigate(path);
				}
				break;

			case "workflow":
				if (workflowId && context.triggerWorkflow) {
					context.triggerWorkflow(workflowId, actionParams, onComplete as never);
				}
				break;

			case "submit":
				// Submit form - collects all field values and triggers workflow
				if (workflowId && context.submitForm) {
					context.submitForm(workflowId, actionParams);
				}
				break;

			case "custom":
				if (props?.customActionId && context.onCustomAction) {
					context.onCustomAction(props.customActionId, actionParams);
				}
				break;
		}
	}, [props, context]);

	// Render button with optional icon
	const renderIcon = () => {
		if (!props?.icon) return null;
		const Icon = getIcon(props.icon);
		return <Icon className="h-4 w-4 mr-2" />;
	};

	return (
		<Button
			variant={props?.variant || "default"}
			size={props?.size || "default"}
			disabled={isDisabled}
			onClick={handleClick}
			className={cn(props?.className)}
		>
			{renderIcon()}
			{label}
		</Button>
	);
}

export default ButtonComponent;
