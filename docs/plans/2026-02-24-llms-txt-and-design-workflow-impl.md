# LLMs.txt + Design-First Workflow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace 6 docs endpoints and 7 MCP schema tools with a single template-based `llms.txt`, add per-app CSS support, and redesign the build skill for design-first app development.

**Architecture:** A hand-authored markdown template (`api/src/templates/llms.txt.md`) with token placeholders (`{sdk_module_docs}`, `{form_model_docs}`, etc.) served by one endpoint (`/api/llms.txt`) and one MCP tool (`get_docs`). Per-app CSS files bypass compilation and get injected client-side via `<style>` tags. The build skill detects MCP vs SDK for doc fetching and adds a design conversation step for apps.

**Tech Stack:** FastAPI (endpoint), FastMCP (MCP tool), React (CSS injection), Tailwind CSS v4 (dark mode), Markdown (template)

**Design doc:** `docs/plans/2026-02-24-llms-txt-and-design-workflow.md`

---

### Task 1: Create the llms.txt template

**Files:**
- Create: `api/src/templates/llms.txt.md`

**Step 1: Create the template file**

Create the template with authored prose and token placeholders. The prose consolidates content from the existing 6 schema tools. Tokens are wrapped in `{curly_braces}` and will be replaced at render time.

```markdown
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
```

**Step 2: Verify the template exists and has all sections**

Run: `wc -l api/src/templates/llms.txt.md && grep -c '{' api/src/templates/llms.txt.md`
Expected: ~250+ lines, 8+ token placeholders

**Step 3: Commit**

```bash
git add api/src/templates/llms.txt.md
git commit -m "feat: add llms.txt template with token placeholders"
```

---

### Task 2: Create the llms.txt generation function and endpoint

**Files:**
- Create: `api/src/services/llms_txt.py`
- Modify: `api/src/routers/docs.py`

**Step 1: Write the generation function**

Create `api/src/services/llms_txt.py` that reads the template file, generates token values, and returns the filled document.

```python
"""
LLMs.txt generator — reads the template and fills auto-generated tokens.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "llms.txt.md"


def _generate_sdk_tokens() -> dict[str, str]:
    """Generate all SDK-related tokens from actual source code."""
    try:
        from src.services.mcp_server.tools.sdk import (
            _generate_decorator_docs,
            _generate_context_docs,
            _generate_error_docs,
            _generate_module_docs,
            _generate_models_docs,
        )
        from bifrost import (
            ai, config, executions, files, forms,
            integrations, knowledge, organizations,
            roles, tables, users, workflows,
        )

        modules = [
            ("ai", ai), ("config", config), ("executions", executions),
            ("files", files), ("forms", forms), ("integrations", integrations),
            ("knowledge", knowledge), ("organizations", organizations),
            ("roles", roles), ("tables", tables), ("users", users),
            ("workflows", workflows),
        ]

        module_lines = []
        for name, module in modules:
            doc = _generate_module_docs(name, module)
            if doc:
                module_lines.append(doc)

        return {
            "decorator_docs": _generate_decorator_docs(),
            "context_docs": _generate_context_docs(),
            "error_docs": _generate_error_docs(),
            "sdk_module_docs": "\n".join(module_lines),
            "sdk_models_docs": _generate_models_docs(),
        }
    except ImportError as e:
        logger.warning(f"Could not generate SDK tokens: {e}")
        return {
            "decorator_docs": "",
            "context_docs": "",
            "error_docs": "",
            "sdk_module_docs": "",
            "sdk_models_docs": "",
        }


def _generate_model_tokens() -> dict[str, str]:
    """Generate Pydantic model documentation tokens."""
    from src.services.mcp_server.schema_utils import models_to_markdown
    from src.models.contracts.forms import (
        FormCreate, FormUpdate, FormSchema, FormField,
    )
    from src.models.contracts.agents import AgentCreate, AgentUpdate
    from src.models.contracts.tables import TableCreate, TableUpdate

    form_docs = models_to_markdown([
        (FormCreate, "FormCreate"),
        (FormUpdate, "FormUpdate"),
        (FormSchema, "FormSchema"),
        (FormField, "FormField"),
    ], "Form Models")

    agent_docs = models_to_markdown([
        (AgentCreate, "AgentCreate"),
        (AgentUpdate, "AgentUpdate"),
    ], "Agent Models")

    table_docs = models_to_markdown([
        (TableCreate, "TableCreate"),
        (TableUpdate, "TableUpdate"),
    ], "Table Models")

    return {
        "form_model_docs": form_docs,
        "agent_model_docs": agent_docs,
        "table_model_docs": table_docs,
    }


def generate_llms_txt() -> str:
    """Read the template and fill all tokens. Regenerated per request."""
    template = _TEMPLATE_PATH.read_text()

    tokens: dict[str, str] = {}
    tokens.update(_generate_sdk_tokens())
    tokens.update(_generate_model_tokens())

    for key, value in tokens.items():
        template = template.replace("{" + key + "}", value)

    return template
```

