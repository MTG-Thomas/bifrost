import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, Sparkles, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PrettyInputDisplay } from "./PrettyInputDisplay";
import { ExecutionStatusBadge, ExecutionStatusIcon } from "./ExecutionStatusBadge";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import {
	formatDate,
	formatBytes,
	formatNumber,
	formatCost,
	formatDuration,
} from "@/lib/utils";
import type { components } from "@/lib/v1";

type ExecutionStatus =
	| components["schemas"]["ExecutionStatus"]
	| "Cancelling"
	| "Cancelled";
type AIUsagePublicSimple = components["schemas"]["AIUsagePublicSimple"];

interface ExecutionSidebarProps {
	/** Current execution status */
	status: ExecutionStatus;
	/** Workflow name */
	workflowName: string;
	/** Who executed the workflow */
	executedByName?: string | null;
	/** Organization name (effective scope) */
	orgName?: string | null;
	/** Start timestamp */
	startedAt?: string | null;
	/** Completion timestamp */
	completedAt?: string | null;
	/** Input parameters passed to the workflow */
	inputData?: unknown;
	/** Whether the execution is complete */
	isComplete: boolean;
	/** Whether the current user is a platform admin */
	isPlatformAdmin: boolean;
	/** Whether data is still loading */
	isLoading: boolean;
	/** Runtime variables (admin only) */
	variablesData?: Record<string, unknown>;
	/** Peak memory usage in bytes */
	peakMemoryBytes?: number | null;
	/** CPU time in seconds */
	cpuTotalSeconds?: number | null;
	/** Duration in milliseconds */
	durationMs?: number | null;
	/** AI usage data */
	aiUsage?: AIUsagePublicSimple[] | null;
	/** AI usage totals */
	aiTotals?: {
		call_count?: number;
		total_input_tokens: number;
		total_output_tokens: number;
		total_cost?: string | number | null;
		total_duration_ms?: number | null;
	} | null;
	/** Stream state for pending status badge details */
	streamState?: {
		queuePosition?: number;
		waitReason?: string;
		availableMemoryMb?: number;
		requiredMemoryMb?: number;
	};
	/** Error message from the execution */
	errorMessage?: string | null;
}

