/**
 * Workspace Maintenance Settings
 *
 * Platform admin page for managing workspace maintenance operations.
 * Provides tools for reindexing, SDK reference scanning, and docs indexing.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import {
	AlertCircle,
	CheckCircle2,
	Loader2,
	RefreshCw,
	FileCode,
	AlertTriangle,
	Settings2,
	Database,
	Search,
	AppWindow,
	Download,
	Upload,
	Play,
} from "lucide-react";
import { toast } from "sonner";
import { authFetch } from "@/lib/api-client";
import { exportAll } from "@/services/exportImport";
import { ImportDialog } from "@/components/ImportDialog";
import { useEditorStore } from "@/stores/editorStore";
import { fileService } from "@/services/fileService";
import {
	webSocketService,
	type ReindexMessage,
	type ReindexProgress,
	type ReindexCompleted,
	type ReindexFailed,
} from "@/services/websocket";
import type { components } from "@/lib/v1";

type FileMetadata = components["schemas"]["FileMetadata"];

interface ReindexJobResponse {
	status: "queued";
	job_id: string;
}

interface SDKIssue {
	file_path: string;
	line_number: number;
	issue_type: "config" | "integration";
	key: string;
}

interface SDKScanResponse {
	files_scanned: number;
	issues_found: number;
	issues: SDKIssue[];
	notification_created: boolean;
}

interface DocsIndexResponse {
	status: string;
	files_indexed: number;
	files_unchanged: number;
	files_deleted: number;
	duration_ms: number;
	message: string | null;
}

interface AppDependencyIssue {
	app_id: string;
	app_name: string;
	app_slug: string;
	file_path: string;
	dependency_type: string;
	dependency_id: string;
}

interface AppDependencyScanResponse {
	apps_scanned: number;
	files_scanned: number;
	dependencies_rebuilt: number;
	issues_found: number;
	issues: AppDependencyIssue[];
	notification_created: boolean;
}

// Reindex streaming state
interface ReindexState {
	jobId: string | null;
	phase: string;
	current: number;
	total: number;
	currentFile: string | null;
}

// Completed reindex result (from WebSocket)
interface ReindexResult {
	counts: ReindexCompleted["counts"];
	warnings: string[];
	errors: ReindexCompleted["errors"];
}

type ScanResultType = "none" | "reindex" | "sdk" | "docs" | "app-deps";

export function Maintenance() {
	// Checklist state
	const [selectedActions, setSelectedActions] = useState<Set<string>>(new Set());
	const [runningAction, setRunningAction] = useState<string | null>(null);
	const [completedActions, setCompletedActions] = useState<Set<string>>(new Set());
	const [actionQueue, setActionQueue] = useState<string[]>([]);

	// Reindex streaming state
	const [reindexState, setReindexState] = useState<ReindexState>({
		jobId: null,
		phase: "",
		current: 0,
		total: 0,
		currentFile: null,
	});
	const unsubscribeRef = useRef<(() => void) | null>(null);

	// Results
	const [lastScanType, setLastScanType] = useState<ScanResultType>("none");
	const [reindexResult, setReindexResult] = useState<ReindexResult | null>(
		null,
	);
	const [sdkScanResult, setSdkScanResult] = useState<SDKScanResponse | null>(
		null,
	);
	const [docsIndexResult, setDocsIndexResult] =
		useState<DocsIndexResponse | null>(null);
	const [appDepScanResult, setAppDepScanResult] =
		useState<AppDependencyScanResponse | null>(null);

	const [isExportingAll, setIsExportingAll] = useState(false);
	const [isImportAllOpen, setIsImportAllOpen] = useState(false);

	const isAnyRunning = runningAction !== null;

	// Cleanup WebSocket subscription on unmount
	useEffect(() => {
		return () => {
			if (unsubscribeRef.current) {
				unsubscribeRef.current();
			}
		};
	}, []);

	// Editor store actions
	const openFileInTab = useEditorStore((state) => state.openFileInTab);
	const openEditor = useEditorStore((state) => state.openEditor);
	const revealLine = useEditorStore((state) => state.revealLine);

	const openFileInEditor = useCallback(
		async (filePath: string, lineNumber?: number) => {
			try {
				// Get file name and extension from path
				const fileName = filePath.split("/").pop() || filePath;
				const extension = fileName.includes(".")
					? fileName.split(".").pop() || ""
					: "";

				// Create minimal FileMetadata
				const fileMetadata: FileMetadata = {
					name: fileName,
					path: filePath,
					type: "file",
					size: 0,
					extension,
					modified: new Date().toISOString(),
					entity_type: null,
					entity_id: null,
				};

				// Fetch file content
				const response = await fileService.readFile(filePath);

				// Open in editor
				openFileInTab(
					fileMetadata,
					response.content,
					response.encoding as "utf-8" | "base64",
					response.etag,
				);

				// Queue line reveal if provided (will execute after editor loads)
				if (lineNumber) {
					revealLine(lineNumber);
				}

				openEditor();

				toast.success("Opened in editor");
			} catch (err) {
				toast.error("Failed to open file", {
					description:
						err instanceof Error ? err.message : "Unknown error",
				});
			}
		},
		[openFileInTab, openEditor, revealLine],
	);

	const finishAction = useCallback((actionId: string) => {
		setCompletedActions((prev) => new Set([...prev, actionId]));
		setRunningAction(null);
	}, []);

	const handleReindexMessage = useCallback((message: ReindexMessage) => {
		switch (message.type) {
			case "progress": {
				const progress = message as ReindexProgress;
				setReindexState((prev) => ({
					...prev,
					phase: progress.phase,
					current: progress.current,
					total: progress.total,
					currentFile: progress.current_file ?? null,
				}));
				break;
			}
			case "completed": {
				const completed = message as ReindexCompleted;
				setReindexResult({
					counts: completed.counts,
					warnings: completed.warnings,
					errors: completed.errors,
				});
				setLastScanType("reindex");
				finishAction("reindex");
				setReindexState({
					jobId: null,
					phase: "",
					current: 0,
					total: 0,
					currentFile: null,
				});

				// Cleanup subscription
				if (unsubscribeRef.current) {
					unsubscribeRef.current();
					unsubscribeRef.current = null;
				}

				const hasErrors = completed.errors.length > 0;
				const hasWarnings = completed.warnings.length > 0;

				if (hasErrors) {
					toast.warning("Reindex completed with errors", {
						description: `${completed.errors.length} unresolved reference${completed.errors.length !== 1 ? "s" : ""}`,
					});
				} else if (hasWarnings) {
					toast.success("Reindex complete", {
						description: `${completed.warnings.length} correction${completed.warnings.length !== 1 ? "s" : ""} made`,
					});
				} else {
					toast.success("Reindex complete", {
						description: `Indexed ${completed.counts.files_indexed} files`,
					});
				}
				break;
			}
			case "failed": {
				const failed = message as ReindexFailed;
				finishAction("reindex");
				setReindexState({
					jobId: null,
					phase: "",
					current: 0,
					total: 0,
					currentFile: null,
				});

				// Cleanup subscription
				if (unsubscribeRef.current) {
					unsubscribeRef.current();
					unsubscribeRef.current = null;
				}

				toast.error("Reindex failed", {
					description: failed.error,
				});
				break;
			}
		}
	}, [finishAction]);

	const handleReindex = async () => {
		setRunningAction("reindex");
		setReindexResult(null);

		try {
			const response = await authFetch("/api/maintenance/reindex", {
				method: "POST",
				body: JSON.stringify({}),
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				toast.error("Reindex failed", {
					description: errorData.detail || "Unknown error",
				});
				finishAction("reindex");
				return;
			}

			const data: ReindexJobResponse = await response.json();

			// Update state with job ID
			setReindexState({
				jobId: data.job_id,
				phase: "Queued",
				current: 0,
				total: 0,
				currentFile: null,
			});

			// Connect to WebSocket for progress updates
			await webSocketService.connectToReindex(data.job_id);

			// Subscribe to progress updates
			unsubscribeRef.current = webSocketService.onReindexProgress(
				data.job_id,
				handleReindexMessage,
			);

			toast.info("Reindex started", {
				description: "Processing workspace files...",
			});
		} catch (err) {
			toast.error("Reindex failed", {
				description:
					err instanceof Error
						? err.message
						: "Unknown error occurred",
			});
			finishAction("reindex");
		}
	};

	const handleSdkScan = async () => {
		setRunningAction("sdk");

		try {
			const response = await authFetch("/api/maintenance/scan-sdk", {
				method: "POST",
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				toast.error("SDK scan failed", {
					description: errorData.detail || "Unknown error",
				});
				return;
			}

			const data: SDKScanResponse = await response.json();
			setSdkScanResult(data);
			setLastScanType("sdk");

			if (data.issues_found === 0) {
				toast.success("No SDK issues found", {
					description: `Scanned ${data.files_scanned} files`,
				});
			} else {
				toast.warning("SDK issues found", {
					description: `Found ${data.issues_found} missing references in ${data.files_scanned} files`,
				});
			}
		} catch (err) {
			toast.error("SDK scan failed", {
				description:
					err instanceof Error
						? err.message
						: "Unknown error occurred",
			});
		} finally {
			finishAction("sdk");
		}
	};

	const handleDocsIndex = async () => {
		setRunningAction("docs");

		try {
			const response = await authFetch("/api/maintenance/index-docs", {
				method: "POST",
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				toast.error("Documentation indexing failed", {
					description: errorData.detail || "Unknown error",
				});
				return;
			}

			const data: DocsIndexResponse = await response.json();
			setDocsIndexResult(data);
			setLastScanType("docs");

			if (data.status === "complete") {
				toast.success("Documentation indexed successfully", {
					description: data.message,
				});
			} else if (data.status === "skipped") {
				toast.info("Documentation indexing skipped", {
					description: data.message,
				});
			} else {
				toast.error("Documentation indexing failed", {
					description: data.message || "Unknown error",
				});
			}
		} catch (err) {
			toast.error("Documentation indexing failed", {
				description:
					err instanceof Error
						? err.message
						: "Unknown error occurred",
			});
		} finally {
			finishAction("docs");
		}
	};

	const handleAppDepScan = async () => {
		setRunningAction("app-deps");

		try {
			const response = await authFetch("/api/maintenance/scan-app-dependencies", {
				method: "POST",
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				toast.error("App dependency scan failed", {
					description: errorData.detail || "Unknown error",
				});
				return;
			}

			const data: AppDependencyScanResponse = await response.json();
			setAppDepScanResult(data);
			setLastScanType("app-deps");

			if (data.issues_found === 0) {
				toast.success("Dependencies rebuilt successfully", {
					description: `Scanned ${data.apps_scanned} apps, ${data.files_scanned} files, rebuilt ${data.dependencies_rebuilt} dependencies`,
				});
			} else {
				toast.warning("Dependencies rebuilt with issues", {
					description: `Rebuilt ${data.dependencies_rebuilt} dependencies, found ${data.issues_found} broken references`,
				});
			}
		} catch (err) {
			toast.error("App dependency scan failed", {
				description:
					err instanceof Error
						? err.message
						: "Unknown error occurred",
			});
		} finally {
			finishAction("app-deps");
		}
	};

	const handleExportAll = async () => {
		setIsExportingAll(true);
		try {
			await exportAll({});
			toast.success("Export downloaded");
		} catch {
			toast.error("Export failed");
		} finally {
			setIsExportingAll(false);
		}
	};

	const toggleAction = (id: string) => {
		setSelectedActions((prev) => {
			const next = new Set(prev);
			if (next.has(id)) {
				next.delete(id);
			} else {
				next.add(id);
			}
			return next;
		});
	};

	// Process the queue - runs next action when current one finishes
	useEffect(() => {
		if (runningAction !== null || actionQueue.length === 0) return;

		const [next, ...rest] = actionQueue;
		setActionQueue(rest);

		const handlers: Record<string, () => Promise<void>> = {
			reindex: handleReindex,
			sdk: handleSdkScan,
			docs: handleDocsIndex,
			"app-deps": handleAppDepScan,
		};

		handlers[next]?.();
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [runningAction, actionQueue]);

	const handleRunSelected = () => {
		if (selectedActions.size === 0) return;
		setCompletedActions(new Set());
		// Reindex first since it's the longest, then the rest in order
		const order = ["reindex", "sdk", "docs", "app-deps"];
		const queue = order.filter((id) => selectedActions.has(id));
		setActionQueue(queue);
	};

	const actions = [
		{
			id: "reindex",
			icon: RefreshCw,
			label: "Reindex Workspace",
			description: "Re-scan workspace files for metadata",
		},
		{
			id: "sdk",
			icon: Search,
			label: "Scan SDK References",
			description: "Find missing config/integration references",
		},
		{
			id: "docs",
			icon: Database,
			label: "Index Documents",
			description: "Index platform docs into knowledge store",
		},
		{
			id: "app-deps",
			icon: AppWindow,
			label: "Rebuild App Dependencies",
			description: "Rebuild app dependency graph",
		},
	];

	return (
		<div className="space-y-6">
			{/* Export/Import Card */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Download className="h-5 w-5" />
						Export / Import
					</CardTitle>
					<CardDescription>
						Export all platform data as a ZIP archive or import from a previous export
					</CardDescription>
				</CardHeader>
				<CardContent>
					<div className="flex items-center gap-4">
						<Button
							onClick={handleExportAll}
							disabled={isExportingAll}
						>
							{isExportingAll ? (
								<Loader2 className="h-4 w-4 mr-2 animate-spin" />
							) : (
								<Download className="h-4 w-4 mr-2" />
							)}
							Export All
						</Button>
						<Button
							variant="outline"
							onClick={() => setIsImportAllOpen(true)}
						>
							<Upload className="h-4 w-4 mr-2" />
							Import All
						</Button>
					</div>
				</CardContent>
			</Card>

			{/* Actions Card */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Settings2 className="h-5 w-5" />
						Maintenance Actions
					</CardTitle>
					<CardDescription>
						Select actions and run them sequentially
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="rounded-md border divide-y">
						{actions.map((action) => {
							const Icon = action.icon;
							const isRunning = runningAction === action.id;
							const isCompleted = completedActions.has(action.id);
							const isQueued = actionQueue.includes(action.id);

							return (
								<div key={action.id}>
									<label
										htmlFor={`action-${action.id}`}
										className={`flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/50 ${
											isRunning ? "bg-muted/50" : ""
										}`}
									>
										{isRunning ? (
											<Loader2 className="h-4 w-4 animate-spin text-primary flex-shrink-0" />
										) : isCompleted ? (
											<CheckCircle2 className="h-4 w-4 text-green-600 flex-shrink-0" />
										) : (
											<Checkbox
												id={`action-${action.id}`}
												checked={selectedActions.has(action.id)}
												onCheckedChange={() => toggleAction(action.id)}
												disabled={isAnyRunning}
											/>
										)}
										<Icon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
										<div className="min-w-0">
											<p className="text-sm font-medium">
												{action.label}
												{isQueued && (
													<span className="text-xs text-muted-foreground ml-2">queued</span>
												)}
											</p>
											<p className="text-xs text-muted-foreground">
												{action.description}
											</p>
										</div>
									</label>

									{/* Reindex progress inline */}
									{isRunning && action.id === "reindex" && reindexState.jobId && (
										<div className="px-4 pb-3 space-y-2">
											<div className="flex items-center justify-between text-xs">
												<span className="font-medium capitalize">
													{reindexState.phase.replace(/_/g, " ") || "Starting..."}
												</span>
												{reindexState.total > 0 && (
													<span className="text-muted-foreground">
														{reindexState.current} / {reindexState.total}
													</span>
												)}
											</div>
											{reindexState.total > 0 && (
												<Progress
													value={(reindexState.current / reindexState.total) * 100}
													className="h-1.5"
												/>
											)}
											{reindexState.currentFile && (
												<p className="text-xs text-muted-foreground truncate font-mono">
													{reindexState.currentFile}
												</p>
											)}
										</div>
									)}
								</div>
							);
						})}
					</div>

					<Button
						onClick={handleRunSelected}
						disabled={selectedActions.size === 0 || isAnyRunning}
					>
						{isAnyRunning ? (
							<Loader2 className="h-4 w-4 mr-2 animate-spin" />
						) : (
							<Play className="h-4 w-4 mr-2" />
						)}
						{isAnyRunning
							? "Running..."
							: `Run Selected (${selectedActions.size})`}
					</Button>
				</CardContent>
			</Card>

			{/* Results Card */}
			<Card>
				<CardHeader>
					<CardTitle>Scan Results</CardTitle>
					<CardDescription>
						Results from the most recent scan operation
					</CardDescription>
				</CardHeader>
				<CardContent>
					{lastScanType === "none" ? (
						<div className="flex items-center justify-center py-8 text-muted-foreground">
							<p>No scan results yet. Run a scan above.</p>
						</div>
					) : lastScanType === "reindex" && reindexResult ? (
						<ReindexResults
							result={reindexResult}
							onOpenFile={openFileInEditor}
						/>
					) : lastScanType === "sdk" && sdkScanResult ? (
						<SdkScanResults
							result={sdkScanResult}
							onOpenFile={openFileInEditor}
						/>
					) : lastScanType === "docs" && docsIndexResult ? (
						<DocsIndexResults result={docsIndexResult} />
					) : lastScanType === "app-deps" && appDepScanResult ? (
						<AppDepScanResults result={appDepScanResult} />
					) : null}
				</CardContent>
			</Card>

			<ImportDialog
				open={isImportAllOpen}
				onOpenChange={setIsImportAllOpen}
				entityType="all"
			/>
		</div>
	);
}

