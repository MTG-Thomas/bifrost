# Org-Grouped File Tree Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Group workspace files by organization with drag-drop to move entities between orgs.

**Architecture:** Backend adds `organization_id` to file list response. Frontend adapter transforms flat list into org-grouped tree structure. FileTree gets minimal changes for org container handling.

**Tech Stack:** Python/FastAPI, TypeScript/React, SQLAlchemy, TanStack Query

---

## Phase 1: Cleanup (Remove App Virtual Files)

### Task 1.1: Remove app handling from github_sync_virtual_files.py

**Files:**
- Modify: `api/src/services/github_sync_virtual_files.py`

**Step 1: Update module docstring (lines 1-19)**

Replace the docstring to remove app references:

```python
"""
Virtual File Provider for GitHub Sync.

Platform entities (forms, agents) don't exist in the `workspace_files` table -
they live in their own database tables. The VirtualFileProvider serializes these
entities on-the-fly so they can participate in GitHub sync.

Virtual files are generated from database entities with:
- Portable workflow refs (UUID -> path::function_name)
- Computed git blob SHA for fast comparison
- Standardized path patterns

Path patterns:
- Forms: forms/{form.id}.form.json
- Agents: agents/{agent.id}.agent.json

Note: Apps are NOT synced via virtual files - they use the app_files table
and will have their own sync mechanism.
"""
```

**Step 2: Remove Application imports (line 30-31)**

Remove:
```python
from src.models.orm.applications import Application, AppVersion
```

**Step 3: Update UUID regex pattern (lines 39-44)**

Change from:
```python
# Matches: {uuid}.app.json, {uuid}.form.json, {uuid}.agent.json
UUID_FILENAME_PATTERN = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"\.(app|form|agent)\.json$"
)
```

To:
```python
# Matches: {uuid}.form.json, {uuid}.agent.json
UUID_FILENAME_PATTERN = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"\.(form|agent)\.json$"
)
```

**Step 4: Update VirtualFile docstring (lines 48-61)**

Update entity_type description from `"app", "form", or "agent"` to `"form" or "agent"`.

**Step 5: Update SerializationError docstring (lines 69-83)**

Update entity_type comment from `# "app", "form", or "agent"` to `# "form" or "agent"`.

**Step 6: Remove app from get_all_virtual_files (lines 126-163)**

Remove these lines:
```python
app_result = await self._get_app_files(workflow_map)
```
```python
virtual_files.extend(app_result.files)
```
```python
errors.extend(app_result.errors)
```

Update the log message:
```python
logger.info(
    f"Generated {len(virtual_files)} virtual files: "
    f"{len(form_result.files)} forms, {len(agent_result.files)} agents, "
    f"{len(errors)} errors"
)
```

**Step 7: Delete `_get_app_files` method entirely (lines 165-218)**

Remove the entire method.

**Step 8: Update get_virtual_file_by_id to remove app handling (around line 360)**

Remove:
```python
if entity_type == "app":
    return await self._get_app_file_by_id(entity_uuid, workflow_map)
```

**Step 9: Delete `_get_app_file_by_id` method entirely (lines 370-402)**

Remove the entire method.

**Step 10: Delete `_serialize_app_to_json` method entirely (lines 467-506)**

Remove the entire method.

**Step 11: Update `get_entity_type_from_path` static method (lines 551-569)**

Remove:
```python
if path.startswith("apps/") and path.endswith(".app.json"):
    return "app"
```

**Step 12: Update `is_virtual_file_path` static method (lines 571-586)**

Remove:
```python
(path.startswith("apps/") and path.endswith(".app.json"))
or
```

**Step 13: Verify no pyright errors**

Run: `cd api && pyright src/services/github_sync_virtual_files.py`
Expected: No errors

---

### Task 1.2: Remove app handling from github_sync.py

**Files:**
- Modify: `api/src/services/github_sync.py`

**Step 1: Remove Application import from `_delete_virtual_file` (line 1624)**

