# Bifrost Platform

Bifrost is an automation platform for building workflows, forms, agents, and apps. Everything below is what you need to build on the platform.

## Workflows & Tools

Workflows are async Python functions decorated with `@workflow`, `@tool`, or `@data_provider`. They run in a sandboxed execution engine with access to the Bifrost SDK.

{decorator_docs}

{context_docs}

{error_docs}

### SDK Modules

All SDK methods are async and must be awaited.

```python
from bifrost import ai, config, files, integrations, knowledge, tables
from bifrost import workflow, data_provider, context
from bifrost import UserError, WorkflowError, ValidationError
```

{sdk_module_docs}

{sdk_models_docs}

### File Locations

The `files` module operates on three storage locations:

| Location | Usage | Example |
|----------|-------|---------|
| `"workspace"` (default) | General-purpose file storage | `files.read("data/report.csv")` |
| `"temp"` | Temporary files scoped to a single execution | `files.write("scratch.txt", content, location="temp")` |
| `"uploads"` | Files uploaded via form file fields (read-only) | `files.read(path, location="uploads")` |

When a form has a `file` field, the workflow receives the S3 path as a string (or list if `multiple: true`). Read with `location="uploads"`:

```python
from bifrost import workflow, files

@workflow
async def handle_upload(resume: str, cover_letters: list[str]) -> dict:
    resume_bytes = await files.read(resume, location="uploads")
    return {"resume_size": len(resume_bytes)}
```

## Forms

Forms collect user input and trigger workflows. Define them as YAML files with a `form_schema` containing typed fields.

{form_model_docs}

### File Upload Fields

```yaml
- name: resume
  type: file
  label: Upload Resume
  options:
    allowed_types: [".pdf", ".docx"]
    max_size_mb: 10
```

File fields pass S3 paths to workflows as strings. Use `multiple: true` for multi-file uploads.

### Data Provider Fields

Forms can use data providers for dynamic dropdowns:

```yaml
- name: customer
  type: select
  label: Customer
  data_provider:
    id: "workflow-uuid"
    label_field: label
    value_field: value
```

Data providers are workflows decorated with `@data_provider` that return `[{"label": "...", "value": "..."}]`.

## Agents

Agents are AI-powered assistants with access to workflows as tools, knowledge bases, and delegation to other agents.

{agent_model_docs}

### Available Channels

| Channel | Description |
|---------|-------------|
| `chat` | Web-based chat interface |
| `voice` | Voice interaction |
| `teams` | Microsoft Teams |
| `slack` | Slack |

### Key Fields

- `tool_ids`: List of workflow UUIDs this agent can call as tools
- `delegated_agent_ids`: Other agent UUIDs it can delegate to
- `knowledge_sources`: Knowledge namespace names for RAG search
- `system_tools`: Built-in tools (`http`, etc.)
- Scope: `"global"` (all orgs) or `"organization"` (scoped)

## Apps

Apps are React + Tailwind applications that run inside the Bifrost platform. You have full creative control — build custom components, use CSS variables, create any UI you can imagine.

### File Structure

```
apps/my-app/
  app.yaml              # Metadata (name, description, dependencies)
  _layout.tsx           # Root layout (MUST use <Outlet />, NOT {children})
  _providers.tsx        # Optional context providers
  styles.css            # Custom CSS (dark mode via .dark selector)
  pages/
    index.tsx           # Home page (/)
    settings.tsx        # /settings
    clients/
      index.tsx         # /clients
      [id].tsx          # /clients/:id
  components/
    MyWidget.tsx        # Custom components
  modules/
    utils.ts            # Utility modules
```

### Imports

Everything comes from a single import:

```tsx
import { Button, Card, useState, useWorkflowQuery } from "bifrost";
```

External npm packages (declared in `app.yaml`):

```tsx
import dayjs from "dayjs";
import { LineChart, Line } from "recharts";
```

### Workflow Hooks

**CRITICAL: Always use workflow UUIDs, not names.**

#### useWorkflowQuery(workflowId, params?, options?)

Auto-executes on mount. For loading data.

