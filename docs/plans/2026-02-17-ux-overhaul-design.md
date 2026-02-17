# UX Overhaul Design

**Date:** 2026-02-17
**Scope:** Comprehensive UX improvements across the Bifrost frontend

---

## Phase 1: Quick Wins (Bugs, Polish, Consistency)

All changes below are small, mechanical, and independent of each other.

### 1.1 Functional Bugs

**`RoleDialog.tsx` -- `can_promote_agent` never submitted (silent data loss)**
- Add `can_promote_agent: values.can_promote_agent` to the mutation body in `onSubmit` (L77-91)

**ChatInput placeholder buttons -- disable until wired up**
- Add `disabled` prop and reduced opacity to Plus and Paperclip buttons (L275-292) so they don't appear interactive

**`Agents.tsx` -- dead `canManageAgents` conditional**
- Remove `const canManageAgents = true` (L110) and all conditional branches that reference it. Simplify to unconditional rendering.

**`AgentDialog.tsx` -- temperature slider silently commits value**
- When `llm_temperature` is null, show slider as disabled/dimmed with a "Customize" toggle. Only enable the slider and commit a value when the user explicitly opts in.

### 1.2 Enter Key for Form Submission

Wrap all form-like UI in `<form onSubmit>`:

| File | Section | Change |
|------|---------|--------|
| `BasicInfo.tsx` | Display name save | Wrap in `<form onSubmit={handleSaveName}>` |
| `BasicInfo.tsx` | Password change | Wrap in `<form onSubmit={handleChangePassword}>` |
| `LLMConfig.tsx` | Main AI config | Wrap primary settings in `<form onSubmit={handleSave}>` |
| `LLMConfig.tsx` | Model pricing inline edit | Enter key triggers save on focused row |
| `FormBuilder.tsx` | Form save | Wrap page content in `<form onSubmit={handleSave}>` |

### 1.3 Error Boundaries

**Create `<PageErrorBoundary>`** -- a reusable error boundary that:
- Preserves the layout shell (sidebar + header)
- Shows a contextual error message with "Try Again" button in the content area
- Resets on navigation (key on `location.pathname`)

**Apply to:**
- Each `<Route>` element's lazy-loaded component
- `EditorOverlay` (Monaco + multiple panels is high crash risk)

### 1.4 Empty States

Standardize all empty state CTAs to use full-text buttons:

```tsx
// Before
<Button variant="outline" size="icon"><Plus /></Button>

// After
<Button variant="outline"><Plus className="mr-2 h-4 w-4" /> Create your first table</Button>
```

Apply to:
- `Tables.tsx` (L400-408)
- `TableDetail.tsx` (L553-560)
- `Agents.tsx` (L556-565)
- `Workflows.tsx` -- add "Open Editor" CTA to empty state (currently has no CTA)

**Dashboard improvements:**
- Make metric cards clickable links to their respective pages
- Add "View all" link on "Recent Failures" → `/history?status=failed`

### 1.5 Consistency Fixes

| Issue | Fix |
|-------|-----|
| Raw `<input type="checkbox">` in `CreateIntegrationDialog.tsx` L391 | Replace with shadcn `<Checkbox>` |
| `<Dialog>` for destructive confirm in `LLMConfig.tsx` L712 | Replace with `<AlertDialog>` |
| Icon-only Execute button in Workflows table view | Add text label to match grid view |
| Layout duplication between `Layout.tsx` and `ContentLayout.tsx` | Extract `useSidebar()` hook |
| Button sizing inconsistency | Convention: `size="icon-sm"` in table rows, `size="icon"` in headers/toolbars |

### 1.6 Accessibility

- **Icon-only buttons:** Add `aria-label` to all icon-only action buttons across list pages (Agents, Workflows, Tables, TableDetail)
- **Avatar upload:** Add `tabIndex={0}`, `role="button"`, `onKeyDown` handler for Enter/Space to `BasicInfo.tsx` avatar zone
- **Settings tabs overflow:** Add `overflow-x-auto` to `TabsList` wrapper in `Settings.tsx`
- **AgentDialog focus:** Add `autoFocus` to Name input field
- **Validation errors:** Add `aria-live="polite"` to error `<Alert>` in `CreateEventSourceDialog.tsx`

### 1.7 Performance Fixes (Critical)

**`FormRenderer.tsx` L318 -- `loadDataProviders` dependency loop**
- Remove `dataProviderState.loading` from the `useCallback` dependency array. Loading state changes shouldn't recreate the function.

**`EditorLayout.tsx` L97 -- fragile `setTimeout(200)` event chain**
- Replace custom event chain (`run-editor-file` → `execute-editor-file` with 200ms delay) with a ref-based approach. Use `useImperativeHandle` or a callback ref so the Run panel can be invoked directly without timing assumptions.

**`TableDetail.tsx` L106-110 -- misleading search**
- Update search placeholder to "Search this page..." to clarify it only searches the current page of results. (Server-side search is a larger effort, defer.)

### 1.8 Responsive

- **Settings tabs:** horizontal scroll on narrow viewports (covered in 1.6)
- **Agents grid:** Follow Workflows pattern -- adjust grid columns based on sidebar state using `useIsDesktop()`

