import { useEffect } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { webSocketService } from "@/services/websocket";
import { useFileActivityStore } from "@/stores/fileActivityStore";

export function useFileActivity() {
	const { isPlatformAdmin } = useAuth();
	const addEvent = useFileActivityStore((s) => s.addEvent);

	useEffect(() => {
		if (!isPlatformAdmin) return;

		// Subscribe to the file-activity channel
		webSocketService.subscribe("file-activity");

		// Register typed callback (returns unsubscribe function)
		const unsubscribe = webSocketService.onFileActivity(addEvent);

		return () => {
			unsubscribe();
			webSocketService.unsubscribe("file-activity");
		};
	}, [isPlatformAdmin, addEvent]);
}
