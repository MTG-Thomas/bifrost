# UX Overhaul Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Comprehensive UX cleanup across the Bifrost frontend -- fix bugs, add Enter key support, improve error handling, standardize empty states, fix accessibility, improve performance, and split monolithic files.

**Architecture:** Phase 1 is independent quick wins (bugs, forms, a11y, consistency) that can be done in any order. Phase 2 splits the five largest files into focused sub-components. Phase 3 (editor merge) is deferred to a separate worktree/plan.

**Tech Stack:** React 18, TypeScript, shadcn/ui (Radix + Tailwind), React Router v6, React Hook Form, Zustand, Sonner (toasts)

---

## Phase 1: Quick Wins

### Task 1: Fix RoleDialog `can_promote_agent` Data Loss Bug

**Files:**
- Modify: `client/src/components/roles/RoleDialog.tsx:74-91`

**Step 1: Add `can_promote_agent` to both mutation bodies**

In `RoleDialog.tsx`, the `onSubmit` handler at line 74 never passes `can_promote_agent` to the API. Fix both the update and create paths:

```tsx
// Line 76-82: UPDATE path - add can_promote_agent
await updateRole.mutateAsync({
    params: { path: { role_id: role.id } },
    body: {
        name: values.name,
        description: values.description || null,
        can_promote_agent: values.can_promote_agent,
    },
});

// Line 84-90: CREATE path - add can_promote_agent
await createRole.mutateAsync({
    body: {
        name: values.name,
        description: values.description || null,
        is_active: true,
        can_promote_agent: values.can_promote_agent,
    },
});
```

**Step 2: Verify the API accepts `can_promote_agent`**

Check that the Pydantic models in `api/shared/models.py` include `can_promote_agent` in the role create/update request models. If not, add it. Then regenerate types:

```bash
cd client && npm run generate:types
```

**Step 3: Commit**

```bash
git add client/src/components/roles/RoleDialog.tsx
git commit -m "$(cat <<'EOF'
fix: submit can_promote_agent field in RoleDialog

The field was collected in the form but silently discarded in onSubmit,
causing a data loss bug where the permission toggle had no effect.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Disable ChatInput Placeholder Buttons

**Files:**
- Modify: `client/src/components/chat/ChatInput.tsx:275-292`

**Step 1: Add tooltip and disabled styling to Plus and Paperclip buttons**

Both buttons at lines 275-292 already have `disabled={disabled || isLoading}` but no indication they're placeholder features. Add `title` and a permanent disabled state:

```tsx
// Line 275-283: Plus button
<Button
    type="button"
    variant="ghost"
    size="icon"
    className="h-8 w-8 rounded-full text-muted-foreground/50 cursor-not-allowed"
    disabled
    title="Coming soon"
>
    <Plus className="h-5 w-5" />
</Button>

// Line 284-292: Paperclip button
<Button
    type="button"
    variant="ghost"
    size="icon"
    className="h-8 w-8 rounded-full text-muted-foreground/50 cursor-not-allowed"
    disabled
    title="Coming soon"
>
    <Paperclip className="h-4 w-4" />
</Button>
```

**Step 2: Commit**

```bash
git add client/src/components/chat/ChatInput.tsx
git commit -m "$(cat <<'EOF'
fix: disable placeholder chat attachment buttons

Plus and Paperclip buttons had no onClick handlers. Mark them as
disabled with "Coming soon" tooltip until file attachments are built.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Remove Dead `canManageAgents` Conditional in Agents.tsx

**Files:**
- Modify: `client/src/pages/Agents.tsx`

**Step 1: Remove `canManageAgents` and simplify all conditional branches**

At line 110: `const canManageAgents = true;` -- delete this line.

Then find-and-replace all uses. Since it's always `true`, every `{canManageAgents && (...)}` wrapper just becomes its contents, and every ternary like `canManageAgents ? "Create..." : "View..."` becomes just the truthy branch.

