# Unified Execution Details: Slideout & Full Page

## Problem

The execution details view exists in two places:
1. **Full page** (`ExecutionDetails.tsx`) — 3-column layout with WebSocket streaming, rerun/cancel, editor integration
2. **Slideout drawer** (`ExecutionDrawer.tsx`) — ~200 lines of custom layout that duplicates metadata grid, error display, result/logs panels

They diverge on features (drawer lacks AI usage, metrics, variables, streaming) and any improvement to one doesn't reach the other. The full page header is also cramped — title, execution ID, and action buttons fight for space in a single row, unusable on mobile.

## Design

### Single component, two modes

`ExecutionDetails` becomes the single source of truth. It already has an `embedded` prop (used elsewhere). We extend `embedded` mode to produce a mobile-friendly single-column layout suitable for the slideout.

`ExecutionDrawer` becomes a thin wrapper: Sheet chrome + `<ExecutionDetails executionId={...} embedded />`.

### Embedded (slideout) layout — single column

Content order, top to bottom:

1. **Compact header** — workflow name + status badge + metadata row (who, org, started, duration) as a tight 2x2 or horizontal grid
2. **Error message** (if any) and **Result panel** (if any)
3. **Input data** — always visible (important to users)
4. **Logs** — with live WebSocket streaming
5. **Collapsible sections** — AI usage, metrics, variables (collapsed by default)

No page header, no back button, no rerun/cancel/editor buttons. The drawer's own header provides "Open in new tab".

### Full page layout — responsive

**Header** (replaces the current single cramped row):
- Row 1: Back button + workflow name (replaces generic "Execution Details" title)
- Row 2: Execution ID (mono, `text-sm`) + status badge
- Row 3: Action buttons — flex-wrap so they stack on mobile

**Body**:
- `lg:` and up: 2-column grid (2/3 content + 1/3 sidebar) — same as today
- Below `lg:`: collapses to single-column, same order as embedded mode

### Component changes

**`ExecutionDetails.tsx`**
- When `embedded`: render single-column layout with the ordering above. WebSocket streaming stays enabled. Hide page header, rerun/cancel/editor buttons, cancel/rerun dialogs.
- When not `embedded`: render the responsive full page with the new header layout.
- Both modes share the same data fetching, WebSocket hooks, and log merging logic.

**`ExecutionSidebar.tsx`**
- Extract compact metadata (status, who, org, started, duration) into a lightweight inline component (or a mode on the existing sidebar) for use in embedded's header area.
- The full sidebar card remains for the full page's right column.
- AI usage, metrics, variables sections get collapsible wrappers that default to collapsed in embedded mode.

**`ExecutionDrawer.tsx`**
- Replace all custom content (~150 lines of metadata grid, error display, result/logs panels) with `<ExecutionDetails executionId={...} embedded />`.
- Keep: Sheet wrapper, sticky header with title + "Open in new tab" button.
- Remove: `useExecution`, `useExecutionLogs` hooks (ExecutionDetails handles its own data), `formatDuration`, custom metadata grid, error display, result/logs rendering.

**`ExecutionLogsPanel.tsx`**
- Already has `embedded` prop — no changes needed.

### Table row navigation (both tables)

Both the workflow executions table (`ExecutionHistory.tsx`) and logs table (`LogsTable.tsx`) currently use `onClick` on `<tr>` elements, which doesn't support middle-click or right-click "Open in new tab".

**Change**: Wrap each row's content in an `<a href="/history/{id}">` that fills the row. Left click calls `preventDefault()` and opens the slideout. Middle-click / Cmd+click / right-click "Open in new tab" work natively via the anchor tag.

Implementation approach: Add a link overlay inside `DataTableRow` when an `href` prop is provided, rather than changing the `<tr>` element itself (which can't be an `<a>` in valid HTML). The link is positioned absolutely to cover the row, with cell content sitting above it via `position: relative`.

### What stays the same

- All data fetching hooks (`useExecution`, `useExecutionStream`, etc.)
- `mergeLogsWithDedup` logic
- `ExecutionResultPanel` component
- `ExecutionLogsPanel` component (already has embedded support)
- WebSocket streaming architecture
- Rerun/cancel functionality (full page only)

## Files to modify

| File | Change |
|------|--------|
| `client/src/pages/ExecutionDetails.tsx` | Add single-column embedded layout, fix header |
| `client/src/components/execution/ExecutionSidebar.tsx` | Extract compact metadata view, add collapsible wrappers |
| `client/src/pages/ExecutionHistory/components/ExecutionDrawer.tsx` | Gut custom content, embed `ExecutionDetails` |
| `client/src/pages/ExecutionHistory.tsx` | Add `href` to table rows, wire slideout |
| `client/src/pages/ExecutionHistory/components/LogsTable.tsx` | Add `href` to table rows |
| `client/src/components/ui/data-table.tsx` | Add `href` prop to `DataTableRow` for accessible link rows |

## Verification

1. `npm run tsc` — type check passes
2. `npm run lint` — lint passes
3. Manual: open logs table, click row → slideout shows with streaming, correct layout
4. Manual: middle-click row → opens full page in new tab
5. Manual: full page header is readable on narrow viewport
6. Manual: slideout content order is correct (header → error/result → input → logs → collapsible)
7. Manual: test on mobile viewport — both slideout and full page are usable
