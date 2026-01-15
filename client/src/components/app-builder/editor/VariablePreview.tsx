/**
 * Variable Preview Panel for App Builder Editor
 *
 * Displays available variables and context values that can be used in expressions.
 * Provides a way to browse and insert variable paths into property fields.
 */

import { useState, useMemo } from "react";
import {
	ChevronRight,
	ChevronDown,
	Copy,
	Check,
	Variable,
	User,
	Workflow,
	FileText,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import type {
	ExpressionContext,
	PageDefinition,
	WorkflowResult,
} from "@/lib/app-builder-helpers";

interface VariablePreviewProps {
	/** Current expression context (for preview mode) */
	context?: Partial<ExpressionContext>;
	/** Current page definition */
	page?: PageDefinition;
	/** Whether we're in row action context (shows row.* variables) */
	isRowContext?: boolean;
	/** Callback when a variable path is clicked (for insertion) */
	onInsertVariable?: (path: string) => void;
	/** Additional CSS classes */
	className?: string;
}

interface VariablePath {
	path: string;
	type: string;
	description?: string;
	value?: unknown;
}

interface VariableSectionProps {
	title: string;
	icon: React.ReactNode;
	paths: VariablePath[];
	defaultOpen?: boolean;
	onInsertVariable?: (path: string) => void;
}

/**
 * Format a value for display in the variable preview
 */
function formatValue(value: unknown): string {
	if (value === undefined) return "";
	if (value === null) return "null";
	if (typeof value === "string") {
		if (value.length > 50) {
			return `"${value.slice(0, 47)}..."`;
		}
		return `"${value}"`;
	}
	if (typeof value === "number" || typeof value === "boolean") {
		return String(value);
	}
	if (Array.isArray(value)) {
		if (value.length === 0) return "[]";
		return `[${value.length} items]`;
	}
	if (typeof value === "object") {
		const keys = Object.keys(value);
		if (keys.length === 0) return "{}";
		if (keys.length <= 3) {
			return `{${keys.join(", ")}}`;
		}
		return `{${keys.length} keys}`;
	}
	return String(value);
}

/**
 * Copy button with feedback
 */
function CopyButton({ value }: { value: string }) {
	const [copied, setCopied] = useState(false);

	const handleCopy = async () => {
		await navigator.clipboard.writeText(`{{ ${value} }}`);
		setCopied(true);
		setTimeout(() => setCopied(false), 1500);
	};

	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<Button
					variant="ghost"
					size="icon-sm"
					className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity"
					onClick={handleCopy}
				>
					{copied ? (
						<Check className="h-3 w-3 text-green-500" />
					) : (
						<Copy className="h-3 w-3" />
					)}
				</Button>
			</TooltipTrigger>
			<TooltipContent side="left">
				{copied ? "Copied!" : "Copy expression"}
			</TooltipContent>
		</Tooltip>
	);
}

/**
 * Collapsible section for a category of variables
 */
