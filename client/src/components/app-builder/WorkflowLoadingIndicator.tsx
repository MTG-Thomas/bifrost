/**
 * Workflow Loading Indicator
 *
 * Platform-level loading indicator that shows when workflows are executing.
 * Displays in the bottom-right corner with a subtle animation.
 */

import { motion, AnimatePresence } from "framer-motion";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";

interface WorkflowLoadingIndicatorProps {
	/** Number of active workflow executions */
	activeCount: number;
	/** Map of execution ID to workflow name for display */
	workflowNames?: Map<string, string>;
}

/**
 * Displays a loading indicator when workflows are executing
 *
 * @example
 * <WorkflowLoadingIndicator
 *   activeCount={activeExecutionIds.length}
 *   workflowNames={activeWorkflowNames}
 * />
 */
export function WorkflowLoadingIndicator({
	activeCount,
	workflowNames,
}: WorkflowLoadingIndicatorProps) {
	// Get first workflow name for display
	const firstWorkflowName = workflowNames?.values().next().value;

	return (
		<AnimatePresence>
			{activeCount > 0 && (
				<motion.div
					initial={{ opacity: 0, y: 20, scale: 0.9 }}
					animate={{ opacity: 1, y: 0, scale: 1 }}
					exit={{ opacity: 0, y: 20, scale: 0.9 }}
					transition={{ duration: 0.2, ease: "easeOut" }}
					className="fixed bottom-4 right-4 z-50"
				>
					<Badge
						variant="secondary"
						className="flex items-center gap-2 py-2 px-3 shadow-lg bg-background border"
					>
						<Loader2 className="h-4 w-4 animate-spin text-primary" />
						<span className="text-sm font-medium">
							{activeCount === 1 && firstWorkflowName
								? `Running ${firstWorkflowName}...`
								: activeCount === 1
									? "Running workflow..."
									: `Running ${activeCount} workflows...`}
						</span>
					</Badge>
				</motion.div>
			)}
		</AnimatePresence>
	);
}

export default WorkflowLoadingIndicator;
