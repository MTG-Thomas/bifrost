import { create } from "zustand";
import type { FileActivityEvent } from "@/services/websocket";

interface FileActivityState {
	recentPushes: FileActivityEvent[];
	activeWatchers: FileActivityEvent[];
	addEvent: (event: FileActivityEvent) => void;
}

const MAX_RECENT = 20;
const MAX_AGE_MS = 5 * 60 * 1000; // 5 minutes

export const useFileActivityStore = create<FileActivityState>((set) => ({
	recentPushes: [],
	activeWatchers: [],
	addEvent: (event) =>
		set((state) => {
			const now = Date.now();

			if (event.type === "file_push") {
				const pruned = state.recentPushes
					.filter(
						(e) =>
							now - new Date(e.timestamp).getTime() < MAX_AGE_MS,
					)
					.slice(-(MAX_RECENT - 1));
				return { recentPushes: [...pruned, event] };
			}

			if (event.type === "watch_start") {
				const key = `${event.user_id}:${event.prefix}`;
				const filtered = state.activeWatchers.filter(
					(w) => `${w.user_id}:${w.prefix}` !== key,
				);
				return { activeWatchers: [...filtered, event] };
			}

			if (event.type === "watch_stop") {
				const key = `${event.user_id}:${event.prefix}`;
				return {
					activeWatchers: state.activeWatchers.filter(
						(w) => `${w.user_id}:${w.prefix}` !== key,
					),
				};
			}

			return state;
		}),
}));
