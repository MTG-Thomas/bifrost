/**
 * Application Runner Page
 *
 * Renders and runs a published App Builder application.
 * Integrates with the Zustand store for runtime state management.
 */

import { useMemo, useCallback, useEffect, useState } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	useApplication,
	useApplicationDefinition,
	useApplicationDraft,
} from "@/hooks/useApplications";
import { useWorkflows, useExecuteWorkflow } from "@/hooks/useWorkflows";
import { AppRenderer } from "@/components/app-builder";
import {
	WorkflowExecutionModal,
	type PendingWorkflow,
} from "@/components/app-builder/WorkflowExecutionModal";
import { useAppBuilderStore } from "@/stores/app-builder.store";
import type { ApplicationDefinition, WorkflowResult, OnCompleteAction } from "@/lib/app-builder-types";
import { evaluateExpression } from "@/lib/expression-parser";
import type { components } from "@/lib/v1";

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];

interface ApplicationRunnerProps {
	/** Whether to render in preview mode (uses draft instead of live) */
	preview?: boolean;
	/** Whether to render in embed mode (minimal chrome, no navigation) */
	embed?: boolean;
}

export function ApplicationRunner({ preview = false, embed = false }: ApplicationRunnerProps) {
	const { applicationId: slugParam, "*": pagePath } = useParams();
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const resetStore = useAppBuilderStore((state) => state.reset);
	const refreshDataSource = useAppBuilderStore((state) => state.refreshDataSource);

	// Workflow execution state
	const [pendingWorkflow, setPendingWorkflow] = useState<PendingWorkflow | null>(null);
	const [workflowResult, setWorkflowResult] = useState<WorkflowResult | undefined>(undefined);

	// Extract embed theme customization from URL params
	const embedTheme = useMemo(() => {
		if (!embed) return null;
		const primaryColor = searchParams.get("primaryColor");
		const backgroundColor = searchParams.get("backgroundColor");
		const textColor = searchParams.get("textColor");
		const logoUrl = searchParams.get("logo");

		if (!primaryColor && !backgroundColor && !textColor && !logoUrl) {
			return null;
		}

		return {
			primaryColor,
			backgroundColor,
			textColor,
			logoUrl,
		};
	}, [embed, searchParams]);

	// Fetch workflows metadata for parameter lookup
	const { data: workflows } = useWorkflows();
	const executeWorkflowMutation = useExecuteWorkflow();

	// Reset the runtime store when the application changes
	useEffect(() => {
		resetStore();
		return () => {
			resetStore();
		};
	}, [slugParam, resetStore]);

	// Fetch application metadata
	const {
		data: application,
		isLoading: isLoadingApp,
		error: appError,
	} = useApplication(slugParam);

	// Fetch the live or draft definition based on preview mode
	const {
		data: liveDefinition,
		isLoading: isLoadingLive,
		error: liveError,
	} = useApplicationDefinition(
		!preview && slugParam ? slugParam : undefined,
	);

	const {
		data: draftDefinition,
		isLoading: isLoadingDraft,
		error: draftError,
	} = useApplicationDraft(
		preview && slugParam ? slugParam : undefined,
	);

	// Use the appropriate definition based on preview mode
	const definition = preview ? draftDefinition : liveDefinition;
	const isLoadingDef = preview ? isLoadingDraft : isLoadingLive;
	const defError = preview ? draftError : liveError;

	// Parse the definition into our ApplicationDefinition type
	const appDefinition = useMemo((): ApplicationDefinition | null => {
		if (!definition?.definition) return null;
		return definition.definition as unknown as ApplicationDefinition;
	}, [definition]);

	// Determine current page
	const currentPage = useMemo(() => {
		if (!appDefinition?.pages?.length) return null;

		// Normalize the page path
		const normalizedPath = pagePath
			? `/${pagePath}`.replace(/\/+/g, "/")
			: "/";

		// Find matching page
		const page = appDefinition.pages.find((p) => {
			const pagePathNormalized = p.path.startsWith("/")
				? p.path
				: `/${p.path}`;
			return pagePathNormalized === normalizedPath;
		});

		// Default to first page if no match
		return page || appDefinition.pages[0];
	}, [appDefinition, pagePath]);

	// Find a workflow by ID or name
	const findWorkflow = useCallback(
		(workflowId: string): WorkflowMetadata | undefined => {
			if (!workflows) return undefined;
			// Try to find by ID first, then by name
			return workflows.find(
				(w) => w.id === workflowId || w.name === workflowId,
			);
		},
		[workflows],
	);

	// Execute workflow with parameters
	const executeWorkflow = useCallback(
		async (workflowId: string, params: Record<string, unknown>): Promise<WorkflowResult | undefined> => {
			const workflow = findWorkflow(workflowId);
			try {
				const response = await executeWorkflowMutation.mutateAsync({
					body: {
						workflow_id: workflow?.id ?? workflowId,
						input_data: params,
						form_id: null,
						transient: false,
						code: null,
						script_name: null,
					},
				});

				// Build workflow result from response
				// Map API statuses to our simplified status type
				const mapStatus = (apiStatus: string): WorkflowResult["status"] => {
					switch (apiStatus) {
						case "Success":
						case "CompletedWithErrors":
							return "completed";
						case "Failed":
						case "Timeout":
						case "Cancelled":
							return "failed";
						case "Running":
						case "Cancelling":
							return "running";
						case "Pending":
						default:
							return "pending";
					}
				};

				const result: WorkflowResult = {
					executionId: response.execution_id,
					workflowId: response.workflow_id ?? undefined,
					workflowName: response.workflow_name ?? undefined,
					status: mapStatus(response.status),
					result: response.result ?? undefined,
					error: response.error ?? undefined,
				};

				// Update workflow result in context
				setWorkflowResult(result);

				toast.success(
					`Workflow "${workflow?.name || workflowId}" executed successfully`,
				);

				return result;
			} catch (error) {
				const errorResult: WorkflowResult = {
					executionId: "",
					workflowId: workflow?.id ?? workflowId,
					workflowName: workflow?.name ?? workflowId,
					status: "failed",
					error: error instanceof Error ? error.message : "Unknown error",
				};
				setWorkflowResult(errorResult);

				toast.error(
					`Failed to execute workflow: ${error instanceof Error ? error.message : "Unknown error"}`,
				);

				return errorResult;
			}
		},
		[executeWorkflowMutation, findWorkflow],
	);

	// Check if workflow has required parameters that need user input
	const hasRequiredParams = useCallback(
		(workflow: WorkflowMetadata, providedParams: Record<string, unknown>): boolean => {
			if (!workflow.parameters) return false;
			return workflow.parameters.some((param) => {
				const paramName = param.name ?? "";
				// Required param not provided
				if (param.required && !(paramName in providedParams)) {
					return true;
				}
				return false;
			});
		},
		[],
	);

	// Execute onComplete actions after workflow completes
	const executeOnCompleteActions = useCallback(
		(actions: OnCompleteAction[], result: WorkflowResult) => {
			// Build a context with the workflow result for expression evaluation
			const context = {
				variables: {} as Record<string, unknown>,
				workflow: result,
			};

			for (const action of actions) {
				switch (action.type) {
					case "navigate":
						if (action.navigateTo) {
							// Evaluate any expressions in the navigation path
							const path = action.navigateTo.includes("{{")
								? String(evaluateExpression(action.navigateTo, context) ?? action.navigateTo)
								: action.navigateTo;
							navigate(path);
						}
						break;

					case "set-variable":
						// Note: This would need to be connected to a page-level variable store
						// For now, we log a warning as the store integration is pending
						if (action.variableName) {
							const value = action.variableValue?.includes("{{")
								? evaluateExpression(action.variableValue, context)
								: action.variableValue ?? result.result;
							// TODO: Wire up to page variable store when available
							console.warn(`[onComplete] set-variable action not yet connected to page store. Would set ${action.variableName} =`, value);
						}
						break;

					case "refresh-table":
						if (action.dataSourceKey) {
							refreshDataSource(action.dataSourceKey);
						}
						break;
				}
			}
		},
		[navigate, refreshDataSource],
	);

	// Workflow trigger handler with onComplete support
	const handleTriggerWorkflow = useCallback(
		async (workflowId: string, params?: Record<string, unknown>, onComplete?: OnCompleteAction[]) => {
			const workflow = findWorkflow(workflowId);
			const providedParams = params ?? {};

			const executeAndComplete = async (finalParams: Record<string, unknown>) => {
				const result = await executeWorkflow(workflowId, finalParams);
				// Execute onComplete actions after workflow finishes
				if (onComplete && onComplete.length > 0 && result) {
					executeOnCompleteActions(onComplete, result);
				}
			};

			if (!workflow) {
				// Workflow not found - execute anyway and let API handle the error
				toast.warning(`Workflow "${workflowId}" not found in metadata, attempting execution...`);
				executeAndComplete(providedParams);
				return;
			}

			// Check if we need to show the modal for required parameters
			if (hasRequiredParams(workflow, providedParams)) {
				// Show modal to collect missing parameters
				setPendingWorkflow({
					workflow,
					providedParams,
					onExecute: async (finalParams) => {
						await executeAndComplete(finalParams);
						setPendingWorkflow(null);
					},
					onCancel: () => setPendingWorkflow(null),
				});
			} else {
				// Execute immediately with provided params
				executeAndComplete(providedParams);
			}
		},
		[findWorkflow, hasRequiredParams, executeWorkflow, executeOnCompleteActions],
	);

	// Refresh table handler - delegates to the Zustand store
	const handleRefreshTable = useCallback(
		(dataSourceKey: string) => {
			refreshDataSource(dataSourceKey);
		},
		[refreshDataSource],
	);

	// Build inline styles for embed theme customization
	const embedThemeStyles = useMemo(() => {
		if (!embedTheme) return undefined;
		const styles: Record<string, string> = {};
		if (embedTheme.primaryColor) {
			styles["--primary"] = embedTheme.primaryColor;
		}
		if (embedTheme.backgroundColor) {
			styles["--background"] = embedTheme.backgroundColor;
		}
		if (embedTheme.textColor) {
			styles["--foreground"] = embedTheme.textColor;
		}
		return styles as React.CSSProperties;
	}, [embedTheme]);

	// Loading state
	if (isLoadingApp || isLoadingDef) {
		return (
			<div className="min-h-screen flex items-center justify-center">
				<div className="flex flex-col items-center gap-4">
					<Loader2 className="h-8 w-8 animate-spin text-primary" />
					<p className="text-muted-foreground">Loading application...</p>
				</div>
			</div>
		);
	}

	// Error states
	if (appError || defError) {
		return (
			<div className="min-h-screen flex items-center justify-center p-4">
				<Card className="max-w-md w-full">
					<CardHeader>
						<div className="flex items-center gap-2 text-destructive">
							<AlertTriangle className="h-5 w-5" />
							<CardTitle>Application Error</CardTitle>
						</div>
						<CardDescription>
							{appError instanceof Error
								? appError.message
								: defError instanceof Error
									? defError.message
									: "Failed to load application"}
						</CardDescription>
					</CardHeader>
					<CardContent>
						<Button variant="outline" onClick={() => navigate("/apps")}>
							<ArrowLeft className="mr-2 h-4 w-4" />
							Back to Applications
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	// No application found
	if (!application) {
		return (
			<div className="min-h-screen flex items-center justify-center p-4">
				<Card className="max-w-md w-full">
					<CardHeader>
						<div className="flex items-center gap-2 text-muted-foreground">
							<AlertTriangle className="h-5 w-5" />
							<CardTitle>Application Not Found</CardTitle>
						</div>
						<CardDescription>
							The requested application does not exist or you don't have
							access to it.
						</CardDescription>
					</CardHeader>
					<CardContent>
						<Button variant="outline" onClick={() => navigate("/apps")}>
							<ArrowLeft className="mr-2 h-4 w-4" />
							Back to Applications
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	// No published version (and not in preview mode)
	if (!preview && !application.is_published) {
		return (
			<div className="min-h-screen flex items-center justify-center p-4">
				<Card className="max-w-md w-full">
					<CardHeader>
						<div className="flex items-center gap-2 text-muted-foreground">
							<AlertTriangle className="h-5 w-5" />
							<CardTitle>Not Published</CardTitle>
						</div>
						<CardDescription>
							This application has not been published yet. Please publish
							the application before accessing it.
						</CardDescription>
					</CardHeader>
					<CardContent className="flex gap-2">
						<Button variant="outline" onClick={() => navigate("/apps")}>
							<ArrowLeft className="mr-2 h-4 w-4" />
							Back
						</Button>
						<Button
							onClick={() => navigate(`/apps/${slugParam}/edit`)}
						>
							Open Editor
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	// No definition available
	if (!appDefinition) {
		return (
			<div className="min-h-screen flex items-center justify-center p-4">
				<Card className="max-w-md w-full">
					<CardHeader>
						<div className="flex items-center gap-2 text-muted-foreground">
							<AlertTriangle className="h-5 w-5" />
							<CardTitle>No Content</CardTitle>
						</div>
						<CardDescription>
							{preview
								? "No draft version is available for this application."
								: "This application has no published content."}
						</CardDescription>
					</CardHeader>
					<CardContent className="flex gap-2">
						<Button variant="outline" onClick={() => navigate("/apps")}>
							<ArrowLeft className="mr-2 h-4 w-4" />
							Back
						</Button>
						<Button
							onClick={() => navigate(`/apps/${slugParam}/edit`)}
						>
							Open Editor
						</Button>
					</CardContent>
				</Card>
			</div>
		);
	}

	// Render the application
	return (
		<div className="min-h-screen bg-background" style={embedThemeStyles}>
			{/* Preview Banner - not shown in embed mode */}
			{preview && !embed && (
				<div className="bg-amber-500 text-amber-950 px-4 py-2 text-center text-sm font-medium">
					Preview Mode - This is the draft version
					<Button
						variant="link"
						size="sm"
						className="ml-2 text-amber-950 underline"
						onClick={() => navigate(`/apps/${slugParam}/edit`)}
					>
						Back to Editor
					</Button>
				</div>
			)}

			{/* Application Content - minimal padding in embed mode */}
			<div className={embed ? "p-4" : "p-6"}>
				<AppRenderer
					definition={currentPage || appDefinition}
					pageId={currentPage?.id}
					onTriggerWorkflow={handleTriggerWorkflow}
					executeWorkflow={executeWorkflow}
					onRefreshTable={handleRefreshTable}
					workflowResult={workflowResult}
				/>
			</div>

			{/* Workflow Parameters Modal */}
			<WorkflowExecutionModal
				pending={pendingWorkflow}
				isExecuting={executeWorkflowMutation.isPending}
			/>
		</div>
	);
}

/**
 * Preview wrapper component
 */
export function ApplicationPreview() {
	return <ApplicationRunner preview />;
}

/**
 * Embed wrapper component for iframe embedding
 * Minimal chrome, no navigation bars
 */
export function ApplicationEmbed() {
	return <ApplicationRunner embed />;
}