Locations to simplify:
- Line 197-200: subtitle ternary → just "Create and manage AI agents with custom prompts and tools"
- Line 203-226: `{canManageAgents && (<ToggleGroup...>)}` → unwrap, remove the conditional
- Line 235-244: `{canManageAgents && (<Button...Create Agent>)}` → unwrap
- Line 271: `viewMode === "grid" || !canManageAgents` → `viewMode === "grid"`
- Line 285: same pattern → `viewMode === "grid"`
- Line 310-324: `{canManageAgents && (<Switch...>)}` → unwrap
- Line 378-391: `{canManageAgents && (<div...actions>)}` → unwrap
- Line 552-554: ternary in empty state → just "Get started by creating your first AI agent"
- Line 556: `{canManageAgents && !searchTerm && (...)}` → `{!searchTerm && (...)}`

**Step 2: Commit**

```bash
git add client/src/pages/Agents.tsx
git commit -m "$(cat <<'EOF'
refactor: remove dead canManageAgents conditional

Was hardcoded to true. Simplify all conditional branches to
unconditional rendering.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Fix AgentDialog Temperature Slider Silent Value Commit

**Files:**
- Modify: `client/src/components/agents/AgentDialog.tsx:1447-1483`

**Step 1: Replace the temperature slider with a toggle + slider pattern**

Currently at lines 1448-1483, the slider shows at 0.7 when value is null and silently commits on move. Replace with:

```tsx
{/* Temperature */}
<FormField
    control={form.control}
    name="llm_temperature"
    render={({ field }) => (
        <FormItem>
            <div className="flex items-center justify-between">
                <FormLabel>
                    Temperature:{" "}
                    {field.value?.toFixed(1) ?? "default"}
                </FormLabel>
                {field.value !== null && (
                    <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => field.onChange(null)}
                        className="h-6 text-xs"
                    >
                        Reset to default
                    </Button>
                )}
            </div>
            <FormControl>
                {field.value !== null ? (
                    <Slider
                        min={0}
                        max={2}
                        step={0.1}
                        value={[field.value]}
                        onValueChange={([val]) => field.onChange(val)}
                        className="flex-1"
                    />
                ) : (
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={() => field.onChange(0.7)}
                    >
                        Customize temperature
                    </Button>
                )}
            </FormControl>
            <FormDescription>
                0 = deterministic, 2 = creative
            </FormDescription>
            <FormMessage />
        </FormItem>
    )}
/>
```

**Step 2: Commit**

```bash
git add client/src/components/agents/AgentDialog.tsx
git commit -m "$(cat <<'EOF'
fix: prevent temperature slider from silently committing value

When llm_temperature was null (use default), the slider rendered at 0.7
and moving it silently set an explicit value. Now shows a "Customize"
button that must be clicked first to opt into a custom value.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Add Enter Key Support to BasicInfo.tsx (User Settings)

**Files:**
- Modify: `client/src/pages/user-settings/BasicInfo.tsx`

**Step 1: Wrap Display Name section in `<form>`**

Find the `<div className="flex justify-end">` with the save button at line 384. Wrap the name fields and button in a form. The card content (with the name Input and email Input) needs to be inside the form:

```tsx
// Wrap the CardContent for the Display Name section:
<CardContent>
    <form onSubmit={(e) => { e.preventDefault(); handleSaveName(); }}>
        <div className="space-y-4">
            {/* ...existing name input, email input... */}
            <div className="flex justify-end">
                <Button
                    type="submit"
                    disabled={savingName || !nameChanged}
                >
                    {/* ...existing button content... */}
                </Button>
            </div>
        </div>
    </form>
</CardContent>
```

Change the Button from `onClick={handleSaveName}` to `type="submit"` (remove onClick).

**Step 2: Wrap Password section in `<form>`**

Same pattern for the password card (lines 419-549). Wrap in `<form onSubmit>`, change the button to `type="submit"`:

```tsx
<CardContent>
    <form onSubmit={(e) => { e.preventDefault(); handleChangePassword(); }}>
        <div className="space-y-4">
            {/* ...existing password fields... */}
            <div className="flex justify-end">
                <Button
                    type="submit"
                    disabled={changingPassword || ...}
                >
                    {/* ...existing button content... */}
                </Button>
            </div>
        </div>
    </form>
</CardContent>
```

**Step 3: Commit**

