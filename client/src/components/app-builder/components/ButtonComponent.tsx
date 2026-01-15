/**
 * Button Component for App Builder
 *
 * Action button supporting navigation, workflow triggers, and custom actions.
 */

import { useCallback, useMemo } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";
import type { OnCompleteAction } from "@/types/app-builder";
import { evaluateExpression } from "@/lib/expression-parser";
import type { RegisteredComponentProps } from "../ComponentRegistry";

type ButtonComponent = components["schemas"]["ButtonComponent"];
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
 *     action_type: "navigate",
 *     navigate_to: "/home"
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
 *     action_type: "workflow",
 *     workflow_id: "analysis-workflow",
 *     action_params: { mode: "full" }
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
 *     action_type: "custom",
 *     custom_action_id: "my-action"
 *   }
 * }
 */
export function ButtonComponent({
	component,
	context,
}: RegisteredComponentProps) {
	const { props } = component as ButtonComponent;
	// Support both 'label' and 'text' for button text
	const labelValue =
		props?.label ?? (props as Record<string, unknown>)?.text ?? "";
	const label = String(
		evaluateExpression(labelValue as string, context) ?? "",
	);

	// Check if this button's workflow is currently executing
	const isWorkflowLoading = useMemo(() => {
		// Support both old format (action_type at top level) and new format (onClick object)
		const onClick = (props as Record<string, unknown>)?.onClick as
			| { type?: string; workflow_id?: string }
			| undefined;
		const actionType = props?.action_type || onClick?.type;
		const workflowId = props?.workflow_id || onClick?.workflow_id;

		// Only check for workflow and submit action types
		if (
			(actionType === "workflow" || actionType === "submit") &&
			workflowId &&
			context.activeWorkflows
		) {
			return context.activeWorkflows.has(workflowId);
		}
		return false;
	}, [props, context.activeWorkflows]);

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
		// Support both old format (action_type at top level) and new format (onClick object)
		const onClick = (props as Record<string, unknown>)?.onClick as
			| {
					type?: string;
					navigate_to?: string;
					workflow_id?: string;
					action_params?: Record<string, unknown>;
					on_complete?: OnCompleteAction[];
					on_error?: OnCompleteAction[];
			  }
			| undefined;

		const actionType = props?.action_type || onClick?.type;
		const navigateTo = props?.navigate_to || onClick?.navigate_to;
		const workflowId = props?.workflow_id || onClick?.workflow_id;
		const modalId = props?.modal_id;
		const actionParams = props?.action_params || onClick?.action_params;
		const onComplete = (props?.on_complete || onClick?.on_complete) as
			| OnCompleteAction[]
			| undefined;
		const onError = ((props as Record<string, unknown>)?.on_error ||
			onClick?.on_error) as OnCompleteAction[] | undefined;

		// Evaluate expressions in actionParams before passing to workflows
		const evaluatedParams: Record<string, unknown> = {};
		if (actionParams) {
			for (const [key, value] of Object.entries(actionParams)) {
				if (typeof value === "string" && value.includes("{{")) {
					evaluatedParams[key] = evaluateExpression(value, context);
				} else {
					evaluatedParams[key] = value;
				}
			}
		}

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
					context.triggerWorkflow(
						workflowId,
						evaluatedParams,
						onComplete,
						onError,
					);
				}
				break;

			case "submit":
				// Submit form - collects all field values and triggers workflow
				if (workflowId && context.submitForm) {
					context.submitForm(
						workflowId,
						evaluatedParams,
						onComplete,
						onError,
					);
				}
				break;

			case "open-modal":
				// Open a modal by its component ID
				if (modalId && context.openModal) {
					context.openModal(modalId);
				}
				break;

			case "custom":
				if (props?.custom_action_id && context.onCustomAction) {
					context.onCustomAction(
						props.custom_action_id,
						evaluatedParams,
					);
				}
				break;
		}
	}, [props, context]);

	// Render button with optional icon (or loading spinner)
	const renderIcon = () => {
		// Show loading spinner when workflow is executing
		if (isWorkflowLoading) {
			return <Loader2 className="h-4 w-4 mr-2 animate-spin" />;
		}
		if (!props?.icon) return null;
		const Icon = getIcon(props.icon);
		return <Icon className="h-4 w-4 mr-2" />;
	};

	return (
		<Button
			variant={props?.variant || "default"}
			size={props?.size || "default"}
			disabled={isDisabled || isWorkflowLoading}
			onClick={handleClick}
			className={cn(props?.class_name)}
		>
			{renderIcon()}
			{label}
		</Button>
	);
}

export default ButtonComponent;