**Step 2: Replace docs.py with the llms.txt endpoint**

Replace the contents of `api/src/routers/docs.py` with a single endpoint:

```python
"""
Public LLMs.txt endpoint — single document for all platform documentation.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["docs"])


@router.get("/api/llms.txt", response_class=PlainTextResponse)
async def get_llms_txt() -> str:
    """Return the full platform documentation as a single markdown document."""
    from src.services.llms_txt import generate_llms_txt
    return generate_llms_txt()
```

**Step 3: Verify the endpoint works**

Run: `curl -s http://localhost:3000/api/llms.txt | head -20`
Expected: First 20 lines of the generated markdown, starting with `# Bifrost Platform`

**Step 4: Commit**

```bash
git add api/src/services/llms_txt.py api/src/routers/docs.py
git commit -m "feat: add llms.txt generation and endpoint, replacing 6 docs endpoints"
```

---

### Task 3: Add get_docs MCP tool

**Files:**
- Create: `api/src/services/mcp_server/tools/docs.py`
- Modify: `api/src/services/mcp_server/tools/__init__.py`

**Step 1: Write the MCP tool**

Create `api/src/services/mcp_server/tools/docs.py`:

```python
"""
Unified documentation MCP tool — returns the llms.txt content.
"""

import logging
from typing import Any

from fastmcp.tools.tool import ToolResult

from src.services.mcp_server.tool_result import success_result

logger = logging.getLogger(__name__)


async def get_docs(context: Any) -> ToolResult:  # noqa: ARG001
    """Get complete Bifrost platform documentation — SDK, forms, agents, apps, tables, events."""
    from src.services.llms_txt import generate_llms_txt

    content = generate_llms_txt()
    return success_result("Bifrost platform documentation", {"schema": content})


TOOLS = [
    ("get_docs", "Get Platform Docs", "Get complete Bifrost platform documentation covering workflows, forms, agents, apps, tables, and events."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register docs tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {"get_docs": get_docs}
    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
```

**Step 2: Register the module in `__init__.py`**

Modify `api/src/services/mcp_server/tools/__init__.py`:
- Add `docs` to the import list
- Add `docs` to `TOOL_MODULES`

```python
from src.services.mcp_server.tools import (
    agents,
    apps,
    code_editor,
    docs,       # ← add
    events,
    execution,
    forms,
    integrations,
    knowledge,
    organizations,
    sdk,
    tables,
    workflow,
)

TOOL_MODULES = [
    agents,
    apps,
    code_editor,
    docs,       # ← add
    events,
    execution,
    forms,
    integrations,
    knowledge,
    organizations,
    sdk,
    tables,
    workflow,
]
```

**Step 3: Commit**

```bash
git add api/src/services/mcp_server/tools/docs.py api/src/services/mcp_server/tools/__init__.py
git commit -m "feat: add get_docs MCP tool wrapping llms.txt generation"
```

---

### Task 4: Remove old schema MCP tools

