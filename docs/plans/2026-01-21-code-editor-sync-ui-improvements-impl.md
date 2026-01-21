# Code Editor Sync UI Improvements - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add app conflict grouping, diff preview on click, and display names for conflicts in the source control panel.

**Architecture:** Backend enriches `SyncConflictInfo` with metadata using existing `extract_entity_metadata()`. Frontend groups conflicts like incoming/outgoing, and shows diff view in the editor area when clicking sync items. Content fetched on-demand via new endpoint.

**Tech Stack:** Python/FastAPI (backend), TypeScript/React (frontend), Monaco DiffEditor

---

## Task 1: Add metadata fields to SyncConflictInfo model

**Files:**
- Modify: `api/src/models/contracts/github.py:418-426`

**Step 1: Write the test**

Create test file `api/tests/unit/models/test_sync_conflict_info.py`:

```python
"""Test SyncConflictInfo model with metadata fields."""
import pytest
from src.models.contracts.github import SyncConflictInfo


def test_sync_conflict_info_with_metadata():
    """SyncConflictInfo should accept metadata fields."""
    conflict = SyncConflictInfo(
        path="forms/contact.form.json",
        local_content='{"name": "Contact Form"}',
        remote_content='{"name": "Contact Form v2"}',
        local_sha="abc123",
        remote_sha="def456",
        display_name="Contact Form",
        entity_type="form",
        parent_slug=None,
    )

    assert conflict.path == "forms/contact.form.json"
    assert conflict.display_name == "Contact Form"
    assert conflict.entity_type == "form"
    assert conflict.parent_slug is None


def test_sync_conflict_info_metadata_optional():
    """Metadata fields should be optional for backwards compatibility."""
    conflict = SyncConflictInfo(
        path="workflows/export.py",
        local_content="def export(): pass",
        remote_content="def export(): return True",
        local_sha="abc123",
        remote_sha="def456",
    )

    assert conflict.display_name is None
    assert conflict.entity_type is None
    assert conflict.parent_slug is None


def test_sync_conflict_info_app_file_with_parent():
    """App files should have parent_slug."""
    conflict = SyncConflictInfo(
        path="apps/dashboard/src/index.tsx",
        local_content="export default App;",
        remote_content="export default AppV2;",
        local_sha="abc123",
        remote_sha="def456",
        display_name="src/index.tsx",
        entity_type="app_file",
        parent_slug="dashboard",
    )

    assert conflict.entity_type == "app_file"
    assert conflict.parent_slug == "dashboard"
```

**Step 2: Run test to verify it fails**

Run: `./test.sh api/tests/unit/models/test_sync_conflict_info.py -v`

Expected: FAIL - `display_name`, `entity_type`, `parent_slug` fields not recognized

**Step 3: Add metadata fields to SyncConflictInfo**

In `api/src/models/contracts/github.py`, find `SyncConflictInfo` (lines 418-426) and add the fields:

```python
class SyncConflictInfo(BaseModel):
    """Information about a conflict between local and remote."""
    path: str = Field(..., description="File path with conflict")
    local_content: str | None = Field(default=None, description="Local content")
    remote_content: str | None = Field(default=None, description="Remote content")
    local_sha: str = Field(..., description="SHA of local content")
    remote_sha: str = Field(..., description="SHA of remote content")
    # Entity metadata for UI display (same as SyncAction)
    display_name: str | None = Field(default=None, description="Human-readable entity name")
    entity_type: str | None = Field(default=None, description="Entity type: form, agent, app, app_file, workflow")
    parent_slug: str | None = Field(default=None, description="For app_file: parent app slug")

    model_config = ConfigDict(from_attributes=True)
```

**Step 4: Run test to verify it passes**

Run: `./test.sh api/tests/unit/models/test_sync_conflict_info.py -v`

Expected: PASS

**Step 5: Run type check**

Run: `cd api && pyright`

Expected: PASS with no new errors

**Step 6: Commit**

```bash
git add api/src/models/contracts/github.py api/tests/unit/models/test_sync_conflict_info.py
git commit -m "feat(api): add metadata fields to SyncConflictInfo model"
```

---

## Task 2: Enrich conflicts with metadata in sync preview endpoint

**Files:**
- Modify: `api/src/routers/github.py:685-694`

**Step 1: Write the test**

Add to `api/tests/unit/routers/test_github_sync_preview.py` (create if needed):

```python
"""Test sync preview endpoint enriches conflicts with metadata."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.github_sync_entity_metadata import extract_entity_metadata


def test_extract_entity_metadata_for_form():
    """Form files should extract name from JSON content."""
    content = b'{"name": "Contact Form", "fields": []}'
    metadata = extract_entity_metadata("forms/contact.form.json", content)

    assert metadata.entity_type == "form"
    assert metadata.display_name == "Contact Form"
    assert metadata.parent_slug is None


def test_extract_entity_metadata_for_agent():
    """Agent files should extract name from JSON content."""
    content = b'{"name": "Support Agent", "model": "gpt-4"}'
    metadata = extract_entity_metadata("agents/support.agent.json", content)

    assert metadata.entity_type == "agent"
    assert metadata.display_name == "Support Agent"
    assert metadata.parent_slug is None


def test_extract_entity_metadata_for_app():
    """App metadata should extract name and include parent_slug."""
    content = b'{"name": "Dashboard App", "version": "1.0"}'
    metadata = extract_entity_metadata("apps/dashboard/app.json", content)

    assert metadata.entity_type == "app"
    assert metadata.display_name == "Dashboard App"
    assert metadata.parent_slug == "dashboard"


def test_extract_entity_metadata_for_app_file():
    """App files should have parent_slug and relative path as display_name."""
    metadata = extract_entity_metadata("apps/dashboard/src/index.tsx", None)

    assert metadata.entity_type == "app_file"
    assert metadata.display_name == "src/index.tsx"
    assert metadata.parent_slug == "dashboard"
```

