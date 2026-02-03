# File Tree Scope Refactor Design

## Overview

Refactor the code editor file tree to remove organization containers at the top level. Instead, show a flat VS Code-style folder hierarchy with organization/scope displayed as metadata below each file name.

## Current State

- File tree grouped by Organization at the top level (virtual "org containers")
- Users navigate: `Global` → `workflows/` → `file.py`
- Or: `Acme Corp` → `forms/` → `contact.py`
- Pathing isn't logically grouped by org, which causes confusion

## Proposed Changes

### 1. Flat File Hierarchy

Remove org containers. Show standard folder structure:

```
workflows/
  myworkflow.py
  Global                    ← smaller, muted, italic

  another.py
  Acme Corp                 ← smaller, muted, italic

forms/
  contact.py
  Acme Corp

config.json                 ← no scope line (workspace file)
```

### 2. Scope Filter at Top

Add filtering controls above the file tree:

- **OrganizationSelect** - Restyled with no margins to blend into panel
- **"Include Global" checkbox** - Toggle to include/exclude global-scoped files

When an org is selected, only files belonging to that org (plus global if toggled) are shown.

### 3. Scope Indicator Per File

Below each filename, display the scope:

- Text only (no icon)
- Smaller font size
- Muted color
- Italicized
- No additional indentation

Files without scope (plain workspace_files) show no second line.

### 4. Change Scope via Context Menu

Right-click on scoped files shows "Change Scope..." option:

- Opens a small popover with OrganizationSelect component
- Selecting a new org triggers the existing cross-org move logic
- Only available for files with `entityType` (workflows, forms, agents)
- Not available for plain workspace files

## Implementation Details

### orgScopedFileOperations.ts

**Remove org container creation:**

```typescript
async list(path: string): Promise<FileNode[]> {
  // Remove the root org container logic
  // Return files directly, filtered by selected org scope
}
```

**Add organizationName to metadata:**

```typescript
function toFileNode(file: FileMetadata, orgId: string | null, orgName: string): FileNode {
  return {
    // ... existing fields
    metadata: {
      realPath: file.path,
      organizationId: orgId,
      organizationName: orgName,  // NEW: for display
    },
  };
}
```

### FileTree.tsx

**Add scope line below filename (~line 996):**

```tsx
<span className="flex-1 truncate">{file.name}</span>
// ... after the button closes

{/* Scope indicator for org-scoped files */}
{file.metadata?.organizationName && (
  <span className="text-xs text-muted-foreground italic ml-6">
    {file.metadata.organizationName}
  </span>
)}
```

**Add context menu item for changing scope (~line 1013):**

```tsx
{file.entityType && (
  <>
    <ContextMenuSeparator />
    <ContextMenuItem onClick={() => onChangeScope(file)}>
      <Building2 className="mr-2 h-4 w-4" />
      Change Scope...
    </ContextMenuItem>
  </>
)}
```

**Add popover with OrganizationSelect:**

State for popover visibility and selected file. On org selection, call `operations.rename()` with the new org path (existing cross-org move logic handles the API call).

### WorkspaceFileTree.tsx

**Add filter state:**

```typescript
const [selectedOrgId, setSelectedOrgId] = useState<string | null | undefined>(undefined);
const [includeGlobal, setIncludeGlobal] = useState(true);
```

**Add filter UI above FileTree:**

```tsx
<div className="border-b">
  <OrganizationSelect
    value={selectedOrgId}
    onChange={setSelectedOrgId}
    showAll={true}
    showGlobal={false}
    className="border-0 rounded-none"  // Blend into panel
  />
  <label className="flex items-center gap-2 px-3 py-2 text-sm">
    <Checkbox checked={includeGlobal} onCheckedChange={setIncludeGlobal} />
    Include Global
  </label>
</div>
<FileTree ... />
```

**Filter logic:**

Either wrap the operations to filter results, or pass filter criteria to a modified `createOrgScopedFileOperations()`.

### Styling

- Increase default sidebar width slightly to accommodate the scope metadata line
- OrganizationSelect: remove outer margins/padding, no visible border when closed

## Files to Modify

1. `client/src/services/orgScopedFileOperations.ts`
   - Remove org container creation at root
   - Add `organizationName` to metadata
   - Accept filter parameters

2. `client/src/components/file-tree/FileTree.tsx`
   - Add scope line rendering below filename
   - Add "Change Scope..." context menu item
   - Add popover with OrganizationSelect for scope changes

3. `client/src/components/file-tree/WorkspaceFileTree.tsx`
   - Add filter state and UI (OrganizationSelect + Include Global checkbox)
   - Wire up filtering to operations

4. `client/src/components/file-tree/icons.ts`
   - Remove org container icon handling (no longer needed)

## Out of Scope

- Changes to backend APIs (existing PATCH endpoints handle org changes)
- Changes to how files are stored
- Multi-select scope changes
