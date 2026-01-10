import { useState } from "react";
import { motion } from "framer-motion";
import { Server, Clock, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ProcessCard } from "./ProcessCard";
import { usePool, type PoolSummary, type ProcessInfo } from "@/services/workers";
import type { ExecutionRowData } from "./ExecutionRow";

interface PoolCardProps {
	pool: PoolSummary;
}

/**
 * Format relative time from ISO date string
 */
function formatRelativeTime(dateStr: string | null | undefined): string {
	if (!dateStr) return "Unknown";
	const date = new Date(dateStr);
	const now = new Date();
	const diffMs = now.getTime() - date.getTime();
	const diffSec = Math.floor(diffMs / 1000);

	if (diffSec < 60) return `${diffSec}s ago`;
	const minutes = Math.floor(diffSec / 60);
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.floor(hours / 24);
	return `${days}d ago`;
}

/**
 * Get status badge variant based on pool status
 */
function getStatusVariant(
	status: string | null | undefined
): "default" | "secondary" | "destructive" | "warning" {
	switch (status?.toLowerCase()) {
		case "online":
		case "active":
			return "default";
		case "offline":
		case "error":
			return "destructive";
		default:
			return "secondary";
	}
}

export function PoolCard({ pool }: PoolCardProps) {
	const [isExpanded, setIsExpanded] = useState(false);

	// Fetch pool details when expanded
	const { data: poolDetail, isLoading: detailLoading } = usePool(
		isExpanded ? pool.worker_id : ""
	);

	// Build execution data from process info
	const getProcessExecutions = (process: ProcessInfo): ExecutionRowData[] => {
		if (!process.current_execution_id) return [];
		return [
			{
				execution_id: process.current_execution_id,
				workflow_name: "Running workflow",
				status: "RUNNING",
				elapsed_seconds: process.uptime_seconds,
			},
		];
	};

	const processes = poolDetail?.processes || [];

	return (
		<motion.div
			initial={{ opacity: 0, y: 20 }}
			animate={{ opacity: 1, y: 0 }}
			transition={{ duration: 0.3 }}
		>
			<Card>
				<CardHeader className="pb-3">
					<div className="flex items-center justify-between">
						<div className="flex items-center gap-3">
							<button
								onClick={() => setIsExpanded(!isExpanded)}
								className="p-1 hover:bg-muted rounded"
							>
								{isExpanded ? (
									<ChevronDown className="h-4 w-4" />
								) : (
									<ChevronRight className="h-4 w-4" />
								)}
							</button>
							<Server className="h-5 w-5 text-muted-foreground" />
							<CardTitle className="text-lg">
								Pool: {pool.worker_id}
							</CardTitle>
							<Badge variant={getStatusVariant(pool.status)}>
								{pool.status || "Unknown"}
							</Badge>
						</div>
						<div className="flex items-center gap-4 text-sm text-muted-foreground">
							<span>{pool.pool_size} processes</span>
							<div className="flex items-center gap-2">
								<span className="flex items-center gap-1">
									<span className="w-2 h-2 bg-green-500 rounded-full" />
									{pool.idle_count} idle
								</span>
								<span className="flex items-center gap-1">
									<span className="w-2 h-2 bg-yellow-500 rounded-full" />
									{pool.busy_count} busy
								</span>
							</div>
						</div>
					</div>
					<div className="flex items-center gap-4 text-sm text-muted-foreground ml-10 mt-2">
						{pool.hostname && <span>Host: {pool.hostname}</span>}
						<div className="flex items-center gap-1">
							<Clock className="h-4 w-4" />
							<span>
								Online since: {formatRelativeTime(pool.started_at)}
							</span>
						</div>
						{pool.last_heartbeat && (
							<span>
								Last heartbeat: {formatRelativeTime(pool.last_heartbeat)}
							</span>
						)}
					</div>
				</CardHeader>

				{isExpanded && (
					<CardContent className="pt-0">
						{detailLoading ? (
							<div className="flex items-center justify-center py-8">
								<Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
							</div>
						) : processes.length > 0 ? (
							<div className="space-y-3">
								{processes.map((process) => (
									<ProcessCard
										key={process.process_id}
										workerId={pool.worker_id}
										process={process}
										executions={getProcessExecutions(process)}
									/>
								))}
							</div>
						) : (
							<p className="text-sm text-muted-foreground text-center py-4">
								No processes registered
							</p>
						)}
					</CardContent>
				)}
			</Card>
		</motion.div>
	);
}