function ReindexResults({
	result,
	onOpenFile,
}: {
	result: ReindexResult;
	onOpenFile: (path: string, line?: number) => void;
}) {
	const hasErrors = result.errors.length > 0;
	const hasWarnings = result.warnings.length > 0;

	return (
		<div className="space-y-4">
			{/* Summary */}
			<div className="flex items-center gap-4 flex-wrap">
				{hasErrors ? (
					<div className="flex items-center gap-2 text-destructive">
						<AlertCircle className="h-5 w-5" />
						<span className="font-medium">
							{result.errors.length} unresolved reference
							{result.errors.length !== 1 ? "s" : ""}
						</span>
					</div>
				) : hasWarnings ? (
					<div className="flex items-center gap-2 text-amber-600">
						<AlertTriangle className="h-5 w-5" />
						<span className="font-medium">
							{result.warnings.length} correction
							{result.warnings.length !== 1 ? "s" : ""} made
						</span>
					</div>
				) : (
					<div className="flex items-center gap-2 text-green-600">
						<CheckCircle2 className="h-5 w-5" />
						<span className="font-medium">
							All references validated
						</span>
					</div>
				)}
			</div>

			{/* Stats grid */}
			<div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
				<div className="rounded-lg border bg-muted/50 p-3 text-center">
					<div className="text-xl font-bold">
						{result.counts.files_indexed}
					</div>
					<div className="text-xs text-muted-foreground">
						Files Indexed
					</div>
				</div>
				<div className="rounded-lg border bg-muted/50 p-3 text-center">
					<div className="text-xl font-bold">
						{result.counts.files_skipped}
					</div>
					<div className="text-xs text-muted-foreground">Skipped</div>
				</div>
				<div className="rounded-lg border bg-muted/50 p-3 text-center">
					<div className="text-xl font-bold">
						{result.counts.workflows_active}
					</div>
					<div className="text-xs text-muted-foreground">
						Workflows
					</div>
				</div>
				<div className="rounded-lg border bg-muted/50 p-3 text-center">
					<div className="text-xl font-bold">
						{result.counts.forms_active}
					</div>
					<div className="text-xs text-muted-foreground">Forms</div>
				</div>
				<div className="rounded-lg border bg-muted/50 p-3 text-center">
					<div className="text-xl font-bold">
						{result.counts.agents_active}
					</div>
					<div className="text-xs text-muted-foreground">Agents</div>
				</div>
				<div className="rounded-lg border bg-muted/50 p-3 text-center">
					<div className="text-xl font-bold">
						{result.counts.files_deleted}
					</div>
					<div className="text-xs text-muted-foreground">Deleted</div>
				</div>
			</div>

			{/* Errors */}
			{hasErrors && (
				<div className="space-y-2">
					<h4 className="text-sm font-medium text-destructive flex items-center gap-2">
						<AlertCircle className="h-4 w-4" />
						Unresolved References (requires action)
					</h4>
					<div className="max-h-64 overflow-y-auto rounded-md border border-destructive/30 bg-destructive/5 p-3 space-y-3">
						{result.errors.map((error, idx) => (
							<div
								key={`${error.file_path}-${error.field}-${idx}`}
								className="space-y-1"
							>
								<div className="flex items-center gap-2 text-sm font-mono">
									<FileCode className="h-4 w-4 text-destructive flex-shrink-0" />
									<button
										type="button"
										onClick={() =>
											onOpenFile(error.file_path)
										}
										className="truncate text-left hover:text-primary hover:underline"
									>
										{error.file_path}
									</button>
								</div>
								<div className="ml-6 text-xs text-muted-foreground space-y-0.5">
									<p>
										<span className="font-medium">
											Field:
										</span>{" "}
										{error.field}
									</p>
									<p>
										<span className="font-medium">
											References:
										</span>{" "}
										<code className="bg-muted px-1 py-0.5 rounded">
											{error.referenced_id}
										</code>
									</p>
									<p className="text-destructive">
										{error.message}
									</p>
								</div>
							</div>
						))}
					</div>
				</div>
			)}

			{/* Warnings */}
			{hasWarnings && (
				<div className="space-y-2">
					<h4 className="text-sm font-medium text-amber-600 flex items-center gap-2">
						<AlertTriangle className="h-4 w-4" />
						Corrections Made
					</h4>
					<div className="max-h-48 overflow-y-auto rounded-md border border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/50 p-3">
						<ul className="space-y-1 text-sm">
							{result.warnings.map((warning, idx) => (
								<li
									key={idx}
									className="text-amber-800 dark:text-amber-200"
								>
									{warning}
								</li>
							))}
						</ul>
					</div>
				</div>
			)}
		</div>
	);
}