export function ExecutionSidebar({
	status,
	workflowName,
	executedByName,
	orgName,
	startedAt,
	completedAt,
	inputData,
	isComplete,
	isPlatformAdmin,
	isLoading,
	variablesData,
	peakMemoryBytes,
	cpuTotalSeconds,
	durationMs,
	aiUsage,
	aiTotals,
	streamState,
	errorMessage,
}: ExecutionSidebarProps) {
	const [isAiUsageOpen, setIsAiUsageOpen] = useState(true);

	const isLoadingVariables = isLoading;

	return (
		<div className="space-y-6">
			{/* Status Card */}
			<Card>
				<CardHeader>
					<CardTitle>Execution Status</CardTitle>
				</CardHeader>
				<CardContent>
					<div className="flex flex-col items-center justify-center py-4 text-center">
						<ExecutionStatusIcon status={status} />
						<div className="mt-4">
							<ExecutionStatusBadge
								status={status}
								queuePosition={streamState?.queuePosition}
								waitReason={streamState?.waitReason}
								availableMemoryMb={streamState?.availableMemoryMb}
								requiredMemoryMb={streamState?.requiredMemoryMb}
							/>
						</div>
					</div>
				</CardContent>
			</Card>

			{/* Error Section */}
			{errorMessage && (
				<motion.div
					initial={{ opacity: 0, y: 20 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ duration: 0.3 }}
				>
					<Card className="border-destructive">
						<CardHeader>
							<CardTitle className="flex items-center gap-2 text-destructive">
								<XCircle className="h-5 w-5" />
								Error
							</CardTitle>
							<CardDescription>
								Workflow execution failed
							</CardDescription>
						</CardHeader>
						<CardContent>
							<pre className="text-sm whitespace-pre-wrap break-words font-mono bg-destructive/10 p-4 rounded-md overflow-x-auto">
								{errorMessage}
							</pre>
						</CardContent>
					</Card>
				</motion.div>
			)}

			{/* Workflow Information Card */}
			<Card>
				<CardHeader>
					<CardTitle>Workflow Information</CardTitle>
				</CardHeader>
				<CardContent className="space-y-4">
					<div>
						<p className="text-sm font-medium text-muted-foreground">
							Workflow Name
						</p>
						<p className="font-mono text-sm mt-1">
							{workflowName}
						</p>
					</div>
					<div>
						<p className="text-sm font-medium text-muted-foreground">
							Executed By
						</p>
						<p className="text-sm mt-1">
							{executedByName}
						</p>
					</div>
					<div>
						<p className="text-sm font-medium text-muted-foreground">
							Effective Scope
						</p>
						<p className="text-sm mt-1">
							{orgName || "Global"}
						</p>
					</div>
					<div>
						<p className="text-sm font-medium text-muted-foreground">
							Started At
						</p>
						<p className="text-sm mt-1">
							{startedAt
								? formatDate(startedAt)
								: "N/A"}
						</p>
					</div>
					{completedAt && (
						<div>
							<p className="text-sm font-medium text-muted-foreground">
								Completed At
							</p>
							<p className="text-sm mt-1">
								{formatDate(completedAt)}
							</p>
						</div>
					)}
				</CardContent>
			</Card>

			{/* Input Parameters - All users */}
			<Card>
				<CardHeader>
					<CardTitle>Input Parameters</CardTitle>
					<CardDescription>
						Workflow parameters that were passed in
					</CardDescription>
				</CardHeader>
				<CardContent>
					<PrettyInputDisplay
						inputData={inputData as Record<string, unknown>}
						showToggle={true}
						defaultView="pretty"
					/>
				</CardContent>
			</Card>

			{/* Runtime Variables - Platform admins only */}
			{isPlatformAdmin && isComplete && (
				<motion.div
					initial={{ opacity: 0, y: 20 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ duration: 0.3, delay: 0.2 }}
				>
					<Card>
						<CardHeader>
							<CardTitle>Runtime Variables</CardTitle>
							<CardDescription>
								Variables captured from script
								namespace (admin only)
							</CardDescription>
						</CardHeader>
						<CardContent>
							<AnimatePresence mode="wait">
								{isLoadingVariables ? (
									<motion.div
										key="loading"
										initial={{ opacity: 0 }}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: 0.2,
										}}
										className="space-y-2"
									>
										<Skeleton className="h-4 w-full" />
										<Skeleton className="h-4 w-4/5" />
										<Skeleton className="h-4 w-3/4" />
									</motion.div>
								) : !variablesData ||
								  Object.keys(variablesData)
										.length === 0 ? (
									<motion.div
										key="empty"
										initial={{ opacity: 0 }}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: 0.2,
										}}
										className="text-center text-muted-foreground py-8"
									>
										No variables captured
									</motion.div>
								) : (
									<motion.div
										key="content"
										initial={{ opacity: 0 }}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: 0.2,
										}}
										className="overflow-x-auto"
									>
										<VariablesTreeView
											data={
												variablesData as Record<
													string,
													unknown
												>
											}
										/>
									</motion.div>
								)}
							</AnimatePresence>
						</CardContent>
					</Card>
				</motion.div>
			)}

			{/* Usage Card - Compute resources (admin) + AI usage (all users) */}
			{isComplete &&
				((isPlatformAdmin &&
					(peakMemoryBytes ||
						cpuTotalSeconds)) ||
					(aiUsage &&
						aiUsage.length > 0)) && (
					<motion.div
						initial={{ opacity: 0, y: 20 }}
						animate={{ opacity: 1, y: 0 }}
						transition={{ duration: 0.3, delay: 0.2 }}
					>
						<Card>
							<CardHeader className="pb-3">
								<CardTitle>Usage</CardTitle>
								<CardDescription>
									Execution metrics and costs
								</CardDescription>
							</CardHeader>
							<CardContent className="space-y-4">
								{/* Compute Resources - Platform admins only */}
								{isPlatformAdmin &&
									(peakMemoryBytes ||
										cpuTotalSeconds) && (
										<div className="space-y-3">
											{peakMemoryBytes && (
												<div>
													<p className="text-sm font-medium text-muted-foreground">
														Peak Memory
													</p>
													<p className="text-sm font-mono">
														{formatBytes(
															peakMemoryBytes,
														)}
													</p>
												</div>
											)}
											{cpuTotalSeconds && (
												<div>
													<p className="text-sm font-medium text-muted-foreground">
														CPU Time
													</p>
													<p className="text-sm font-mono">
														{cpuTotalSeconds.toFixed(
															3,
														)}
														s
													</p>
												</div>
											)}
											{durationMs && (
												<div>
													<p className="text-sm font-medium text-muted-foreground">
														Duration
													</p>
													<p className="text-sm font-mono">
														{(
															durationMs /
															1000
														).toFixed(
															2,
														)}
														s
													</p>
												</div>
											)}
										</div>
									)}

								{/* Divider when both sections are shown */}
								{isPlatformAdmin &&
									(peakMemoryBytes ||
										cpuTotalSeconds) &&
									aiUsage &&
									aiUsage.length >
										0 && (
										<div className="border-t pt-4" />
									)}

								{/* AI Usage - Available to all users */}
								{aiUsage &&
									aiUsage.length >
										0 && (
										<Collapsible
											open={isAiUsageOpen}
											onOpenChange={
												setIsAiUsageOpen
											}
										>
											<div className="flex items-center justify-between">
												<div className="flex items-center gap-2">
													<Sparkles className="h-4 w-4 text-purple-500" />
													<span className="text-sm font-medium">
														AI Usage
													</span>
													<Badge
														variant="secondary"
														className="text-xs"
													>
														{aiTotals
															?.call_count ||
															aiUsage
																.length}{" "}
														{(aiTotals
															?.call_count ||
															aiUsage
																.length) ===
														1
															? "call"
															: "calls"}
													</Badge>
												</div>
												<CollapsibleTrigger
													asChild
												>
													<Button
														variant="ghost"
														size="sm"
													>
														<ChevronDown
															className={`h-4 w-4 transition-transform duration-200 ${
																isAiUsageOpen
																	? "rotate-180"
																	: ""
															}`}
														/>
													</Button>
												</CollapsibleTrigger>
											</div>
											{aiTotals && (
												<p className="mt-1 text-xs text-muted-foreground">
													Total:{" "}
													{formatNumber(
														aiTotals
															.total_input_tokens,
													)}{" "}
													in /{" "}
													{formatNumber(
														aiTotals
															.total_output_tokens,
													)}{" "}
													out tokens
													{aiTotals
														.total_cost &&
														` | ${formatCost(aiTotals.total_cost)}`}
												</p>
											)}
											<CollapsibleContent>
												<div className="mt-3 overflow-x-auto">
													<table className="w-full text-xs">
														<thead>
															<tr className="border-b">
																<th className="text-left py-2 pr-2 font-medium text-muted-foreground">
																	Provider
																</th>
																<th className="text-left py-2 pr-2 font-medium text-muted-foreground">
																	Model
																</th>
																<th className="text-right py-2 pr-2 font-medium text-muted-foreground">
																	In
																</th>
																<th className="text-right py-2 pr-2 font-medium text-muted-foreground">
																	Out
																</th>
																<th className="text-right py-2 pr-2 font-medium text-muted-foreground">
																	Cost
																</th>
																<th className="text-right py-2 font-medium text-muted-foreground">
																	Time
																</th>
															</tr>
														</thead>
														<tbody>
															{(
																aiUsage as AIUsagePublicSimple[]
															).map(
																(
																	usage,
																	index,
																) => (
																	<tr
																		key={
																			index
																		}
																		className="border-b last:border-0"
																	>
																		<td className="py-2 pr-2 capitalize">
																			{
																				usage.provider
																			}
																		</td>
																		<td className="py-2 pr-2 font-mono text-muted-foreground">
																			{usage
																				.model
																				.length >
																			20
																				? `${usage.model.substring(0, 18)}...`
																				: usage.model}
																		</td>
																		<td className="py-2 pr-2 text-right font-mono">
																			{formatNumber(
																				usage.input_tokens,
																			)}
																		</td>
																		<td className="py-2 pr-2 text-right font-mono">
																			{formatNumber(
																				usage.output_tokens,
																			)}
																		</td>
																		<td className="py-2 pr-2 text-right font-mono">
																			{formatCost(
																				usage.cost,
																			)}
																		</td>
																		<td className="py-2 text-right font-mono">
																			{formatDuration(
																				usage.duration_ms,
																			)}
																		</td>
																	</tr>
																),
															)}
														</tbody>
														{aiTotals && (
															<tfoot>
																<tr className="bg-muted/50 font-medium">
																	<td
																		colSpan={
																			2
																		}
																		className="py-2 pr-2"
																	>
																		Total
																	</td>
																	<td className="py-2 pr-2 text-right font-mono">
																		{formatNumber(
																			aiTotals
																				.total_input_tokens,
																		)}
																	</td>
																	<td className="py-2 pr-2 text-right font-mono">
																		{formatNumber(
																			aiTotals
																				.total_output_tokens,
																		)}
																	</td>
																	<td className="py-2 pr-2 text-right font-mono">
																		{formatCost(
																			aiTotals
																				.total_cost,
																		)}
																	</td>
																	<td className="py-2 text-right font-mono">
																		{formatDuration(
																			aiTotals
																				.total_duration_ms,
																		)}
																	</td>
																</tr>
															</tfoot>
														)}
													</table>
												</div>
											</CollapsibleContent>
										</Collapsible>
									)}
							</CardContent>
						</Card>
					</motion.div>
				)}
		</div>
	);
}