**Step 2: Run test to verify it passes (existing functionality)**

Run: `./test.sh api/tests/unit/routers/test_github_sync_preview.py -v`

Expected: PASS (these test existing `extract_entity_metadata` function)

**Step 3: Update sync preview endpoint to enrich conflicts**

In `api/src/routers/github.py`, find the `conflicts=` section around line 685 and update:

```python
# Add import at top of file if not present
from src.services.github_sync_entity_metadata import extract_entity_metadata

# ... in get_sync_preview function, around line 685 ...

# Build enriched conflicts list
enriched_conflicts = []
for c in preview.conflicts:
    # Extract metadata from remote content (or local if remote is None)
    content = c.remote_content or c.local_content
    content_bytes = content.encode("utf-8") if content else None
    metadata = extract_entity_metadata(c.path, content_bytes)

    enriched_conflicts.append(SyncConflictInfo(
        path=c.path,
        local_content=c.local_content,
        remote_content=c.remote_content,
        local_sha=c.local_sha,
        remote_sha=c.remote_sha,
        display_name=metadata.display_name,
        entity_type=metadata.entity_type,
        parent_slug=metadata.parent_slug,
    ))

return SyncPreviewResponse(
    to_pull=[...],  # unchanged
    to_push=[...],  # unchanged
    conflicts=enriched_conflicts,  # use enriched list
    # ... rest unchanged
)
```

**Step 4: Run type check**

Run: `cd api && pyright`

Expected: PASS

**Step 5: Run all tests**

Run: `./test.sh api/tests/`

Expected: PASS

**Step 6: Commit**

```bash
git add api/src/routers/github.py api/tests/unit/routers/test_github_sync_preview.py
git commit -m "feat(api): enrich sync conflicts with entity metadata"
```

---

## Task 3: Add endpoint for on-demand content fetching

**Files:**
- Modify: `api/src/routers/github.py` (add new endpoint)
- Modify: `api/src/models/contracts/github.py` (add request/response models)

**Step 1: Add request/response models**

In `api/src/models/contracts/github.py`, add after `SyncConflictInfo`:

```python
class SyncContentRequest(BaseModel):
    """Request to fetch content for diff preview."""
    path: str = Field(..., description="File path to fetch content for")
    source: Literal["local", "remote"] = Field(..., description="Which side to fetch")

    model_config = ConfigDict(from_attributes=True)


class SyncContentResponse(BaseModel):
    """Response with file content for diff preview."""
    path: str = Field(..., description="File path")
    content: str | None = Field(default=None, description="File content (null if not found)")

    model_config = ConfigDict(from_attributes=True)
```

**Step 2: Write the test**

Add to `api/tests/unit/routers/test_github_sync_content.py`:

```python
"""Test sync content endpoint for diff preview."""
import pytest


def test_sync_content_request_model():
    """SyncContentRequest should validate source field."""
    from src.models.contracts.github import SyncContentRequest

    req = SyncContentRequest(path="forms/test.form.json", source="local")
    assert req.source == "local"

    req = SyncContentRequest(path="forms/test.form.json", source="remote")
    assert req.source == "remote"


def test_sync_content_response_model():
    """SyncContentResponse should allow null content."""
    from src.models.contracts.github import SyncContentResponse

    # File exists
    resp = SyncContentResponse(path="forms/test.form.json", content='{"name": "Test"}')
    assert resp.content == '{"name": "Test"}'

    # File doesn't exist (new file)
    resp = SyncContentResponse(path="forms/new.form.json", content=None)
    assert resp.content is None
```

**Step 3: Run test to verify it fails then passes**

Run: `./test.sh api/tests/unit/routers/test_github_sync_content.py -v`

Expected: PASS after adding models

**Step 4: Add the endpoint**

In `api/src/routers/github.py`, add a new endpoint:

```python
@router.post(
    "/sync/content",
    response_model=SyncContentResponse,
    summary="Get content for diff preview",
    description="Fetch local or remote content for a file to display in diff view",
)
async def get_sync_content(
    request: SyncContentRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> SyncContentResponse:
    """
    Fetch file content for diff preview.

    - source="local": Read from database (serialized entity)
    - source="remote": Read from GitHub repo
    """
    try:
        config = await get_github_config(db, ctx.org_id)

        if not config or not config.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub not configured",
            )

        repo = _extract_repo_from_url(config.repo_url)

        sync_service = GitHubSyncService(
            db=db,
            github_token=config.token,
            repo=repo,
            branch=config.branch,
        )

        if request.source == "local":
            # Get serialized content from database
            content = await sync_service.get_local_content(request.path)
        else:
            # Get content from GitHub
            content = await sync_service.get_remote_content(request.path)

        return SyncContentResponse(path=request.path, content=content)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching sync content: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch content",
        )
```

