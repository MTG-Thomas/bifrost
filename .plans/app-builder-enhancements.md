# App Builder Enhancement Backlog

Identified during Project Management Demo build (Jan 2026).

## Priority 1: Inline Table Select Column

**Status**: Not implemented
**Impact**: HIGH - Key UX feature for quick status updates

### Current State
- DataTable supports 4 column types: `text`, `number`, `date`, `badge`
- No way to render interactive elements (dropdowns) in table cells
- Users must click row → open modal/detail → change status → close

### Desired State
```json
{
  "columns": [
    {
      "key": "status",
      "header": "Status",
      "type": "select",
      "selectOptions": [
        {"value": "new", "label": "New"},
        {"value": "in_progress", "label": "In Progress"},
        {"value": "completed", "label": "Completed"}
      ],
      "onChange": {
        "workflowId": "update_task_status",
        "actionParams": {
          "taskId": "{{ row.id }}",
          "status": "{{ value }}"
        }
      }
    }
  ]
}
```

### Implementation Notes
- Estimated effort: ~150-200 lines of code
- Need to handle event propagation (prevent row click)
- Should support both static options and data provider
- Consider optimistic UI updates

### Workaround
Use row action button that opens a modal with status select.

---

## Priority 2: RepeatFor Loop Variable in HTML Component

**Status**: Works, but context access is inconsistent
**Impact**: MEDIUM - Affects custom rendering patterns

### Current State
- `repeatFor` works on all components
- Loop variable accessible as `{{ comment.field }}` in regular components
- In HTML component JSX templates: `{context.workflow.comment.field}` (different syntax)

### Desired State
Consistent syntax across all components:
- Regular: `{{ comment.field }}`
- HTML JSX: `{{ comment.field }}` or `{comment.field}`

### Notes
This may be working as designed, but the inconsistency is confusing. Document clearly or unify.

---

## Priority 3: Data Provider for Select Columns

**Status**: Not applicable yet (depends on Priority 1)
**Impact**: MEDIUM - Would enable dynamic options in table selects

### Desired State
```json
{
  "columns": [
    {
      "key": "assignee_id",
      "header": "Assignee",
      "type": "select",
      "dataProviderId": "team_members_provider",
      "dataProviderParams": {
        "projectId": "{{ row.project_id }}"
      }
    }
  ]
}
```

---

## Documentation Improvements

### Completed
- [x] Added `{{ params.* }}` route parameters to expressions.mdx
- [x] Added context variables summary table

### TODO
- [ ] Add repeatFor examples to components.mdx
- [ ] Document HTML component JSX template syntax
- [ ] Add "refresh after action" pattern to actions.mdx (navigate to same page triggers launch workflow)
- [ ] Document launchWorkflowParams for passing route params to launch workflows

---

## Research Findings (Jan 2026)

### Route Parameters - CONFIRMED WORKING
- Syntax: `/customers/:id` in page path
- Access: `{{ params.id }}` in expressions
- Two-pass matching ensures static paths match before dynamic

### repeatFor - CONFIRMED WORKING
- Works on ALL components (defined on BaseComponentProps)
- Config: `items`, `itemKey`, `as`
- Loop variable injected into expression context

### Refresh After Action - CONFIRMED WORKING
- `onComplete: [{ type: "navigate", navigateTo: "/same-page" }]` re-runs launch workflow
- `onComplete: [{ type: "refresh-table", dataSourceKey: "customers" }]` for in-place refresh
- Navigation naturally triggers usePageData effect which re-executes launch workflow

### HTML Component - CONFIRMED WORKING
- Supports both raw HTML (sanitized with DOMPurify) and JSX templates
- JSX detected by presence of `className=` or `{context.`
- Full context access in JSX mode
