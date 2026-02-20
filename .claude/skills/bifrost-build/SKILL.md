---
name: bifrost:build
description: Build Bifrost workflows, forms, and apps. Use when user wants to create, debug, or modify Bifrost artifacts. Supports SDK-first (local dev + git) and MCP-only modes.
---

# Bifrost Build

Create and debug Bifrost artifacts.

## First: Check Prerequisites

```bash
echo "SDK: $BIFROST_SDK_INSTALLED | Login: $BIFROST_LOGGED_IN | MCP: $BIFROST_MCP_CONFIGURED"
echo "Source: $BIFROST_HAS_SOURCE | Path: $BIFROST_SOURCE_PATH | URL: $BIFROST_DEV_URL"
```

**If SDK or Login is false/empty:** Direct user to run `/bifrost:setup` first.

## Core Principle: Local First

**In SDK-First mode, everything happens locally until it needs the platform.** Use local file tools (Glob, Read, Grep) for all exploration. Write files locally, test locally, commit and sync to deploy.

**Exploration & Discovery** -> Local file tools (Glob, Read, Grep)
- Source code in the workspace or adjacent projects
- Existing app patterns, table schemas, workflow code
- `.bifrost/*.yaml` manifests (the source of truth)
- Projects being ported to Bifrost

**Writing & Creating** -> Local files + git + `bifrost sync`
- Write workflow/form/agent files in the git repo
- Add entries to `.bifrost/*.yaml` manifests
- Commit, push, then `bifrost sync` to deploy

**Testing** -> `bifrost run` for workflows, platform for everything else
- Workflows: `bifrost run <file> --workflow <name> --params '{...}'`
- Forms, apps, agents: These require the platform to render/execute — use preview URLs or MCP execution tools after sync

**MCP tools are for platform operations only:**
- Schema docs (`get_app_schema`, `get_table_schema`, `get_sdk_schema`) — platform capability reference not in local files
- Execution/logs (`execute_workflow`, `get_execution`) — for forms/apps/agents that need the platform runtime
- Platform state verification (`list_workflows`, etc.) — ONLY when debugging sync divergence

**Never use MCP for:** discovery (`list_*`), reading code (`list_content`, `search_content`, `read_content_lines`), or creating artifacts when a local workspace exists.

## Development Mode

**Auto-detect:** If a `.bifrost/` directory exists in the workspace, you are in **SDK-First** mode. Otherwise, use **MCP-Only** mode. Only ask the user if the situation is ambiguous.

### SDK-First (Local Development)

Best for: developers who want git history, local testing, code review before deploying.

**Requirements:** Git repository, Bifrost SDK installed, GitHub sync configured in platform.

**Discovery: ALWAYS read local `.bifrost/*.yaml` files first.** These are the source of truth — the platform is synced FROM them. Never call MCP discovery tools when the same data is in local YAML.

| To find... | Read this file | NOT this MCP tool |
|---|---|---|
| Registered workflows/tools/data_providers | `.bifrost/workflows.yaml` | ~~list_workflows~~ |
| Integration config + data provider refs | `.bifrost/integrations.yaml` | ~~list_integrations~~ |
| Forms and their linked workflows | `.bifrost/forms.yaml` + `forms/*.form.yaml` | ~~list_forms~~ |
| Agents and their tool assignments | `.bifrost/agents.yaml` + `agents/*.agent.yaml` | ~~list_agents~~ |
| Organizations | `.bifrost/organizations.yaml` | ~~list_organizations~~ |
| Tables | `.bifrost/tables.yaml` | ~~list_tables~~ |
| Event sources and subscriptions | `.bifrost/events.yaml` | ~~list_event_sources~~ |
| Apps | `.bifrost/apps.yaml` + `apps/*/app.yaml` | ~~list_apps~~ |