In the `_delete_virtual_file` method, remove:
```python
from src.models.orm.applications import Application
```

**Step 2: Remove app deletion handling (lines 1640-1643)**

Remove:
```python
if entity_type == "app":
    stmt = delete(Application).where(Application.id == entity_id)
    await self.db.execute(stmt)
    logger.debug(f"Deleted app {entity_id}")
```

**Step 3: Verify no pyright errors**

Run: `cd api && pyright src/services/github_sync.py`
Expected: No errors

---

### Task 1.3: Remove app detection from entity_detector.py

**Files:**
- Modify: `api/src/services/file_storage/entity_detector.py`

**Step 1: Update module docstring (lines 1-14)**

Remove `- Apps (.app.json)` from the list.

**Step 2: Update function docstring (lines 20-39)**

Remove `- Apps (.app.json): stored in applications table` and update return type description.

**Step 3: Remove app detection (lines 43-44)**

Remove:
```python
if path.endswith(".app.json"):
    return "app"
```

**Step 4: Verify no pyright errors**

Run: `cd api && pyright src/services/file_storage/entity_detector.py`
Expected: No errors

---

### Task 1.4: Remove app handling from file_storage/service.py

**Files:**
- Modify: `api/src/services/file_storage/service.py`

**Step 1: Remove app case from `_extract_metadata` (lines 474-475)**

Remove:
```python
elif path.endswith(".app.json"):
    entity_type = "app"
```

**Step 2: Verify no pyright errors**

Run: `cd api && pyright src/services/file_storage/service.py`
Expected: No errors

---

### Task 1.5: Remove unused OrganizationScopeDialog

**Files:**
- Delete: `client/src/components/editor/OrganizationScopeDialog.tsx`

**Step 1: Delete the file**

```bash
rm client/src/components/editor/OrganizationScopeDialog.tsx
```

**Step 2: Verify no TypeScript errors**

Run: `cd client && npm run tsc`
Expected: No errors (file was not imported anywhere)

---

### Task 1.6: Commit cleanup changes

**Step 1: Stage and commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: Remove .app.json virtual file handling and unused OrganizationScopeDialog

Apps will use app_files table with separate sync mechanism.
Entities now default to role_based access with no roles.

- Remove app serialization from github_sync_virtual_files.py
- Remove app deletion from github_sync.py
- Remove .app.json detection from entity_detector.py
- Remove app case from file_storage/service.py
- Delete unused OrganizationScopeDialog.tsx

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2: Backend - Add organization_id to File List

### Task 2.1: Add organization_id to FileMetadata model

**Files:**
- Modify: `api/src/models/contracts/editor.py`

**Step 1: Add organization_id field to FileMetadata (after line 42)**

```python
class FileMetadata(BaseModel):
    """
    File or folder metadata
    Used in directory listing responses
    """
    path: str = Field(..., description="Relative path from /home/repo")
    name: str = Field(..., description="File or folder name")
    type: FileType = Field(..., description="File or folder")
    size: int | None = Field(default=None, description="Size in bytes (null for folders)")
    extension: str | None = Field(default=None, description="File extension (null for folders)")
    modified: str = Field(..., description="Last modified timestamp (ISO 8601)")
    entity_type: Literal["workflow", "form", "agent", "module"] | None = Field(
        default=None,
        description="Platform entity type if file is a platform entity, null for regular files"
    )
    entity_id: str | None = Field(
        default=None,
        description="Platform entity ID if file is a platform entity, null for regular files"
    )
    organization_id: str | None = Field(
        default=None,
        description="Organization ID for scoped entities, null for global or non-entity files"
    )

    model_config = ConfigDict(from_attributes=True)
```

Note: Also remove "app" from the entity_type Literal since we removed app handling.

**Step 2: Verify no pyright errors**

Run: `cd api && pyright src/models/contracts/editor.py`
Expected: No errors

---

### Task 2.2: Fetch organization_id in list_files_editor endpoint

**Files:**
- Modify: `api/src/routers/files.py`