function VariableSection({
	title,
	icon,
	paths,
	defaultOpen = true,
	onInsertVariable,
}: VariableSectionProps) {
	const [isOpen, setIsOpen] = useState(defaultOpen);

	return (
		<Collapsible
			open={isOpen}
			onOpenChange={setIsOpen}
			className="border-b last:border-b-0"
		>
			<CollapsibleTrigger className="flex w-full items-center gap-2 px-3 py-2 hover:bg-muted/50 transition-colors">
				{isOpen ? (
					<ChevronDown className="h-4 w-4 text-muted-foreground" />
				) : (
					<ChevronRight className="h-4 w-4 text-muted-foreground" />
				)}
				{icon}
				<span className="text-sm font-medium">{title}</span>
				<span className="ml-auto text-xs text-muted-foreground">
					{paths.length}
				</span>
			</CollapsibleTrigger>
			<CollapsibleContent>
				<div className="px-2 pb-2">
					{paths.map(({ path, type, description, value }) => {
						const formattedValue = formatValue(value);
						const hasValue = value !== undefined;

						return (
							<div
								key={path}
								className="group flex items-center justify-between rounded px-2 py-1 hover:bg-muted/50 cursor-pointer"
								onClick={() => onInsertVariable?.(path)}
							>
								<div className="flex-1 min-w-0">
									<div className="flex items-center gap-2">
										<code className="text-xs font-mono text-primary break-all">
											{path}
										</code>
										{hasValue && formattedValue && (
											<code className="text-xs font-mono text-emerald-600 dark:text-emerald-400 truncate max-w-[150px]">
												= {formattedValue}
											</code>
										)}
									</div>
									<div className="flex items-center gap-2 mt-0.5">
										<span className="text-[10px] text-muted-foreground font-medium uppercase">
											{type}
										</span>
										{description && (
											<span className="text-[10px] text-muted-foreground truncate">
												• {description}
											</span>
										)}
									</div>
								</div>
								<CopyButton value={path} />
							</div>
						);
					})}
					{paths.length === 0 && (
						<p className="text-xs text-muted-foreground px-2 py-2 italic">
							No variables available
						</p>
					)}
				</div>
			</CollapsibleContent>
		</Collapsible>
	);
}

/**
 * Section for displaying workflow results with expandable tree views
 */
interface WorkflowResultsSectionProps {
	workflowData: Record<string, WorkflowResult> | undefined;
	defaultOpen?: boolean;
}

function WorkflowResultsSection({
	workflowData,
	defaultOpen = false,
}: WorkflowResultsSectionProps) {
	const [isOpen, setIsOpen] = useState(defaultOpen);

	const hasData = workflowData && Object.keys(workflowData).length > 0;
	const count = hasData ? Object.keys(workflowData).length : 0;

	return (
		<Collapsible
			open={isOpen}
			onOpenChange={setIsOpen}
			className="border-b last:border-b-0"
		>
			<CollapsibleTrigger className="flex w-full items-center gap-2 px-3 py-2 hover:bg-muted/50 transition-colors">
				{isOpen ? (
					<ChevronDown className="h-4 w-4 text-muted-foreground" />
				) : (
					<ChevronRight className="h-4 w-4 text-muted-foreground" />
				)}
				<Workflow className="h-4 w-4 text-amber-500" />
				<span className="text-sm font-medium">Workflow Results</span>
				<span className="ml-auto text-xs text-muted-foreground">
					{count}
				</span>
			</CollapsibleTrigger>
			<CollapsibleContent>
				<div className="px-2 pb-2">
					{hasData ? (
						Object.entries(workflowData).map(([key, result]) => (
							<WorkflowResultItem
								key={key}
								dataSourceId={key}
								result={result}
							/>
						))
					) : (
						<p className="text-xs text-muted-foreground px-2 py-2 italic">
							No workflow results yet. Execute a workflow to see
							data here.
						</p>
					)}
				</div>
			</CollapsibleContent>
		</Collapsible>
	);
}

/**
 * Individual workflow result with expandable tree view
 */
interface WorkflowResultItemProps {
	dataSourceId: string;
	result: WorkflowResult;
}