function SdkScanResults({
	result,
	onOpenFile,
}: {
	result: SDKScanResponse;
	onOpenFile: (path: string, line?: number) => void;
}) {
	const hasIssues = result.issues_found > 0;

	// Group issues by file
	const issuesByFile = result.issues.reduce(
		(acc, issue) => {
			if (!acc[issue.file_path]) {
				acc[issue.file_path] = [];
			}
			acc[issue.file_path].push(issue);
			return acc;
		},
		{} as Record<string, SDKIssue[]>,
	);

	return (
		<div className="space-y-4">
			{/* Summary */}
			<div className="flex items-center gap-4">
				{hasIssues ? (
					<div className="flex items-center gap-2 text-amber-600">
						<AlertTriangle className="h-5 w-5" />
						<span className="font-medium">
							{result.issues_found} missing reference
							{result.issues_found !== 1 ? "s" : ""}
						</span>
					</div>
				) : (
					<div className="flex items-center gap-2 text-green-600">
						<CheckCircle2 className="h-5 w-5" />
						<span className="font-medium">
							No missing references
						</span>
					</div>
				)}
				<Badge variant="secondary">
					{result.files_scanned} file
					{result.files_scanned !== 1 ? "s" : ""} scanned
				</Badge>
			</div>

			{/* Issues by file */}
			{hasIssues && (
				<div className="space-y-2">
					<h4 className="text-sm font-medium text-muted-foreground">
						Missing SDK references:
					</h4>
					<div className="max-h-64 overflow-y-auto rounded-md border bg-muted/50 p-3 space-y-3">
						{Object.entries(issuesByFile).map(
							([filePath, issues]) => (
								<div key={filePath} className="space-y-1">
									<div className="flex items-center gap-2 text-sm font-mono font-medium">
										<FileCode className="h-4 w-4 text-muted-foreground flex-shrink-0" />
										<button
											type="button"
											onClick={() =>
												onOpenFile(
													filePath,
													issues[0]?.line_number,
												)
											}
											className="truncate text-left hover:text-primary hover:underline"
										>
											{filePath}
										</button>
									</div>
									<ul className="ml-6 space-y-0.5">
										{issues.map((issue, idx) => (
											<li
												key={`${issue.key}-${idx}`}
												className="text-sm text-muted-foreground flex items-center gap-2"
											>
												<Badge
													variant="outline"
													className="text-xs px-1.5 py-0"
												>
													{issue.issue_type}
												</Badge>
												<code className="text-xs bg-muted px-1 py-0.5 rounded">
													{issue.key}
												</code>
												<button
													type="button"
													onClick={() =>
														onOpenFile(
															filePath,
															issue.line_number,
														)
													}
													className="text-xs hover:text-primary hover:underline"
												>
													line {issue.line_number}
												</button>
											</li>
										))}
									</ul>
								</div>
							),
						)}
					</div>
				</div>
			)}
		</div>
	);
}