**Step 1: Add helper function to batch-fetch org IDs (before list_files_editor)**

```python
async def _fetch_entity_org_ids(
    db: AsyncSession,
    entity_ids_by_type: dict[str, list[str]],
) -> dict[str, str | None]:
    """
    Batch-fetch organization_id for entities grouped by type.

    Returns a mapping of entity_id -> organization_id (or None for global).
    """
    from uuid import UUID
    from sqlalchemy import select
    from src.models.orm import Workflow, Form, Agent

    org_map: dict[str, str | None] = {}

    # Fetch workflow org IDs
    workflow_ids = entity_ids_by_type.get("workflow", [])
    if workflow_ids:
        stmt = select(Workflow.id, Workflow.organization_id).where(
            Workflow.id.in_([UUID(id) for id in workflow_ids])
        )
        result = await db.execute(stmt)
        for row in result:
            org_map[str(row.id)] = str(row.organization_id) if row.organization_id else None

    # Fetch form org IDs
    form_ids = entity_ids_by_type.get("form", [])
    if form_ids:
        stmt = select(Form.id, Form.organization_id).where(
            Form.id.in_([UUID(id) for id in form_ids])
        )
        result = await db.execute(stmt)
        for row in result:
            org_map[str(row.id)] = str(row.organization_id) if row.organization_id else None

    # Fetch agent org IDs
    agent_ids = entity_ids_by_type.get("agent", [])
    if agent_ids:
        stmt = select(Agent.id, Agent.organization_id).where(
            Agent.id.in_([UUID(id) for id in agent_ids])
        )
        result = await db.execute(stmt)
        for row in result:
            org_map[str(row.id)] = str(row.organization_id) if row.organization_id else None

    return org_map
```

**Step 2: Update list_files_editor to include organization_id**

```python
async def list_files_editor(
    ctx: Context,
    user: CurrentSuperuser,
    path: str = Query(..., description="Directory path relative to workspace root"),
    recursive: bool = Query(default=False, description="If true, return all files recursively"),
    db: AsyncSession = Depends(get_db),
) -> list[FileMetadata]:
    """
    List files and folders in a directory with rich metadata.

    Cloud mode only - used by browser editor.
    """
    try:
        storage = FileStorageService(db)
        workspace_files = await storage.list_files(path, recursive=recursive)

        # Collect entity IDs by type for batch org lookup
        entity_ids_by_type: dict[str, list[str]] = {}
        for wf in workspace_files:
            if wf.entity_type and wf.entity_id:
                entity_ids_by_type.setdefault(wf.entity_type, []).append(str(wf.entity_id))

        # Batch-fetch organization IDs
        org_map = await _fetch_entity_org_ids(db, entity_ids_by_type)

        files = []
        for wf in workspace_files:
            is_folder = wf.path.endswith("/")
            clean_path = wf.path.rstrip("/") if is_folder else wf.path
            entity_id_str = str(wf.entity_id) if wf.entity_id else None

            files.append(FileMetadata(
                path=clean_path,
                name=clean_path.split("/")[-1],
                type=FileType.FOLDER if is_folder else FileType.FILE,
                size=wf.size_bytes if not is_folder else None,
                extension=wf.path.split(".")[-1] if "." in wf.path and not is_folder else None,
                modified=wf.updated_at.isoformat() if wf.updated_at else datetime.now(timezone.utc).isoformat(),
                entity_type=wf.entity_type if not is_folder else None,
                entity_id=entity_id_str,
                organization_id=org_map.get(entity_id_str) if entity_id_str else None,
            ))
        return files

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Directory not found: {path}")
```

**Step 3: Add imports at top of file**

Add to imports section:
```python
from src.models.orm import Workflow, Form, Agent
```

**Step 4: Verify no pyright errors**

Run: `cd api && pyright src/routers/files.py`
Expected: No errors

---

### Task 2.3: Regenerate TypeScript types

**Step 1: Ensure dev stack is running**

```bash
docker ps --filter "name=bifrost" | grep -q "bifrost-dev-api" || ./debug.sh
```

