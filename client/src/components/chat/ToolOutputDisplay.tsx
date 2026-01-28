// client/src/components/chat/ToolOutputDisplay.tsx
/**
 * ToolOutputDisplay Component
 *
 * Renders tool output text with pattern-based syntax highlighting.
 * Recognizes standard formats:
 * - Diff lines: +/- prefixes get green/red coloring
 * - Grep format: file:line: gets cyan coloring
 * - Status messages: Updated/Deleted/Created get blue coloring
 * - Errors: Error: prefix gets red coloring
 */

import { cn } from "@/lib/utils";

interface ToolOutputDisplayProps {
	text: string;
	className?: string;
}

/**
 * Determine CSS class for a line based on its content pattern.
 */
function getLineClass(line: string): string {
	// Diff format: added lines
	if (line.startsWith("+")) {
		return "text-green-600 dark:text-green-400";
	}

	// Diff format: removed lines
	if (line.startsWith("-")) {
		return "text-red-600 dark:text-red-400";
	}

	// Grep format: file:line: match
	if (/^[\w./]+:\d+:/.test(line)) {
		return "text-cyan-600 dark:text-cyan-400";
	}

	// Status messages
	if (/^(Updated|Deleted|Created|Found)\s/.test(line)) {
		return "text-blue-600 dark:text-blue-400";
	}

	// Error messages
	if (line.startsWith("Error:") || line.startsWith("✗")) {
		return "text-red-600 dark:text-red-400";
	}

	// Success indicators
	if (line.startsWith("✓")) {
		return "text-green-600 dark:text-green-400";
	}

	return "";
}

export function ToolOutputDisplay({
	text,
	className,
}: ToolOutputDisplayProps) {
	const lines = text.split("\n");

	return (
		<pre
			className={cn(
				"font-mono text-sm whitespace-pre-wrap overflow-x-auto",
				className
			)}
		>
			{lines.map((line, i) => (
				<div key={i} className={getLineClass(line)}>
					{line || "\u00A0"} {/* Non-breaking space for empty lines */}
				</div>
			))}
		</pre>
	);
}