**When to use MCP tools in SDK-First mode:**
- `execute_workflow` — to test a workflow on the platform
- `get_execution` — to check execution logs
- `get_workflow_schema`, `get_sdk_schema` — to look up SDK docs
- `list_workflows` etc. — ONLY to verify platform state diverged from local (post-sync debugging)

**Creation flow:**
1. Write workflow/form/agent files locally in the git repo
2. Add entries to `.bifrost/*.yaml` manifest files
3. Test workflows locally with `bifrost run <file> --workflow <name> --params '{...}'`
4. Iterate until happy with the result
5. `git add && git commit && git push` to push to GitHub
6. `bifrost sync` to sync with the platform (runs preflight, pulls/pushes changes)
7. If conflicts: show them, help user resolve with `bifrost sync --resolve`
8. If preflight errors: fix issues (syntax, broken refs) and re-sync
9. Verify deployment with MCP tools (`execute_workflow`, `get_execution`)

### MCP-Only (Remote Development)

Best for: quick iterations, non-developers, working without a local git repo.

**Discovery: Use MCP tools** (`list_workflows`, `list_integrations`, etc.) since there are no local files.

**Flow:**
1. Understand the goal
2. Read SDK docs via `get_workflow_schema`, `get_sdk_schema`
3. Write workflow file via `replace_content`, then `register_workflow` to register it. For forms/apps use `create_form`, `create_app`.
4. Test via `execute_workflow` or access preview URL
5. Check logs via `get_execution` if issues
6. Iterate with `patch_content` or `replace_content`

## Workspace Structure (SDK-First)

The git repo mirrors the platform's `_repo/` storage in S3:

```
my-workspace/
  .bifrost/                        # Manifest (configuration as code)
    organizations.yaml
    roles.yaml
    workflows.yaml                 # Workflow identity, org, roles, runtime config
    forms.yaml                     # Form identity, org, roles
    agents.yaml                    # Agent identity, org, roles
    apps.yaml                      # App identity, org, roles
    integrations.yaml              # Integration definitions + config schema
    configs.yaml                   # Config values (secrets redacted)
    tables.yaml                    # Table schema declarations
    events.yaml                    # Event sources + subscriptions
    knowledge.yaml                 # Namespace declarations
  workflows/
    onboard_user.py                # Workflow code
    ticket_classifier.py
  forms/
    {uuid}.form.yaml               # Form definition (fields, workflow ref)
  agents/
    {uuid}.agent.yaml              # Agent definition (prompt, tools, channels)
  apps/
    my-dashboard/
      app.yaml                     # App metadata
      pages/index.tsx              # App code files
  modules/
    shared/utils.py                # Shared Python modules
```

### Manifest is Configuration as Code

The `.bifrost/*.yaml` files declare **all platform entities**, their UUIDs, org bindings, roles, and runtime config. Entity files (forms, agents, workflows) contain the **portable definition** only.

| Data | Location | Examples |
|------|----------|---------|
| Entity identity | `.bifrost/*.yaml` | id, path, function_name, type |
| Org/role binding | `.bifrost/*.yaml` | organization_id, roles, access_level |
| Runtime config | `.bifrost/*.yaml` | endpoint_enabled, timeout_seconds |
| Portable definition | Entity file | form fields, agent prompt, workflow code |
| Cross-references | Entity file | workflow UUID in form, tool UUIDs in agent |

## UUID Generation (CRITICAL for SDK-First)

**All entity UUIDs must be generated BEFORE writing files.** When creating related entities, generate UUIDs upfront so cross-references are valid at write time.

Example: creating a workflow + form + agent that uses it:

```python
import uuid
wf_id = str(uuid.uuid4())   # Generate first
form_id = str(uuid.uuid4())
agent_id = str(uuid.uuid4())
```

Then use these IDs in all files:
1. Write `workflows/my_workflow.py` with the code
2. Write `.bifrost/workflows.yaml` with `id: {wf_id}`
3. Write `forms/{form_id}.form.yaml` with `workflow_id: {wf_id}`
4. Write `.bifrost/forms.yaml` with `id: {form_id}`
5. Write `agents/{agent_id}.agent.yaml` with `tool_ids: [{wf_id}]`
6. Write `.bifrost/agents.yaml` with `id: {agent_id}`