**Step 2: Generate types**

```bash
cd client && npm run generate:types
```

**Step 3: Verify organization_id appears in generated types**

Check `client/src/lib/v1.d.ts` contains `organization_id` in `FileMetadata`.

---

### Task 2.4: Commit backend changes

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(api): Add organization_id to file list response

Batch-fetches org IDs from workflow/form/agent tables and includes
in FileMetadata response. Enables frontend to group files by org.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3: Frontend - Org-Scoped File Operations Adapter

### Task 3.1: Create orgScopedFileOperations adapter

**Files:**
- Create: `client/src/services/orgScopedFileOperations.ts`

**Step 1: Create the adapter file**

```typescript
/**
 * Org-Scoped File Operations Adapter
 *
 * Wraps the workspace fileService and transforms the flat file list into
 * an org-grouped structure with virtual org containers at the root.
 *
 * Path convention:
 * - Root lists org containers: "org:global", "org:{uuid}"
 * - Within org: "org:global/workflows/file.py" -> real path "workflows/file.py"
 */

import type { FileNode, FileContent, FileOperations } from "@/components/file-tree/types";
import { fileService, type FileMetadata } from "./fileService";
import { authFetch } from "@/lib/api-client";

export interface Organization {
	id: string;
	name: string;
}

const ORG_PREFIX = "org:";
const GLOBAL_ORG_ID = "global";

/**
 * Extract org ID from virtual path
 * "org:global/workflows/file.py" -> "global"
 * "org:abc123/forms/test.json" -> "abc123"
 */
function extractOrgId(path: string): string | null {
	if (!path.startsWith(ORG_PREFIX)) return null;
	const withoutPrefix = path.slice(ORG_PREFIX.length);
	const slashIdx = withoutPrefix.indexOf("/");
	return slashIdx === -1 ? withoutPrefix : withoutPrefix.slice(0, slashIdx);
}

/**
 * Extract real path from virtual path
 * "org:global/workflows/file.py" -> "workflows/file.py"
 * "org:abc123" -> ""
 */
function extractRealPath(path: string): string {
	if (!path.startsWith(ORG_PREFIX)) return path;
	const withoutPrefix = path.slice(ORG_PREFIX.length);
	const slashIdx = withoutPrefix.indexOf("/");
	return slashIdx === -1 ? "" : withoutPrefix.slice(slashIdx + 1);
}

/**
 * Build virtual path from org ID and real path
 */
function buildVirtualPath(orgId: string | null, realPath: string): string {
	const orgPart = orgId ?? GLOBAL_ORG_ID;
	return realPath ? `${ORG_PREFIX}${orgPart}/${realPath}` : `${ORG_PREFIX}${orgPart}`;
}

/**
 * Convert FileMetadata to FileNode with virtual path
 */
function toFileNode(file: FileMetadata, orgId: string | null): FileNode {
	return {
		path: buildVirtualPath(orgId, file.path),
		name: file.name,
		type: file.type,
		size: file.size,
		extension: file.extension,
		modified: file.modified,
		entityType: file.entity_type,
		entityId: file.entity_id,
		metadata: {
			realPath: file.path,
			organizationId: orgId,
		},
	};
}

/**
 * Create org container FileNode
 */
function createOrgContainer(orgId: string | null, orgName: string): FileNode {
	return {
		path: `${ORG_PREFIX}${orgId ?? GLOBAL_ORG_ID}`,
		name: orgName,
		type: "folder",
		size: null,
		extension: null,
		modified: new Date().toISOString(),
		metadata: {
			isOrgContainer: true,
			organizationId: orgId,
		},
	};
}

/**
 * Update entity organization via API
 */
async function updateEntityOrganization(
	entityType: string,
	entityId: string,
	organizationId: string | null,
): Promise<void> {
	const endpoints: Record<string, string> = {
		workflow: `/api/workflows/${entityId}`,
		form: `/api/forms/${entityId}`,
		agent: `/api/agents/${entityId}`,
	};

	const endpoint = endpoints[entityType];
	if (!endpoint) {
		throw new Error(`Unknown entity type: ${entityType}`);
	}

	const response = await authFetch(endpoint, {
		method: "PATCH",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ organization_id: organizationId }),
	});

	if (!response.ok) {
		throw new Error(`Failed to update ${entityType} organization: ${response.statusText}`);
	}
}

/**
 * Create org-scoped file operations adapter
 *
 * @param organizations - List of available organizations
 * @returns FileOperations implementation with org grouping
 */
export function createOrgScopedFileOperations(
	organizations: Organization[],
): FileOperations {
	// Build org name lookup
	const orgNames = new Map<string, string>(
		organizations.map((org) => [org.id, org.name]),
	);
	orgNames.set(GLOBAL_ORG_ID, "Global");

	return {
		async list(path: string): Promise<FileNode[]> {
			// Root: return org containers
			if (path === "") {
				// Fetch all files recursively to determine which orgs have content
				const allFiles = await fileService.listFilesEditor("", true);

				// Group files by organization
				const orgHasContent = new Set<string>();
				orgHasContent.add(GLOBAL_ORG_ID); // Always show Global

				for (const file of allFiles) {
					const orgId = file.organization_id ?? GLOBAL_ORG_ID;
					orgHasContent.add(orgId);
				}

				// Build org containers (Global first, then others alphabetically)
				const containers: FileNode[] = [createOrgContainer(null, "Global")];

				const sortedOrgs = organizations
					.filter((org) => orgHasContent.has(org.id))
					.sort((a, b) => a.name.localeCompare(b.name));

				for (const org of sortedOrgs) {
					containers.push(createOrgContainer(org.id, org.name));
				}

				return containers;
			}

			// Within an org container
			const orgId = extractOrgId(path);
			if (orgId === null) {
				// Shouldn't happen, but fallback to direct listing
				const files = await fileService.listFilesEditor(path);
				return files.map((f) => toFileNode(f, null));
			}

			const realPath = extractRealPath(path);
			const dbOrgId = orgId === GLOBAL_ORG_ID ? null : orgId;

			// Fetch files at this real path
			const files = await fileService.listFilesEditor(realPath);

			// Filter to only files belonging to this org
			// For folders, include if any descendant belongs to this org
			const filteredFiles = files.filter((file) => {
				if (file.type === "folder") {
					// Folders are always included - they'll be empty if no matching files
					return true;
				}
				// For files, check org match (null org = global)
				const fileOrgId = file.organization_id ?? null;
				return fileOrgId === dbOrgId;
			});

			return filteredFiles.map((f) => toFileNode(f, dbOrgId));
		},

		async read(path: string): Promise<FileContent> {
			const realPath = extractRealPath(path);
			const response = await fileService.readFile(realPath);
			return {
				content: response.content,
				encoding: response.encoding as "utf-8" | "base64",
				etag: response.etag,
			};
		},

		async write(
			path: string,
			content: string,
			encoding?: "utf-8" | "base64",
			etag?: string,
		): Promise<void> {
			const realPath = extractRealPath(path);
			await fileService.writeFile(realPath, content, encoding, etag);
		},

		async createFolder(path: string): Promise<void> {
			const realPath = extractRealPath(path);
			await fileService.createFolder(realPath);
		},

		async delete(path: string): Promise<void> {
			const realPath = extractRealPath(path);
			await fileService.deleteFile(realPath);
		},

		async rename(oldPath: string, newPath: string): Promise<void> {
			const oldOrgId = extractOrgId(oldPath);
			const newOrgId = extractOrgId(newPath);
			const oldRealPath = extractRealPath(oldPath);
			const newRealPath = extractRealPath(newPath);

			// Check if this is a cross-org move (drag to different org container)
			if (oldOrgId !== newOrgId && newRealPath === "") {
				// This is a drop onto an org container - need to update entity org
				// The file's metadata should have entityType and entityId
				// We'll need to refetch the file info to get these
				const files = await fileService.listFilesEditor("", true);
				const file = files.find((f) => f.path === oldRealPath);

				if (file?.entity_type && file?.entity_id) {
					const targetOrgId = newOrgId === GLOBAL_ORG_ID ? null : newOrgId;
					await updateEntityOrganization(file.entity_type, file.entity_id, targetOrgId);
					return;
				}

				throw new Error("Cannot move non-entity files between organizations");
			}

			// Regular rename/move within same org
			await fileService.renameFile(oldRealPath, newRealPath);
		},
	};
}
```

