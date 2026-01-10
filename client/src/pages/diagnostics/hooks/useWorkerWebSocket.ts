/**
 * WebSocket hook for real-time pool updates
 *
 * Subscribes to the platform_workers channel and handles:
 * - worker_heartbeat: Full pool state updates
 * - worker_online: New pool registration
 * - worker_offline: Pool disconnection
 * - process_state_changed: Process state transitions
 */

import { useEffect, useState, useCallback } from "react";
import { webSocketService } from "@/services/websocket";
import type { PoolDetail, QueueItem, ProcessInfo, ProcessState } from "@/services/workers";

// Internal types for WebSocket messages
interface HeartbeatProcessInfo {
	process_id: string;
	pid: number;
	state: ProcessState;
	memory_mb: number;
	uptime_seconds: number;
	executions_completed: number;
	execution?: {
		execution_id: string;
		started_at: string;
		elapsed_seconds: number;
	};
}

interface PoolHeartbeatMessage {
	type: "worker_heartbeat";
	worker_id: string;
	hostname?: string;
	status?: string;
	started_at?: string;
	timestamp?: string;
	pool_size?: number;
	idle_count?: number;
	busy_count?: number;
	processes?: HeartbeatProcessInfo[];
}

interface PoolOnlineMessage {
	type: "worker_online";
	worker_id: string;
	hostname?: string;
	started_at?: string;
}

interface PoolOfflineMessage {
	type: "worker_offline";
	worker_id: string;
}

interface ProcessStateChangedMessage {
	type: "process_state_changed";
	worker_id: string;
	process_id: string;
	pid: number;
	old_state: ProcessState;
	new_state: ProcessState;
}

type PoolMessage =
	| PoolHeartbeatMessage
	| PoolOnlineMessage
	| PoolOfflineMessage
	| ProcessStateChangedMessage;

interface UseWorkerWebSocketReturn {
	pools: PoolDetail[];
	queue: QueueItem[];
	isConnected: boolean;
}

/**
 * Hook to subscribe to real-time pool updates via WebSocket
 */
export function useWorkerWebSocket(): UseWorkerWebSocketReturn {
	const [pools, setPools] = useState<PoolDetail[]>([]);
	const [queue] = useState<QueueItem[]>([]);
	const [isConnected, setIsConnected] = useState(false);

	const handleMessage = useCallback((message: PoolMessage) => {
		switch (message.type) {
			case "worker_heartbeat": {
				// Update or add pool
				setPools((prev) => {
					const idx = prev.findIndex(
						(p) => p.worker_id === message.worker_id
					);

					// Convert heartbeat processes to ProcessInfo format
					const processes: ProcessInfo[] = (message.processes || []).map((p) => ({
						process_id: p.process_id,
						pid: p.pid,
						state: p.state,
						current_execution_id: p.execution?.execution_id || null,
						executions_completed: p.executions_completed,
						started_at: null,
						uptime_seconds: p.uptime_seconds,
						memory_mb: p.memory_mb,
						is_alive: true,
					}));

					const updatedPool: PoolDetail = {
						worker_id: message.worker_id,
						hostname: message.hostname || null,
						status: message.status || null,
						started_at: message.started_at || null,
						last_heartbeat: message.timestamp || null,
						min_workers: 2,
						max_workers: 10,
						processes,
					};

					if (idx >= 0) {
						const updated = [...prev];
						updated[idx] = updatedPool;
						return updated;
					}
					return [...prev, updatedPool];
				});
				break;
			}

			case "worker_online": {
				// Add new pool
				setPools((prev) => {
					// Check if already exists
					if (prev.some((p) => p.worker_id === message.worker_id)) {
						return prev;
					}
					return [
						...prev,
						{
							worker_id: message.worker_id,
							hostname: message.hostname || null,
							status: "online",
							started_at: message.started_at || null,
							last_heartbeat: null,
							min_workers: 2,
							max_workers: 10,
							processes: [],
						},
					];
				});
				break;
			}

			case "worker_offline": {
				// Remove pool
				setPools((prev) =>
					prev.filter((p) => p.worker_id !== message.worker_id)
				);
				break;
			}

			case "process_state_changed": {
				// Update process state within pool
				setPools((prev) => {
					const idx = prev.findIndex(
						(p) => p.worker_id === message.worker_id
					);
					if (idx < 0) return prev;

					const updated = [...prev];
					const pool = { ...updated[idx] };
					pool.processes = pool.processes.map((proc) =>
						proc.process_id === message.process_id
							? { ...proc, state: message.new_state }
							: proc
					);
					updated[idx] = pool;
					return updated;
				});
				break;
			}
		}
	}, []);

	useEffect(() => {
		let mounted = true;

		const connect = async () => {
			try {
				// Subscribe to platform workers channel
				await webSocketService.connect(["platform:workers"]);

				if (!mounted) return;

				if (webSocketService.isConnected()) {
					setIsConnected(true);
				}

				// Note: The WebSocket service would need to be extended to handle
				// pool-specific message types. For now, we're setting up the structure.
				// The actual message handling would require adding onPoolMessage()
				// method to webSocketService similar to onExecutionUpdate().
			} catch (error) {
				console.error("[useWorkerWebSocket] Failed to connect:", error);
				if (mounted) {
					setIsConnected(false);
				}
			}
		};

		connect();

		return () => {
			mounted = false;
			webSocketService.unsubscribe("platform:workers");
		};
	}, [handleMessage]);

	return {
		pools,
		queue,
		isConnected,
	};
}