**Step 5: Add helper methods to GitHubSyncService**

In `api/src/services/github_sync.py`, add methods to the `GitHubSyncService` class:

```python
async def get_local_content(self, path: str) -> str | None:
    """Get serialized content for a file from the database."""
    # Use existing virtual file serialization logic
    virtual_files = await self._serialize_entities_to_virtual_files()
    for vf in virtual_files:
        if vf.path == path:
            return vf.content
    return None

async def get_remote_content(self, path: str) -> str | None:
    """Get file content from GitHub."""
    try:
        response = await self.github.get_file_content(path)
        return response.content if response else None
    except Exception:
        return None
```

**Step 6: Run type check and tests**

Run: `cd api && pyright && cd .. && ./test.sh api/tests/`

Expected: PASS

**Step 7: Commit**

```bash
git add api/src/routers/github.py api/src/models/contracts/github.py api/src/services/github_sync.py api/tests/unit/routers/test_github_sync_content.py
git commit -m "feat(api): add endpoint for on-demand sync content fetching"
```

---

## Task 4: Regenerate TypeScript types

**Files:**
- Modify: `client/src/lib/v1.d.ts` (auto-generated)

**Step 1: Ensure dev stack is running**

Run: `docker ps --filter "name=bifrost" | grep -q "bifrost-dev-api" || ./debug.sh`

**Step 2: Regenerate types**

Run: `cd client && npm run generate:types`

Expected: Types regenerated with new `SyncConflictInfo` fields and `SyncContentRequest`/`SyncContentResponse`

**Step 3: Verify types include new fields**

Run: `grep -A 10 "SyncConflictInfo" client/src/lib/v1.d.ts`

Expected: Should show `display_name`, `entity_type`, `parent_slug` fields

**Step 4: Commit**

```bash
git add client/src/lib/v1.d.ts
git commit -m "chore(client): regenerate types with sync metadata fields"
```

---

## Task 5: Add groupConflicts function

**Files:**
- Modify: `client/src/components/editor/groupSyncActions.ts`

**Step 1: Add GroupedConflict interface and groupConflicts function**

In `client/src/components/editor/groupSyncActions.ts`, add:

```typescript
import type { SyncAction, SyncConflictInfo } from "@/hooks/useGitHub";

// ... existing GroupedEntity interface and groupSyncActions function ...

/**
 * A grouped conflict (for apps with child file conflicts)
 */
export interface GroupedConflict {
	/** The main conflict (app.json or standalone entity) */
	conflict: SyncConflictInfo;
	/** Child file conflicts for app entities */
	childConflicts: SyncConflictInfo[];
}

/**
 * Group conflicts by entity for display.
 *
 * Apps are grouped together with their child file conflicts.
 * Other entities (forms, agents, workflows) remain individual.
 */
export function groupConflicts(conflicts: SyncConflictInfo[]): GroupedConflict[] {
	const appGroups = new Map<string, GroupedConflict>();
	const standaloneConflicts: GroupedConflict[] = [];

	for (const conflict of conflicts) {
		if (conflict.entity_type === "app" && conflict.parent_slug) {
			// App metadata (app.json) - create or update group
			const existing = appGroups.get(conflict.parent_slug);
			if (existing) {
				// Replace placeholder with actual app metadata
				existing.conflict = conflict;
			} else {
				appGroups.set(conflict.parent_slug, {
					conflict,
					childConflicts: [],
				});
			}
		} else if (conflict.entity_type === "app_file" && conflict.parent_slug) {
			// App file - add to group
			const existing = appGroups.get(conflict.parent_slug);
			if (existing) {
				existing.childConflicts.push(conflict);
			} else {
				// Create placeholder group (app.json may come later or not be conflicted)
				appGroups.set(conflict.parent_slug, {
					conflict: {
						...conflict,
						entity_type: "app",
						display_name: conflict.parent_slug,
					} as SyncConflictInfo,
					childConflicts: [conflict],
				});
			}
		} else {
			// Standalone entity (form, agent, workflow, unknown)
			standaloneConflicts.push({
				conflict,
				childConflicts: [],
			});
		}
	}

	// Combine: apps first, then standalone entities
	// Sort apps by display name, standalone by display name
	const sortedApps = Array.from(appGroups.values()).sort((a, b) =>
		(a.conflict.display_name || "").localeCompare(b.conflict.display_name || "")
	);
	const sortedStandalone = standaloneConflicts.sort((a, b) =>
		(a.conflict.display_name || "").localeCompare(b.conflict.display_name || "")
	);

	return [...sortedApps, ...sortedStandalone];
}
```

**Step 2: Run type check**

Run: `cd client && npm run tsc`

Expected: PASS

**Step 3: Commit**

```bash
git add client/src/components/editor/groupSyncActions.ts
git commit -m "feat(client): add groupConflicts function for conflict grouping"
```

---

## Task 6: Add diff preview state to editor store

**Files:**
- Modify: `client/src/stores/editorStore.ts`

**Step 1: Add diff preview state and actions**

In `client/src/stores/editorStore.ts`, add to the interface and implementation:

```typescript
// Add to EditorState interface (around line 87)
export interface DiffPreviewState {
	path: string;
	displayName: string;
	entityType: string;
	localContent: string | null;
	remoteContent: string | null;
	isConflict: boolean;
	resolution?: "keep_local" | "keep_remote";
	onResolve?: (resolution: "keep_local" | "keep_remote") => void;
}

// Add to EditorState interface
interface EditorState {
	// ... existing fields ...

	// Diff preview state
	diffPreview: DiffPreviewState | null;

	// Actions
	// ... existing actions ...
	setDiffPreview: (preview: DiffPreviewState | null) => void;
	clearDiffPreview: () => void;
}

// Add to the store implementation (in create<EditorState>())
// Initial state
diffPreview: null,

// Actions
setDiffPreview: (preview) => set({ diffPreview: preview }),
clearDiffPreview: () => set({ diffPreview: null }),
```

**Step 2: Run type check**

Run: `cd client && npm run tsc`

Expected: PASS

**Step 3: Commit**

```bash
git add client/src/stores/editorStore.ts
git commit -m "feat(client): add diff preview state to editor store"
```

---

## Task 7: Create SyncDiffView component

**Files:**
- Create: `client/src/components/editor/SyncDiffView.tsx`

**Step 1: Create the diff view component**

Create `client/src/components/editor/SyncDiffView.tsx`:

```typescript
/**
 * SyncDiffView - Shows a readonly diff for sync preview
 *
 * Displays local vs remote content using Monaco DiffEditor.
 * For conflicts, includes resolution buttons.
 */

import { useEffect, useRef } from "react";
import { DiffEditor, type DiffOnMount } from "@monaco-editor/react";
import { useTheme } from "@/contexts/ThemeContext";
import type * as Monaco from "monaco-editor";
import { Button } from "@/components/ui/button";
import { X, FileText, Bot, AppWindow, Workflow, FileCode, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useEditorStore, type DiffPreviewState } from "@/stores/editorStore";

/** Icon mapping for entity types */
const ENTITY_ICONS = {
	form: { icon: FileText, className: "text-green-500" },
	agent: { icon: Bot, className: "text-orange-500" },
	app: { icon: AppWindow, className: "text-purple-500" },
	workflow: { icon: Workflow, className: "text-blue-500" },
	app_file: { icon: FileCode, className: "text-gray-500" },
} as const;

interface SyncDiffViewProps {
	preview: DiffPreviewState;
	isLoading?: boolean;
}

export function SyncDiffView({ preview, isLoading = false }: SyncDiffViewProps) {
	const { theme } = useTheme();
	const editorRef = useRef<Monaco.editor.IStandaloneDiffEditor | null>(null);
	const clearDiffPreview = useEditorStore((state) => state.clearDiffPreview);

	const handleMount: DiffOnMount = (editor) => {
		editorRef.current = editor;
	};

	useEffect(() => {
		return () => {
			editorRef.current?.dispose();
		};
	}, []);

	// Get entity icon
	const entityType = preview.entityType as keyof typeof ENTITY_ICONS | null;
	const iconConfig = entityType ? ENTITY_ICONS[entityType] : null;
	const IconComponent = iconConfig?.icon ?? FileCode;
	const iconClassName = iconConfig?.className ?? "text-gray-500";

	// Determine language from path
	const getLanguage = (path: string): string => {
		if (path.endsWith(".json")) return "json";
		if (path.endsWith(".py")) return "python";
		if (path.endsWith(".tsx") || path.endsWith(".ts")) return "typescript";
		if (path.endsWith(".jsx") || path.endsWith(".js")) return "javascript";
		return "plaintext";
	};

	return (
		<div className="flex flex-col h-full bg-background">
			{/* Header */}
			<div className="flex items-center justify-between p-3 border-b bg-muted/30">
				<div className="flex items-center gap-2">
					<IconComponent className={cn("h-5 w-5", iconClassName)} />
					<div>
						<h3 className="text-sm font-semibold">{preview.displayName}</h3>
						<p className="text-xs text-muted-foreground">{preview.path}</p>
					</div>
				</div>
				<Button
					variant="ghost"
					size="icon"
					className="h-6 w-6"
					onClick={clearDiffPreview}
					title="Close diff view"
				>
					<X className="h-4 w-4" />
				</Button>
			</div>

			{/* Diff labels */}
			<div className="flex border-b text-xs">
				<div className="flex-1 px-3 py-1 bg-red-500/10 text-center">
					Local (Database)
				</div>
				<div className="flex-1 px-3 py-1 bg-green-500/10 text-center">
					Incoming (GitHub)
				</div>
			</div>

			{/* Diff editor or loading state */}
			<div className="flex-1 min-h-0">
				{isLoading ? (
					<div className="flex h-full items-center justify-center">
						<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
					</div>
				) : (
					<DiffEditor
						height="100%"
						language={getLanguage(preview.path)}
						theme={theme === "dark" ? "vs-dark" : "light"}
						original={preview.localContent ?? ""}
						modified={preview.remoteContent ?? ""}
						onMount={handleMount}
						options={{
							readOnly: true,
							minimap: { enabled: false },
							scrollBeyondLastLine: false,
							renderSideBySide: true,
						}}
					/>
				)}
			</div>

			{/* Resolution buttons for conflicts */}
			{preview.isConflict && preview.onResolve && (
				<div className="flex items-center justify-end gap-2 p-3 border-t bg-muted/30">
					<Button
						variant="outline"
						size="sm"
						onClick={() => preview.onResolve?.("keep_local")}
						className={cn(
							preview.resolution === "keep_local" && "bg-blue-500 text-white"
						)}
					>
						Keep Local
					</Button>
					<Button
						variant="outline"
						size="sm"
						onClick={() => preview.onResolve?.("keep_remote")}
						className={cn(
							preview.resolution === "keep_remote" && "bg-blue-500 text-white"
						)}
					>
						Accept Incoming
					</Button>
				</div>
			)}
		</div>
	);
}
```