**Step 2: Verify no TypeScript errors**

Run: `cd client && npm run tsc`
Expected: No errors

---

### Task 3.2: Add listFilesEditor with recursive option to fileService

**Files:**
- Modify: `client/src/services/fileService.ts`

**Step 1: Check if listFilesEditor exists and supports recursive**

If not, add or update:

```typescript
async listFilesEditor(path: string, recursive = false): Promise<FileMetadata[]> {
	const params = new URLSearchParams({ path });
	if (recursive) {
		params.set("recursive", "true");
	}
	const response = await authFetch(`/api/files/editor?${params}`);
	if (!response.ok) {
		throw new Error(`Failed to list files: ${response.statusText}`);
	}
	return response.json();
}
```

**Step 2: Export FileMetadata type if not already exported**

Ensure `FileMetadata` type is exported from fileService (or re-exported from generated types).

**Step 3: Verify no TypeScript errors**

Run: `cd client && npm run tsc`
Expected: No errors

---

### Task 3.3: Commit adapter

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(client): Add org-scoped file operations adapter

Creates orgScopedFileOperations.ts that wraps fileService and
transforms flat file list into org-grouped structure with virtual
containers. Supports cross-org entity moves via PATCH API.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4: Frontend - FileTree Changes

### Task 4.1: Update FileTree to handle org containers