function WorkflowResultItem({ dataSourceId, result }: WorkflowResultItemProps) {
	const [isExpanded, setIsExpanded] = useState(false);
	const [copied, setCopied] = useState(false);

	const handleCopyPath = async (path: string) => {
		await navigator.clipboard.writeText(`{{ ${path} }}`);
		setCopied(true);
		setTimeout(() => setCopied(false), 1500);
	};

	const basePath = `workflow.${dataSourceId}`;

	return (
		<div className="rounded border bg-muted/30 mb-2 last:mb-0">
			{/* Header with workflow name and status */}
			<div
				className="flex items-center gap-2 px-2 py-1.5 cursor-pointer hover:bg-muted/50"
				onClick={() => setIsExpanded(!isExpanded)}
			>
				{isExpanded ? (
					<ChevronDown className="h-3 w-3 text-muted-foreground" />
				) : (
					<ChevronRight className="h-3 w-3 text-muted-foreground" />
				)}
				<code className="text-xs font-mono text-primary">
					{dataSourceId}
				</code>
				<span
					className={cn(
						"text-[10px] px-1.5 py-0.5 rounded",
						result.status === "completed" &&
							"bg-green-500/20 text-green-600 dark:text-green-400",
						result.status === "failed" &&
							"bg-red-500/20 text-red-600 dark:text-red-400",
						result.status === "running" &&
							"bg-blue-500/20 text-blue-600 dark:text-blue-400",
					)}
				>
					{result.status}
				</span>
			</div>

			{/* Expanded content with tree view */}
			{isExpanded && (
				<div className="border-t px-2 py-2 space-y-2">
					{/* Quick copy paths */}
					<div className="flex flex-wrap gap-1">
						<Tooltip>
							<TooltipTrigger asChild>
								<Button
									variant="outline"
									size="sm"
									className="h-6 text-[10px] px-2"
									onClick={() =>
										handleCopyPath(`${basePath}.result`)
									}
								>
									{copied ? (
										<Check className="h-3 w-3 mr-1 text-green-500" />
									) : (
										<Copy className="h-3 w-3 mr-1" />
									)}
									.result
								</Button>
							</TooltipTrigger>
							<TooltipContent>
								Copy {basePath}.result expression
							</TooltipContent>
						</Tooltip>
					</div>

					{/* Tree view of result data */}
					{result.result !== undefined && (
						<div className="bg-background rounded border p-2 max-h-48 overflow-auto">
							{typeof result.result === "object" &&
							result.result !== null ? (
								<VariablesTreeView
									data={
										result.result as Record<string, unknown>
									}
								/>
							) : (
								<code className="text-xs font-mono text-emerald-600 dark:text-emerald-400">
									{formatValue(result.result)}
								</code>
							)}
						</div>
					)}

					{/* Error display */}
					{result.error && (
						<div className="bg-red-500/10 rounded border border-red-500/20 p-2">
							<code className="text-xs font-mono text-red-600 dark:text-red-400">
								{result.error}
							</code>
						</div>
					)}
				</div>
			)}
		</div>
	);
}

/**
 * Variable Preview Panel
 *
 * Shows available variables organized by category with copy functionality.
 */
