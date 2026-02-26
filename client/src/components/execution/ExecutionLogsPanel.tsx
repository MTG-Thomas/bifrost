import { useRef, useEffect, useCallback, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
	CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { components } from "@/lib/v1";
import type { StreamingLog } from "@/stores/executionStreamStore";

// Re-export from generated types
export type ExecutionStatus = components["schemas"]["ExecutionStatus"];
export type ExecutionLogPublic = components["schemas"]["ExecutionLogPublic"];

// Union type that handles both API logs and streaming logs
export type LogEntry = ExecutionLogPublic | StreamingLog;

interface ExecutionLogsPanelProps {
	/** Logs from API (persisted) */
	logs?: LogEntry[];
	/** Logs from real-time streaming */
	streamingLogs?: StreamingLog[];
	/** Current execution status */
	status?: ExecutionStatus;
	/** Whether the WebSocket connection is active */
	isConnected?: boolean;
	/** Whether logs are still loading from API */
	isLoading?: boolean;
	/** Whether the user is a platform admin (shows DEBUG logs) */
	isPlatformAdmin?: boolean;
	/** Optional className for the card */
	className?: string;
	/** Maximum height for the logs container */
	maxHeight?: string;
	/** Render without Card wrapper (for embedding in other panels) */
	embedded?: boolean;
}

const levelColors: Record<string, string> = {
	debug: "text-gray-500",
	info: "text-blue-600",
	warning: "text-yellow-600",
	error: "text-red-600",
	traceback: "text-orange-600",
};

export function ExecutionLogsPanel({
	logs = [],
	streamingLogs = [],
	status,
	isConnected = false,
	isLoading = false,
	isPlatformAdmin = false,
	className,
	maxHeight = "600px",
	embedded = false,
}: ExecutionLogsPanelProps) {
	const logsContainerRef = useRef<HTMLDivElement>(null);
	const [autoScroll, setAutoScroll] = useState(true);
	const [copied, setCopied] = useState(false);

	const isRunning =
		status === "Running" || status === "Pending" || status === "Cancelling";
	const isComplete =
		status === "Success" ||
		status === "Failed" ||
		status === "CompletedWithErrors" ||
		status === "Timeout" ||
		status === "Cancelled";

	// Combine API logs with streaming logs for display during execution
	const displayLogs = isRunning ? [...logs, ...streamingLogs] : logs;

	// Auto-scroll to bottom when new logs arrive.
	// Uses scrollTop instead of scrollIntoView to avoid scrolling the outer page.
	useEffect(() => {
		const container = logsContainerRef.current;
		if (autoScroll && container && displayLogs.length > 0) {
			container.scrollTop = container.scrollHeight;
		}
	}, [displayLogs.length, autoScroll]);

	// Handle scroll to detect if user has scrolled up (pause auto-scroll)
	const handleLogsScroll = useCallback(() => {
		const container = logsContainerRef.current;
		if (!container) return;

		// Check if scrolled to bottom (with 50px threshold)
		const isAtBottom =
			container.scrollHeight -
				container.scrollTop -
				container.clientHeight <
			50;
		setAutoScroll(isAtBottom);
	}, []);

	const handleCopyLogs = useCallback(() => {
		const text = displayLogs
			.map((log) => {
				const time = log.timestamp
					? new Date(log.timestamp).toLocaleTimeString()
					: "";
				const level = (log.level || "INFO").toUpperCase();
				return `${time}  ${level}  ${log.message || ""}`;
			})
			.join("\n");
		navigator.clipboard.writeText(text).then(() => {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		});
	}, [displayLogs]);

	const renderLogEntry = (log: LogEntry, index: number) => {
		const level = log.level?.toLowerCase() || "info";
		const levelColor = levelColors[level] || "text-gray-600";
		// data field only exists on ExecutionLogPublic, not StreamingLog
		const data = "data" in log ? log.data : undefined;

		return (
			<div
				key={index}
				className="flex gap-3 text-sm font-mono border-b pb-2 last:border-0"
			>
				<span className="text-muted-foreground whitespace-nowrap">
					{log.timestamp
						? new Date(log.timestamp).toLocaleTimeString()
						: ""}
				</span>
				<span
					className={`font-semibold uppercase min-w-[60px] ${levelColor}`}
				>
					{log.level}
				</span>
				<span className="flex-1 whitespace-pre-wrap">
					{log.message}
				</span>
				{data && Object.keys(data).length > 0 && (
					<details className="text-xs">
						<summary className="cursor-pointer text-muted-foreground">
							data
						</summary>
						<pre className="mt-1 p-2 bg-muted rounded">
							{JSON.stringify(data, null, 2)}
						</pre>
					</details>
				)}
			</div>
		);
	};

	// Embedded content (no Card wrapper)
	const renderEmbeddedContent = () => {
		const logsContent =
			displayLogs.length === 0 && !isLoading ? (
				<div className="text-center text-muted-foreground py-8">
					{isRunning
						? "Waiting for logs..."
						: isComplete
							? "No logs captured"
							: "No execution in progress"}
				</div>
			) : isLoading && displayLogs.length === 0 ? (
				<div className="text-center text-muted-foreground py-8">
					<Loader2 className="h-4 w-4 animate-spin inline mr-2" />
					Loading logs...
				</div>
			) : (
				<div
					ref={logsContainerRef}
					onScroll={handleLogsScroll}
					className="space-y-2 overflow-y-auto p-4"
					style={{ maxHeight }}
				>
					{displayLogs.map(renderLogEntry)}
				</div>
			);

		return (
			<div className={className}>
				{/* Header */}
				<div className="px-4 py-2 border-b bg-muted/30">
					<div className="flex items-center gap-2">
						<span className="text-sm font-medium">Logs</span>
						{isConnected && isRunning && (
							<Badge variant="secondary" className="text-xs">
								<Loader2 className="mr-1 h-3 w-3 animate-spin" />
								Live
							</Badge>
						)}
					</div>
					<p className="text-xs text-muted-foreground">
						Python logger output
						{!isPlatformAdmin && " (INFO, WARNING, ERROR only)"}
					</p>
				</div>
				{/* Content */}
				{logsContent}
			</div>
		);
	};

	// Use embedded rendering if requested
	if (embedded) {
		return renderEmbeddedContent();
	}

	// Running state - show logs as they stream in
	if (isRunning) {
		return (
			<Card className={className}>
				<CardHeader>
					<div className="flex items-center justify-between">
						<CardTitle className="flex items-center gap-2">
							Logs
							{isConnected && (
								<Badge variant="secondary" className="text-xs">
									<Loader2 className="mr-1 h-3 w-3 animate-spin" />
									Live
								</Badge>
							)}
						</CardTitle>
						{displayLogs.length > 0 && (
							<Button variant="ghost" size="icon" className="h-8 w-8" onClick={handleCopyLogs}>
								{copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
							</Button>
						)}
					</div>
					<CardDescription>
						Python logger output from workflow execution
						{!isPlatformAdmin && " (INFO, WARNING, ERROR only)"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					{displayLogs.length === 0 && !isLoading ? (
						<div className="text-center text-muted-foreground py-8">
							Waiting for logs...
						</div>
					) : isLoading && displayLogs.length === 0 ? (
						<div className="text-center text-muted-foreground py-8">
							<Loader2 className="h-4 w-4 animate-spin inline mr-2" />
							Loading logs...
						</div>
					) : (
						<div
							ref={logsContainerRef}
							onScroll={handleLogsScroll}
							className="space-y-2 overflow-y-auto"
							style={{ maxHeight }}
						>
							{displayLogs.map(renderLogEntry)}
								</div>
					)}
				</CardContent>
			</Card>
		);
	}

	// Complete state - show persisted logs
	if (isComplete) {
		return (
			<Card className={className}>
				<CardHeader>
					<div className="flex items-center justify-between">
						<CardTitle>Logs</CardTitle>
						{logs.length > 0 && (
							<Button variant="ghost" size="icon" className="h-8 w-8" onClick={handleCopyLogs}>
								{copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
							</Button>
						)}
					</div>
					<CardDescription>
						Python logger output from workflow execution
						{!isPlatformAdmin && " (INFO, WARNING, ERROR only)"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					<AnimatePresence mode="wait">
						{isLoading ? (
							<motion.div
								key="loading"
								initial={{ opacity: 0 }}
								animate={{ opacity: 1 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.2 }}
								className="space-y-2"
							>
								<Skeleton className="h-4 w-full" />
								<Skeleton className="h-4 w-5/6" />
								<Skeleton className="h-4 w-4/5" />
							</motion.div>
						) : logs.length === 0 ? (
							<motion.div
								key="empty"
								initial={{ opacity: 0 }}
								animate={{ opacity: 1 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.2 }}
								className="text-center text-muted-foreground py-8"
							>
								No logs captured
							</motion.div>
						) : (
							<motion.div
								key="content"
								initial={{ opacity: 0 }}
								animate={{ opacity: 1 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.2 }}
								ref={logsContainerRef}
								className="space-y-2 overflow-y-auto"
								style={{ maxHeight }}
							>
								{logs.map(renderLogEntry)}
							</motion.div>
						)}
					</AnimatePresence>
				</CardContent>
			</Card>
		);
	}

	// Default/unknown state
	return (
		<Card className={className}>
			<CardHeader>
				<CardTitle>Logs</CardTitle>
				<CardDescription>
					Python logger output from workflow execution
				</CardDescription>
			</CardHeader>
			<CardContent>
				<div className="text-center text-muted-foreground py-8">
					No execution in progress
				</div>
			</CardContent>
		</Card>
	);
}