**Files:**
- Modify: `client/src/components/file-tree/FileTree.tsx`

**Step 1: Add org container checks in context menu**

Find the context menu section and add checks for `metadata?.isOrgContainer`:

```typescript
// In the context menu items, disable rename/delete for org containers
const isOrgContainer = file.metadata?.isOrgContainer === true;

// Hide rename option for org containers
{!isOrgContainer && config?.enableRename && (
	<ContextMenuItem onClick={() => handleStartRename(file)}>
		Rename
	</ContextMenuItem>
)}

// Hide delete option for org containers
{!isOrgContainer && config?.enableDelete && (
	<ContextMenuItem onClick={() => handleDelete(file)}>
		Delete
	</ContextMenuItem>
)}
```

**Step 2: Disable drag on org containers**

In the drag handler setup:

```typescript
// Don't allow dragging org containers
const canDrag = config?.enableDragMove && !file.metadata?.isOrgContainer;

<div
	draggable={canDrag}
	onDragStart={canDrag ? (e) => handleDragStart(e, file) : undefined}
	// ...
>
```

**Step 3: Verify no TypeScript errors**

Run: `cd client && npm run tsc`
Expected: No errors

---

### Task 4.2: Add cross-org move confirmation dialog

**Files:**
- Modify: `client/src/components/file-tree/FileTree.tsx`

**Step 1: Add state for confirmation dialog**

```typescript
const [pendingOrgMove, setPendingOrgMove] = useState<{
	file: FileNode;
	targetOrg: FileNode;
} | null>(null);
```

**Step 2: Update handleDrop to detect cross-org moves**

```typescript
const handleDrop = async (e: React.DragEvent, targetFolder: FileNode | null) => {
	e.preventDefault();
	setDragOverFolder(null);

	// ... existing validation ...

	// Check if this is a cross-org move (dropping onto org container)
	if (targetFolder?.metadata?.isOrgContainer) {
		const sourceOrgId = draggedFile.metadata?.organizationId;
		const targetOrgId = targetFolder.metadata?.organizationId;

		if (sourceOrgId !== targetOrgId) {
			// Check if file is an entity (can be moved between orgs)
			if (!draggedFile.entityType) {
				toast.error("Only entities can be moved between organizations");
				return;
			}

			// Show confirmation dialog
			setPendingOrgMove({ file: draggedFile, targetOrg: targetFolder });
			return;
		}
	}

	// ... existing move logic ...
};
```