| Property | Type | Description |
|----------|------|-------------|
| `data` | `T \| null` | Result data |
| `isLoading` | `boolean` | True while executing |
| `isError` | `boolean` | True if failed |
| `error` | `string \| null` | Error message |
| `refetch` | `() => Promise<T>` | Re-execute |
| `logs` | `StreamingLog[]` | Real-time logs |

Options: `{ enabled?: boolean }` — set `false` to defer.

#### useWorkflowMutation(workflowId)

Manual execution via `execute()`. For user-triggered actions.

| Property | Type | Description |
|----------|------|-------------|
| `execute` | `(params?) => Promise<T>` | Run workflow |
| `isLoading` | `boolean` | True while executing |
| `data` | `T \| null` | Last result |
| `error` | `string \| null` | Error message |
| `reset` | `() => void` | Reset state |

```tsx
// Load data on mount
const { data, isLoading } = useWorkflowQuery("workflow-uuid", { limit: 10 });

// Button-triggered action
const { execute, isLoading } = useWorkflowMutation("workflow-uuid");
const result = await execute({ name: "New Item" });

// Conditional loading
const { data } = useWorkflowQuery("workflow-uuid", { id }, { enabled: !!id });
```

#### Other Hooks

- `useUser()` — current authenticated user `{ id, email, name }`
- `useAppState(key, initialValue)` — persistent cross-page state
- `useParams()` — URL path parameters
- `useSearchParams()` — query string parameters
- `useNavigate()` — programmatic navigation `navigate("/path")`
- `useLocation()` — current location object

### Pre-included Components (standard shadcn/ui)

These are available from `"bifrost"` without installation. They are standard shadcn/ui components — use them exactly as documented in the shadcn/ui docs.

**Layout:** Card, CardHeader, CardFooter, CardTitle, CardAction, CardDescription, CardContent

**Forms:** Button, Input, Label, Textarea, Checkbox, Switch, Select (+ SelectTrigger, SelectContent, SelectItem, SelectGroup, SelectLabel, SelectValue, SelectSeparator), RadioGroup, RadioGroupItem, Combobox, MultiCombobox, TagsInput

**Display:** Badge, Avatar (+ AvatarImage, AvatarFallback), Alert (+ AlertTitle, AlertDescription), Skeleton, Progress

**Navigation:** Tabs (+ TabsList, TabsTrigger, TabsContent), Pagination (+ PaginationContent, PaginationEllipsis, PaginationItem, PaginationLink, PaginationNext, PaginationPrevious)

**Feedback:** Dialog (+ DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger), AlertDialog (+ sub-components), Tooltip (+ TooltipContent, TooltipProvider, TooltipTrigger), Popover (+ PopoverContent, PopoverTrigger, PopoverAnchor)

**Data:** Table (+ TableHeader, TableBody, TableFooter, TableHead, TableRow, TableCell, TableCaption)

**Date:** CalendarPicker, DateRangePicker

**Icons:** All lucide-react icons (e.g., `Settings`, `ChevronRight`, `Search`, `Plus`, `Trash2`, `Users`, `Mail`)

**Utilities:** `cn(...)` (Tailwind class merging), `toast(message)` (Sonner notifications), `format(date, pattern)` (date-fns)

### Custom Components

Need a component not listed above? Build it in `components/`. shadcn/ui components are just TSX files — recreate any shadcn component, customize it, or build entirely new ones from scratch with React and Tailwind.

For example, to add a Sheet component, create `components/Sheet.tsx` using Radix primitives and Tailwind — the same pattern shadcn/ui uses. Or build a rich text editor, a kanban board, a color picker — anything you can build in React.

### Custom CSS

Add a `styles.css` file to your app root for custom styles:

```css
/* CSS variables for theming */
:root {
  --app-primary: oklch(0.5 0.18 260);
  --app-surface: #fffef9;
}

/* Dark mode — inherits from platform toggle */
.dark {
  --app-primary: oklch(0.7 0.15 260);
  --app-surface: #1e1e22;
}

/* Custom classes */
.paper-bg {
  background-color: var(--app-surface);
  background-image: repeating-linear-gradient(
    transparent, transparent 1.7rem,
    rgba(0,0,0,0.06) 1.7rem, rgba(0,0,0,0.06) 1.75rem
  );
}
```

