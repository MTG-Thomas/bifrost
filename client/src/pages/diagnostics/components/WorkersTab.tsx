import { useState } from "react";
import { RefreshCw, Loader2, WifiOff, Server, Activity } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { usePools, useQueueStatus, usePoolStats } from "@/services/workers";
import { getErrorMessage } from "@/lib/api-error";
import { QueueSection } from "./QueueSection";
import { PoolCard } from "./WorkerCard";
import { useWorkerWebSocket } from "../hooks/useWorkerWebSocket";

export function WorkersTab() {
	// Fetch initial data via REST API
	const {
		data: poolsData,
		isLoading: poolsLoading,
		error: poolsError,
		refetch: refetchPools,
	} = usePools();

	const {
		data: queueData,
		isLoading: queueLoading,
		refetch: refetchQueue,
	} = useQueueStatus({ limit: 50 });

	const { data: statsData } = usePoolStats();

	// Real-time updates via WebSocket
	const { isConnected } = useWorkerWebSocket();

	const [isRefreshing, setIsRefreshing] = useState(false);

	const handleRefresh = async () => {
		setIsRefreshing(true);
		try {
			await Promise.all([refetchPools(), refetchQueue()]);
		} finally {
			setIsRefreshing(false);
		}
	};

	const pools = poolsData?.pools || [];
	const queueItems = queueData?.items || [];

	return (
		<div className="space-y-6">
			{/* Connection Status Banner */}
			{!isConnected && (
				<Alert className="border-amber-500/50 text-amber-700 dark:text-amber-400 [&>svg]:text-amber-600">
					<WifiOff className="h-4 w-4" />
					<AlertDescription>
						Connecting to real-time worker updates... Data may not be
						current.
					</AlertDescription>
				</Alert>
			)}

			{/* Error State */}
			{poolsError && (
				<Alert variant="destructive">
					<AlertDescription>
						Failed to load pools:{" "}
						{getErrorMessage(poolsError, "Unknown error")}
					</AlertDescription>
				</Alert>
			)}

			{/* Header with Refresh */}
			<div className="flex items-center justify-between">
				<div className="flex items-center gap-2">
					<h2 className="text-lg font-semibold">Process Pools</h2>
					{isConnected && (
						<span className="flex items-center gap-1 text-xs text-green-600">
							<span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
							Live
						</span>
					)}
				</div>
				<Button
					variant="outline"
					size="sm"
					onClick={handleRefresh}
					disabled={isRefreshing || poolsLoading}
				>
					<RefreshCw
						className={`h-4 w-4 mr-2 ${isRefreshing ? "animate-spin" : ""}`}
					/>
					Refresh
				</Button>
			</div>

			{/* Pool Stats Summary */}
			{statsData && (
				<div className="grid grid-cols-4 gap-4">
					<Card>
						<CardContent className="pt-4">
							<div className="flex items-center gap-2">
								<Server className="h-4 w-4 text-muted-foreground" />
								<span className="text-sm text-muted-foreground">Pools</span>
							</div>
							<p className="text-2xl font-bold">{statsData.total_pools}</p>
						</CardContent>
					</Card>
					<Card>
						<CardContent className="pt-4">
							<div className="flex items-center gap-2">
								<Activity className="h-4 w-4 text-muted-foreground" />
								<span className="text-sm text-muted-foreground">Processes</span>
							</div>
							<p className="text-2xl font-bold">{statsData.total_processes}</p>
						</CardContent>
					</Card>
					<Card>
						<CardContent className="pt-4">
							<div className="flex items-center gap-2">
								<span className="w-2 h-2 bg-green-500 rounded-full" />
								<span className="text-sm text-muted-foreground">Idle</span>
							</div>
							<p className="text-2xl font-bold text-green-600">{statsData.total_idle}</p>
						</CardContent>
					</Card>
					<Card>
						<CardContent className="pt-4">
							<div className="flex items-center gap-2">
								<span className="w-2 h-2 bg-yellow-500 rounded-full" />
								<span className="text-sm text-muted-foreground">Busy</span>
							</div>
							<p className="text-2xl font-bold text-yellow-600">{statsData.total_busy}</p>
						</CardContent>
					</Card>
				</div>
			)}

			{/* Queue Section */}
			<QueueSection
				items={queueItems}
				isLoading={queueLoading}
				onRefresh={refetchQueue}
			/>

			{/* Pools List */}
			{poolsLoading && pools.length === 0 ? (
				<div className="flex items-center justify-center py-12">
					<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
				</div>
			) : pools.length === 0 ? (
				<Card>
					<CardContent className="flex flex-col items-center justify-center py-12 text-center">
						<Server className="h-12 w-12 text-muted-foreground mb-4" />
						<h3 className="text-lg font-semibold">No pools connected</h3>
						<p className="mt-2 text-sm text-muted-foreground max-w-md">
							Process pools register themselves when workers start.
							If you expect pools to be running, check the worker logs
							for connection issues.
						</p>
					</CardContent>
				</Card>
			) : (
				<div className="space-y-4">
					{pools.map((pool) => (
						<PoolCard key={pool.worker_id} pool={pool} />
					))}
				</div>
			)}
		</div>
	);
}
