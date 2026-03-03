import { lazy, ComponentType } from "react";

/**
 * Wrapper around React.lazy that auto-reloads the page once on chunk load failure.
 * After a deploy, old chunk hashes no longer exist. A reload fetches the new index.html
 * with correct chunk references.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyWithReload<T extends ComponentType<any>>(
	importFn: () => Promise<{ default: T }>,
) {
	return lazy(() =>
		importFn().catch((error) => {
			// Only reload once per session to avoid infinite reload loops
			const key = "chunk-reload-ts";
			const lastReload = sessionStorage.getItem(key);
			const now = Date.now();

			// If we haven't reloaded in the last 10 seconds, reload
			if (!lastReload || now - Number(lastReload) > 10_000) {
				sessionStorage.setItem(key, String(now));
				window.location.reload();
			}

			// If we already reloaded recently, let the error propagate
			// to the error boundary
			throw error;
		}),
	);
}
