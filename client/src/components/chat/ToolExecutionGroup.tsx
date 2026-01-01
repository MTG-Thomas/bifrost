/**
 * ToolExecutionGroup Component
 *
 * Wrapper component that displays tool executions with a visual
 * hierarchy indicator (vertical line) similar to Claude Code.
 *
 * Provides:
 * - Indentation to show tools are "under" a message
 * - Vertical connecting line on the left
 * - Proper spacing for the tool badges
 */

import { cn } from "@/lib/utils";

interface ToolExecutionGroupProps {
	children: React.ReactNode;
	className?: string;
}

export function ToolExecutionGroup({
	children,
	className,
}: ToolExecutionGroupProps) {
	return (
		<div className={cn("relative pl-6 ml-4", className)}>
			{/* Vertical connecting line */}
			<div className="absolute left-0 top-0 bottom-0 w-px bg-border" />
			{/* Tool badges container */}
			<div className="flex flex-wrap gap-2 py-2">{children}</div>
		</div>
	);
}