```bash
git add client/src/pages/user-settings/BasicInfo.tsx
git commit -m "$(cat <<'EOF'
fix: add Enter key support for name and password forms

Wrap display name and password change sections in <form> elements
so Enter key triggers submission.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Create PageErrorBoundary and Apply to Routes

**Files:**
- Create: `client/src/components/PageErrorBoundary.tsx`
- Modify: `client/src/App.tsx`

**Step 1: Create `PageErrorBoundary` component**

A lighter version of `ErrorBoundary` that fits within the layout shell (no `min-h-screen`, no `bg-background`). It resets when the URL changes.

```tsx
// client/src/components/PageErrorBoundary.tsx
import { Component, ErrorInfo, ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
    children: ReactNode;
    /** When this key changes, the boundary resets. Pass location.pathname. */
    resetKey?: string;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

export class PageErrorBoundary extends Component<Props, State> {
    constructor(props: Props) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error: Error): Partial<State> {
        return { hasError: true, error };
    }

    componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        if (import.meta.env.DEV) {
            console.error("PageErrorBoundary caught:", error, errorInfo);
        }
    }

    componentDidUpdate(prevProps: Props) {
        if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
            this.setState({ hasError: false, error: null });
        }
    }

    handleReset = () => {
        this.setState({ hasError: false, error: null });
    };

    render() {
        if (this.state.hasError) {
            return (
                <div className="flex items-center justify-center min-h-[400px] p-4">
                    <Card className="w-full max-w-lg">
                        <CardHeader>
                            <div className="flex items-center gap-3">
                                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-destructive/10">
                                    <AlertTriangle className="h-5 w-5 text-destructive" />
                                </div>
                                <div>
                                    <CardTitle className="text-lg">Something went wrong</CardTitle>
                                    <CardDescription>This page encountered an error</CardDescription>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <Alert variant="destructive">
                                <AlertDescription className="font-mono text-sm">
                                    {this.state.error?.message || "Unknown error"}
                                </AlertDescription>
                            </Alert>
                        </CardContent>
                        <CardFooter>
                            <Button onClick={this.handleReset}>
                                <RotateCcw className="mr-2 h-4 w-4" />
                                Try Again
                            </Button>
                        </CardFooter>
                    </Card>
                </div>
            );
        }
        return this.props.children;
    }
}
```

**Step 2: Create a wrapper component that provides `location.pathname` as resetKey**

Since `PageErrorBoundary` is a class component and can't use hooks, create a thin wrapper:

```tsx
// Add to the same file or create client/src/components/RouteErrorBoundary.tsx
import { useLocation } from "react-router-dom";
import { PageErrorBoundary } from "./PageErrorBoundary";
import { ReactNode } from "react";

export function RouteErrorBoundary({ children }: { children: ReactNode }) {
    const location = useLocation();
    return (
        <PageErrorBoundary resetKey={location.pathname}>
            {children}
        </PageErrorBoundary>
    );
}
```

**Step 3: Wrap route elements in `App.tsx`**

In `App.tsx`, wrap each lazy-loaded page inside `<RouteErrorBoundary>`. Instead of wrapping every single route individually, wrap the `<Outlet />` in both `Layout` and `ContentLayout` components. This is simpler and catches errors from any child route.

Modify `client/src/components/layout/Layout.tsx` line 55-57:

```tsx
import { RouteErrorBoundary } from "@/components/PageErrorBoundary";
// ...
<main className="flex-1 overflow-auto p-6 lg:p-8">
    <PasskeySetupBanner />
    <RouteErrorBoundary>
        <Outlet />
    </RouteErrorBoundary>
</main>
```

Modify `client/src/components/layout/ContentLayout.tsx` line 69-71:

```tsx
import { RouteErrorBoundary } from "@/components/PageErrorBoundary";
// ...
<main className="flex-1 overflow-auto">
    <RouteErrorBoundary>
        <Outlet />
    </RouteErrorBoundary>
</main>
```

Also wrap `<EditorOverlay />` in `App.tsx` line 199:

```tsx
<PageErrorBoundary>
    <EditorOverlay />
</PageErrorBoundary>
```

**Step 4: Commit**

```bash
git add client/src/components/PageErrorBoundary.tsx client/src/components/layout/Layout.tsx client/src/components/layout/ContentLayout.tsx client/src/App.tsx
git commit -m "$(cat <<'EOF'
feat: add page-level error boundaries

Create PageErrorBoundary that preserves the layout shell when a page
crashes. Resets on navigation. Applied to both Layout and ContentLayout
outlets, and to EditorOverlay.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Standardize Empty State CTAs