function DocsIndexResults({ result }: { result: DocsIndexResponse }) {
	const isSuccess = result.status === "complete";
	const isSkipped = result.status === "skipped";

	const formatDuration = (ms: number) => {
		if (ms < 1000) return `${ms}ms`;
		return `${(ms / 1000).toFixed(1)}s`;
	};

	return (
		<div className="space-y-4">
			{/* Summary */}
			<div className="flex items-center gap-4 flex-wrap">
				{isSuccess ? (
					<div className="flex items-center gap-2 text-green-600">
						<CheckCircle2 className="h-5 w-5" />
						<span className="font-medium">Indexing complete</span>
					</div>
				) : isSkipped ? (
					<div className="flex items-center gap-2 text-amber-600">
						<AlertCircle className="h-5 w-5" />
						<span className="font-medium">Indexing skipped</span>
					</div>
				) : (
					<div className="flex items-center gap-2 text-destructive">
						<AlertTriangle className="h-5 w-5" />
						<span className="font-medium">Indexing failed</span>
					</div>
				)}
			</div>

			{/* Stats */}
			{isSuccess && (
				<div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
					<div className="rounded-lg border bg-muted/50 p-3 text-center">
						<div className="text-2xl font-bold">
							{result.files_indexed}
						</div>
						<div className="text-xs text-muted-foreground">
							Indexed
						</div>
					</div>
					<div className="rounded-lg border bg-muted/50 p-3 text-center">
						<div className="text-2xl font-bold">
							{result.files_unchanged}
						</div>
						<div className="text-xs text-muted-foreground">
							Unchanged
						</div>
					</div>
					<div className="rounded-lg border bg-muted/50 p-3 text-center">
						<div className="text-2xl font-bold">
							{result.files_deleted}
						</div>
						<div className="text-xs text-muted-foreground">
							Deleted
						</div>
					</div>
					<div className="rounded-lg border bg-muted/50 p-3 text-center">
						<div className="text-2xl font-bold">
							{formatDuration(result.duration_ms)}
						</div>
						<div className="text-xs text-muted-foreground">
							Duration
						</div>
					</div>
				</div>
			)}

			{/* Message */}
			{result.message && (
				<p className="text-sm text-muted-foreground">
					{result.message}
				</p>
			)}
		</div>
	);
}