**Step 3: Add confirmation dialog UI**

```typescript
// Add near other dialogs in the component
<AlertDialog open={!!pendingOrgMove} onOpenChange={() => setPendingOrgMove(null)}>
	<AlertDialogContent>
		<AlertDialogHeader>
			<AlertDialogTitle>Move to {pendingOrgMove?.targetOrg.name}?</AlertDialogTitle>
			<AlertDialogDescription>
				Move "{pendingOrgMove?.file.name}" to {pendingOrgMove?.targetOrg.name}?
				This will change which organization has access to this entity.
			</AlertDialogDescription>
		</AlertDialogHeader>
		<AlertDialogFooter>
			<AlertDialogCancel>Cancel</AlertDialogCancel>
			<AlertDialogAction onClick={handleConfirmOrgMove}>Move</AlertDialogAction>
		</AlertDialogFooter>
	</AlertDialogContent>
</AlertDialog>
```

**Step 4: Add confirm handler**

```typescript
const handleConfirmOrgMove = async () => {
	if (!pendingOrgMove) return;

	const { file, targetOrg } = pendingOrgMove;
	setPendingOrgMove(null);

	try {
		// The adapter's rename() handles cross-org moves specially
		await operations.rename(file.path, targetOrg.path);
		toast.success(`Moved to ${targetOrg.name}`);
		await refreshTree();
	} catch (error) {
		toast.error(error instanceof Error ? error.message : "Failed to move");
	}
};
```

**Step 5: Add AlertDialog imports**

```typescript
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
```

**Step 6: Verify no TypeScript errors**

Run: `cd client && npm run tsc`
Expected: No errors

---

### Task 4.3: Commit FileTree changes

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(file-tree): Add org container handling and cross-org move confirmation

- Disable drag/rename/delete on org containers
- Show confirmation dialog when moving entities between orgs
- Use adapter's rename() for cross-org moves (calls entity PATCH API)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5: Frontend - Wire Up Workspace Editor

### Task 5.1: Update WorkspaceFileTree to use org-scoped adapter

**Files:**
- Modify: `client/src/components/editor/WorkspaceFileTree.tsx`

**Step 1: Add imports**

```typescript
import { createOrgScopedFileOperations } from "@/services/orgScopedFileOperations";
import { useOrganizations } from "@/hooks/useOrganizations";
import { orgScopedIconResolver } from "@/components/file-tree/icons";
```

**Step 2: Fetch organizations and create adapter**

```typescript
// Inside the component
const { data: organizations = [] } = useOrganizations();

const operations = useMemo(
	() => createOrgScopedFileOperations(organizations),
	[organizations],
);
```

**Step 3: Update FileTree usage**

```typescript
<FileTree
	operations={operations}
	iconResolver={orgScopedIconResolver}
	editor={editorCallbacks}
	config={{
		enableUpload: true,
		enableDragMove: true,
		enableCreate: true,
		enableRename: true,
		enableDelete: true,
	}}
	refreshTrigger={refreshTrigger}
/>
```

**Step 4: Verify no TypeScript errors**

Run: `cd client && npm run tsc`
Expected: No errors

---

### Task 5.2: Create useOrganizations hook if needed

**Files:**
- Create or verify: `client/src/hooks/useOrganizations.ts`

**Step 1: Check if hook exists**

If not, create:

```typescript
import { useQuery } from "@tanstack/react-query";
import { authFetch } from "@/lib/api-client";

export interface Organization {
	id: string;
	name: string;
	domain?: string;
}

export function useOrganizations() {
	return useQuery({
		queryKey: ["organizations"],
		queryFn: async (): Promise<Organization[]> => {
			const response = await authFetch("/api/organizations");
			if (!response.ok) {
				throw new Error("Failed to fetch organizations");
			}
			return response.json();
		},
	});
}
```