**Files:**
- Modify: `client/src/pages/Tables.tsx:400-408`
- Modify: `client/src/pages/TableDetail.tsx:552-560`
- Modify: `client/src/pages/Agents.tsx:556-565`
- Modify: `client/src/pages/Workflows.tsx:898-913`

**Step 1: Fix Tables.tsx empty state**

Replace the icon-only button (lines 400-408). Also, only show create CTA when there's no search term:

```tsx
{!searchTerm && (
    <Button
        variant="outline"
        onClick={handleAdd}
        className="mt-4"
    >
        <Plus className="mr-2 h-4 w-4" />
        Create your first table
    </Button>
)}
```

**Step 2: Fix TableDetail.tsx empty state**

Replace the icon-only button at lines 552-560:

```tsx
<Button
    variant="outline"
    onClick={handleAdd}
    className="mt-4"
>
    <Plus className="mr-2 h-4 w-4" />
    Add a document
</Button>
```

**Step 3: Fix Agents.tsx empty state**

Replace lines 556-565:

```tsx
{!searchTerm && (
    <Button
        variant="outline"
        onClick={handleCreate}
        className="mt-4"
    >
        <Plus className="mr-2 h-4 w-4" />
        Create your first agent
    </Button>
)}
```

**Step 4: Add CTA to Workflows.tsx empty state**

At lines 898-913, the empty state has no button. Add one that opens the editor:

```tsx
{!searchTerm && (
    <Button
        variant="outline"
        onClick={() => openEditor()}
        className="mt-4"
    >
        <Code className="mr-2 h-4 w-4" />
        Open editor
    </Button>
)}
```

