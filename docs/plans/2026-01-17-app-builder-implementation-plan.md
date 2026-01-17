# App Builder Layout Improvements - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix Row width rendering and update demo app to use proper layout patterns instead of HTML/style workarounds.

**Architecture:** Fix CSS flex-basis issue in LayoutRenderer so percentage widths work in flex rows. Then systematically update demo pages via MCP tools to remove HTML components and inline styles.

**Tech Stack:** React, TypeScript, Tailwind CSS, Bifrost MCP tools

**Design Doc:** `docs/plans/2026-01-17-app-builder-layout-improvements.md`

---

## Task 1: Fix Row Width Rendering

**Files:**
- Modify: `client/src/components/app-builder/LayoutRenderer.tsx:746-758`

**Step 1: Understand the current code**

The `renderComponent` function wraps components with width in a div:
```tsx
if (normalizedComponent.width || normalizedComponent.class_name || normalizedComponent.style) {
  return wrapWithSelectable(
    <div
      key={normalizedComponent.id}
      className={cn(widthClass, normalizedComponent.class_name, className)}
      style={normalizedComponent.style ?? undefined}
    >
      {wrappedComponent}
    </div>,
  );
}
```

The issue: `w-1/3` sets `width: 33.333%` but in a flex container with `flex-wrap`, items need `flex-shrink-0` and proper `flex-basis` to not collapse.

**Step 2: Create helper function for width styles**

Add after `getWidthClasses` function (around line 143):

```tsx
/**
 * Get inline styles for component width to ensure proper flex behavior
 * Percentage widths need flex-basis set for flex-wrap to work correctly
 */
function getWidthStyles(width?: ComponentWidth): React.CSSProperties {
  const widthMap: Record<string, string> = {
    "full": "100%",
    "1/2": "50%",
    "1/3": "33.333%",
    "1/4": "25%",
    "2/3": "66.666%",
    "3/4": "75%",
  };

  if (width && width !== "auto" && widthMap[width]) {
    return {
      flexBasis: widthMap[width],
      flexShrink: 0,
      maxWidth: widthMap[width],
    };
  }
  return {};
}
```

**Step 3: Update renderComponent to use width styles**

Modify the wrapper div (around line 748-757):

```tsx
// Wrap component if it has width, class_name, or style
if (normalizedComponent.width || normalizedComponent.class_name || normalizedComponent.style) {
  const widthStyles = getWidthStyles(normalizedComponent.width ?? undefined);
  return wrapWithSelectable(
    <div
      key={normalizedComponent.id}
      className={cn(widthClass, normalizedComponent.class_name, className)}
      style={{ ...widthStyles, ...normalizedComponent.style }}
    >
      {wrappedComponent}
    </div>,
  );
}
```

**Step 4: Verify in browser**

1. Navigate to `http://localhost:3000/apps/pm-demo/preview/projects/<any-project-id>`
2. Verify "Project Details" (1/3) and "Ask AI" (2/3) cards are side-by-side
3. Take screenshot to confirm

**Step 5: Commit**

```bash
git add client/src/components/app-builder/LayoutRenderer.tsx
git commit -m "fix: Row children with width now render side-by-side

Add flex-basis and flex-shrink-0 to components with percentage widths
so they work correctly in flex-wrap containers."
```

---

## Task 2: Verify repeat_for Works

**Files:**
- Test via MCP: `pm-demo` app, `task-detail` page

**Step 1: Create a test component using repeat_for**

Use MCP to add a simple repeat_for test to task-detail page:

```bash
# Use mcp__bifrost__update_component or create_component to add:
{
  "type": "column",
  "repeat_for": {
    "items": "{{ workflow.taskData.result.comments }}",
    "item_key": "id",
    "as": "comment"
  },
  "children": [
    { "type": "text", "text": "{{ comment.author }}: {{ comment.content }}" }
  ]
}
```

**Step 2: Check browser for rendered output**

Navigate to a task with comments and verify the repeat_for renders multiple items.

**Step 3: Document findings**

If it works: proceed to Task 3
If broken: create fix task before proceeding

---

## Task 3: Update Project Detail Page

**Files:**
- Modify via MCP: `pm-demo` app, `project-detail` page

**Step 1: Verify layout is now correct**

After Task 1, the 1/3 + 2/3 cards should render side-by-side. Verify in browser.

**Step 2: Add AI response area**

Add a Text component after the Ask button to show workflow results:

```json
{
  "id": "ai-response",
  "type": "card",
  "visible": "{{ workflow.askAiResponse }}",
  "title": "AI Response",
  "children": [
    {
      "id": "ai-response-text",
      "type": "text",
      "text": "{{ workflow.askAiResponse.answer }}"
    }
  ]
}
```

Note: The actual workflow result path depends on how the Ask AI workflow is configured. Inspect the workflow to get the correct path.

**Step 3: Verify in browser**

Test the Ask AI feature and confirm response displays.

**Step 4: Commit via git (if files were modified directly) or note MCP changes**

---

## Task 4: Update Task Detail Page

**Files:**
- Modify via MCP: `pm-demo` app, `task-detail` page

**Step 1: Verify 1/3 + 2/3 layout works**

After Task 1, Status and Details cards should be side-by-side.

**Step 2: Fix data bindings if needed**

