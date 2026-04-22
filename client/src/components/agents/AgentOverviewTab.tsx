/**
 * Overview tab for an agent's detail page.
 *
 * Layout (mirrors /tmp/agent-mockup/src/pages/AgentDetailPage.tsx `OverviewTab`):
 *   main column  →  stat row, activity sparkline card, recent activity list
 *   side column  →  needs-attention card (red), Configuration KV, Budgets KV
 */

import { Link } from "react-router-dom";
import {
	Activity,
	AlertTriangle,
	CheckCircle,
	Clock,
	Info,
	ThumbsDown,
	ThumbsUp,
	XCircle,
} from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/agents/Sparkline";
import { StatCard } from "@/components/agents/StatCard";
import { useAgent } from "@/hooks/useAgents";
import { useAgentRuns } from "@/services/agentRuns";
import { useAgentStats } from "@/services/agents";
import {
	cn,
	formatCost,
	formatDuration,
	formatNumber,
	formatRelativeTime,
} from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];

export interface AgentOverviewTabProps {
	agentId: string;
}

export function AgentOverviewTab({ agentId }: AgentOverviewTabProps) {
	const { data: agent } = useAgent(agentId);
	const { data: stats, isLoading: statsLoading } = useAgentStats(agentId);
	const { data: runsList, isLoading: runsLoading } = useAgentRuns({
		agentId,
		limit: 10,
	});

	const recentRuns = (runsList?.items ?? []) as unknown as AgentRun[];
	const needsReview = recentRuns.filter(
		(r) => r.verdict === "down" && r.status === "completed",
	).length;
	const unreviewed = recentRuns.filter(
		(r) => r.verdict == null && r.status === "completed",
	).length;

	const successRate = stats?.success_rate ?? 0;
	const sparkColor =
		successRate >= 0.9
			? "text-emerald-500"
			: successRate >= 0.75
				? "text-yellow-500"
				: "text-rose-500";

	return (
		<div className="grid gap-4 lg:grid-cols-[1fr_320px]">
			{/* Main column */}
			<div className="flex flex-col gap-4">
				{/* Stat row — 4 stats */}
				{statsLoading ? (
					<div className="grid grid-cols-2 gap-4 md:grid-cols-4">
						{[...Array(4)].map((_, i) => (
							<Skeleton key={i} className="h-[92px] w-full" />
						))}
					</div>
				) : stats ? (
					<div className="grid grid-cols-2 gap-4 md:grid-cols-4">
						<StatCard
							label="Runs (7d)"
							value={formatNumber(stats.runs_7d)}
						/>
						<StatCard
							label="Success rate"
							value={`${Math.round(successRate * 100)}%`}
							delta={
								stats.runs_7d > 0 ? `${stats.runs_7d} runs` : "—"
							}
						/>
						<StatCard
							label="Avg duration"
							value={formatDuration(stats.avg_duration_ms)}
						/>
						<StatCard
							label="Spend (7d)"
							value={formatCost(stats.total_cost_7d)}
						/>
					</div>
				) : null}

				{/* Activity — last 7 days */}
				<div className="overflow-hidden rounded-[10px] border bg-card">
					<div className="flex items-center justify-between border-b px-4 py-3">
						<div className="flex items-center gap-2 text-[14.5px] font-semibold">
							<Activity className="h-3.5 w-3.5" /> Activity — last 7
							days
						</div>
						<span className="text-xs text-muted-foreground">
							Daily buckets
						</span>
					</div>
					<div className="h-[140px] p-4">
						{stats &&
						stats.runs_by_day.length > 1 &&
						stats.runs_by_day.some((v) => v > 0) ? (
							<Sparkline
								values={stats.runs_by_day}
								colorClass={sparkColor}
							/>
						) : (
							<div className="flex h-full items-center justify-center text-sm text-muted-foreground">
								No activity yet
							</div>
						)}
					</div>
				</div>

				{/* Recent activity */}
				<div className="overflow-hidden rounded-[10px] border bg-card">
					<div className="flex items-center justify-between border-b px-4 py-3">
						<div className="text-[14.5px] font-semibold">
							Recent activity
						</div>
						<button
							type="button"
							onClick={() => {
								document.querySelector<HTMLElement>(
									'[role="tab"][value="runs"]',
								)?.click();
							}}
							className="text-[12.5px] text-muted-foreground hover:text-foreground"
						>
							View all runs →
						</button>
					</div>
					<div>
						{runsLoading ? (
							<div className="space-y-1 p-3">
								<Skeleton className="h-12 w-full" />
								<Skeleton className="h-12 w-full" />
								<Skeleton className="h-12 w-full" />
							</div>
						) : recentRuns.length === 0 ? (
							<p className="py-8 text-center text-[13px] text-muted-foreground">
								No runs yet for this agent.
							</p>
						) : (
							recentRuns.slice(0, 6).map((r) => (
								<ActivityRow
									key={r.id}
									run={r}
									agentId={agentId}
								/>
							))
						)}
					</div>
				</div>
			</div>

			{/* Side column */}
			<div className="flex flex-col gap-4">
				{needsReview > 0 ? (
					<Link
						to={`/agents/${agentId}/review`}
						className="block overflow-hidden rounded-[10px] border border-rose-500/40 bg-card transition-colors hover:border-rose-500/70"
					>
						<div className="border-b border-rose-500/20 px-4 py-3">
							<div className="flex items-center gap-2 text-[14.5px] font-semibold text-rose-500">
								<AlertTriangle className="h-3.5 w-3.5" />
								Needs attention
							</div>
						</div>
						<div className="space-y-2 p-4 text-[13px]">
							<div>
								<strong>{needsReview}</strong> run
								{needsReview === 1 ? "" : "s"} marked 👎
							</div>
							{unreviewed > 0 ? (
								<div className="text-muted-foreground">
									{unreviewed} completed run
									{unreviewed === 1 ? "" : "s"} awaiting review
								</div>
							) : null}
							<div className="mt-1 w-full rounded-md bg-rose-500/15 px-3 py-1.5 text-center text-[12.5px] font-medium text-rose-500">
								Open review flipbook →
							</div>
						</div>
					</Link>
				) : unreviewed > 0 ? (
					<Link
						to={`/agents/${agentId}/review`}
						className="block overflow-hidden rounded-[10px] border bg-card transition-colors hover:border-border/80"
					>
						<div className="border-b px-4 py-3">
							<div className="flex items-center gap-2 text-[14.5px] font-semibold">
								<Info className="h-3.5 w-3.5" />
								{unreviewed} to review
							</div>
						</div>
						<div className="space-y-2 p-4 text-[13px]">
							<div className="text-muted-foreground">
								Completed runs awaiting a verdict
							</div>
							<div className="mt-1 w-full rounded-md border bg-muted/60 px-3 py-1.5 text-center text-[12.5px]">
								Open review flipbook →
							</div>
						</div>
					</Link>
				) : null}

				{/* Configuration */}
				<div className="overflow-hidden rounded-[10px] border bg-card">
					<div className="border-b px-4 py-3">
						<div className="text-[14.5px] font-semibold">
							Configuration
						</div>
					</div>
					<dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 p-4 text-[13px]">
						<dt className="text-muted-foreground">Model</dt>
						<dd className="font-mono text-[12.5px]">
							{agent?.llm_model ?? "default"}
						</dd>
						<dt className="text-muted-foreground">Channels</dt>
						<dd>
							{(agent?.channels ?? []).join(", ") || "—"}
						</dd>
						<dt className="text-muted-foreground">Access</dt>
						<dd>
							{agent?.access_level === "authenticated"
								? "Any user"
								: "Role-based"}
						</dd>
						<dt className="text-muted-foreground">Owner</dt>
						<dd className="truncate font-mono text-[12.5px]">
							{agent?.created_by ?? "system"}
						</dd>
					</dl>
				</div>

				{/* Budgets */}
				<div className="overflow-hidden rounded-[10px] border bg-card">
					<div className="border-b px-4 py-3">
						<div className="text-[14.5px] font-semibold">Budgets</div>
					</div>
					<dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 p-4 text-[13px]">
						<dt className="text-muted-foreground">Max iterations</dt>
						<dd className="tabular-nums">
							{agent?.max_iterations ?? "—"}
						</dd>
						<dt className="text-muted-foreground">Max tokens</dt>
						<dd className="tabular-nums">
							{agent?.max_token_budget?.toLocaleString() ?? "—"}
						</dd>
					</dl>
				</div>
			</div>
		</div>
	);
}

