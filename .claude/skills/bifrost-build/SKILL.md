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

**Writing & Creating** -> Local files + git + `bifrost watch`
- Write workflow/form/agent files in the git repo
- Add entries to `.bifrost/*.yaml` manifests
- `bifrost watch` auto-syncs changes to the platform on save

**Testing** -> `bifrost run` for workflows, platform for everything else
- Workflows: `bifrost run <file> --workflow <name> --params '{...}'`
- Forms, apps, agents: These require the platform to render/execute — use preview URLs or `bifrost api` after sync

**Platform operations via `bifrost api`:**
- Schema docs — download once to `/tmp/bifrost-docs/`, grep locally
- Execution — `bifrost api POST /api/workflows/{id}/execute '{"workflow_id":"...","input_data":{...},"sync":true}'`
- Discovery (when debugging sync divergence) — `bifrost api GET /api/workflows`

**MCP tools are for platform-managed creation only:**
- `create_form`, `create_app`, `create_agent` — complex platform artifacts
- `create_event_source`, `create_event_subscription` — event management
- `search_knowledge` — RAG search

**Never use MCP for:** discovery (`list_*`), reading code (`list_content`, `search_content`, `read_content_lines`), schema docs, or executing workflows when a local workspace exists.

## Development Mode

**Auto-detect:** If a `.bifrost/` directory exists in the workspace, you are in **SDK-First** mode. Otherwise, use **MCP-Only** mode. Only ask the user if the situation is ambiguous.

### SDK-First (Local Development)

Best for: developers who want git history, local testing, code review before deploying.

**Requirements:** Git repository, Bifrost SDK installed, GitHub sync configured in platform.

#### Start Watch Mode

Before any build work, ensure `bifrost watch` is running as a background task:

```bash
# Check if already running
pgrep -f 'bifrost watch' > /dev/null 2>&1 && echo "RUNNING" || echo "NOT RUNNING"
```

If not running, start it:
```bash
bifrost watch  # Run as background Bash task
```

Watch mode auto-syncs local file changes to the platform. Manifest validation errors print to its output — check if sync seems broken.

#### Download Platform Docs (Once Per Session)

Auto-detect the best method and fetch the unified docs:

```bash
mkdir -p /tmp/bifrost-docs
# If MCP is available — use get_docs tool, save result to file
# If SDK is available:
bifrost api GET /api/llms.txt > /tmp/bifrost-docs/llms.txt
# Fallback: ask user for Bifrost URL, then:
# curl -s $BIFROST_URL/api/llms.txt > /tmp/bifrost-docs/llms.txt
```

Then use Grep/Read on `/tmp/bifrost-docs/llms.txt` for reference.

#### Discovery: Read Local Files First

**ALWAYS read local `.bifrost/*.yaml` files first.** These are the source of truth — the platform is synced FROM them. Never call MCP discovery tools when the same data is in local YAML.

| To find... | Read this file | NOT this |
|---|---|---|
| Registered workflows/tools/data_providers | `.bifrost/workflows.yaml` | ~~`bifrost api GET /api/workflows`~~ |
| Integration config + data provider refs | `.bifrost/integrations.yaml` | ~~`bifrost api GET /api/integrations`~~ |
| Forms and their linked workflows | `.bifrost/forms.yaml` + `forms/*.form.yaml` | ~~`bifrost api GET /api/forms`~~ |
| Agents and their tool assignments | `.bifrost/agents.yaml` + `agents/*.agent.yaml` | ~~`bifrost api GET /api/agents`~~ |
| Organizations | `.bifrost/organizations.yaml` | ~~`bifrost api GET /api/organizations`~~ |
| Tables | `.bifrost/tables.yaml` | ~~`bifrost api GET /api/tables`~~ |
| Event sources and subscriptions | `.bifrost/events.yaml` | ~~`bifrost api GET /api/events`~~ |
| Apps | `.bifrost/apps.yaml` + `apps/*/app.yaml` | ~~`bifrost api GET /api/applications`~~ |

#### When to use `bifrost api` in SDK-First mode

| Need | Command |
|------|---------|
| Platform docs (SDK, forms, apps, agents, tables) | `grep` in `/tmp/bifrost-docs/llms.txt` (downloaded above) |
| Run a workflow (sync) | `bifrost api POST /api/workflows/{id}/execute '{"workflow_id":"...","input_data":{...},"sync":true}'` |
| Check execution logs | `bifrost api GET /api/executions/{id}` |
| List executions | `bifrost api GET /api/executions` |
| Verify platform state | `bifrost api GET /api/workflows` (only for debugging sync divergence) |

#### Creation flow

1. Start `bifrost watch` (if not already running)
2. Write workflow/form/agent files locally in the git repo
3. Add entries to `.bifrost/*.yaml` manifest files
4. Watch mode auto-syncs files to the platform
5. Test workflows: `bifrost run <file> --workflow <name> --params '{...}'`
6. Test on platform: `bifrost api POST /api/workflows/{id}/execute '{"workflow_id":"...","input_data":{...},"sync":true}'`
7. When happy: `git add && git commit && git push`

### MCP-Only (Remote Development)

Best for: quick iterations, non-developers, working without a local git repo.

**Discovery: Use MCP tools** (`list_workflows`, `list_integrations`, etc.) since there are no local files.

**Flow:**
1. Understand the goal
2. Read platform docs via `get_docs` tool (single unified doc covering SDK, forms, agents, apps, tables, events)
3. Write workflow file via `replace_content`, then `register_workflow` to register it. For forms/apps use `create_form`, `create_app`.
4. Test via `execute_workflow` or access preview URL
5. Check logs via `get_execution` if issues
6. Iterate with `patch_content` or `replace_content`

## Workspace Structure (SDK-First)