---

## Phase 2: Split Large Files

Break the five 1500+ line monolithic files into focused sub-components.

### 2.1 `EntityManagement.tsx` (1,844 lines)

Split into:
- `EntityManagement.tsx` -- page shell, tab routing, state management
- `EntityTypeList.tsx` -- entity type list/grid with CRUD
- `EntityInstanceList.tsx` -- entity instance list with filtering
- `EntityDialog.tsx` -- create/edit entity dialog
- `EntityImportExport.tsx` -- import/export functionality

### 2.2 `IntegrationDetail.tsx` (1,834 lines)

Split into:
- `IntegrationDetail.tsx` -- page shell, tabs, breadcrumbs
- `IntegrationOverview.tsx` -- overview/status tab content
- `IntegrationConfig.tsx` -- configuration fields tab
- `IntegrationActions.tsx` -- actions/methods tab
- `IntegrationTestPanel.tsx` -- test/debug panel

### 2.3 `ExecutionDetails.tsx` (1,767 lines)

Split into:
- `ExecutionDetails.tsx` -- page shell, header, status
- `ExecutionTimeline.tsx` -- step timeline/log view
- `ExecutionStepDetail.tsx` -- individual step detail panel
- `ExecutionInputOutput.tsx` -- input/output JSON viewers

### 2.4 `UsageReports.tsx` (1,758 lines)

Split into:
- `UsageReports.tsx` -- page shell, date range picker, filters
- `UsageCharts.tsx` -- chart components (trend, breakdown)
- `UsageTable.tsx` -- tabular data view
- `UsageSummaryCards.tsx` -- top-level metric cards

### 2.5 `FileTree.tsx` (1,715 lines)

Split into:
- `FileTree.tsx` -- tree container, scroll, state management
- `FileTreeNode.tsx` -- individual tree node (recursive)
- `FileTreeContextMenu.tsx` -- right-click context menu
- `FileTreeDragDrop.tsx` -- drag and drop logic
- `useFileTreeActions.ts` -- hook for create/rename/delete/upload operations

---

## Phase 3: Editor Merge (Dedicated Worktree)

Consolidate the App Builder into the workspace Code Editor.

### 3.1 Core Concept

The workspace Code Editor becomes the single editor for all file types. When app files are open, app-specific features (preview, dependencies) become available. The separate `AppCodeEditorLayout` is eliminated.

### 3.2 Changes to the Code Editor

**Context-aware sidebar tabs:**
- Existing: File Explorer, Search, Source Control, Run
- New: **Dependencies** tab (package icon) -- appears when an app file is active. Shows npm packages with add/remove UI. Replaces the package management that was in the App Builder.

**Preview panel (VS Code-style):**
- When an app file (`.tsx`, `.jsx`, `.css`, etc.) is active, a preview toggle icon appears in the editor tab bar
- Clicking it opens a resizable side-by-side preview panel (iframe rendering the app)
- Preview hot-reloads on save via the existing WebSocket mechanism
- Panel is closable, toggleable, remembers open/closed state per session

**File awareness:**
- Editor detects file type from extension and path
- App files under `_apps/` paths trigger app-specific behavior (preview availability, dependency tab)
- Workflow files (`.py` with `@workflow` decorator) continue to trigger Run panel behavior

### 3.3 "Edit App" Dialog

On the Apps list page, add an Edit button (alongside the existing code/open buttons, mirroring how Workflows have code + edit buttons):

The dialog contains:
- **General tab:** App name, description, slug
- **Embed tab:** Embed settings (moved from `EmbedSettingsDialog`), HMAC secrets management, code snippets
- **Publish tab:** Publish/unpublish controls, preview URL

This replaces the current disconnected embed configuration.

### 3.4 Apps List Page Changes

- Add "Edit" icon button to each app card/row (opens the Edit dialog)
- "Code" button opens the workspace editor navigated to the app's files
- Remove the current `AppCodeEditorLayout` route (`/apps/:id/edit/*`)

### 3.5 Files to Delete After Migration

- `components/app-code-editor/AppCodeEditorLayout.tsx` (553 lines)
- `components/app-code-editor/` directory (all files)
- Move `EmbedSettingsDialog.tsx` content into the new Edit App dialog

### 3.6 Files to Modify

- `components/editor/EditorLayout.tsx` -- add Dependencies sidebar tab, preview panel
- `components/editor/CodeEditor.tsx` -- add preview toggle icon for app files
- `components/editor/EditorSidebar.tsx` (new) -- extracted sidebar tab management
- `pages/Apps.tsx` -- add Edit button, remove editor route
- `App.tsx` -- remove `/apps/:id/edit/*` routes

---

## Phasing Summary

| Phase | Scope | Approach |
|-------|-------|----------|
| Phase 1 | Bugs, Enter key, error boundaries, empty states, consistency, a11y, perf, responsive | Work on main branch, one commit per category |
| Phase 2 | Split 5 large files | Work on main branch, one commit per file split |
| Phase 3 | Editor merge | Dedicated worktree, feature branch |
