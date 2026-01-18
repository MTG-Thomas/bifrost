/**
 * File Path Validation
 *
 * Provides path validation for code engine file trees.
 * Mirrors backend validation rules from api/src/routers/app_code_files.py
 *
 * Path conventions:
 * - Root: only _layout, _providers allowed
 * - pages/: index, _layout, [param]/, named subfolders
 * - components/: files or subfolders (free naming)
 * - modules/: files or subfolders (free naming)
 */

/**
 * Result of path validation
 */
export interface PathValidationResult {
	valid: boolean;
	error?: string;
}

/**
 * Path validator function type
 */
export type PathValidator = (path: string) => PathValidationResult;

// Valid root-level files (no directory prefix)
const ROOT_ALLOWED_FILES = new Set(["_layout", "_providers"]);

// Valid top-level directories
const VALID_TOP_DIRS = new Set(["pages", "components", "modules"]);

// Pattern for dynamic route segments like [id] or [slug]
const DYNAMIC_SEGMENT_PATTERN = /^\[[\w-]+\]$/;

// Pattern for valid file/folder names (alphanumeric, underscore, hyphen)
const VALID_NAME_PATTERN = /^[\w-]+$/;

/**
 * Validate a file path against code engine conventions
 *
 * Rules:
 * - Root level: only _layout, _providers allowed
 * - Files must be in pages/, components/, or modules/
 * - Dynamic segments [param] only allowed in pages/
 * - Names must be alphanumeric with underscores/hyphens
 *
 * @param path - The file or folder path to validate
 * @returns Validation result with error message if invalid
 */
export function validateAppCodePath(path: string): PathValidationResult {
	if (!path) {
		return { valid: false, error: "File path cannot be empty" };
	}

	// Normalize path (remove leading/trailing slashes)
	const normalizedPath = path.replace(/^\/+|\/+$/g, "");

	// Split into segments
	const segments = normalizedPath.split("/");

	// Check for empty segments (double slashes)
	if (segments.some((seg) => !seg)) {
		return {
			valid: false,
			error: "Path cannot contain empty segments (double slashes)",
		};
	}

	// Root level file (no directory)
	if (segments.length === 1) {
		if (!ROOT_ALLOWED_FILES.has(segments[0])) {
			return {
				valid: false,
				error: `Root-level file must be one of: ${Array.from(ROOT_ALLOWED_FILES).sort().join(", ")}. Use pages/, components/, or modules/ directories for other files.`,
			};
		}
		return { valid: true };
	}

	// Check top-level directory
	const topDir = segments[0];
	if (!VALID_TOP_DIRS.has(topDir)) {
		return {
			valid: false,
			error: `Files must be in one of: ${Array.from(VALID_TOP_DIRS).sort().join(", ")}. Got: '${topDir}'`,
		};
	}

	// Validate remaining segments
	for (const segment of segments.slice(1)) {
		// Dynamic segments only allowed in pages/
		if (DYNAMIC_SEGMENT_PATTERN.test(segment)) {
			if (topDir !== "pages") {
				return {
					valid: false,
					error: `Dynamic segments like [${segment.slice(1, -1)}] are only allowed in pages/`,
				};
			}
			continue;
		}

		// Validate segment name
		if (!VALID_NAME_PATTERN.test(segment)) {
			return {
				valid: false,
				error: `Invalid path segment '${segment}'. Use only alphanumeric characters, underscores, and hyphens.`,
			};
		}

		// Special files in pages/
		if (topDir === "pages" && (segment === "index" || segment === "_layout")) {
			continue;
		}

		// _layout only allowed in pages/ at any level
		if (segment === "_layout" && topDir !== "pages") {
			return {
				valid: false,
				error: "_layout files are only allowed in pages/",
			};
		}
	}

	return { valid: true };
}

/**
 * Create a path validator that validates paths relative to a parent folder
 *
 * @param parentPath - The parent folder path (empty string for root)
 * @returns A validator function for child paths
 */
export function createRelativePathValidator(
	parentPath: string,
): (childName: string) => PathValidationResult {
	return (childName: string) => {
		const fullPath = parentPath ? `${parentPath}/${childName}` : childName;
		return validateAppCodePath(fullPath);
	};
}
