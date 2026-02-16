# Dependency Management UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a UI panel in the app code editor for searching, adding, removing, and editing npm dependencies stored in `app.yaml`.

**Architecture:** A dedicated API endpoint (`GET/PUT /api/applications/{app_id}/dependencies`) reads/writes the dependencies section of `app.yaml` via `FileIndexService`. The frontend adds a `DependencyPanel` component that replaces the file tree in the left sidebar when the "Packages" tab is active. npm package search uses the public registry API (`registry.npmjs.org`).

**Tech Stack:** React, TypeScript, shadcn/ui components, npm registry API, PyYAML (backend)

---

## Background: How Things Work Now

### app.yaml Storage
- `app.yaml` is stored at `apps/{slug}/app.yaml` in the `file_index` (DB-backed via `FileIndexService`)
- The render endpoint reads dependencies from it via `_parse_dependencies()` in `api/src/routers/app_code_files.py`
- The existing file CRUD API (`/api/applications/{app_id}/files/`) rejects `app.yaml` — `validate_file_path()` requires `.ts`/`.tsx` extensions, and the list endpoint skips it
- There is currently NO UI for editing `app.yaml`

### Editor Layout
- `AppCodeEditorLayout` (at `client/src/components/app-code-editor/AppCodeEditorLayout.tsx`) has:
  - Toolbar (top): sidebar toggle, file name, view mode buttons, save/run
  - Left sidebar (240px): `FileTree` component
  - Center: Editor and/or preview depending on view mode
  - Status bar (bottom)
- The sidebar can be collapsed via a toggle button

### Key Files
- `api/src/routers/app_code_files.py` — has `_parse_dependencies()`, render endpoint, file CRUD
- `api/src/services/file_index_service.py` — `FileIndexService` for reading/writing to DB-backed storage
- `client/src/components/app-code-editor/AppCodeEditorLayout.tsx` — main editor layout
- `client/src/components/file-tree/adapters/appCodeOperations.ts` — file API adapter

---

### Task 1: Add Dependencies API Endpoint

**Files:**
- Modify: `api/src/routers/app_code_files.py`
- Create: `api/tests/unit/services/test_dependencies_api.py`

The existing file API rejects `app.yaml` due to path validation. Add a dedicated endpoint pair for reading and writing dependencies.

**Step 1: Write failing test**

Create `api/tests/unit/services/test_dependencies_api.py`:

```python
"""Tests for the dependencies API endpoint helper."""
from src.routers.app_code_files import _parse_dependencies, _serialize_dependencies


def test_serialize_empty_dependencies():
    """Empty deps produce valid YAML with empty dependencies."""
    result = _serialize_dependencies({}, existing_yaml=None)
    assert "dependencies:" not in result or "dependencies: {}" in result


def test_serialize_adds_dependencies_to_existing():
    """Adding deps preserves other app.yaml fields."""
    existing = "name: My App\ndescription: Cool app\n"
    result = _serialize_dependencies(
        {"recharts": "2.12", "dayjs": "1.11"}, existing_yaml=existing
    )
    assert "name: My App" in result
    assert "recharts" in result
    assert "dayjs" in result


def test_serialize_replaces_existing_dependencies():
    """Updating deps replaces the old dependencies section."""
    existing = "name: My App\ndependencies:\n  old-pkg: '1.0'\n"
    result = _serialize_dependencies({"new-pkg": "2.0"}, existing_yaml=existing)
    assert "new-pkg" in result
    assert "old-pkg" not in result


def test_serialize_creates_yaml_from_scratch():
    """When no existing YAML, creates a minimal app.yaml."""
    result = _serialize_dependencies({"recharts": "2.12"}, existing_yaml=None)
    assert "recharts" in result


def test_roundtrip_parse_serialize():
    """Serialized deps can be parsed back identically."""
    deps = {"recharts": "^2.12", "dayjs": "~1.11.3", "@tanstack/react-table": "8.20"}
    yaml_str = _serialize_dependencies(deps, existing_yaml="name: Test\n")
    parsed = _parse_dependencies(yaml_str)
    assert parsed == deps
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/services/test_dependencies_api.py -v`
Expected: FAIL — `_serialize_dependencies` doesn't exist

**Step 3: Implement `_serialize_dependencies()` in the router**

In `api/src/routers/app_code_files.py`, add:

```python
def _serialize_dependencies(
    deps: dict[str, str], existing_yaml: str | None
) -> str:
    """Serialize dependencies back into app.yaml content.

    Preserves existing non-dependency fields. If no existing YAML,
    creates a minimal file.
    """
    # Parse existing YAML or start fresh
    data: dict = {}
    if existing_yaml:
        try:
            parsed = yaml.safe_load(existing_yaml)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            pass

    # Update dependencies
    if deps:
        data["dependencies"] = deps
    else:
        data.pop("dependencies", None)

    return yaml.dump(data, default_flow_style=False, sort_keys=False)
```

**Step 4: Add the GET/PUT endpoints**

In `api/src/routers/app_code_files.py`, add to `render_router` (which has prefix `/api/applications/{app_id}`):

```python
@render_router.get(
    "/dependencies",
    summary="Get app dependencies",
)
async def get_dependencies(
    app_id: UUID = Path(..., description="Application UUID"),
    ctx: Context = None,
    user: CurrentUser = None,
) -> dict[str, str]:
    """Read dependencies from app.yaml."""
    app = await get_application_or_404(ctx, app_id)
    file_index = FileIndexService(ctx.db)

    try:
        yaml_content = await file_index.read(f"apps/{app.slug}/app.yaml")
        return _parse_dependencies(yaml_content)
    except Exception:
        return {}


@render_router.put(
    "/dependencies",
    summary="Update app dependencies",
)
async def put_dependencies(
    deps: dict[str, str],
    app_id: UUID = Path(..., description="Application UUID"),
    ctx: Context = None,
    user: CurrentUser = None,
) -> dict[str, str]:
    """Write dependencies to app.yaml.

    Validates the dependency map, updates app.yaml preserving other fields,
    and returns the validated dependencies.
    """
    app = await get_application_or_404(ctx, app_id)

    # Validate incoming deps using existing validation
    validated: dict[str, str] = {}
    for name, version in deps.items():
        if len(validated) >= _MAX_DEPENDENCIES:
            break
        name_str = str(name)
        version_str = str(version)
        if not _PKG_NAME_RE.match(name_str):
            continue
        if not _VERSION_RE.match(version_str):
            continue
        validated[name_str] = version_str

    # Read existing app.yaml
    file_index = FileIndexService(ctx.db)
    existing_yaml: str | None = None
    try:
        existing_yaml = await file_index.read(f"apps/{app.slug}/app.yaml")
    except Exception:
        pass

    # Serialize and write back
    new_yaml = _serialize_dependencies(validated, existing_yaml)

    from src.services.file_storage.service import get_file_storage_service
    storage = get_file_storage_service(ctx.db)
    await storage.write_file(
        path=f"apps/{app.slug}/app.yaml",
        content=new_yaml.encode("utf-8"),
        updated_by=user.email or "unknown",
    )

    # Invalidate render cache since deps changed
    app_storage = AppStorageService()
    await app_storage.invalidate_render_cache(str(app.id))

    return validated
```

Note: Check if `invalidate_render_cache` exists on `AppStorageService`. If not, use `delete_render_cache` or similar. Read `api/src/services/app_storage.py` to find the right method name.

**Step 5: Run tests**

Run: `./test.sh tests/unit/services/test_dependencies_api.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add api/src/routers/app_code_files.py api/tests/unit/services/test_dependencies_api.py
git commit -m "feat: add GET/PUT dependencies API endpoint"
```

---

### Task 2: Create npm Search Utility

**Files:**
- Create: `client/src/lib/npm-search.ts`

**Step 1: Create the npm search module**

```typescript
/**
 * npm Registry Search
 *
 * Searches the public npm registry for packages.
 * Used by the dependency management panel.
 */

export interface NpmPackageResult {
	name: string;
	version: string;
	description: string;
}

const NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search";

/**
 * Search the npm registry for packages.
 *
 * @param query - Search text
 * @param size - Max results (default 8)
 * @param signal - AbortSignal for cancellation
 * @returns Array of matching packages
 */
export async function searchNpmPackages(
	query: string,
	size: number = 8,
	signal?: AbortSignal,
): Promise<NpmPackageResult[]> {
	if (!query.trim()) return [];

	const url = `${NPM_SEARCH_URL}?text=${encodeURIComponent(query)}&size=${size}`;
	const response = await fetch(url, { signal });

	if (!response.ok) {
		throw new Error(`npm search failed: ${response.statusText}`);
	}

	const data = await response.json();
	return (data.objects || []).map(
		(obj: { package: { name: string; version: string; description?: string } }) => ({
			name: obj.package.name,
			version: obj.package.version,
			description: obj.package.description || "",
		}),
	);
}
```