**Files:**
- Modify: `api/src/services/mcp_server/tools/apps.py` (remove `get_app_schema`, `get_component_docs` from TOOLS list and `register_tools`)
- Modify: `api/src/services/mcp_server/tools/sdk.py` (remove `get_sdk_schema` from TOOLS list; keep helper functions used by `llms_txt.py`)
- Modify: `api/src/services/mcp_server/tools/forms.py` (remove `get_form_schema` from TOOLS list)
- Modify: `api/src/services/mcp_server/tools/agents.py` (remove `get_agent_schema` from TOOLS list)
- Modify: `api/src/services/mcp_server/tools/tables.py` (remove `get_table_schema` from TOOLS list)
- Modify: `api/src/services/mcp_server/tools/data_providers.py` (remove `get_data_provider_schema` from TOOLS list)
- Delete: `api/src/services/mcp_server/component_docs.py`

**Step 1: Remove schema tools from each module's TOOLS list**

For each file, remove the schema tool entry from the `TOOLS` list and the corresponding entry from `tool_funcs` in `register_tools`. The function definitions can stay (sdk.py helpers are still used by `llms_txt.py`).

**apps.py** — Remove these two entries from `TOOLS` (line ~1154):
```python
# Remove:
("get_app_schema", "Get App Schema", "..."),
("get_component_docs", "Get Component Docs", "..."),
```
And remove from `tool_funcs` dict in `register_tools`:
```python
# Remove:
"get_app_schema": get_app_schema,
"get_component_docs": get_component_docs,
```

**sdk.py** — Remove from `TOOLS` (line ~393):
```python
# Remove:
("get_sdk_schema", "Get SDK Schema", "..."),
```
And `tool_funcs`:
```python
# Remove:
"get_sdk_schema": get_sdk_schema,
```
**IMPORTANT**: Keep the helper functions (`_generate_module_docs`, `_generate_decorator_docs`, etc.) — they're imported by `llms_txt.py`.

**forms.py** — Remove from `TOOLS` (line ~672):
```python
# Remove:
("get_form_schema", "Get Form Schema", "..."),
```

**agents.py** — Remove from `TOOLS` (line ~714):
```python
# Remove:
("get_agent_schema", "Get Agent Schema", "..."),
```

**tables.py** — Remove from `TOOLS` (line ~509):
```python
# Remove:
("get_table_schema", "Get Table Schema", "..."),
```

**data_providers.py** — Remove from `TOOLS` (line ~73):
```python
# Remove:
("get_data_provider_schema", "Get Data Provider Schema", "..."),
```

**Step 2: Delete component_docs.py**

```bash
rm api/src/services/mcp_server/component_docs.py
```

Also remove any imports of `component_docs` or `COMPONENT_DOCS` from `apps.py` (the `get_component_docs` function imports from it).

**Step 3: Verify MCP server still starts**

Run: `docker compose -f docker-compose.dev.yml logs api --tail=20`
Expected: No import errors, server running

**Step 4: Commit**

```bash
git add -A
git commit -m "feat: remove 7 schema MCP tools and component_docs.py, replaced by get_docs"
```

---

### Task 5: Per-app CSS — backend (path validation + render endpoint)

**Files:**
- Modify: `api/src/routers/app_code_files.py:72` (VALID_FILENAME_PATTERN), `:80-182` (validate_file_path), `:434` (_COMPILABLE_EXTENSIONS), `:442-526` (render_app)
- Modify: `api/src/models/contracts/applications.py:304-312` (AppRenderResponse)

**Step 1: Write a test for CSS path validation**

Create or add to test file `api/tests/unit/routers/test_app_file_validation.py`:

```python
import pytest
from src.routers.app_code_files import validate_file_path


def test_css_file_at_root_allowed():
    """styles.css is valid at app root."""
    validate_file_path("styles.css")  # Should not raise


def test_css_file_only_styles_allowed():
    """Only styles.css is allowed, not arbitrary CSS files."""
    with pytest.raises(Exception):
        validate_file_path("theme.css")


def test_css_in_subdirectory_rejected():
    """CSS files are only allowed at root, not in subdirectories."""
    with pytest.raises(Exception):
        validate_file_path("pages/styles.css")
```

**Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/routers/test_app_file_validation.py -v`
Expected: FAIL — `styles.css` currently rejected by validation

**Step 3: Update path validation to allow styles.css**

In `api/src/routers/app_code_files.py`, modify the root-level file handling (line ~112-131):

Add `styles.css` to allowed root files. The simplest approach: in the root-level check, before the extension check, handle `styles.css` as a special case:

```python
# Root level file (no directory)
if len(segments) == 1:
    filename = segments[0]

    # Allow styles.css at root
    if filename == "styles.css":
        return

    # Must have .ts or .tsx extension
    if not re.search(r"\.tsx?$", filename):
        ...
```

**Step 4: Update AppRenderResponse model**

In `api/src/models/contracts/applications.py` (line ~304), add `styles` field:

```python
class AppRenderResponse(BaseModel):
    """All compiled files needed to render an application."""

    files: list[RenderFileResponse]
    total: int
    dependencies: dict[str, str] = Field(
        default_factory=dict,
        description="npm dependencies {name: version} for esm.sh loading",
    )
    styles: dict[str, str] = Field(
        default_factory=dict,
        description="CSS files {path: content} for style injection",
    )
```

**Step 5: Update render endpoint to separate CSS from compilable files**

In `api/src/routers/app_code_files.py`, modify `render_app()` (line ~442-526):

After reading file contents from S3 (line ~486), separate CSS files before compilation:

```python
    # Separate CSS files from compilable code
    css_files: dict[str, str] = {}
    code_files: dict[str, str] = {}
    for rel_path, content in file_contents.items():
        if rel_path.endswith(".css"):
            css_files[rel_path] = content
        else:
            code_files[rel_path] = content

    # Use code_files (not file_contents) for compilation logic below
    file_contents = code_files
```

And include CSS in the response (line ~520-526):

```python
    return AppRenderResponse(
        files=files, total=len(files),
        dependencies=dependencies, styles=css_files,
    )
```

Also update the Redis cache path — the cached dict currently stores all files. CSS files should either be included in the cache or fetched separately. Simplest: include them in the cache dict (they're small), but filter them out when building `RenderFileResponse` objects. Alternatively, store CSS in the cache and separate at response time.

**Step 6: Run tests**

Run: `./test.sh tests/unit/routers/test_app_file_validation.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add api/src/routers/app_code_files.py api/src/models/contracts/applications.py api/tests/unit/routers/test_app_file_validation.py
git commit -m "feat: backend support for per-app styles.css"
```

---

### Task 6: Per-app CSS — frontend (style injection)

**Files:**
- Modify: `client/src/components/jsx-app/JsxAppShell.tsx`

**Step 1: Add CSS injection to JsxAppShell**

In `JsxAppShell.tsx`, after the app files are fetched and state is set, add a `useEffect` that injects CSS from the render response:

```tsx
// After the existing state declarations, add:
const [appStyles, setAppStyles] = useState<Record<string, string>>({});

// In the fetch function, after setting files:
setAppStyles(data.styles || {});

// CSS injection effect
useEffect(() => {
  const cssContent = Object.values(appStyles).join("\n");
  if (!cssContent) return;

  const styleEl = document.createElement("style");
  styleEl.dataset.bifrostApp = appId;
  styleEl.textContent = cssContent;
  document.head.appendChild(styleEl);

  return () => {
    styleEl.remove();
  };
}, [appStyles, appId]);
```

**Step 2: Update the AppRenderResponse TypeScript interface**

In the same file, find the `AppRenderResponse` interface (~line 35) and add:

```typescript
interface AppRenderResponse {
  files: Array<{ path: string; code: string }>;
  total: number;
  dependencies: Record<string, string>;
  styles?: Record<string, string>;  // ← add
}
```

**Step 3: Regenerate TypeScript types**

Run: `cd client && npm run generate:types`
Expected: `v1.d.ts` updated with `styles` field on `AppRenderResponse`

**Step 4: Test manually**

Create a test app with `styles.css` via `bifrost watch` or direct file write, verify the styles appear in `<head>` when the app loads.

**Step 5: Commit**

```bash
git add client/src/components/jsx-app/JsxAppShell.tsx
git commit -m "feat: inject per-app CSS via style tag in JsxAppShell"
```

---

### Task 7: Update SKILL.md

**Files:**
- Modify: `.claude/skills/bifrost-build/SKILL.md`

**Step 1: Rewrite the documentation fetch section**

Replace the "Download Schema Docs (Once Per Session)" section with:

```markdown
#### Download Platform Docs (Once Per Session)