Check if Priority, Assignee, etc. show values. If not, inspect workflow result structure and fix expressions.

**Step 3: Replace HTML comments with repeat_for**

Remove the HTML component for comments feed. Replace with:

```json
{
  "id": "comments-list",
  "type": "column",
  "gap": 12,
  "children": [
    {
      "id": "comment-item",
      "type": "row",
      "repeat_for": {
        "items": "{{ workflow.taskData.result.comments }}",
        "item_key": "id",
        "as": "comment"
      },
      "gap": 12,
      "children": [
        {
          "id": "comment-avatar",
          "type": "badge",
          "text": "{{ comment.author | slice:0:1 }}"
        },
        {
          "id": "comment-content",
          "type": "column",
          "gap": 4,
          "children": [
            {
              "id": "comment-author",
              "type": "text",
              "text": "{{ comment.author }}"
            },
            {
              "id": "comment-body",
              "type": "text",
              "text": "{{ comment.content }}"
            }
          ]
        }
      ]
    }
  ]
}
```

**Step 4: Add empty state**

```json
{
  "id": "no-comments",
  "type": "text",
  "visible": "{{ !workflow.taskData.result.comments || workflow.taskData.result.comments.length === 0 }}",
  "text": "No comments yet"
}
```

**Step 5: Verify in browser**

---

## Task 5: Update Customer Detail Page

**Files:**
- Modify via MCP: `pm-demo` app, `customer-detail` page

**Step 1: Verify 1/2 + 1/2 layout works**

**Step 2: Replace HTML projects list with repeat_for**

Similar pattern to Task 4 - replace HTML component with proper component tree using repeat_for.

**Step 3: Verify in browser**

---

## Task 6: Update Dashboard

**Files:**
- Modify via MCP: `pm-demo` app, `dashboard` page

**Step 1: Remove inline style workarounds**

Find components using `style: { flex: 2 }` and `style: { flex: 1 }`. Replace with proper `width` props:
- `flex: 2` in a 2:1 ratio → `width: "2/3"`
- `flex: 1` in a 2:1 ratio → `width: "1/3"`

**Step 2: Replace HTML "Needs Attention" with repeat_for**

**Step 3: Replace HTML "Recent Activity" with repeat_for**

**Step 4: Verify in browser**

---

## Task 7: Final Review

**Step 1: Audit for remaining HTML components**

Use MCP to get all pages and search for `type: "html"`. List any remaining.

**Step 2: Audit for remaining inline styles**

Search for `style:` in page definitions. List any remaining.

**Step 3: Document exceptions**

If any HTML/style usage is justified (truly custom rendering), document why.

**Step 4: Manual walkthrough**

Click through entire demo app verifying:
- All layouts render correctly
- All data displays (no empty values)
- All interactive elements work

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat: complete app builder layout improvements

- Row width rendering fixed (flex-basis)
- Demo pages updated to use repeat_for instead of HTML
- Inline style workarounds removed
- AI response areas added"
```

---

## Checkpoint Summary

| Task | Description | Verification | Status |
|------|-------------|--------------|--------|
| 1 | Fix Row width CSS | Cards side-by-side in browser | ✅ DONE - Used flex grow ratios (flex:1, flex:2) instead of percentage widths |
| 2 | Verify repeat_for | Test component renders list | ⏭️ SKIP - HTML component works, repeat_for can be tested when replacing HTML |
| 3 | Project detail | Layout + AI response area | ✅ DONE - Layout working |
| 4 | Task detail | Layout + comments list | ✅ DONE - Fixed repeat_for for layout containers + expression parser for loop variables |
| 5 | Customer detail | Layout + projects list | ✅ DONE - Data paths fixed, HTML replaced with repeat_for |
| 6 | Dashboard | Remove style workarounds | ✅ DONE - Width props instead of flex styles, HTML replaced with repeat_for |
| 7 | Final review | No HTML/style escapes remain | ✅ DONE - All pages verified clean |

## Implementation Notes

### Task 1 - Key Learning
The plan's approach (flex-basis + flex-shrink-0) didn't work because Tailwind's width classes (`w-1/3`) set CSS `width` which overrides flex-basis. The fix was:
1. Use **flex grow ratios** instead: `flex: 1` for 1/3, `flex: 2` for 2/3
2. Don't apply Tailwind width classes when using flex styles
3. This lets flexbox handle gaps automatically while maintaining proportions

### Task 4 - Key Bug Fixes
Two critical bugs were fixed to make `repeat_for` work on layout containers (row/column/grid):

1. **LayoutRenderer.tsx**: Added `repeat_for` handling to `renderLayoutContainer` function.
   - Previously `repeat_for` only worked on non-container components because `row`/`column`/`grid` types were routed to a separate code path that didn't handle repeat_for.

2. **expression-parser.ts**: Updated `buildEvaluationContext` to include dynamically added keys from context.
   - Previously only specific keys (user, workflow, field, etc.) were recognized.
   - Now expressions like `{{ comment.author }}` work when repeat_for adds 'comment' to context via the 'as' property.

### Task 4 - Data Binding Fix
The task-detail page had incorrect data paths with extra `.result.`:
- Wrong: `workflow.taskData.result.task.title`
- Correct: `workflow.taskData.task.title`

Compare with project-detail which correctly uses `workflow.projectData.project.name` (no `.result.`)