**Step 2: Commit**

```bash
git add client/src/lib/npm-search.ts
git commit -m "feat: add npm registry search utility"
```

---

### Task 3: Create useAppDependencies Hook

**Files:**
- Create: `client/src/hooks/useAppDependencies.ts`

This hook manages reading and writing dependencies via the new API endpoint.

**Step 1: Create the hook**

```typescript
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
		return () => { cancelled = true; };
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
					description: err instanceof Error ? err.message : "Unknown error",
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
```

**Step 2: Commit**

```bash
git add client/src/hooks/useAppDependencies.ts
git commit -m "feat: add useAppDependencies hook"
```

---

### Task 4: Create DependencyPanel Component

**Files:**
- Create: `client/src/components/app-code-editor/DependencyPanel.tsx`

This is the main UI component — search bar, results dropdown, installed package list.

**Step 1: Create the component**

```tsx
/**
 * Dependency Panel
 *
 * Manages npm dependencies for an app. Provides search, add, remove,
 * and version editing. Displayed in the left sidebar of the editor.
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Search, X, Package, Loader2 } from "lucide-react";
import { useAppDependencies } from "@/hooks/useAppDependencies";
import { searchNpmPackages, type NpmPackageResult } from "@/lib/npm-search";

interface DependencyPanelProps {
	appId: string;
}

export function DependencyPanel({ appId }: DependencyPanelProps) {
	const {
		dependencies,
		isLoading,
		isSaving,
		addDependency,
		removeDependency,
	} = useAppDependencies(appId);

	const [searchQuery, setSearchQuery] = useState("");
	const [searchResults, setSearchResults] = useState<NpmPackageResult[]>([]);
	const [isSearching, setIsSearching] = useState(false);
	const [showResults, setShowResults] = useState(false);
	const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const abortRef = useRef<AbortController | null>(null);
	const panelRef = useRef<HTMLDivElement>(null);

	// Debounced search
	const handleSearchChange = useCallback((value: string) => {
		setSearchQuery(value);

		if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
		if (abortRef.current) abortRef.current.abort();

		if (!value.trim()) {
			setSearchResults([]);
			setShowResults(false);
			return;
		}

		searchTimerRef.current = setTimeout(async () => {
			setIsSearching(true);
			const controller = new AbortController();
			abortRef.current = controller;

			try {
				const results = await searchNpmPackages(value, 8, controller.signal);
				setSearchResults(results);
				setShowResults(true);
			} catch {
				// Abort or network error — ignore
			} finally {
				setIsSearching(false);
			}
		}, 300);
	}, []);

	// Close dropdown when clicking outside
	useEffect(() => {
		function handleClickOutside(e: MouseEvent) {
			if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
				setShowResults(false);
			}
		}
		document.addEventListener("mousedown", handleClickOutside);
		return () => document.removeEventListener("mousedown", handleClickOutside);
	}, []);

	// Cleanup timer on unmount
	useEffect(() => {
		return () => {
			if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
			if (abortRef.current) abortRef.current.abort();
		};
	}, []);

	const handleAdd = useCallback(
		async (pkg: NpmPackageResult) => {
			setShowResults(false);
			setSearchQuery("");
			setSearchResults([]);
			await addDependency(pkg.name, pkg.version);
		},
		[addDependency],
	);

	const depEntries = Object.entries(dependencies);

	if (isLoading) {
		return (
			<div className="p-3 space-y-3">
				<Skeleton className="h-8 w-full" />
				<Skeleton className="h-6 w-3/4" />
				<Skeleton className="h-6 w-1/2" />
			</div>
		);
	}

	return (
		<div ref={panelRef} className="h-full flex flex-col">
			{/* Search */}
			<div className="p-2 border-b relative">
				<div className="relative">
					<Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
					<Input
						value={searchQuery}
						onChange={(e) => handleSearchChange(e.target.value)}
						placeholder="Search npm packages..."
						className="h-8 pl-8 pr-8 text-sm"
					/>
					{isSearching && (
						<Loader2 className="absolute right-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 animate-spin text-muted-foreground" />
					)}
				</div>

				{/* Search results dropdown */}
				{showResults && searchResults.length > 0 && (
					<div className="absolute left-2 right-2 top-full mt-1 z-50 bg-popover border rounded-md shadow-md max-h-64 overflow-auto">
						{searchResults.map((pkg) => {
							const isInstalled = pkg.name in dependencies;
							return (
								<button
									key={pkg.name}
									className="w-full text-left px-3 py-2 hover:bg-accent text-sm border-b last:border-b-0 disabled:opacity-50"
									onClick={() => handleAdd(pkg)}
									disabled={isInstalled || isSaving}
								>
									<div className="flex items-center justify-between">
										<span className="font-medium truncate">
											{pkg.name}
										</span>
										<span className="text-xs text-muted-foreground ml-2 flex-shrink-0">
											{isInstalled ? "installed" : pkg.version}
										</span>
									</div>
									{pkg.description && (
										<p className="text-xs text-muted-foreground truncate mt-0.5">
											{pkg.description}
										</p>
									)}
								</button>
							);
						})}
					</div>
				)}
			</div>

			{/* Installed packages */}
			<div className="flex-1 overflow-auto">
				{depEntries.length === 0 ? (
					<div className="p-4 text-center text-sm text-muted-foreground">
						<Package className="h-8 w-8 mx-auto mb-2 opacity-30" />
						<p>No packages installed</p>
						<p className="text-xs mt-1">Search above to add npm packages</p>
					</div>
				) : (
					<div className="py-1">
						{depEntries.map(([name, version]) => (
							<div
								key={name}
								className="flex items-center justify-between px-3 py-1.5 hover:bg-accent/50 group"
							>
								<div className="min-w-0">
									<div className="text-sm font-medium truncate">
										{name}
									</div>
									<div className="text-xs text-muted-foreground">
										{version}
									</div>
								</div>
								<Button
									variant="ghost"
									size="icon"
									className="h-6 w-6 opacity-0 group-hover:opacity-100 flex-shrink-0"
									onClick={() => removeDependency(name)}
									disabled={isSaving}
									title={`Remove ${name}`}
								>
									<X className="h-3.5 w-3.5" />
								</Button>
							</div>
						))}
					</div>
				)}
			</div>

			{/* Footer with count */}
			{depEntries.length > 0 && (
				<div className="px-3 py-1.5 border-t text-xs text-muted-foreground">
					{depEntries.length}/20 packages
					{isSaving && (
						<Loader2 className="inline-block h-3 w-3 animate-spin ml-2" />
					)}
				</div>
			)}
		</div>
	);
}
```