The git repo mirrors the platform's storage in S3:

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
    - name: resume
      type: file
      label: Upload Resume
      options: { allowed_types: [".pdf", ".docx"], max_size_mb: 10 }
```

File fields pass S3 paths to workflows as strings (or lists if `multiple: true`). Read them with `await files.read(path, location="uploads")`.

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
4. If `bifrost watch` is running, files auto-sync to platform
5. Otherwise: `git add . && git commit -m "Add onboarding workflow and form" && git push && bifrost sync`

### What `bifrost sync` Does

1. **Preview** — fetches remote state, computes diff (pull/push), runs preflight
2. **Preflight validation** — checks syntax, linting, cross-references, manifest validity
3. **Execute** — if no conflicts, auto-syncs; if conflicts, shows them for resolution

### What `bifrost watch` Does

Watches the local `.bifrost/` workspace for file changes and auto-syncs them to the platform. Runs preflight on each change and prints errors to stdout. Equivalent to running `bifrost sync` automatically on every save.

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

## CLI & MCP Tools Guidance

> **SDK-First mode:** Read `.bifrost/*.yaml` files for discovery, `bifrost api` for platform operations, MCP only for creation of platform-managed artifacts.

### `bifrost api` reference (SDK-First)

| Need | Command |
|------|---------|
| List workflows | `bifrost api GET /api/workflows` |
| Get workflow details | `bifrost api GET /api/workflows/{id}` |
| Execute workflow (sync) | `bifrost api POST /api/workflows/{id}/execute '{"workflow_id":"...","input_data":{...},"sync":true}'` |
| List/get executions | `bifrost api GET /api/executions[/{id}]` |
| List forms | `bifrost api GET /api/forms` |
| List agents | `bifrost api GET /api/agents` |
| List apps | `bifrost api GET /api/applications` |
| List integrations | `bifrost api GET /api/integrations` |
| List tables | `bifrost api GET /api/tables` |

### MCP tools (SDK-First: creation and events only)

| Need | Tool | Notes |
|------|------|-------|
| Create a form | `create_form` | Platform-managed artifact |
| Create an app | `create_app` | Platform-managed artifact |
| Create an agent | `create_agent` | Platform-managed artifact |
| Create event triggers | `create_event_source`, `create_event_subscription` | Webhooks and schedules |
| Manage events | `update_event_source`, `delete_event_source` | Modify triggers |
| RAG search | `search_knowledge` | Knowledge base queries |
| Validate an app | `validate_app` or `bifrost push --validate` | Static analysis |
| Push files to platform | `bifrost push <path>` (CLI) | Batch push — use `--clean` to delete remote-only files |
| Get app dependencies | `get_app_dependencies` | Read npm deps from app.yaml |
| Update app dependencies | `update_app_dependencies` | Add/remove/update npm deps |

### Editing via MCP (MCP-Only mode)

- Prefer `patch_content` for surgical string replacements — it's precise and safe
- Use `replace_content` only when replacing an entire file or when `patch_content` fails (ambiguous match)
- Use `get_content` / `read_content_lines` to read before editing

## Building Apps

Apps are React-based dashboards/tools built with TSX files. They run in a sandboxed runtime with access to platform components and workflow data.

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

### App File Structure

```
apps/my-app/
  app.yaml              # App metadata (name, description)
  _layout.tsx           # Root layout - MUST use <Outlet />, NOT {children}
  _providers.tsx        # Optional context providers
  styles.css            # Custom CSS (dark mode via .dark selector)
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

**UI Components:** Standard shadcn/ui components are pre-included (Button, Card, Dialog, Table, etc.). See platform docs (`/tmp/bifrost-docs/llms.txt`) for the full list of components, hooks, utilities, and icons. Need more? Build custom components in `components/` — shadcn components are just TSX files.

**Custom CSS:** Add a `styles.css` file to your app root for custom styles (CSS variables, dark mode via `.dark` selector, custom classes). See platform docs for examples.

### App Development Workflow (SDK-First)

1. Write app files locally in `apps/{slug}/`
2. Add entry to `.bifrost/apps.yaml`
3. `bifrost watch` auto-syncs to platform (or `bifrost push apps/{slug}` for manual push)
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
1. Sync workflow to platform (bifrost watch or bifrost sync)
2. create_form(name="New User", workflow_id=<id>, fields=[...])
   -> returns form URL
```

## Testing

- **Workflows (local):** `bifrost run <file> --workflow <name> --params '{...}'`
- **Workflows (remote):** `bifrost api POST /api/workflows/{id}/execute '{"workflow_id":"...","input_data":{...},"sync":true}'` — blocks until result
- **Check execution logs:** `bifrost api GET /api/executions/{id}`
- **Forms:** Access at `$BIFROST_DEV_URL/forms/{form_id}`, submit, check `bifrost api GET /api/executions`
- **Apps:** Preview at `$BIFROST_DEV_URL/apps/{slug}/preview`, validate with `validate_app`, publish with `publish_app`, then live at `$BIFROST_DEV_URL/apps/{slug}`
- **Events (schedule):** Wait for next cron tick, check `bifrost api GET /api/executions` for the subscribed workflow
- **Events (webhook):** `curl -X POST $BIFROST_DEV_URL/api/hooks/{source_id} -H 'Content-Type: application/json' -d '{...}'`, check `bifrost api GET /api/executions`

## Debugging

### SDK-First Debugging
1. Check execution logs via `bifrost api GET /api/executions/{id}`
2. Verify platform state with `bifrost api GET /api/workflows` (only if local/remote divergence suspected)
3. Test workflows with `bifrost api POST /api/workflows/{id}/execute '{"workflow_id":"...","input_data":{...},"sync":true}'`
4. Check `bifrost watch` output for sync errors

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