Make sure `openEditor` is available from `useEditorStore` (it's likely already imported for the "Open in editor" buttons elsewhere on that page).

**Step 5: Commit**

```bash
git add client/src/pages/Tables.tsx client/src/pages/TableDetail.tsx client/src/pages/Agents.tsx client/src/pages/Workflows.tsx
git commit -m "$(cat <<'EOF'
fix: standardize empty state CTAs with full-text buttons

Replace icon-only Plus buttons in empty states with labeled buttons.
Add "Open editor" CTA to Workflows empty state which previously had
no action button.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Dashboard Improvements (Clickable Cards + View All)

**Files:**
- Modify: `client/src/pages/Dashboard.tsx`

**Step 1: Make metric cards clickable links**

Import `Link` from react-router-dom (currently only `useNavigate` is imported). Wrap each `<Card>` in a `<Link>`:

```tsx
import { useNavigate, Link } from "react-router-dom";
```

For each metric card, wrap in Link and add hover styling:

```tsx
<Link to="/workflows" className="block">
    <Card className="hover:border-primary/50 transition-colors cursor-pointer">
        {/* ...existing CardHeader + CardContent... */}
    </Card>
</Link>
```

Map of cards to routes:
- Workflows → `/workflows`
- Forms → `/forms`
- Executions (30d) → `/history`
- Success Rate → `/history`
- Time Saved (24h) → `/reports/roi`
- Value (24h) → `/reports/roi`

**Step 2: Add "View all" link to Recent Failures**

In the Recent Failures card header (around line 289-301), add a link:

```tsx
<div className="flex items-center gap-2">
    {/* ...existing badges... */}
    <Link
        to="/history?status=failed"
        className="text-xs text-muted-foreground hover:text-foreground transition-colors"
    >
        View all
    </Link>
</div>
```

**Step 3: Commit**

```bash
git add client/src/pages/Dashboard.tsx
git commit -m "$(cat <<'EOF'
feat: make dashboard metric cards clickable and add View All link

Metric cards now navigate to their respective pages on click. Recent
Failures section has a "View all" link to filtered execution history.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Consistency Fixes

**Files:**
- Modify: `client/src/components/integrations/CreateIntegrationDialog.tsx:381-393`
- Modify: `client/src/pages/settings/LLMConfig.tsx:711-747`
- Modify: `client/src/pages/Workflows.tsx:883-889`

**Step 1: Replace raw checkbox in CreateIntegrationDialog**

Replace lines 381-393 with shadcn Checkbox:

```tsx
import { Checkbox } from "@/components/ui/checkbox";
// ...
<label className="flex items-center gap-2 text-sm">
    <Checkbox
        checked={field.required}
        onCheckedChange={(checked) =>
            updateConfigField(index, {
                required: checked === true,
            })
        }
    />
    Required
</label>
```

**Step 2: Replace Dialog with AlertDialog in LLMConfig**

Replace lines 711-747. Change imports from `Dialog/DialogContent/DialogHeader/DialogTitle/DialogDescription/DialogFooter` to `AlertDialog` variants:

```tsx
<AlertDialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
    <AlertDialogContent>
        <AlertDialogHeader>
            <AlertDialogTitle>Remove AI Configuration</AlertDialogTitle>
            <AlertDialogDescription>
                Are you sure you want to remove the AI provider configuration?
                This will disable AI chat functionality until reconfigured.
            </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>
                Cancel
            </AlertDialogCancel>
            <AlertDialogAction
                onClick={handleDelete}
                disabled={saving}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
                {saving ? (
                    <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        Removing...
                    </>
                ) : (
                    "Remove Configuration"
                )}
            </AlertDialogAction>
        </AlertDialogFooter>
    </AlertDialogContent>
</AlertDialog>
```

**Step 3: Add text label to Workflows table Execute button**

At lines 883-889, change the icon-only button to match grid view:

```tsx
<Button
    variant="outline"
    size="sm"
    onClick={() => handleExecute(workflow.name ?? "")}
>
    <PlayCircle className="mr-1 h-4 w-4" />
    Execute
</Button>
```

**Step 4: Commit**

```bash
git add client/src/components/integrations/CreateIntegrationDialog.tsx client/src/pages/settings/LLMConfig.tsx client/src/pages/Workflows.tsx
git commit -m "$(cat <<'EOF'
fix: UI consistency -- checkbox component, AlertDialog, button labels

- Replace raw <input checkbox> with shadcn Checkbox in integration dialog
- Use AlertDialog instead of Dialog for destructive AI config removal
- Add text label to Workflows table Execute button

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Extract useSidebar Hook (Layout Deduplication)

**Files:**
- Create: `client/src/hooks/useSidebar.ts`
- Modify: `client/src/components/layout/Layout.tsx`
- Modify: `client/src/components/layout/ContentLayout.tsx`

**Step 1: Create the hook**

```tsx
// client/src/hooks/useSidebar.ts
import { useState, useCallback } from "react";

export function useSidebar() {
    const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
    const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
        return localStorage.getItem("sidebar-collapsed") === "true";
    });

    const toggleSidebar = useCallback(() => {
        const newState = !isSidebarCollapsed;
        setIsSidebarCollapsed(newState);
        localStorage.setItem("sidebar-collapsed", String(newState));
    }, [isSidebarCollapsed]);

    return {
        isMobileMenuOpen,
        setIsMobileMenuOpen,
        isSidebarCollapsed,
        toggleSidebar,
    };
}
```

**Step 2: Use in Layout.tsx**

Replace lines 13-17 and the `toggleSidebar` function with:

```tsx
import { useSidebar } from "@/hooks/useSidebar";
// ...
const { isMobileMenuOpen, setIsMobileMenuOpen, isSidebarCollapsed, toggleSidebar } = useSidebar();
```

**Step 3: Use in ContentLayout.tsx**

Same replacement.

**Step 4: Commit**

```bash
git add client/src/hooks/useSidebar.ts client/src/components/layout/Layout.tsx client/src/components/layout/ContentLayout.tsx
git commit -m "$(cat <<'EOF'
refactor: extract useSidebar hook from Layout components

Deduplicate sidebar state management shared between Layout.tsx and
ContentLayout.tsx into a single hook.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Accessibility Fixes

**Files:**
- Modify: `client/src/pages/Agents.tsx` (icon buttons)
- Modify: `client/src/pages/Workflows.tsx` (icon buttons)
- Modify: `client/src/pages/Tables.tsx` (icon buttons)
- Modify: `client/src/pages/TableDetail.tsx` (icon buttons)
- Modify: `client/src/pages/user-settings/BasicInfo.tsx` (avatar zone)
- Modify: `client/src/pages/Settings.tsx` (tabs overflow)
- Modify: `client/src/components/agents/AgentDialog.tsx` (autoFocus)
- Modify: `client/src/components/events/CreateEventSourceDialog.tsx` (aria-live)

**Step 1: Add aria-labels to icon-only buttons**