export function VariablePreview({
	context,
	page,
	isRowContext = false,
	onInsertVariable,
	className,
}: VariablePreviewProps) {
	// Build user variables with actual values from context
	const userPaths = useMemo((): VariablePath[] => {
		const user = context?.user;
		return [
			{
				path: "user.id",
				type: "string",
				description: "User ID",
				value: user?.id,
			},
			{
				path: "user.name",
				type: "string",
				description: "Display name",
				value: user?.name,
			},
			{
				path: "user.email",
				type: "string",
				description: "Email address",
				value: user?.email,
			},
			{
				path: "user.role",
				type: "string",
				description: "User role",
				value: user?.role,
			},
		];
	}, [context?.user]);

	// Build field variables from page inputs with actual values
	const fieldPaths = useMemo((): VariablePath[] => {
		const paths: VariablePath[] = [];
		// If we have context with field values, show them
		const fieldData = context?.field;
		if (fieldData) {
			for (const [key, value] of Object.entries(fieldData)) {
				paths.push({
					path: `field.${key}`,
					type: typeof value === "object" ? "object" : typeof value,
					description: "Input value",
					value,
				});
			}
		}
		// Add hint for input components if no fields yet
		if (paths.length === 0) {
			paths.push({
				path: "field.<fieldId>",
				type: "any",
				description:
					"Input field value (use fieldId from input component)",
			});
		}
		return paths;
	}, [context?.field]);

	// Build row context variables (for table actions)
	const rowPaths = useMemo((): VariablePath[] => {
		if (!isRowContext) return [];
		return [
			{
				path: "row",
				type: "object",
				description: "Current row data object",
			},
			{
				path: "row.<fieldName>",
				type: "any",
				description:
					"Access row field by name (e.g., row.id, row.name)",
			},
		];
	}, [isRowContext]);

	// Build page variables with actual values from context
	const pagePaths = useMemo((): VariablePath[] => {
		const paths: VariablePath[] = [];
		// Use actual runtime values from context if available
		const runtimeVariables = context?.variables;
		const pageVariables = page?.variables;

		// First add runtime variables with their actual values
		if (runtimeVariables && Object.keys(runtimeVariables).length > 0) {
			for (const [key, value] of Object.entries(runtimeVariables)) {
				paths.push({
					path: `variables.${key}`,
					type: typeof value === "object" ? "object" : typeof value,
					description: "Page variable",
					value,
				});
			}
		}
		// Add page-defined variables that aren't in runtime yet
		if (pageVariables) {
			for (const [key, defaultValue] of Object.entries(pageVariables)) {
				if (!runtimeVariables || !(key in runtimeVariables)) {
					paths.push({
						path: `variables.${key}`,
						type: "any",
						description: "Page variable (default)",
						value: defaultValue,
					});
				}
			}
		}
		// Add hint if no variables
		if (paths.length === 0) {
			paths.push({
				path: "variables.<name>",
				type: "any",
				description: "Page-level variables set via set-variable action",
			});
		}
		return paths;
	}, [page?.variables, context?.variables]);

	// Build URL/route params
	const queryPaths = useMemo((): VariablePath[] => {
		const paths: VariablePath[] = [];
		const paramsData = context?.params;

		// Add actual route params if available
		if (paramsData && Object.keys(paramsData).length > 0) {
			for (const [key, value] of Object.entries(paramsData)) {
				paths.push({
					path: `params.${key}`,
					type: "string",
					description: "Route parameter",
					value,
				});
			}
		} else {
			paths.push({
				path: "params.<param>",
				type: "string",
				description: "Route parameter (e.g., params.userId)",
			});
		}
		return paths;
	}, [context?.params]);

	return (
		<div className={cn("flex flex-col h-full", className)}>
			<div className="flex items-center gap-2 px-3 py-2 border-b">
				<Variable className="h-4 w-4 text-muted-foreground" />
				<h3 className="text-sm font-semibold">Available Variables</h3>
			</div>
			<p className="px-3 py-2 text-xs text-muted-foreground border-b">
				Click a variable to copy its expression. Use{" "}
				<code className="bg-muted px-1 rounded">{"{{ path }}"}</code>{" "}
				syntax in any text field.
			</p>
			<div className="flex-1 overflow-y-auto">
				<VariableSection
					title="User"
					icon={<User className="h-4 w-4 text-blue-500" />}
					paths={userPaths}
					onInsertVariable={onInsertVariable}
				/>
				{isRowContext && (
					<VariableSection
						title="Row (Table Context)"
						icon={<FileText className="h-4 w-4 text-orange-500" />}
						paths={rowPaths}
						onInsertVariable={onInsertVariable}
					/>
				)}
				<VariableSection
					title="Form Fields"
					icon={<FileText className="h-4 w-4 text-green-500" />}
					paths={fieldPaths}
					defaultOpen={false}
					onInsertVariable={onInsertVariable}
				/>
				<WorkflowResultsSection
					workflowData={context?.workflow}
					defaultOpen={false}
				/>
				<VariableSection
					title="Page Variables"
					icon={<Variable className="h-4 w-4 text-cyan-500" />}
					paths={pagePaths}
					defaultOpen={false}
					onInsertVariable={onInsertVariable}
				/>
				<VariableSection
					title="URL Parameters"
					icon={<FileText className="h-4 w-4 text-gray-500" />}
					paths={queryPaths}
					defaultOpen={false}
					onInsertVariable={onInsertVariable}
				/>
			</div>
		</div>
	);
}

export default VariablePreview;