Use in components: `<div className="paper-bg rounded-lg">`. Tailwind classes and custom CSS classes can be mixed freely.

### External Dependencies

Declare npm packages in `app.yaml`:

```yaml
name: My Dashboard
description: Analytics dashboard
dependencies:
  recharts: "2.12"
  dayjs: "1.11"
```

Max 20 packages. Loaded at runtime from esm.sh CDN.

### Sandbox Constraints

Apps run in an isolated scope. You **cannot** access:
- `window`, `document`, `fetch`, `XMLHttpRequest`
- Node.js APIs
- ES dynamic `import()`

Use `useWorkflowQuery`/`useWorkflowMutation` for data fetching (calls Bifrost workflows, which can access any external API).

### Layout Tips

- `_layout.tsx`: Use `<Outlet />` with `h-full overflow-hidden` on root div
- Scrollable pages: `flex flex-col h-full overflow-hidden` on page root, `shrink-0` on headers, `flex-1 min-h-0 overflow-auto` on scrollable content

## Tables

Tables provide structured data storage with schema validation and multi-tenancy.

{table_model_docs}

### Column Types

| Type | Options |
|------|---------|
| `string` | minLength, maxLength, enum |
| `number` | minimum, maximum |
| `integer` | minimum, maximum |
| `boolean` | — |
| `date` | — |
| `datetime` | — |
| `json` | — |
| `array` | — |

### Scope & Visibility

| Scope | Visible to |
|-------|-----------|
| `global` | All organizations |
| `organization` | Only the owning org |
| `application` | Only the owning app |

## Data Providers

Data providers are workflows that return label/value pairs for form dropdowns.

Return format: `[{"label": "Display Name", "value": "unique-id"}]`

Reference in forms:

```yaml
- name: customer
  type: select
  data_provider:
    id: "data-provider-workflow-uuid"
    label_field: label
    value_field: value
```

Use `@data_provider` decorator — see Workflows section for syntax.

## Events

### Schedule Source

```
create_event_source(name="Daily Report", source_type="schedule",
                    cron_expression="0 9 * * *", timezone="America/New_York")
create_event_subscription(source_id=<id>, workflow_id=<id>,
                          input_mapping={"report_type": "daily"})
```

### Webhook Source

```
create_event_source(name="HaloPSA Tickets", source_type="webhook",
                    adapter_name="generic")
  → returns callback_url: /api/hooks/{source_id}
create_event_subscription(source_id=<id>, workflow_id=<id>,
                          event_type="ticket.created")
```

Configure the external service to POST to the callback_url.

## Manifest YAML Formats (SDK-First / Git Sync)

The `.bifrost/*.yaml` manifest files declare all platform entities as configuration-as-code. Each entity has a manifest entry (identity, org binding, roles) and optionally an entity file (portable definition).

### Workspace Structure

The workspace root is your git repository root. Only `.bifrost/*.yaml` manifests are required — all other directories are convention, not enforced.

```
<repo-root>/
  .bifrost/                   # REQUIRED — manifest files (source of truth)
    organizations.yaml        # Org definitions
    roles.yaml                # Role definitions
    workflows.yaml            # Workflow identity + runtime config
    forms.yaml                # Form identity + org/role binding
    agents.yaml               # Agent identity + org/role binding
    apps.yaml                 # App identity + org/role binding
    integrations.yaml         # Integration definitions + config schema
    configs.yaml              # Config values (secrets redacted)
    tables.yaml               # Table schema declarations
    events.yaml               # Event sources + subscriptions
    knowledge.yaml            # Namespace declarations
  workflows/                  # Convention — workflow Python files
    onboard_user.py
  forms/                      # Convention — form definition files
    {uuid}.form.yaml
  agents/                     # Convention — agent definition files
    {uuid}.agent.yaml
  apps/                       # Convention — app source directories
    my-dashboard/
      app.yaml                # App metadata + dependencies
      _layout.tsx
      styles.css
      pages/index.tsx
      components/
  modules/                    # Convention — shared Python modules
    shared/utils.py
```

{manifest_docs}