**Preflight catches missing IDs as a safety net**, but generating upfront avoids errors.

## Entity YAML Formats

### Workflow Manifest Entry (`.bifrost/workflows.yaml`)

```yaml
workflows:
  onboard_user:                    # Key = human-readable name
    id: "f8a1b3c2-..."
    path: workflows/onboard_user.py
    function_name: onboard_user
    type: workflow                 # workflow | tool | data_provider
    organization_id: "9a3f2b1c-..."  # null for global
    roles: ["b7e2a4d1-..."]
    access_level: role_based       # role_based | authenticated | public
    endpoint_enabled: false
    timeout_seconds: 1800
    category: Onboarding
    tags:
      - hr
      - onboarding
```

Workflow code is a standard `.py` file with `@workflow`/`@tool`/`@data_provider` decorators.

### Form (`forms/{uuid}.form.yaml`)

```yaml
name: Onboarding Form
description: New employee onboarding request
workflow_id: "f8a1b3c2-..."        # UUID reference to workflow
launch_workflow_id: null           # Optional startup workflow
form_schema:
  fields:
    - name: employee_name
      type: text
      label: Employee Name
      required: true
    - name: department
      type: select
      label: Department
      options:
        - { label: Engineering, value: Engineering }
        - { label: Sales, value: Sales }
    - name: license_type
      type: select
      label: M365 License
      default_value: E3
      options:
        - { label: E1, value: E1 }
        - { label: E3, value: E3 }
        - { label: E5, value: E5 }
```

**Form manifest entry** (`.bifrost/forms.yaml`):
```yaml
forms:
  Onboarding Form:
    id: "d2e5f8a1-..."
    path: forms/d2e5f8a1-....form.yaml
    organization_id: "9a3f2b1c-..."
    roles: ["b7e2a4d1-..."]
    access_level: role_based       # role_based | authenticated | public
```

### Agent (`agents/{uuid}.agent.yaml`)

```yaml
name: Support Agent
description: Handles tier 1 support tickets
system_prompt: You are a helpful support agent...
channels:
  - chat
llm_model: claude-sonnet-4-5-20250929
llm_temperature: 0.7
llm_max_tokens: 4096
tool_ids:                          # Workflow UUIDs this agent can call
  - "a2b4c6d8-..."
  - "e1f2a3b4-..."
delegated_agent_ids: []            # Other agents it can delegate to
knowledge_sources:                 # Knowledge namespace names
  - tickets
system_tools:                      # Built-in tools
  - http
```

**Agent manifest entry** (`.bifrost/agents.yaml`):
```yaml
agents:
  Support Agent:
    id: "c3d4e5f6-..."
    path: agents/c3d4e5f6-....agent.yaml
    organization_id: "9a3f2b1c-..."
    roles: ["b7e2a4d1-..."]
    access_level: authenticated
```

### App (`apps/{slug}/app.yaml`)

```yaml
name: Dashboard
description: Client overview dashboard
dependencies:              # Optional: npm packages loaded from esm.sh
  recharts: "2.12"
  dayjs: "1.11"
```

App pages and components are sibling files in the same directory.

**App manifest entry** (`.bifrost/apps.yaml`):
```yaml
apps:
  Dashboard:
    id: "a1b2c3d4-..."
    path: apps/my-dashboard/app.yaml
    organization_id: "9a3f2b1c-..."
    roles: ["b7e2a4d1-..."]
    access_level: role_based
```

## Sync Workflow (SDK-First)

### Creating New Entities

1. Generate UUID(s) for all new entities
2. Write entity files (workflow `.py`, form `.form.yaml`, agent `.agent.yaml`)
3. Add entries to `.bifrost/*.yaml` manifest files with the UUIDs
4. `git add . && git commit -m "Add onboarding workflow and form"`
5. `git push`
6. `bifrost sync`

