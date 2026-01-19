/**
 * Groups sync actions by entity for UI display.
 *
 * - Forms, agents, workflows: individual items
 * - Apps: grouped with their files as children
 */

import type { SyncAction } from "@/hooks/useGitHub";

export interface GroupedEntity {
	/** The main entity action (or app metadata action) */
	action: SyncAction;
	/** Child files for app entities */
	childFiles: SyncAction[];
}

/**
 * Group sync actions by entity for display.
 *
 * Apps are grouped together with their files as children.
 * Other entities (forms, agents, workflows) remain individual.
 */
export function groupSyncActions(actions: SyncAction[]): GroupedEntity[] {
	const appGroups = new Map<string, GroupedEntity>();
	const standaloneEntities: GroupedEntity[] = [];

	for (const action of actions) {
		if (action.entity_type === "app" && action.parent_slug) {
			// App metadata (app.json) - create or update group
			const existing = appGroups.get(action.parent_slug);
			if (existing) {
				// Replace placeholder with actual app metadata
				existing.action = action;
			} else {
				appGroups.set(action.parent_slug, {
					action,
					childFiles: [],
				});
			}
		} else if (action.entity_type === "app_file" && action.parent_slug) {
			// App file - add to group
			const existing = appGroups.get(action.parent_slug);
			if (existing) {
				existing.childFiles.push(action);
			} else {
				// Create placeholder group (app.json may come later)
				appGroups.set(action.parent_slug, {
					action: {
						...action,
						entity_type: "app",
						display_name: action.parent_slug,
					},
					childFiles: [action],
				});
			}
		} else {
			// Standalone entity (form, agent, workflow, unknown)
			standaloneEntities.push({
				action,
				childFiles: [],
			});
		}
	}

	// Combine: apps first, then standalone entities
	// Sort apps by display name, standalone by display name
	const sortedApps = Array.from(appGroups.values()).sort((a, b) =>
		(a.action.display_name || "").localeCompare(b.action.display_name || "")
	);
	const sortedStandalone = standaloneEntities.sort((a, b) =>
		(a.action.display_name || "").localeCompare(b.action.display_name || "")
	);

	return [...sortedApps, ...sortedStandalone];
}