function ActivityRow({
	run,
	agentId,
}: {
	run: AgentRun;
	agentId: string;
}) {
	const status = (run.status ?? "").toLowerCase();
	const iconTone =
		status === "completed"
			? "bg-emerald-500/15 text-emerald-500"
			: status === "failed" || status === "budget_exceeded"
				? "bg-rose-500/15 text-rose-500"
				: "bg-muted text-muted-foreground";
	const Icon =
		status === "completed"
			? CheckCircle
			: status === "running"
				? Clock
				: XCircle;

	return (
		<Link
			to={`/agents/${agentId}/runs/${run.id}`}
			className="flex items-center gap-3 border-b px-4 py-3 text-[13px] last:border-b-0 hover:bg-accent/40"
		>
			<div
				className={cn(
					"grid h-6 w-6 shrink-0 place-items-center rounded-full",
					iconTone,
				)}
			>
				<Icon className="h-3 w-3" />
			</div>
			<div className="min-w-0 flex-1">
				<div className="truncate">
					{run.did ?? asText(run.output) ?? "—"}
				</div>
				<div className="mt-0.5 text-[12px] text-muted-foreground">
					{run.asked ? `"${truncate(run.asked, 60)}"` : "—"} ·{" "}
					{formatRelativeTime(run.started_at ?? run.created_at ?? "")} ·{" "}
					{formatDuration(run.duration_ms ?? 0)}
				</div>
			</div>
			{run.verdict === "up" ? (
				<ThumbsUp className="h-3.5 w-3.5 text-emerald-500" />
			) : run.verdict === "down" ? (
				<ThumbsDown className="h-3.5 w-3.5 text-rose-500" />
			) : null}
		</Link>
	);
}

function truncate(s: string, n: number): string {
	return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

function asText(v: unknown): string | null {
	if (v == null) return null;
	if (typeof v === "string") return v;
	try {
		return JSON.stringify(v);
	} catch {
		return null;
	}
}