function AppDepScanResults({ result }: { result: AppDependencyScanResponse }) {
	const hasIssues = result.issues_found > 0;

	// Group issues by app
	const issuesByApp = result.issues.reduce(
		(acc, issue) => {
			const key = issue.app_slug;
			if (!acc[key]) {
				acc[key] = {
					app_name: issue.app_name,
					app_slug: issue.app_slug,
					issues: [],
				};
			}
			acc[key].issues.push(issue);
			return acc;
		},
		{} as Record<
			string,
			{
				app_name: string;
				app_slug: string;
				issues: AppDependencyIssue[];
			}
		>,
	);

	return (
		<div className="space-y-4">
			{/* Summary */}
			<div className="flex items-center gap-4 flex-wrap">
				{hasIssues ? (
					<div className="flex items-center gap-2 text-amber-600">
						<AlertTriangle className="h-5 w-5" />
						<span className="font-medium">
							{result.issues_found} broken reference
							{result.issues_found !== 1 ? "s" : ""}
						</span>
					</div>
				) : (
					<div className="flex items-center gap-2 text-green-600">
						<CheckCircle2 className="h-5 w-5" />
						<span className="font-medium">
							All dependencies valid
						</span>
					</div>
				)}
				<Badge variant="secondary">
					{result.apps_scanned} app
					{result.apps_scanned !== 1 ? "s" : ""} scanned
				</Badge>
				<Badge variant="outline">
					{result.files_scanned} file
					{result.files_scanned !== 1 ? "s" : ""}
				</Badge>
				<Badge variant="outline">
					{result.dependencies_rebuilt} dependenc
					{result.dependencies_rebuilt !== 1 ? "ies" : "y"} rebuilt
				</Badge>
			</div>

			{/* Issues by app */}
			{hasIssues && (
				<div className="space-y-2">
					<h4 className="text-sm font-medium text-muted-foreground">
						Missing workflow references:
					</h4>
					<div className="max-h-64 overflow-y-auto rounded-md border bg-muted/50 p-3 space-y-3">
						{Object.entries(issuesByApp).map(
							([appSlug, { app_name, issues }]) => (
								<div key={appSlug} className="space-y-1">
									<div className="flex items-center gap-2 text-sm font-medium">
										<AppWindow className="h-4 w-4 text-muted-foreground flex-shrink-0" />
										<span>{app_name}</span>
										<Badge
											variant="outline"
											className="text-xs px-1.5 py-0"
										>
											{appSlug}
										</Badge>
									</div>
									<ul className="ml-6 space-y-1">
										{issues.map((issue, idx) => (
											<li
												key={`${issue.dependency_id}-${idx}`}
												className="text-sm text-muted-foreground"
											>
												<span className="font-mono text-xs">
													{issue.file_path}
												</span>
												<span className="mx-1">→</span>
												<code className="bg-destructive/10 text-destructive px-1 py-0.5 rounded text-xs">
													{issue.dependency_type}:{" "}
													{issue.dependency_id}
												</code>
											</li>
										))}
									</ul>
								</div>
							),
						)}
					</div>
				</div>
			)}
		</div>
	);
}