### What `bifrost sync` Does

1. **Preview** — fetches remote state, computes diff (pull/push), runs preflight
2. **Preflight validation** — checks syntax, linting, cross-references, manifest validity
3. **Execute** — if no conflicts, auto-syncs; if conflicts, shows them for resolution

### Preflight Checks

Preflight runs automatically during sync and validates:

| Check | Category | Severity | What it catches |
|-------|----------|----------|-----------------|
| Manifest parse | `manifest` | error | Invalid YAML, missing required fields |
| File existence | `manifest` | error | Manifest references file that doesn't exist |
| Python syntax | `syntax` | error | `SyntaxError` in `.py` files |
| Ruff linting | `lint` | warning | Style violations (non-blocking) |
| UUID references | `ref` | error | Form references non-existent workflow |
| Cross-references | `ref` | error | Broken org/role/integration refs in manifest |
| Orphan detection | `orphan` | warning | Forms referencing workflows not in manifest |
| Secret configs | `health` | warning | Config values that need manual setup |
| OAuth setup | `health` | warning | OAuth providers needing credentials |

**Errors block sync. Warnings are informational.**

### Resolving Conflicts

```bash
bifrost sync --preview                    # Preview only
bifrost sync --resolve workflows/billing.py=keep_remote
bifrost sync --resolve a.py=keep_local --resolve b.py=keep_remote
bifrost sync --confirm-orphans            # Acknowledge orphan warnings
```

## Before Building

Clarify with the user:
1. **Which organization?** Check `.bifrost/organizations.yaml` (SDK-First) or `list_organizations` (MCP-Only)
2. **What triggers this?** (webhook, form, schedule, manual)
3. **If webhook:** Get sample payload
4. **What integrations?** Check `.bifrost/integrations.yaml` (SDK-First) or `list_integrations` (MCP-Only)
5. **Error handling requirements?**
6. **If migrating from Rewst:** Use `/rewst-migration` skill for cutover guidance

## MCP Tools Guidance

> **SDK-First mode:** Read `.bifrost/*.yaml` files instead of calling discovery tools. Use MCP only for execution, events, and SDK docs.

### When to use MCP tools

| Need | Tool | Notes |
|------|------|-------|
| SDK/decorator docs | `get_workflow_schema`, `get_sdk_schema` | Platform capability reference |
| Form field types | `get_form_schema` | Field type docs not in local files |
| App structure docs | `get_app_schema` | App Builder patterns + component list |
| Agent structure docs | `get_agent_schema` | Agent channels/config docs |
| Data provider docs | `get_data_provider_schema` | Data provider patterns |
| Run a workflow | `execute_workflow` | Test on platform after sync |
| Check execution logs | `get_execution`, `list_executions` | Debug workflow runs |
| Create event triggers | `create_event_source`, `create_event_subscription` | Webhooks and schedules |
| Manage events | `update_event_source`, `delete_event_source` | Modify triggers |
| Validate an app | `validate_app` or `bifrost push --validate` | Static analysis: bad components, workflow refs |
| Push files to platform | `bifrost push <path>` (CLI) | Batch push local files to `_repo/` — use `--clean` to delete remote-only files, `--validate` for apps |
| Get app dependencies | `get_app_dependencies` | Read npm deps from app.yaml |
| Update app dependencies | `update_app_dependencies` | Add/remove/update npm deps in app.yaml |

### Editing via MCP (MCP-Only mode)

- Prefer `patch_content` for surgical string replacements — it's precise and safe
- Use `replace_content` only when replacing an entire file or when `patch_content` fails (ambiguous match)
- Use `get_content` / `read_content_lines` to read before editing

## Building Apps

Apps are React-based dashboards/tools built with TSX files. They run in a sandboxed runtime with access to platform components and workflow data.

### App File Structure