**Step 2: Run type check**

Run: `cd client && npm run tsc`

Expected: PASS

**Step 3: Commit**

```bash
git add client/src/components/editor/SyncDiffView.tsx
git commit -m "feat(client): add SyncDiffView component for diff preview"
```

---

## Task 8: Add API service for sync content

**Files:**
- Modify: `client/src/services/` (add or extend GitHub service)

**Step 1: Add fetchSyncContent function**

Create or add to `client/src/services/githubService.ts`:

```typescript
import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type SyncContentRequest = components["schemas"]["SyncContentRequest"];
export type SyncContentResponse = components["schemas"]["SyncContentResponse"];

export async function fetchSyncContent(
	path: string,
	source: "local" | "remote"
): Promise<string | null> {
	const response = await apiClient.post<SyncContentResponse>(
		"/api/github/sync/content",
		{ path, source }
	);
	return response.content ?? null;
}
```

**Step 2: Run type check**

Run: `cd client && npm run tsc`

Expected: PASS

**Step 3: Commit**

```bash
git add client/src/services/githubService.ts
git commit -m "feat(client): add fetchSyncContent API service"
```

---

## Task 9: Update CodeEditor to show diff view

**Files:**
- Modify: `client/src/components/editor/CodeEditor.tsx`

**Step 1: Import and render SyncDiffView**

In `client/src/components/editor/CodeEditor.tsx`:

```typescript
// Add imports
import { SyncDiffView } from "./SyncDiffView";

// In the component, add after existing state hooks
const diffPreview = useEditorStore((state) => state.diffPreview);

// Add early return for diff preview (before the regular editor render)
// Place after the gitConflict check around line 510
if (diffPreview) {
	return <SyncDiffView preview={diffPreview} />;
}
```

**Step 2: Run type check**

Run: `cd client && npm run tsc`

Expected: PASS

**Step 3: Commit**

```bash
git add client/src/components/editor/CodeEditor.tsx
git commit -m "feat(client): show SyncDiffView when diff preview is active"
```

---

## Task 10: Update SourceControlPanel with click handlers and grouped conflicts

**Files:**
- Modify: `client/src/components/editor/SourceControlPanel.tsx`

**Step 1: Import new dependencies**

Add imports at top of file:

```typescript
import { groupConflicts, type GroupedConflict } from "./groupSyncActions";
import { useEditorStore, type DiffPreviewState } from "@/stores/editorStore";
import { fetchSyncContent } from "@/services/githubService";
```

**Step 2: Add click handler for diff preview**

Add inside `SourceControlPanel` function:

```typescript
const setDiffPreview = useEditorStore((state) => state.setDiffPreview);

// Handler for clicking any sync item to show diff
const handleShowDiff = async (
	path: string,
	displayName: string,
	entityType: string,
	localContent: string | null,
	remoteContent: string | null,
	isConflict: boolean,
	resolution?: "keep_local" | "keep_remote",
	onResolve?: (res: "keep_local" | "keep_remote") => void
) => {
	// Set preview immediately with available content
	setDiffPreview({
		path,
		displayName,
		entityType,
		localContent,
		remoteContent,
		isConflict,
		resolution,
		onResolve,
	});

	// If missing content, fetch it
	if (localContent === null && !isConflict) {
		try {
			const content = await fetchSyncContent(path, "local");
			setDiffPreview((prev) => prev ? { ...prev, localContent: content } : null);
		} catch (error) {
			console.error("Failed to fetch local content:", error);
		}
	}
	if (remoteContent === null && !isConflict) {
		try {
			const content = await fetchSyncContent(path, "remote");
			setDiffPreview((prev) => prev ? { ...prev, remoteContent: content } : null);
		} catch (error) {
			console.error("Failed to fetch remote content:", error);
		}
	}
};
```

**Step 3: Update ConflictList to use groupConflicts**

Replace the `ConflictList` component (around line 727-794) to use grouped conflicts and pass click handler:

```typescript
function ConflictList({
	conflicts,
	resolutions,
	onResolve,
	onShowDiff,
}: {
	conflicts: SyncConflictInfo[];
	resolutions: Record<string, "keep_local" | "keep_remote">;
	onResolve: (path: string, resolution: "keep_local" | "keep_remote") => void;
	onShowDiff: (
		path: string,
		displayName: string,
		entityType: string,
		localContent: string | null,
		remoteContent: string | null,
		isConflict: boolean,
		resolution?: "keep_local" | "keep_remote",
		onResolve?: (res: "keep_local" | "keep_remote") => void
	) => void;
}) {
	const [expanded, setExpanded] = useState(true);
	const resolvedCount = Object.keys(resolutions).length;
	const groupedConflicts = groupConflicts(conflicts);

	// Count total individual conflicts (including children)
	const totalConflicts = groupedConflicts.reduce(
		(acc, g) => acc + 1 + g.childConflicts.length,
		0
	);

	return (
		<div className={cn("border-t flex flex-col min-h-0", expanded && "flex-1")}>
			{/* Header - unchanged */}
			<button
				onClick={() => setExpanded(!expanded)}
				className="w-full px-4 py-2 flex items-center gap-2 hover:bg-muted/30 transition-colors text-left flex-shrink-0"
			>
				{expanded ? (
					<ChevronDown className="h-4 w-4 flex-shrink-0" />
				) : (
					<ChevronRight className="h-4 w-4 flex-shrink-0" />
				)}
				<AlertCircle className="h-4 w-4 text-orange-500 flex-shrink-0" />
				<span className="text-sm font-medium flex-1 truncate">Conflicts</span>
				<span
					className={cn(
						"text-xs w-10 text-center py-0.5 rounded-full flex-shrink-0",
						resolvedCount === totalConflicts
							? "bg-green-500/20 text-green-700"
							: "bg-orange-500/20 text-orange-700"
					)}
				>
					{resolvedCount}/{totalConflicts}
				</span>
			</button>
			{expanded && (
				<div className="flex-1 overflow-y-auto px-4 pb-2 min-h-0">
					{groupedConflicts.map((group) => (
						<ConflictGroupItem
							key={group.conflict.path}
							group={group}
							resolutions={resolutions}
							onResolve={onResolve}
							onShowDiff={onShowDiff}
						/>
					))}
				</div>
			)}
		</div>
	);
}
```

**Step 4: Create ConflictGroupItem component**

Add new component in the same file:

```typescript
/**
 * Renders a single conflict group (standalone entity or app with children)
 */
function ConflictGroupItem({
	group,
	resolutions,
	onResolve,
	onShowDiff,
}: {
	group: GroupedConflict;
	resolutions: Record<string, "keep_local" | "keep_remote">;
	onResolve: (path: string, resolution: "keep_local" | "keep_remote") => void;
	onShowDiff: (
		path: string,
		displayName: string,
		entityType: string,
		localContent: string | null,
		remoteContent: string | null,
		isConflict: boolean,
		resolution?: "keep_local" | "keep_remote",
		onResolve?: (res: "keep_local" | "keep_remote") => void
	) => void;
}) {
	const [expanded, setExpanded] = useState(false);
	const { conflict, childConflicts } = group;
	const hasChildren = childConflicts.length > 0;
	const resolution = resolutions[conflict.path];

	// Handle resolving all children at once
	const handleResolveAll = (res: "keep_local" | "keep_remote") => {
		onResolve(conflict.path, res);
		childConflicts.forEach((child) => onResolve(child.path, res));
	};

	// Check if all (including children) are resolved
	const allResolved = resolution !== undefined &&
		childConflicts.every((c) => resolutions[c.path] !== undefined);

	const handleClick = () => {
		onShowDiff(
			conflict.path,
			conflict.display_name || conflict.path,
			conflict.entity_type || "workflow",
			conflict.local_content ?? null,
			conflict.remote_content ?? null,
			true,
			resolution,
			(res) => onResolve(conflict.path, res)
		);
	};

	return (
		<div className="py-1">
			{/* Main entity row */}
			<div
				className={cn(
					"flex items-center gap-2 text-xs py-1.5 px-2 rounded cursor-pointer",
					!allResolved && "bg-orange-500/10",
					allResolved && "bg-green-500/10",
					"hover:bg-muted/50"
				)}
				onClick={handleClick}
			>
				{/* Expand/collapse for apps with children */}
				{hasChildren ? (
					<button
						onClick={(e) => {
							e.stopPropagation();
							setExpanded(!expanded);
						}}
						className="p-0.5 hover:bg-muted rounded"
					>
						{expanded ? (
							<ChevronDown className="h-3 w-3" />
						) : (
							<ChevronRight className="h-3 w-3" />
						)}
					</button>
				) : (
					<span className="w-4" />
				)}

				{/* Entity icon */}
				<EntityIcon entityType={conflict.entity_type} />

				{/* Display name */}
				<span className="flex-1 truncate" title={conflict.path}>
					{conflict.display_name || conflict.path}
				</span>

				{/* Child count for apps */}
				{hasChildren && (
					<span className="text-xs text-muted-foreground">
						{childConflicts.length} file{childConflicts.length !== 1 ? "s" : ""}
					</span>
				)}
			</div>

			{/* Resolution buttons */}
			<div className="flex gap-1 ml-8 mt-1">
				<button
					onClick={(e) => {
						e.stopPropagation();
						handleResolveAll("keep_local");
					}}
					className={cn(
						"px-2 py-0.5 text-xs rounded",
						allResolved && resolution === "keep_local"
							? "bg-blue-500 text-white"
							: "bg-muted hover:bg-muted/80"
					)}
				>
					{hasChildren ? "Keep All Local" : "Keep Local"}
				</button>
				<button
					onClick={(e) => {
						e.stopPropagation();
						handleResolveAll("keep_remote");
					}}
					className={cn(
						"px-2 py-0.5 text-xs rounded",
						allResolved && resolution === "keep_remote"
							? "bg-blue-500 text-white"
							: "bg-muted hover:bg-muted/80"
					)}
				>
					{hasChildren ? "Accept All Incoming" : "Accept Incoming"}
				</button>
			</div>

			{/* Expanded child conflicts */}
			{hasChildren && expanded && (
				<div className="ml-6 mt-1 border-l-2 border-muted pl-2 space-y-0.5">
					{childConflicts.map((child) => {
						const childResolution = resolutions[child.path];
						return (
							<div
								key={child.path}
								className={cn(
									"flex items-center gap-2 text-xs py-1 px-1 rounded cursor-pointer",
									!childResolution && "text-orange-500",
									childResolution && "text-green-500",
									"hover:bg-muted/30"
								)}
								onClick={() => onShowDiff(
									child.path,
									child.display_name || child.path,
									child.entity_type || "app_file",
									child.local_content ?? null,
									child.remote_content ?? null,
									true,
									childResolution,
									(res) => onResolve(child.path, res)
								)}
							>
								<FileCode className="h-3 w-3" />
								<span className="truncate">{child.display_name || child.path}</span>
								{childResolution && (
									<span className="text-xs text-muted-foreground ml-auto">
										{childResolution === "keep_local" ? "local" : "incoming"}
									</span>
								)}
							</div>
						);
					})}
				</div>
			)}
		</div>
	);
}

// Helper component for entity icons
function EntityIcon({ entityType }: { entityType: string | null | undefined }) {
	const ENTITY_ICONS = {
		form: { icon: FileText, className: "text-green-500" },
		agent: { icon: Bot, className: "text-orange-500" },
		app: { icon: AppWindow, className: "text-purple-500" },
		workflow: { icon: Workflow, className: "text-blue-500" },
		app_file: { icon: FileCode, className: "text-gray-500" },
	} as const;

	const iconConfig = entityType ? ENTITY_ICONS[entityType as keyof typeof ENTITY_ICONS] : null;
	const IconComponent = iconConfig?.icon ?? FileCode;
	const iconClassName = iconConfig?.className ?? "text-gray-500";

	return <IconComponent className={cn("h-4 w-4 flex-shrink-0", iconClassName)} />;
}
```

