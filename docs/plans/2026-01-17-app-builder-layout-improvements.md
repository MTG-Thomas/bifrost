# App Builder Layout Improvements

## Problem Statement

The Bifrost App Builder's layout system has gaps that prevent AI agents (and humans) from building professional-quality applications without falling back to HTML escape hatches. The goal is for an AI to look at the schema and intuitively build the same layouts they'd build in React/CSS.

## Current State Analysis

### What's Working
- Container model (row, column, grid, card, tabs) is conceptually sound
- Expression syntax for dynamic data binding is clean
- Component library covers basics (text, heading, button, badge, table, forms)
- `width` classes ARE being applied to DOM (`w-1/3`, `w-2/3`)

### Confirmed Issues

**1. Row + width children not rendering side-by-side**
- Children with `width: "1/3"` and `width: "2/3"` in a Row stack vertically instead of horizontally
- DOM inspection shows classes are correct (`w-1/3`, `w-2/3`, `flex flex-row flex-wrap`)
- Root cause: CSS issue - likely `flex-wrap` combined with percentage widths without proper flex-basis/shrink settings
- Workaround in demo: inline `style: { flex: 2 }` instead of using `width`

**2. HTML component overuse for lists**
- Every list in the demo uses `HtmlComponent` with inline JSX
- `repeat_for` exists in schema but isn't used
- Need to verify `repeat_for` works and document it, OR add higher-level list components

**3. Missing response areas for workflows**
- "Ask AI" section has input + button but nowhere to display results
- This is a demo incompleteness, not a schema issue
- Pattern needed: Text component with `visible` tied to workflow result existence

**4. Detail pages show labels without values**
- Task detail "Details" card shows "Priority", "Assignee" labels but no values
- Likely data binding issue with workflow results

## Proposed Changes

### Phase 1: Fix Row Width Rendering (Critical)

**File:** `client/src/components/app-builder/LayoutRenderer.tsx`

The row renders with `flex flex-row flex-wrap`. Children get `w-1/3` etc. but these don't work properly with flex-wrap.

**Fix approach:**
```tsx
// Current (broken)
<div className={cn("flex flex-row flex-wrap", ...)}>

// Option A: Remove flex-wrap, require explicit wrapping
<div className={cn("flex flex-row", ...)}>

// Option B: Set flex-basis on children with width
// In renderComponent, when width is set:
<div className={cn(widthClass, "flex-shrink-0")} style={{ flexBasis: widthToPercent(width) }}>
```

Recommend Option B - keeps `flex-wrap` for responsive behavior but ensures children respect their widths.

### Phase 2: Verify/Fix repeat_for

**Schema (already exists):**
```json
{
  "repeat_for": {
    "items": "{{ workflow.comments }}",
    "item_key": "id",
    "as": "comment"
  }
}
```

**Tasks:**
1. Test `repeat_for` on a Card component in the demo
2. Fix any frontend bugs
3. Replace HTML components in demo with proper `repeat_for` patterns
4. Document nested `repeat_for` scoping (parent variables remain accessible)

### Phase 3: Demo App Improvements

**Project Detail Page:**
- Fix 1/3 + 2/3 card layout (will work after Phase 1)
- Add response area for AI questions with visibility expression
- Ensure workflow data bindings work

**Task Detail Page:**
- Fix 1/3 + 2/3 card layout
- Fix data binding for Priority, Assignee, etc.
- Replace HTML comments feed with `repeat_for` pattern

**Customer Detail Page:**
- Fix 1/2 + 1/2 card layout
- Replace HTML projects list with `repeat_for`

**Dashboard:**
- Replace inline `style: { flex: 2 }` with proper `width` props (after Phase 1)
- Replace HTML "Needs Attention" and "Recent Activity" with `repeat_for`

### Phase 4: Missing Components (If Needed)

After completing Phases 1-3, assess if we still need:

- **ListComponent** - vertical list with consistent item styling (avatar, primary/secondary text, timestamp)
- **FeedComponent** - activity feed with timeline styling
- **AlertComponent** - info/warning/error/success messages

These may not be needed if `repeat_for` + existing components cover the use cases.

## Success Criteria

1. Row children with `width: "1/3"` and `width: "2/3"` render side-by-side
2. Demo app uses zero HTML components for lists (all use `repeat_for`)
3. Demo app uses zero inline `style` for layout (all use schema props)
4. All detail pages show proper data (no empty values)
5. AI response areas exist and show/hide based on workflow state
6. An AI agent can recreate the demo layouts using only the MCP schema without guessing

## Implementation Order

1. **Fix Row width rendering** - unblocks everything else
2. **Test repeat_for** - determine if it works or needs fixes
3. **Update project-detail page** - most visible issues
4. **Update task-detail page** - similar pattern
5. **Update customer-detail page** - similar pattern
6. **Update dashboard** - replace style workarounds
7. **Final review** - ensure no HTML/style escape hatches remain

## Out of Scope

- New component types (unless Phase 4 proves necessary)
- Visual editor improvements
- Performance optimization
- Mobile responsiveness (beyond what flex-wrap provides)