```
apps/my-app/
  app.yaml              # App metadata (name, description)
  _layout.tsx           # Root layout - MUST use <Outlet />, NOT {children}
  _providers.tsx         # Optional context providers
  pages/
    index.tsx           # Home page (route: /)
    settings.tsx        # Settings page (route: /settings)
    [id].tsx            # Dynamic route (route: /:id)
  components/
    MyWidget.tsx        # Reusable components
  modules/
    utils.ts            # Utility modules
```

### Critical App Rules

1. **Use standard ES imports** — the server-side compiler transforms them automatically:
   ```tsx
   // Bifrost platform imports (hooks, components, icons, utilities):
   import { Button, Card, useWorkflowQuery, useState } from "bifrost";

   // External npm packages (declared in app.yaml dependencies):
   import dayjs from "dayjs";
   import { LineChart, Line } from "recharts";
   ```
   Everything from `"bifrost"` is also available in scope without importing (backwards compatible), but explicit imports are recommended.

2. **Root layout uses `<Outlet />`**, not `{children}`

3. **Use workflow UUIDs**, not workflow names, in `useWorkflowQuery` / `useWorkflowMutation`

4. **Scrollable content** needs flex layout: parent `flex flex-col h-full`, child `flex-1 overflow-auto`

### External Dependencies (npm packages)

Apps can use npm packages loaded at runtime from esm.sh CDN. Declare them in `app.yaml`:

```yaml
name: My Dashboard
description: Analytics dashboard
dependencies:
  recharts: "2.12"
  dayjs: "1.11"
```

**Rules:** Max 20 packages. Version format: semver with optional `^`/`~` (e.g., `"2.12"`, `"^1.5.3"`). Package names: lowercase, hyphens, optional `@scope/` prefix.

**Managing via API:**
- `GET /api/applications/{app_id}/dependencies` — current deps
- `PUT /api/applications/{app_id}/dependencies` — update deps

**Managing via MCP:** Include `dependencies` field in `app.yaml` when using `push_files`.

### Available from "bifrost"

**Hooks:**
- `useWorkflowQuery(workflowId, params, options)` — auto-executes on mount, returns `{ data, isLoading, error, refetch }`
- `useWorkflowMutation(workflowId)` — returns `{ mutate, mutateAsync, isPending, data, error }`
- `useUser()` — current user info
- `navigate(path)` — programmatic navigation

**UI Components (shadcn/ui):**
- Layout: Card (+ Header, Footer, Title, Content, Description, Action)
- Forms: Button, Input, Label, Textarea, Checkbox, Switch, Select (+sub), RadioGroup, Combobox, MultiCombobox, TagsInput, Slider
- Display: Badge, Avatar (+sub), Alert (+sub), Skeleton, Progress, Separator
- Navigation: Tabs (+sub), Pagination (+sub)
- Feedback: Dialog (+sub), AlertDialog (+sub), Tooltip (+sub), Popover (+sub), Sheet (+sub), HoverCard (+sub)
- Data: Table (+sub), Accordion (+sub), Collapsible (+sub)
- Calendar/Date: Calendar, DateRangePicker
- Menus: DropdownMenu (+sub), ContextMenu (+sub), Command (+sub)
- Toggle: Toggle, ToggleGroup, ToggleGroupItem
- Routing: Link, NavLink, Outlet, Navigate

**Utilities:**
- `cn(...)` — Tailwind class merging (clsx + twMerge)
- `format(date, pattern)` — date-fns format function (e.g., `format(new Date(), 'yyyy-MM-dd')`)
- `toast(message)` — Sonner toast notifications

**Icons:** All lucide-react icons available (e.g., `<Settings />`, `<ChevronRight />`, `<Search />`)

### App Development Workflow (SDK-First)

1. Write app files locally in `apps/{slug}/`
2. Add entry to `.bifrost/apps.yaml`
3. `bifrost push apps/{slug}` to push to platform (or `bifrost sync` if using git workflow)
4. `bifrost push apps/{slug} --validate` to push and validate in one step
5. Preview at `$BIFROST_DEV_URL/apps/{slug}/preview`