**Step 5: Update ConflictList usage to pass onShowDiff**

In the main render, update the `ConflictList` call:

```typescript
{(syncPreview.conflicts?.length ?? 0) > 0 && (
	<ConflictList
		conflicts={syncPreview.conflicts ?? []}
		resolutions={conflictResolutions}
		onResolve={handleResolveConflict}
		onShowDiff={handleShowDiff}
	/>
)}
```

**Step 6: Add missing imports**

```typescript
import {
	// ... existing imports ...
	FileText,
	Bot,
	AppWindow,
	Workflow,
	FileCode,
} from "lucide-react";
```

**Step 7: Run type check and lint**

Run: `cd client && npm run tsc && npm run lint`

Expected: PASS

**Step 8: Commit**

```bash
git add client/src/components/editor/SourceControlPanel.tsx
git commit -m "feat(client): add grouped conflicts and click-to-preview in source control"
```

---

## Task 11: Add click handlers for incoming/outgoing items

**Files:**
- Modify: `client/src/components/editor/EntitySyncItem.tsx`
- Modify: `client/src/components/editor/SourceControlPanel.tsx`

**Step 1: Add onClick prop to EntitySyncItem**

In `client/src/components/editor/EntitySyncItem.tsx`, add click handler support:

```typescript
interface EntitySyncItemProps {
	/** The primary sync action (for single entities) or app metadata (for apps) */
	action: SyncAction;
	/** Child files for app entities */
	childFiles?: SyncAction[];
	/** Whether this is a conflict item */
	isConflict?: boolean;
	/** Resolution state for conflicts */
	resolution?: "keep_local" | "keep_remote";
	/** Callback for conflict resolution */
	onResolve?: (resolution: "keep_local" | "keep_remote") => void;
	/** Callback when item is clicked for preview */
	onClick?: () => void;
	/** Callback when child file is clicked for preview */
	onChildClick?: (childAction: SyncAction) => void;
}

export function EntitySyncItem({
	action,
	childFiles = [],
	isConflict = false,
	resolution,
	onResolve,
	onClick,
	onChildClick,
}: EntitySyncItemProps) {
	// ... existing code ...

	return (
		<div className="py-1">
			{/* Main entity row - add onClick */}
			<div
				className={cn(
					"flex items-center gap-2 text-xs py-1.5 px-2 rounded",
					isConflict && !resolution && "bg-orange-500/10",
					isConflict && resolution && "bg-green-500/10",
					!isConflict && "hover:bg-muted/30",
					onClick && "cursor-pointer"
				)}
				onClick={onClick}
			>
				{/* ... existing content ... */}
			</div>

			{/* ... conflict resolution buttons (if applicable) ... */}

			{/* Expanded app files - add click handler */}
			{hasChildren && expanded && (
				<div className="ml-6 mt-1 border-l-2 border-muted pl-2 space-y-0.5">
					{childFiles.map((file) => (
						<div
							key={file.path}
							className={cn(
								"flex items-center gap-2 text-xs py-0.5 px-1 text-muted-foreground",
								onChildClick && "cursor-pointer hover:bg-muted/30 rounded"
							)}
							onClick={() => onChildClick?.(file)}
						>
							{getActionIcon(file.action)}
							<FileCode className="h-3 w-3" />
							<span className="truncate">{file.display_name || file.path}</span>
						</div>
					))}
				</div>
			)}
		</div>
	);
}
```

