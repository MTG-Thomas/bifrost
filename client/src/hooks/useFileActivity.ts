import { useEffect, useCallback } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { authFetch } from "@/lib/api-client";
import { webSocketService } from "@/services/websocket";
import { useFileActivityStore } from "@/stores/fileActivityStore";

interface WatcherData {
	user_id: string;
	user_name: string;
	prefix: string;
	started_at: string;
}

const POLL_INTERVAL_MS = 15_000; // 15 seconds

export function useFileActivity() {
	const { isPlatformAdmin } = useAuth();
	const addEvent = useFileActivityStore((s) => s.addEvent);
	const setWatchers = useFileActivityStore((s) => s.setWatchers);

	const fetchWatchers = useCallback(() => {
		authFetch("/api/files/watchers")
			.then((res) => res.json())
			.then((data: { watchers: WatcherData[] }) => {
				setWatchers(
					data.watchers.map((w) => ({
						type: "watch_start" as const,
						user_id: w.user_id,
						user_name: w.user_name,
						prefix: w.prefix,
						file_count: 0,
						is_watch: true,
						timestamp: w.started_at,
					})),
				);
			})
			.catch(() => {});
	}, [setWatchers]);

	useEffect(() => {
		if (!isPlatformAdmin) return;

		// Seed immediately + poll to catch start/stop changes
		fetchWatchers();
		const interval = setInterval(fetchWatchers, POLL_INTERVAL_MS);

		// Subscribe to the file-activity channel for real-time updates
		webSocketService.subscribe("file-activity");
		const unsubscribe = webSocketService.onFileActivity(addEvent);

		return () => {
			clearInterval(interval);
			unsubscribe();
			webSocketService.unsubscribe("file-activity");
		};
	}, [isPlatformAdmin, addEvent, fetchWatchers]);
}
