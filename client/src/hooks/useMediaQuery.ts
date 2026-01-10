import { useSyncExternalStore } from "react";

/**
 * Hook that returns true if the viewport matches the given media query.
 * Uses useSyncExternalStore for proper synchronization with the browser.
 * @param query - CSS media query string (e.g., "(min-width: 1280px)")
 */
export function useMediaQuery(query: string): boolean {
	const subscribe = (callback: () => void) => {
		const mediaQuery = window.matchMedia(query);
		mediaQuery.addEventListener("change", callback);
		return () => mediaQuery.removeEventListener("change", callback);
	};

	const getSnapshot = () => {
		return window.matchMedia(query).matches;
	};

	const getServerSnapshot = () => {
		// Default to false on server (assumes mobile-first)
		return false;
	};

	return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/**
 * Hook that returns true if viewport is at or above the xl breakpoint (1280px).
 */
export function useIsDesktop(): boolean {
	return useMediaQuery("(min-width: 1280px)");
}