**Step 2: Update SyncActionList to pass click handlers**

In `SourceControlPanel.tsx`, update `SyncActionList`:

```typescript
function SyncActionList({
	title,
	icon,
	actions,
	direction,
	onShowDiff,
}: {
	title: string;
	icon: React.ReactNode;
	actions: SyncAction[];
	direction: "incoming" | "outgoing";
	onShowDiff: (
		path: string,
		displayName: string,
		entityType: string,
		localContent: string | null,
		remoteContent: string | null
	) => void;
}) {
	const [expanded, setExpanded] = useState(true);
	const groupedEntities = groupSyncActions(actions);

	return (
		<div className={cn("border-t flex flex-col min-h-0", expanded && "flex-1")}>
			{/* ... existing header ... */}
			{expanded && (
				<div className="flex-1 overflow-y-auto px-4 pb-2 min-h-0">
					{groupedEntities.map((entity) => (
						<EntitySyncItem
							key={entity.action.path}
							action={entity.action}
							childFiles={entity.childFiles}
							onClick={() => onShowDiff(
								entity.action.path,
								entity.action.display_name || entity.action.path,
								entity.action.entity_type || "workflow",
								direction === "outgoing" ? null : null,  // Will be fetched
								direction === "incoming" ? null : null   // Will be fetched
							)}
							onChildClick={(child) => onShowDiff(
								child.path,
								child.display_name || child.path,
								child.entity_type || "app_file",
								null,
								null
							)}
						/>
					))}
				</div>
			)}
		</div>
	);
}
```

**Step 3: Update SyncActionList usage**

```typescript
{(syncPreview.to_pull?.length ?? 0) > 0 && (
	<SyncActionList
		title="Incoming"
		icon={<Download className="h-4 w-4 text-blue-500 flex-shrink-0" />}
		actions={syncPreview.to_pull ?? []}
		direction="incoming"
		onShowDiff={(path, displayName, entityType, local, remote) =>
			handleShowDiff(path, displayName, entityType, local, remote, false)
		}
	/>
)}

{(syncPreview.to_push?.length ?? 0) > 0 && (
	<SyncActionList
		title="Outgoing"
		icon={<Upload className="h-4 w-4 text-green-500 flex-shrink-0" />}
		actions={syncPreview.to_push ?? []}
		direction="outgoing"
		onShowDiff={(path, displayName, entityType, local, remote) =>
			handleShowDiff(path, displayName, entityType, local, remote, false)
		}
	/>
)}
```

**Step 4: Run type check and lint**

Run: `cd client && npm run tsc && npm run lint`

Expected: PASS

**Step 5: Commit**

```bash
git add client/src/components/editor/EntitySyncItem.tsx client/src/components/editor/SourceControlPanel.tsx
git commit -m "feat(client): add click-to-preview for incoming/outgoing sync items"
```

---

## Task 12: Final verification and cleanup

**Step 1: Run full backend checks**

Run: `cd api && pyright && ruff check .`

Expected: PASS

**Step 2: Run all backend tests**

Run: `./test.sh api/tests/`

Expected: All tests pass

**Step 3: Run full frontend checks**

Run: `cd client && npm run tsc && npm run lint`

Expected: PASS

**Step 4: Manual testing**

1. Start the dev stack: `./debug.sh`
2. Open the editor and navigate to Source Control
3. Make changes that create incoming/outgoing/conflict states
4. Verify:
   - Apps in conflicts are grouped with child files
   - Clicking any item opens diff view in editor
   - Forms/agents show display names, not filenames
   - Resolution buttons work at both app and file level
5. Close diff view by clicking X or navigating to file tree

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat: complete code editor sync UI improvements

- Group app conflicts with expandable child files
- Click any sync item to see diff preview in editor
- Show entity display names instead of filenames for conflicts
- Add on-demand content fetching for diff view
- Support app-level and per-file conflict resolution"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add metadata fields to SyncConflictInfo | `github.py` |
| 2 | Enrich conflicts with metadata | `routers/github.py` |
| 3 | Add content fetch endpoint | `routers/github.py`, `github.py` |
| 4 | Regenerate TypeScript types | `v1.d.ts` |
| 5 | Add groupConflicts function | `groupSyncActions.ts` |
| 6 | Add diff preview state to store | `editorStore.ts` |
| 7 | Create SyncDiffView component | `SyncDiffView.tsx` |
| 8 | Add API service for content | `githubService.ts` |
| 9 | Update CodeEditor for diff view | `CodeEditor.tsx` |
| 10 | Update SourceControlPanel | `SourceControlPanel.tsx` |
| 11 | Add click handlers to items | `EntitySyncItem.tsx`, `SourceControlPanel.tsx` |
| 12 | Final verification | All |