In `Agents.tsx` table view (lines 499-524), add `aria-label` to each:
```tsx
<Button variant="ghost" size="icon-sm" onClick={...} aria-label="Edit agent">
    <Pencil className="h-4 w-4" />
</Button>
<Button variant="ghost" size="icon-sm" onClick={...} aria-label="Copy MCP URL">
    {/* ...Copy/Check icon... */}
</Button>
<Button variant="ghost" size="icon-sm" onClick={...} aria-label="Delete agent">
    <Trash2 className="h-4 w-4" />
</Button>
```

Also add `aria-label` to the grid view card action buttons (lines 378-391).

In `Agents.tsx` header (lines 227-244):
```tsx
<Button variant="outline" size="icon" onClick={() => refetch()} aria-label="Refresh">
    <RefreshCw className="h-4 w-4" />
</Button>
<Button variant="outline" size="icon" onClick={handleCreate} aria-label="Create agent">
    <Plus className="h-4 w-4" />
</Button>
```

Apply the same pattern to `Workflows.tsx`, `Tables.tsx`, and `TableDetail.tsx` -- every `size="icon"` or `size="icon-sm"` button that has only an icon child needs `aria-label`.

**Step 2: Make avatar upload zone keyboard-accessible**

In `BasicInfo.tsx` at lines 283-289, add keyboard support:

```tsx
<div
    className={`relative group cursor-pointer ${isDragging ? "ring-2 ring-primary ring-offset-2" : ""}`}
    onDragOver={handleDragOver}
    onDragLeave={handleDragLeave}
    onDrop={handleDrop}
    onClick={() => fileInputRef.current?.click()}
    onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            fileInputRef.current?.click();
        }
    }}
    tabIndex={0}
    role="button"
    aria-label="Upload profile picture"
>
```

**Step 3: Fix Settings tabs overflow**

In `Settings.tsx` at line 42, wrap `TabsList` in a scrollable container:

```tsx
<div className="overflow-x-auto">
    <TabsList>
        {/* ...existing triggers... */}
    </TabsList>
</div>
```

**Step 4: Add autoFocus to AgentDialog Name input**

In `AgentDialog.tsx` at line 372:

```tsx
<Input
    placeholder="Sales Assistant"
    autoFocus
    {...field}
/>
```

**Step 5: Add aria-live to CreateEventSourceDialog errors**

In `CreateEventSourceDialog.tsx` at line 253:

```tsx
<Alert variant="destructive" role="alert" aria-live="polite">
```

**Step 6: Commit**

```bash
git add client/src/pages/Agents.tsx client/src/pages/Workflows.tsx client/src/pages/Tables.tsx client/src/pages/TableDetail.tsx client/src/pages/user-settings/BasicInfo.tsx client/src/pages/Settings.tsx client/src/components/agents/AgentDialog.tsx client/src/components/events/CreateEventSourceDialog.tsx
git commit -m "$(cat <<'EOF'
fix: accessibility improvements across the frontend

- Add aria-label to all icon-only action buttons
- Make avatar upload zone keyboard-accessible
- Add horizontal scroll for Settings tabs on narrow viewports
- Set autoFocus on AgentDialog Name input
- Add aria-live to event source validation errors

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Performance Fixes

**Files:**
- Modify: `client/src/components/forms/FormRenderer.tsx:317-318`
- Modify: `client/src/components/editor/EditorLayout.tsx:86-104`
- Modify: `client/src/pages/TableDetail.tsx` (search placeholder)

**Step 1: Fix FormRenderer loadDataProviders dependency loop**

At line 318, remove `dataProviderState.loading` from the dependency array:

```tsx
// Before:
[fields, evaluateDataProviderInputs, dataProviderState.loading],

// After:
[fields, evaluateDataProviderInputs],
```

The loading state is tracked inside the function via `setDataProviderState` -- it doesn't need to be a dependency. Having it there causes the callback to be recreated on every loading toggle, which triggers the useEffect at line 322.

**Step 2: Replace setTimeout event chain in EditorLayout**

At lines 86-104 in `EditorLayout.tsx`, the current pattern dispatches `execute-editor-file` after a 200ms timeout. Replace with a callback-based approach:

Create a ref that the RunPanel can register its execute handler on:

```tsx
// In EditorLayout.tsx, replace the event listener:
const executeRef = useRef<(() => void) | null>(null);

