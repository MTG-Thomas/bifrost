/**
 * Hook for managing app dependencies via the API.
 *
 * Reads/writes to GET/PUT /api/applications/{appId}/dependencies.
 */

import { useState, useEffect, useCallback } from "react";
import { authFetch } from "@/lib/api-client";
import { toast } from "sonner";

interface UseAppDependenciesResult {
	/** Current dependencies {name: version} */
	dependencies: Record<string, string>;
	/** Whether initial load is in progress */
	isLoading: boolean;
	/** Whether a save is in progress */
	isSaving: boolean;
	/** Add a package */
	addDependency: (name: string, version: string) => Promise<void>;
	/** Remove a package */
	removeDependency: (name: string) => Promise<void>;
	/** Update a package version */
	updateVersion: (name: string, version: string) => Promise<void>;
}

export function useAppDependencies(appId: string): UseAppDependenciesResult {
	const [dependencies, setDependencies] = useState<Record<string, string>>({});
	const [isLoading, setIsLoading] = useState(true);
	const [isSaving, setIsSaving] = useState(false);

	// Fetch dependencies on mount
	useEffect(() => {
		let cancelled = false;

		async function load() {
			setIsLoading(true);
			try {
				const response = await authFetch(
					`/api/applications/${appId}/dependencies`,
				);
				if (!response.ok) throw new Error("Failed to load dependencies");
				const data = await response.json();
				if (!cancelled) setDependencies(data);
			} catch (err) {
				if (!cancelled) {
					console.error("Failed to load dependencies:", err);
				}
			} finally {
				if (!cancelled) setIsLoading(false);
			}
		}

		load();
		return () => {
			cancelled = true;
		};
	}, [appId]);

	// Save dependencies to API
	const saveDeps = useCallback(
		async (newDeps: Record<string, string>) => {
			setIsSaving(true);
			try {
				const response = await authFetch(
					`/api/applications/${appId}/dependencies`,
					{
						method: "PUT",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify(newDeps),
					},
				);
				if (!response.ok) throw new Error("Failed to save dependencies");
				const validated = await response.json();
				setDependencies(validated);
			} catch (err) {
				toast.error("Failed to save dependencies", {
					description:
						err instanceof Error ? err.message : "Unknown error",
				});
				throw err;
			} finally {
				setIsSaving(false);
			}
		},
		[appId],
	);

	const addDependency = useCallback(
		async (name: string, version: string) => {
			const newDeps = { ...dependencies, [name]: version };
			await saveDeps(newDeps);
			toast.success(`Added ${name}@${version}`);
		},
		[dependencies, saveDeps],
	);

	const removeDependency = useCallback(
		async (name: string) => {
			const newDeps = { ...dependencies };
			delete newDeps[name];
			await saveDeps(newDeps);
			toast.success(`Removed ${name}`);
		},
		[dependencies, saveDeps],
	);

	const updateVersion = useCallback(
		async (name: string, version: string) => {
			const newDeps = { ...dependencies, [name]: version };
			await saveDeps(newDeps);
		},
		[dependencies, saveDeps],
	);

	return {
		dependencies,
		isLoading,
		isSaving,
		addDependency,
		removeDependency,
		updateVersion,
	};
}
