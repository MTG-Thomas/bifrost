/**
 * StatCard Component for App Builder
 *
 * Displays a statistic with optional trend indicator and click action.
 */

import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import type { StatCardComponentProps } from "@/lib/app-builder-types";
import type { RegisteredComponentProps } from "../ComponentRegistry";
import { evaluateExpression } from "@/lib/expression-parser";

function getTrendIcon(direction: "up" | "down" | "neutral") {
	switch (direction) {
		case "up":
			return <TrendingUp className="h-4 w-4 text-green-500" />;
		case "down":
			return <TrendingDown className="h-4 w-4 text-red-500" />;
		case "neutral":
			return <Minus className="h-4 w-4 text-muted-foreground" />;
	}
}

function getTrendColor(direction: "up" | "down" | "neutral") {
	switch (direction) {
		case "up":
			return "text-green-500";
		case "down":
			return "text-red-500";
		case "neutral":
			return "text-muted-foreground";
	}
}

export function StatCardComponent({ component, context }: RegisteredComponentProps) {
	const { props } = component as StatCardComponentProps;

	// Evaluate expressions
	const title = String(evaluateExpression(props.title, context) ?? "");
	const value = String(evaluateExpression(props.value, context) ?? "");
	const description = props.description
		? String(evaluateExpression(props.description, context) ?? "")
		: undefined;

	const handleClick = () => {
		if (!props.onClick) return;

		if (props.onClick.type === "navigate" && props.onClick.navigateTo && context.navigate) {
			const path = String(evaluateExpression(props.onClick.navigateTo, context) ?? "");
			context.navigate(path);
		} else if (props.onClick.type === "workflow" && props.onClick.workflowId && context.triggerWorkflow) {
			context.triggerWorkflow(props.onClick.workflowId);
		}
	};

	const isClickable = !!props.onClick;

	return (
		<Card
			className={cn(
				"transition-colors",
				isClickable && "cursor-pointer hover:bg-accent",
				props.className
			)}
			onClick={isClickable ? handleClick : undefined}
		>
			<CardContent className="p-6">
				<div className="flex items-center justify-between">
					<p className="text-sm font-medium text-muted-foreground">{title}</p>
					{props.icon && (
						<span className="text-muted-foreground">{props.icon}</span>
					)}
				</div>
				<div className="mt-2 flex items-baseline gap-2">
					<p className="text-2xl font-bold">{value}</p>
					{props.trend && (
						<div className={cn("flex items-center gap-1 text-sm", getTrendColor(props.trend.direction))}>
							{getTrendIcon(props.trend.direction)}
							<span>{props.trend.value}</span>
						</div>
					)}
				</div>
				{description && (
					<p className="mt-1 text-sm text-muted-foreground">{description}</p>
				)}
			</CardContent>
		</Card>
	);
}