useEffect(() => {
    const handleRunEvent = () => {
        setSidebarPanel("run");
        setSidebarVisible(true);
        // If RunPanel is already mounted, execute immediately
        // Otherwise, store a pending flag and RunPanel will pick it up on mount
        if (executeRef.current) {
            executeRef.current();
        }
    };

    window.addEventListener("run-editor-file", handleRunEvent);
    return () => {
        window.removeEventListener("run-editor-file", handleRunEvent);
    };
}, [setSidebarPanel]);
```

Then pass `executeRef` to `RunPanel` as a prop and have RunPanel register itself:

```tsx
// In RunPanel, on mount:
useEffect(() => {
    if (executeRef) {
        executeRef.current = handleExecuteEvent;
    }
    return () => {
        if (executeRef) {
            executeRef.current = null;
        }
    };
}, [handleExecuteEvent, executeRef]);
```

Remove the `window.addEventListener("execute-editor-file", ...)` from RunPanel since it's now called directly via ref.

**Step 3: Update TableDetail search placeholder**

In `TableDetail.tsx`, change the SearchBox placeholder:

```tsx
<SearchBox
    value={searchTerm}
    onChange={setSearchTerm}
    placeholder="Search this page..."
    className="w-64"
/>
```

**Step 4: Commit**

```bash
git add client/src/components/forms/FormRenderer.tsx client/src/components/editor/EditorLayout.tsx client/src/components/editor/RunPanel.tsx client/src/pages/TableDetail.tsx
git commit -m "$(cat <<'EOF'
perf: fix dependency loop and fragile event chain

- Remove dataProviderState.loading from loadDataProviders deps to prevent
  infinite re-creation loop in FormRenderer
- Replace setTimeout(200) event chain between EditorLayout and RunPanel
  with a ref-based callback that doesn't depend on mount timing
- Clarify TableDetail search only searches current page

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Run Verification

**Step 1: Run TypeScript type checking**

```bash
cd client && npm run tsc
```

Expected: 0 errors.

**Step 2: Run linter**

```bash
cd client && npm run lint
```

Expected: 0 errors (warnings OK).

**Step 3: Run tests**

```bash
./test.sh --client
```

Expected: All tests pass.

**Step 4: Fix any issues found and commit fixes**

---

## Phase 2: Split Large Files

Each task below follows the same pattern: read the file, identify natural split boundaries, extract sub-components, verify the page still renders correctly.

**Important:** These are pure refactors -- no behavior changes. Each split should be a single commit. The page should render identically before and after.

### Task 14: Split EntityManagement.tsx (1,844 lines)

**Files:**
- Modify: `client/src/pages/EntityManagement.tsx`
- Create: `client/src/components/entity-management/EntityTypeList.tsx`
- Create: `client/src/components/entity-management/EntityInstanceList.tsx`
- Create: `client/src/components/entity-management/EntityDialog.tsx`
- Create: `client/src/components/entity-management/EntityImportExport.tsx`

**Step 1: Read the full file and identify split boundaries**

Read `EntityManagement.tsx` fully. Identify:
- The page shell (tabs, header, state management) -- stays in `EntityManagement.tsx`
- Entity type list/grid rendering -- extract to `EntityTypeList.tsx`
- Entity instance list with filtering -- extract to `EntityInstanceList.tsx`
- Create/edit dialog -- extract to `EntityDialog.tsx`
- Import/export functionality -- extract to `EntityImportExport.tsx`

**Step 2: Extract components one at a time**

For each extraction:
1. Create the new file with the component
2. Pass required props from the parent
3. Replace the inline code in `EntityManagement.tsx` with the imported component
4. Verify TypeScript compiles: `cd client && npm run tsc`

**Step 3: Commit**

