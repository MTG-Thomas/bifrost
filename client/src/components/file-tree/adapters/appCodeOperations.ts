/**
 * App Code File Operations Adapter
 *
 * Provides FileOperations for the App Code Builder editor.
 * Works with the /api/applications/{app_id}/files endpoints.
 */

import { authFetch } from "@/lib/api-client";
import type { FileNode, FileContent, FileOperations } from "../types";

/**
 * App code file from API response
 */
interface AppCodeFileResponse {
	path: string;
	source: string;
}

/**
 * Convert app code file response to FileNode
 */
function toFileNode(file: AppCodeFileResponse): FileNode {
	const pathParts = file.path.split("/");
	const name = pathParts[pathParts.length - 1];

	return {
		path: file.path,
		name,
		type: "file", // App code API only has files, folders are virtual
		size: file.source.length,
		extension: null, // App code files don't have extensions in path
		modified: null,
		metadata: {},
	};
}

/**
 * Create app code file operations for a specific application
 *
 * @param appId - Application UUID
 * @returns FileOperations implementation for app code files
 */
export function createAppCodeOperations(appId: string): FileOperations {
	const baseUrl = `/api/applications/${appId}/files`;

	return {
		async list(path: string): Promise<FileNode[]> {
			const response = await authFetch(`${baseUrl}?mode=draft`);

			if (!response.ok) {
				throw new Error(`Failed to list files: ${response.statusText}`);
			}

			const data = await response.json();
			const files: AppCodeFileResponse[] = data.files || [];

			// Filter to files under the requested path
			// The API returns all files, so we filter client-side
			const filteredFiles = path
				? files.filter((f) => f.path.startsWith(path + "/") || f.path === path)
				: files;

			return filteredFiles.map(toFileNode);
		},

		async read(path: string): Promise<FileContent> {
			const response = await authFetch(`${baseUrl}/${encodeURIComponent(path)}?mode=draft`);

			if (!response.ok) {
				throw new Error(`Failed to read file: ${response.statusText}`);
			}

			const file: AppCodeFileResponse = await response.json();

			return {
				content: file.source,
				encoding: "utf-8",
			};
		},

		async write(
			path: string,
			content: string,
			_encoding: "utf-8" | "base64" = "utf-8",
			_etag?: string,
		): Promise<void> {
			// Use PUT for both create and update (upsert semantics)
			const response = await authFetch(`${baseUrl}/${encodeURIComponent(path)}`, {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ source: content }),
			});

			if (!response.ok) {
				throw new Error(`Failed to write file: ${response.statusText}`);
			}
		},

		async createFolder(_path: string): Promise<void> {
			// App code files don't have real folders - they're virtual based on paths
			// Creating a folder is a no-op, files will create the path structure
		},

		async delete(path: string): Promise<void> {
			const response = await authFetch(`${baseUrl}/${encodeURIComponent(path)}`, {
				method: "DELETE",
			});

			if (!response.ok && response.status !== 404) {
				throw new Error(`Failed to delete file: ${response.statusText}`);
			}
		},

		async rename(oldPath: string, newPath: string): Promise<void> {
			// Read the old file
			const content = await this.read(oldPath);

			// Create at new path
			await this.write(newPath, content.content);

			// Delete old file
			await this.delete(oldPath);
		},
	};
}
