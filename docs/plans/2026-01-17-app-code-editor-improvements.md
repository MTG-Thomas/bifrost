# App Code Editor Improvements

Follow-up to the JSX App Builder design. Addresses MCP tooling, real-time updates, and UX polish.

## Changes

### 1. Rename JSX → Code

The "code" engine supports TSX, TS, and potentially other file types. Rename throughout:

**Database:**
- `app_jsx_files` → `app_code_files`
- `engine: "jsx"` → `engine: "code"`

**Backend:**
- `JsxFile` model → `AppCodeFile`
- `JsxFileCreate`, etc. → `AppCodeFileCreate`, etc.
- Router file: `app_jsx_files.py` → `app_code_files.py`

**Frontend:**
- `jsx-editor/` → `app-code-editor/`
- `JsxEditorLayout` → `AppCodeEditorLayout`
- `JsxCodeEditor` → `AppCodeEditor`
- `JsxPreview` → `AppCodePreview`
- `useJsxEditor` → `useAppCodeEditor`
- `jsx-compiler.ts` → `app-code-compiler.ts`
- `jsx-runtime.ts` → `app-code-runtime.ts`
- `jsx-platform/` → `app-code-platform/`
- `JsxApplicationEditor.tsx` → `AppCodeEditor.tsx` (page)

### 2. MCP Tools for Code Files

Add tools to the Bifrost MCP server for programmatic file management:

| Tool | Parameters | Description |
|------|------------|-------------|
| `code_list_files` | `app_id` | List all code files for an app's draft version |
| `code_get_file` | `app_id`, `path` | Get a file's source and compiled code |
| `code_create_file` | `app_id`, `path`, `source` | Create a new file |
| `code_update_file` | `app_id`, `path`, `source` | Update an existing file |
| `code_delete_file` | `app_id`, `path` | Delete a file |

Tools use the draft version automatically—no version ID needed.

**Implementation:**
- Add tools to `/api/src/services/mcp_server/server.py`
- Call existing REST endpoints internally
- Return compiled code + any compilation errors

### 3. WebSocket Real-Time Updates

Use existing `app:draft:{app_id}` channel for live updates.

**Backend changes:**
- Ensure `publish_app_draft_update()` payload includes: `path`, `source`, `compiled`, `action` (created/updated/deleted)

**Frontend changes:**
- Create `useAppCodeUpdates(appId)` hook that subscribes to WebSocket
- On file created/deleted: invalidate file tree query
- On file updated:
  - If current file → update Monaco editor + preview
  - Show toast: "File updated externally"

**Flow:**
1. MCP tool calls `code_update_file`
2. Backend saves, compiles, broadcasts via WebSocket
3. Editor receives message with `{ path, source, compiled }`
4. Monaco and preview update immediately

### 4. UX Improvements

#### 4.1 Creation Flow → Modal

Replace full-page creation form with modal:

1. User clicks "New App" on `/apps`
2. `AppEngineSelector` dialog appears (existing)
3. If "Code Editor" selected → show creation modal (name, slug, description, org)
4. On create → navigate to editor with files scaffolded

#### 4.2 Initial File Scaffolding

When creating a code-engine app, auto-create starter files:

**`_layout`:**
```tsx
const user = useUser();

return (
  <div className="min-h-screen flex flex-col">
    <header className="h-14 border-b flex items-center justify-between px-6">
      <span className="font-semibold">My App</span>
      <span className="text-sm text-muted-foreground">{user?.name}</span>
    </header>
    <main className="flex-1 p-6">
      <Outlet />
    </main>
  </div>
);
```

**`pages/index`:**
```tsx
return (
  <Column gap={4}>
    <Heading level={1}>Welcome</Heading>
    <Text>Start building your app by editing this page.</Text>
  </Column>
);
```

**Implementation:**
- Add scaffolding logic to `create_application` endpoint
- Only when `engine: "code"`

#### 4.3 Enforce Path Conventions

Restrict file/folder creation to valid paths:

**Allowed structure:**
```
_layout              ← root only
_providers           ← root only
pages/               ← routes
  _layout            ← nested layouts
  index              ← page files
  [param]/           ← dynamic segments
  subfolder/         ← nested routes
components/          ← UI components (nested folders allowed)
modules/             ← hooks, utils, services (nested folders allowed)
```

**File tree rules:**

| Location | Can Create |
|----------|------------|
| Root | `_layout`, `_providers`, `pages/`, `components/`, `modules/` only |
| `pages/` | `index`, `_layout`, `[paramName]/`, named subfolders |
| `pages/*/` | Same rules recursively |
| `components/` | Files or subfolders (free naming) |
| `components/*/` | Files or subfolders recursively |
| `modules/` | Files or subfolders (free naming) |
| `modules/*/` | Files or subfolders recursively |

**NOT allowed:**
- Arbitrary folders at root
- Files at root other than `_layout`, `_providers`

**Implementation:**
- Update `FileTree` component to show contextual "New" options
- Validate paths in backend API (reject invalid paths with 400)

---

## Implementation Order

1. **Rename JSX → Code** (database migration + code changes)
2. **Path validation** (backend API)
3. **Initial scaffolding** (backend, on app create)
4. **MCP tools** (backend)
5. **WebSocket payload update** (backend)
6. **Real-time hook** (frontend)
7. **Editor integration** (frontend - wire up hook to editor/preview)
8. **Creation modal** (frontend)
9. **File tree path enforcement** (frontend)

---

## Files to Modify

**Backend:**
- `api/alembic/versions/` - new migration for table rename
- `api/src/models/orm/applications.py` - rename model
- `api/src/models/contracts/applications.py` - rename contracts
- `api/src/routers/app_jsx_files.py` → `app_code_files.py`
- `api/src/routers/applications.py` - scaffolding on create
- `api/src/services/mcp_server/server.py` - add tools
- `api/src/core/pubsub.py` - update payload if needed

**Frontend:**
- `client/src/pages/JsxApplicationEditor.tsx` → rename + modal
- `client/src/components/jsx-editor/` → rename directory
- `client/src/components/file-tree/` - path enforcement
- `client/src/lib/jsx-*.ts` → rename files
- `client/src/lib/jsx-platform/` → rename directory
- `client/src/hooks/useAppCodeUpdates.ts` - new hook