Auto-detect the best method and fetch the unified docs:

```bash
# If MCP is available — use get_docs tool, save result to file
# If SDK is available:
bifrost api GET /api/llms.txt > /tmp/bifrost-docs/llms.txt
# Fallback: ask user for Bifrost URL, then:
# curl -s $BIFROST_URL/api/llms.txt > /tmp/bifrost-docs/llms.txt
```

Then use Grep/Read on `/tmp/bifrost-docs/llms.txt` for reference.
```

**Step 2: Add design-first workflow for apps**

Add a new section after "Before Building" (or incorporate into the "Building Apps" section):

```markdown
### App Design Workflow

Before writing any app code, understand what you're building visually.

**New app:**
1. Ask: "What should this app feel like? Any products you'd like it inspired by?"
2. Explore key screens and interactions with the user
3. Decide component strategy: pre-included shadcn for standard UI, custom components in `components/` for anything distinctive
4. If a distinct visual identity is desired, plan `styles.css` — colors, typography, spacing, dark mode
5. Then start building

**Existing app:**
1. Read existing `styles.css` and `components/` first
2. Match established design patterns
3. Don't introduce conflicting styles

**Key principle:** Don't default to the simplest component that technically works. A rich text editor is not a `<Textarea>`. An email composer is not an `<Input>`. Build custom components when the UX demands it.
```

**Step 3: Update the component documentation references**

Replace the "Available from bifrost" component list with a shorter version that references the llms.txt:

```markdown
**UI Components:** Standard shadcn/ui components are pre-included (Button, Card, Dialog, Table, etc.). See platform docs (`/tmp/bifrost-docs/llms.txt`) for the full list. Need more? Build custom components in `components/` — shadcn components are just TSX files.
```

**Step 4: Remove MCP schema tool references**

Remove any references to `get_app_schema`, `get_component_docs`, `get_sdk_schema`, `get_form_schema`, `get_agent_schema`, `get_table_schema`, `get_data_provider_schema` from the skill file. Replace with `get_docs` where applicable.

**Step 5: Commit**

```bash
git add .claude/skills/bifrost-build/SKILL.md
git commit -m "feat: redesign build skill with llms.txt fetch and design-first app workflow"
```

---

### Task 8: Verification and cleanup

**Files:**
- Various (verification only)

**Step 1: Run pyright**

Run: `cd api && pyright`
Expected: 0 errors (or same baseline as before)

**Step 2: Run ruff**

Run: `cd api && ruff check .`
Expected: No new violations

**Step 3: Run frontend type check**

Run: `cd client && npm run tsc`
Expected: 0 errors

**Step 4: Run full test suite**

Run: `./test.sh`
Expected: All tests pass. Any tests that previously tested the removed endpoints or MCP tools will need to be removed or updated.

**Step 5: Check for stale test files**

Search for tests referencing removed tools:

```bash
grep -r "get_app_schema\|get_component_docs\|get_sdk_schema\|get_form_schema\|get_agent_schema\|get_table_schema\|get_data_provider_schema\|/api/docs/" api/tests/
```

Remove or update any found.

**Step 6: Verify llms.txt endpoint**

Run: `curl -s http://localhost:3000/api/llms.txt | wc -l`
Expected: 200+ lines of generated documentation

**Step 7: Final commit**

```bash
git add -A
git commit -m "chore: cleanup stale tests and verify full integration"
```