```bash
git add client/src/pages/EntityManagement.tsx client/src/components/entity-management/
git commit -m "$(cat <<'EOF'
refactor: split EntityManagement.tsx into focused components

Extract EntityTypeList, EntityInstanceList, EntityDialog, and
EntityImportExport from the 1844-line monolith. No behavior changes.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Split IntegrationDetail.tsx (1,834 lines)

**Files:**
- Modify: `client/src/pages/IntegrationDetail.tsx`
- Create: `client/src/components/integrations/IntegrationOverview.tsx`
- Create: `client/src/components/integrations/IntegrationConfig.tsx`
- Create: `client/src/components/integrations/IntegrationActions.tsx`
- Create: `client/src/components/integrations/IntegrationTestPanel.tsx`

**Step 1: Read the full file and identify tab-based split boundaries**

The page uses tabs. Each tab content area is a natural extraction point.

**Step 2: Extract components one at a time**

Same pattern as Task 14. Extract each tab's content into its own component.

**Step 3: Verify and commit**

```bash
git add client/src/pages/IntegrationDetail.tsx client/src/components/integrations/
git commit -m "$(cat <<'EOF'
refactor: split IntegrationDetail.tsx into tab components

Extract IntegrationOverview, IntegrationConfig, IntegrationActions,
and IntegrationTestPanel. No behavior changes.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Split ExecutionDetails.tsx (1,767 lines)

**Files:**
- Modify: `client/src/pages/ExecutionDetails.tsx`
- Create: `client/src/components/execution/ExecutionTimeline.tsx`
- Create: `client/src/components/execution/ExecutionStepDetail.tsx`
- Create: `client/src/components/execution/ExecutionInputOutput.tsx`

**Step 1: Read and identify boundaries**

Look for: timeline/step list rendering, individual step detail panel, input/output JSON viewers.

**Step 2: Extract and verify**

**Step 3: Commit**

```bash
git add client/src/pages/ExecutionDetails.tsx client/src/components/execution/
git commit -m "$(cat <<'EOF'
refactor: split ExecutionDetails.tsx into focused components

Extract ExecutionTimeline, ExecutionStepDetail, and
ExecutionInputOutput. No behavior changes.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Split UsageReports.tsx (1,758 lines)

**Files:**
- Modify: `client/src/pages/UsageReports.tsx`
- Create: `client/src/components/reports/UsageSummaryCards.tsx`
- Create: `client/src/components/reports/UsageCharts.tsx`
- Create: `client/src/components/reports/UsageTable.tsx`

**Step 1: Read and identify boundaries**

Look for: summary metric cards at top, chart sections, tabular data section.

**Step 2: Extract and verify**

**Step 3: Commit**

```bash
git add client/src/pages/UsageReports.tsx client/src/components/reports/
git commit -m "$(cat <<'EOF'
refactor: split UsageReports.tsx into focused components

Extract UsageSummaryCards, UsageCharts, and UsageTable.
No behavior changes.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Split FileTree.tsx (1,715 lines)

**Files:**
- Modify: `client/src/components/editor/FileTree.tsx`
- Create: `client/src/components/editor/FileTreeNode.tsx`
- Create: `client/src/components/editor/FileTreeContextMenu.tsx`
- Create: `client/src/hooks/useFileTreeActions.ts`

**Step 1: Read and identify boundaries**

Look for: tree node rendering (recursive), context menu component, create/rename/delete/upload operations (extract to hook).

**Step 2: Extract and verify**

The trickiest part is the recursive tree node -- it needs access to the file tree state. Pass callbacks as props.

**Step 3: Commit**

```bash
git add client/src/components/editor/FileTree.tsx client/src/components/editor/FileTreeNode.tsx client/src/components/editor/FileTreeContextMenu.tsx client/src/hooks/useFileTreeActions.ts
git commit -m "$(cat <<'EOF'
refactor: split FileTree.tsx into focused components

Extract FileTreeNode (recursive rendering), FileTreeContextMenu,
and useFileTreeActions hook. No behavior changes.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: Final Verification

**Step 1: Run full type check**

```bash
cd client && npm run tsc
```

**Step 2: Run full lint**

```bash
cd client && npm run lint
```

**Step 3: Run all tests**

```bash
./test.sh
```

**Step 4: Manual smoke test**

Start the dev stack (`./debug.sh`) and click through each modified page to verify rendering is correct:
- Dashboard (clickable cards, View All link)
- Agents (empty state CTA, table view labels)
- Workflows (empty state CTA, table Execute label)
- Tables / TableDetail (empty state CTAs)
- Settings (tabs scroll, AI config AlertDialog)
- User Settings > Basic Info (Enter key for name/password)
- Roles (create/edit with can_promote_agent toggle)
- Chat (disabled attachment buttons)
- Editor (run shortcut works without timing issues)
- Entity Management, Integration Detail, Execution Details, Usage Reports (split files render correctly)