**Step 2: Verify no TypeScript errors**

Run: `cd client && npm run tsc`
Expected: No errors

---

### Task 5.3: Commit workspace integration

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(editor): Wire up org-scoped file tree in workspace editor

Uses orgScopedFileOperations adapter with useOrganizations hook
to display files grouped by organization with drag-drop support.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6: Add Entity Management Prompt

### Task 6.1: Add prompt after git sync success

**Files:**
- Modify: `client/src/components/editor/SourceControlPanel.tsx`

**Step 1: Add Link import**

```typescript
import { Link } from "react-router-dom";
```

**Step 2: Update success handler (around line 332-343)**

After `toast.success("Synced with GitHub");`, add an info toast:

```typescript
if (complete.status === "success") {
	toast.success("Synced with GitHub");

	// Check if there were incoming changes that may include new entities
	const hadIncomingChanges = (syncPreview?.to_pull?.length ?? 0) > 0;
	if (hadIncomingChanges) {
		toast.info(
			<div className="flex flex-col gap-1">
				<span>New entities have restricted access by default.</span>
				<Link
					to="/entity-management"
					className="text-primary underline hover:no-underline"
				>
					Go to Entity Management to assign access
				</Link>
			</div>,
			{ duration: 8000 },
		);
	}

	// ... rest of success handling ...
}
```

**Step 3: Verify no TypeScript errors**

Run: `cd client && npm run tsc`
Expected: No errors

---

### Task 6.2: Commit Entity Management prompt

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(source-control): Add Entity Management prompt after git sync

Shows info toast after successful sync with incoming changes,
prompting users to visit Entity Management to assign access.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 7: Testing & Verification

### Task 7.1: Run full test suite

**Step 1: Run backend tests**

```bash
./test.sh
```

Expected: All tests pass

**Step 2: Run frontend type check and lint**

```bash
cd client && npm run tsc && npm run lint
```

Expected: No errors

### Task 7.2: Manual verification checklist

- [ ] File tree shows org containers at root (Global + orgs with content)
- [ ] Expanding org container shows files grouped by path within that org
- [ ] Org containers have Building2 icon (orange) and cannot be renamed/deleted
- [ ] Dragging entity to different org shows confirmation dialog
- [ ] Confirming move updates entity organization and refreshes tree
- [ ] Module files (non-entities) cannot be dragged between orgs
- [ ] Git sync with incoming changes shows Entity Management prompt
- [ ] `.app.json` files no longer appear in virtual file list

### Task 7.3: Final commit

```bash
git add -A
git commit -m "$(cat <<'EOF'
test: Verify org-grouped file tree implementation

- All backend tests pass
- TypeScript compilation clean
- Manual verification complete

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## File Summary

### New Files
- `client/src/services/orgScopedFileOperations.ts` - Adapter for org grouping
- `client/src/hooks/useOrganizations.ts` - Hook to fetch orgs (if not exists)

### Modified Files (Backend)
- `api/src/services/github_sync_virtual_files.py` - Remove app handling
- `api/src/services/github_sync.py` - Remove app deletion
- `api/src/services/file_storage/entity_detector.py` - Remove app detection
- `api/src/services/file_storage/service.py` - Remove app case
- `api/src/models/contracts/editor.py` - Add organization_id to FileMetadata
- `api/src/routers/files.py` - Fetch org IDs in list endpoint

### Modified Files (Frontend)
- `client/src/services/fileService.ts` - Add recursive option to listFilesEditor
- `client/src/components/file-tree/FileTree.tsx` - Org container handling
- `client/src/components/editor/WorkspaceFileTree.tsx` - Use org-scoped adapter
- `client/src/components/editor/SourceControlPanel.tsx` - Entity Management prompt

### Deleted Files
- `client/src/components/editor/OrganizationScopeDialog.tsx` - Unused