**Step 2: Commit**

```bash
git add client/src/components/app-code-editor/DependencyPanel.tsx
git commit -m "feat: add DependencyPanel component with search and manage"
```

---

### Task 5: Integrate DependencyPanel into Editor Layout

**Files:**
- Modify: `client/src/components/app-code-editor/AppCodeEditorLayout.tsx`

Add sidebar tab switching between Files and Packages, plus a toolbar button.

**Step 1: Add the sidebar tab state and imports**

At the top of `AppCodeEditorLayout.tsx`, add imports:

```typescript
import { Package } from "lucide-react";
import { DependencyPanel } from "./DependencyPanel";
```

Add `Package` to the existing lucide-react import (merge with existing icons).

Inside the component, add state:

```typescript
type SidebarTab = "files" | "packages";
const [sidebarTab, setSidebarTab] = useState<SidebarTab>("files");
```

**Step 2: Replace the sidebar section**

Replace the sidebar div (the `<div className="w-60 border-r ...">` containing `<FileTree>`) with:

```tsx
{!sidebarCollapsed && (
    <div className="w-60 border-r flex-shrink-0 flex flex-col">
        {/* Tab switcher */}
        <div className="flex border-b">
            <button
                className={`flex-1 px-3 py-1.5 text-xs font-medium ${
                    sidebarTab === "files"
                        ? "border-b-2 border-primary text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                }`}
                onClick={() => setSidebarTab("files")}
            >
                Files
            </button>
            <button
                className={`flex-1 px-3 py-1.5 text-xs font-medium ${
                    sidebarTab === "packages"
                        ? "border-b-2 border-primary text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                }`}
                onClick={() => setSidebarTab("packages")}
            >
                Packages
            </button>
        </div>

        {/* Tab content */}
        {sidebarTab === "files" ? (
            <div className="flex-1 overflow-auto">
                <FileTree
                    operations={operations}
                    iconResolver={appCodeIconResolver}
                    editor={editorCallbacks}
                    refreshTrigger={fileTreeRefresh}
                    config={{
                        enableUpload: false,
                        enableDragMove: true,
                        enableCreate: true,
                        enableRename: true,
                        enableDelete: true,
                        emptyMessage: "No files yet",
                        loadingMessage: "Loading files...",
                        pathValidator: validateAppCodePath,
                    }}
                />
            </div>
        ) : (
            <DependencyPanel appId={appId} />
        )}
    </div>
)}
```

**Step 3: Add toolbar button for quick access**

In the toolbar, before the view mode toggles, add a Package button that opens the sidebar to the packages tab:

```tsx
{/* Package manager button */}
<Button
    variant={sidebarTab === "packages" && !sidebarCollapsed ? "secondary" : "ghost"}
    size="icon"
    className="h-7 w-7"
    onClick={() => {
        if (sidebarCollapsed) {
            setSidebarCollapsed(false);
        }
        setSidebarTab(sidebarTab === "packages" ? "files" : "packages");
    }}
    title="Package manager"
