/**
 * Overview tab for an agent's detail page.
 *
 * Composes:
 *   - per-agent stats summary (re-uses formatting from FleetStats)
 *   - recent runs list (RunCard mini)
 *   - flagged runs (NeedsReviewCard) on the side
 *
 * Loading + empty states are surfaced inline so the parent page stays
 * presentational.
 */

import { Link } from "react-router-dom";
import { Activity } from "lucide-react";

import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { NeedsReviewCard } from "@/components/agents/NeedsReviewCard";
import { RunCard } from "@/components/agents/RunCard";
import { useAgentRuns } from "@/services/agentRuns";
import { useAgentStats } from "@/services/agents";
import {
	cn,
	formatCost,
	formatDuration,
	formatNumber,
} from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];

export interface AgentOverviewTabProps {
	agentId: string;
}

export function AgentOverviewTab({ agentId }: AgentOverviewTabProps) {
	const { data: stats, isLoading: statsLoading } = useAgentStats(agentId);
	const { data: runsList, isLoading: runsLoading } = useAgentRuns({
		agentId,
		limit: 10,
	});

	const recentRuns = (runsList?.items ?? []) as unknown as AgentRun[];
	const flaggedRuns = recentRuns.filter((r) => r.verdict === "down");

	return (
		<div className="grid gap-4 lg:grid-cols-[1fr_320px]">
			<div className="flex flex-col gap-4">
				{/* Stats strip */}
				{statsLoading ? (
					<div className="grid grid-cols-2 gap-3 md:grid-cols-4">
						{[...Array(4)].map((_, i) => (
							<Skeleton key={i} className="h-20 w-full" />
						))}
					</div>
				) : stats ? (
					<div className="grid grid-cols-2 gap-3 md:grid-cols-4">
						<MiniStat
							label="Runs (7d)"
							value={formatNumber(stats.runs_7d)}
						/>
						<MiniStat
							label="Success rate"
							value={`${Math.round(stats.success_rate * 100)}%`}
							valueClass={successColor(stats.success_rate)}
						/>
						<MiniStat
							label="Avg duration"
							value={formatDuration(stats.avg_duration_ms)}
						/>
						<MiniStat
							label="Spend (7d)"
							value={formatCost(stats.total_cost_7d)}
						/>
					</div>
				) : null}

				{/* Recent activity */}
				<Card>
					<CardHeader className="pb-3">
						<div className="flex items-center justify-between">
							<CardTitle className="flex items-center gap-2 text-base">
								<Activity className="h-4 w-4" /> Recent activity
							</CardTitle>
							<Link
								to={`/agents/${agentId}?tab=runs`}
								className="text-xs text-primary hover:underline"
							>
								View all runs →
							</Link>
						</div>
					</CardHeader>
					<CardContent className="flex flex-col gap-2">
						{runsLoading ? (
							<>
								<Skeleton className="h-12 w-full" />
								<Skeleton className="h-12 w-full" />
								<Skeleton className="h-12 w-full" />
							</>
						) : recentRuns.length === 0 ? (
							<p className="py-4 text-center text-sm text-muted-foreground">
								No runs yet for this agent.
							</p>
						) : (
							recentRuns
								.slice(0, 6)
								.map((r) => (
									<RunCard
										key={r.id}
										run={r}
										verdict={
											(r.verdict as
												| "up"
												| "down"
												| null) ?? null
										}
									/>
								))
						)}
					</CardContent>
				</Card>
			</div>

			{/* Side: needs-review */}
			<div className="flex flex-col gap-3">
				<h3 className="text-sm font-medium text-muted-foreground">
					Needs review
				</h3>
				{runsLoading ? (
					<Skeleton className="h-24 w-full" />
				) : flaggedRuns.length === 0 ? (
					<Card>
						<CardContent className="py-6 text-center text-sm text-muted-foreground">
							No flagged runs.
						</CardContent>
					</Card>
				) : (
					flaggedRuns.map((r) => (
						<NeedsReviewCard key={r.id} run={r} />
					))
				)}
			</div>
		</div>
	);
}

function MiniStat({
	label,
	value,
	valueClass,
}: {
	label: string;
	value: string;
	valueClass?: string;
}) {
	return (
		<div className="rounded-lg border bg-card p-3">
			<div className="text-xs text-muted-foreground">{label}</div>
			<div
				className={cn(
					"mt-1 text-2xl font-semibold tabular-nums",
					valueClass,
				)}
			>
				{value}
			</div>
		</div>
	);
}

function successColor(rate: number): string | undefined {
	if (rate >= 0.9) return "text-emerald-600 dark:text-emerald-400";
	if (rate >= 0.75) return "text-yellow-600 dark:text-yellow-400";
	return "text-rose-600 dark:text-rose-400";
}
