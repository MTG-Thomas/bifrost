import { useState } from "react";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import { useFormContext } from "@/contexts/FormContext";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Loader2, Maximize2, Minimize2 } from "lucide-react";

interface FormContextPanelProps {
	className?: string;
}

/**
 * Developer panel showing form context variables
 * Displays workflow results, query params, and field values in a tree view
 * Only visible to platform admins when Developer Mode is enabled
 *
 * Designed to be embedded in a drawer - no Card wrapper
 */
export function FormContextPanel({ className }: FormContextPanelProps) {
	const { context, isLoadingLaunchWorkflow } = useFormContext();
	const [expanded, setExpanded] = useState(false);

	const hasWorkflow = Object.keys(context.workflow).length > 0;
	const hasQuery = Object.keys(context.query).length > 0;
	const hasField = Object.keys(context.field).length > 0;

	return (
		<div className={cn("flex flex-col h-full", className)}>
			{/* Header */}
			<div className="flex items-center justify-between pb-3 shrink-0">
				<h3 className="text-sm font-medium flex items-center gap-2">
					Form Context
					{isLoadingLaunchWorkflow && (
						<Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
					)}
				</h3>
				<Button
					variant="ghost"
					size="icon"
					className="h-6 w-6"
					onClick={() => setExpanded(!expanded)}
					title={expanded ? "Collapse" : "Expand"}
				>
					{expanded ? (
						<Minimize2 className="h-3.5 w-3.5" />
					) : (
						<Maximize2 className="h-3.5 w-3.5" />
					)}
				</Button>
			</div>

			{/* Scrollable content */}
			<div
				className={cn(
					"overflow-y-auto space-y-4 pr-1",
					expanded ? "max-h-none" : "max-h-[500px]"
				)}
			>
				{/* Workflow Results */}
				<ContextSection
					title="context.workflow"
					description="Launch workflow results"
					hasData={hasWorkflow}
					isLoading={isLoadingLaunchWorkflow}
					emptyMessage="No launch workflow configured"
				>
					<VariablesTreeView data={context.workflow} />
				</ContextSection>

				{/* Query Parameters */}
				<ContextSection
					title="context.query"
					description="URL query parameters"
					hasData={hasQuery}
					emptyMessage="No query parameters"
				>
					<VariablesTreeView data={context.query} />
				</ContextSection>

				{/* Field Values */}
				<ContextSection
					title="context.field"
					description="Current field values"
					hasData={hasField}
					emptyMessage="No field values yet"
				>
					<VariablesTreeView data={context.field} />
				</ContextSection>
			</div>
		</div>
	);
}

interface ContextSectionProps {
	title: string;
	description: string;
	hasData: boolean;
	isLoading?: boolean;
	emptyMessage: string;
	children: React.ReactNode;
}

function ContextSection({
	title,
	description,
	hasData,
	isLoading,
	emptyMessage,
	children,
}: ContextSectionProps) {
	return (
		<div className="space-y-1.5">
			<div>
				<code className="text-xs font-semibold text-primary">
					{title}
				</code>
				<p className="text-xs text-muted-foreground">{description}</p>
			</div>
			<div className="pl-2 border-l-2 border-muted">
				{isLoading ? (
					<div className="flex items-center gap-2 text-xs text-muted-foreground py-1">
						<Loader2 className="h-3 w-3 animate-spin" />
						Loading...
					</div>
				) : hasData ? (
					children
				) : (
					<p className="text-xs text-muted-foreground italic py-1">
						{emptyMessage}
					</p>
				)}
			</div>
		</div>
	);
}