### App Development Workflow (MCP-Only)

1. `create_app(name="My App")` — scaffolds `_layout.tsx` + `pages/index.tsx`
2. Edit files with `patch_content` / `replace_content`
3. Preview at `$BIFROST_DEV_URL/apps/{slug}/preview` (live updates)
4. Validate with `validate_app(app_id)` to catch issues

### App Validation

Use `validate_app(app_id)` MCP tool or `POST /api/applications/{app_id}/validate` to check for:
- Unknown components (JSX tags not in the component registry)
- Forbidden patterns (`require()`, `module.exports`)
- Bad workflow ID format (must be UUID)
- Non-existent workflow IDs (checks DB for active workflows)
- Missing required files (`_layout.tsx`)

## Triggering Workflows

Three patterns for connecting triggers to workflows:

### Schedule
```
1. create_event_source(name="Daily Report", source_type="schedule", cron_expression="0 9 * * *", timezone="America/New_York")
2. create_event_subscription(source_id=<id>, workflow_id=<id>, input_mapping={"report_type": "daily"})
```

### Webhook
```
1. create_event_source(name="HaloPSA Tickets", source_type="webhook", adapter_name="generic")
   -> returns callback_url: /api/hooks/{source_id}
2. create_event_subscription(source_id=<id>, workflow_id=<id>, event_type="ticket.created")
3. Configure external service to POST to callback_url
```

### Form
```
1. Sync workflow to platform (bifrost sync or register_workflow)
2. create_form(name="New User", workflow_id=<id>, fields=[...])
   -> returns form URL
```

## Testing

- **Workflows (local):** `bifrost run <file> --workflow <name> --params '{...}'`
- **Workflows (remote):** `execute_workflow` with workflow ID, check `get_execution` for logs
- **Forms:** Access at `$BIFROST_DEV_URL/forms/{form_id}`, submit, check `list_executions`
- **Apps:** Preview at `$BIFROST_DEV_URL/apps/{slug}/preview`, validate with `validate_app`, publish with `publish_app`, then live at `$BIFROST_DEV_URL/apps/{slug}`
- **Events (schedule):** Wait for next cron tick, check `list_executions` for the subscribed workflow
- **Events (webhook):** `curl -X POST $BIFROST_DEV_URL/api/hooks/{source_id} -H 'Content-Type: application/json' -d '{...}'`, check `list_executions`

## Debugging

### MCP-First Debugging
1. Check execution logs via `get_execution`
2. Verify integrations with `list_integrations`
3. Test workflows with `execute_workflow`
4. Inspect workflow metadata with `get_workflow`

### When Errors Suggest System Bugs

If an error appears to be a backend bug (not user error or doc issue):

**If BIFROST_HAS_SOURCE is true:**
> "This appears to be a backend bug ({error description}). I have access to the Bifrost source code at $BIFROST_SOURCE_PATH. Would you like me to debug and fix this on the backend?"

**If BIFROST_HAS_SOURCE is false:**
> "This appears to be a backend bug ({error description}). Please report this to the platform team with these details: {error details}"

### Issue Categories
- **Documentation/Schema issue** -> Note for recommendation, work around, continue
- **System bug** -> Detect source access, offer to fix or escalate

## App URLs

- **Preview:** `$BIFROST_DEV_URL/apps/{slug}/preview`
- **Live (after `publish_app`):** `$BIFROST_DEV_URL/apps/{slug}`

## Session Summary

At end of session, provide:

```markdown
## Session Summary

### Completed
- [What was built/accomplished]

### System Bugs Fixed (if source available)
- [Bug] -> [Fix] -> [File]

### Documentation Recommendations
- [Tool/Schema]: [Issue] -> [Recommendation]

### Notes for Future Sessions
- [Relevant context]
```