>
    <Package className="h-4 w-4" />
</Button>
```

Add this after the sidebar toggle button and file name, before the view mode section. Add a separator div between them:

```tsx
<div className="w-px h-4 bg-border mx-1" />
```

**Step 4: Run TypeScript check**

Run: `cd client && npx tsc --noEmit`
Expected: PASS

**Step 5: Run ESLint on changed files**

Run: `cd client && npx eslint --quiet src/components/app-code-editor/AppCodeEditorLayout.tsx src/components/app-code-editor/DependencyPanel.tsx src/hooks/useAppDependencies.ts src/lib/npm-search.ts`
Expected: PASS

**Step 6: Commit**

```bash
git add client/src/components/app-code-editor/AppCodeEditorLayout.tsx
git commit -m "feat: integrate dependency panel into editor sidebar with tab switching"
```

---

### Task 6: Regenerate Types, Verify, and Run Tests

**Step 1: Regenerate TypeScript types** (the new endpoints will show up in OpenAPI)

```bash
cd client && npm run generate:types
```

**Step 2: Run backend checks**

```bash
cd api && pyright && ruff check .
```

**Step 3: Run frontend checks**

```bash
cd client && npx tsc --noEmit && npx eslint --quiet
```

**Step 4: Run tests**

```bash
cd .. && ./test.sh tests/unit/services/
```

**Step 5: Commit types if changed**

```bash
git add client/src/lib/v1.d.ts
git commit -m "chore: regenerate types with dependencies endpoints"
```

---

### Task 7: Manual Integration Test

Not automated — verify manually with a running dev stack.

**Step 1: Open any app in the editor**

Navigate to `/apps/{slug}/edit`.

**Step 2: Click the "Packages" tab** in the left sidebar (or the 📦 button in the toolbar).

**Step 3: Search for "dayjs"** in the search box. Should see results from npm registry with description and version.

**Step 4: Click to add dayjs.** It should appear in the installed list. Check that `app.yaml` now has `dependencies: dayjs: "1.11.x"`.

**Step 5: Use the dependency in a page file:**

```tsx
import { Card } from "bifrost";
import dayjs from "dayjs";

export default function TestPage() {
    return (
        <Card className="p-6">
            <p>Today: {dayjs().format("MMMM D, YYYY")}</p>
        </Card>
    );
}
```

Save and check the preview — should show the formatted date.

**Step 6: Remove dayjs** from the packages panel. Verify it's gone from the list and `app.yaml`.

**Step 7: Test edge cases:**
- Search with no results (e.g. "xyznonexistent12345")
- Add a scoped package (e.g. "@tanstack/react-table")
- Verify the file tree still works when switching back to Files tab

---

## Files Changed Summary

| File | Change |
|------|--------|
| `api/src/routers/app_code_files.py` | Add `_serialize_dependencies()`, GET/PUT `/dependencies` endpoints |
| `api/tests/unit/services/test_dependencies_api.py` | **NEW** — serialization tests |
| `client/src/lib/npm-search.ts` | **NEW** — npm registry search |
| `client/src/hooks/useAppDependencies.ts` | **NEW** — hook for deps CRUD via API |
| `client/src/components/app-code-editor/DependencyPanel.tsx` | **NEW** — search, list, add/remove UI |
| `client/src/components/app-code-editor/AppCodeEditorLayout.tsx` | Add sidebar tabs (Files/Packages), toolbar button |
| `client/src/lib/v1.d.ts` | Regenerated with new endpoints |
