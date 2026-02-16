# Dependency Management UI

**Goal:** Add a UI panel in the app code editor for managing npm dependencies declared in `app.yaml`. Users can search for packages, add them, remove them, and edit versions — all without manually editing YAML.

## Design

### Location

The left sidebar in `AppCodeEditorLayout` toggles between two views:
- **Files** (existing file tree) — folder icon tab
- **Packages** (new) — cube/package icon tab

A toolbar button (📦) also activates the Packages tab.

### Layout

```
┌──────────────────────────────────────────────────────┐
│ Toolbar  [...view modes...]  [📦]  [Save] [Publish]  │
├──────────┬───────────────────────────────────────────┤
│ [Files]  │                                           │
│[Packages]│        Editor / Preview                   │
│──────────│                                           │
│ 🔍 Search│                                           │
│──────────│                                           │
│ recharts │                                           │
│   2.15 ✕ │                                           │
│ dayjs    │                                           │
│   1.11 ✕ │                                           │
│          │                                           │
├──────────┴───────────────────────────────────────────┤
│ Status Bar                                           │
└──────────────────────────────────────────────────────┘
```

### Panel Contents

1. **Search input** at the top — queries npm registry (`https://registry.npmjs.org/-/v1/search?text=...`) for autocomplete. Shows results in a dropdown: package name, description snippet, latest version.
2. **Installed packages list** — each row shows package name, version (click to edit inline), and a remove (X) button.
3. **Add from search** — selecting a search result adds the package at its latest version.

### Data Flow

- **Read:** Parse `app.yaml` from the file listing API (already fetched by the editor's file tree).
- **Write:** Update `app.yaml` via `PUT /api/applications/{appId}/files/app.yaml`. This triggers server-side compilation + WebSocket push, so the preview updates automatically.
- **No new API endpoints needed.** The existing file CRUD API handles everything.

### npm Registry Search

Use the public npm registry search API:
```
GET https://registry.npmjs.org/-/v1/search?text=recharts&size=8
```

Returns package name, description, latest version. No auth required. Debounce at 300ms.

### Sidebar Tab Switching

Two icon buttons at the top of the sidebar area:
- Folder icon → Files (file tree)
- Package icon → Packages

State stored in component state (not persisted). Defaults to Files.

### Edge Cases

- **No app.yaml yet:** Create it on first dependency add with `name: <app-name>` + `dependencies:` section.
- **Malformed app.yaml:** Show error message, offer to reset the dependencies section.
- **Network errors on npm search:** Show "Search failed" message, allow manual entry as fallback.
- **Max 20 dependencies:** Enforced by backend validation. Show count in the panel header.

## Files Changed

| File | Change |
|------|--------|
| `client/src/components/app-code-editor/AppCodeEditorLayout.tsx` | Add sidebar tab switching, toolbar button, render DependencyPanel |
| `client/src/components/app-code-editor/DependencyPanel.tsx` | **NEW** — search, list, add/remove/edit deps |
| `client/src/hooks/useAppDependencies.ts` | **NEW** — hook for reading/writing deps from app.yaml via file API |
| `client/src/lib/npm-search.ts` | **NEW** — npm registry search with debounce |
