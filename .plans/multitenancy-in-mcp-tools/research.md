# Multi-tenancy in MCP Tools - Research

## Overview

Investigation into how Bifrost's MCP tools handle multi-tenancy, what's missing, and what changes are needed to properly support organization scoping.

---

## Current State

### MCP Tools Inventory (37 tools)

Located in `/api/src/services/mcp/tools/`:

| Category | Tools | Org Support |
|----------|-------|-------------|
| **Workflows** | `list_workflows`, `get_workflow`, `execute_workflow`, `create_workflow`, `validate_workflow` | Filesystem-based, no org scoping |
| **Forms** | `list_forms`, `get_form`, `create_form`, `update_form`, `get_form_schema` | Yes - uses `context.org_id` |
| **Apps** | `list_apps`, `get_app`, `create_app`, `update_app`, `publish_app`, `get_app_schema` | Yes - uses `context.org_id` |
| **Pages** | `get_page`, `create_page`, `update_page`, `delete_page` | Via parent app |
| **Components** | `list_components`, `get_component`, `create_component`, `update_component`, `delete_component`, `move_component` | Via parent app |
| **Files** | `list_files`, `read_file`, `write_file`, `delete_file`, `search_files`, `create_folder` | Filesystem-based |
| **Execution** | `list_executions`, `get_execution` | TBD |
| **Data Providers** | `get_data_provider_schema` | Schema only |
| **Integrations** | `list_integrations` | Global |
| **Knowledge** | `search_knowledge` | TBD |

### Missing Tools

| Category | Needed Tools | Notes |
|----------|-------------|-------|
| **Organizations** | `list_organizations`, `create_organization`, `get_organization` | Required for org-aware workflows |
| **Tables** | `list_tables`, `create_table`, `get_table`, `update_table` | Tables exist in ORM but no MCP access |

---

## Multi-tenancy Model

### Organization Structure

**File**: `/api/src/models/orm/organizations.py`

```python
class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    slug: Mapped[str] = mapped_column(unique=True)
    description: Mapped[Optional[str]]
    # ... relationships to users, roles, tables, apps, etc.
```

### Scoping Pattern

Resources use a nullable `organization_id` field:
- `organization_id = NULL` → **Global resource** (visible to all orgs)
- `organization_id = UUID` → **Org-specific resource** (visible only to that org)

### Current Filtering Logic

**Example from `apps.py:192-193`**:
```python
# Users see: their org's apps + global apps
query = query.where(
    (Application.organization_id == context.org_id) |
    (Application.organization_id.is_(None))
)
```

**Platform admins** (via `mcp_platform_admin` capability) see all resources regardless of org.

---

## Entity Relationships

```
Organization
├── Tables (scoped via organization_id)
├── Applications (scoped via organization_id)
│   └── Pages
│       └── Components
├── Forms (scoped via organization_id)
├── Agents (scoped via organization_id)
└── Users (via organization_users junction)
```

---

## Detailed Analysis

### Applications (`create_app`)

**File**: `/api/src/services/mcp/tools/apps.py:167`

```python
@bifrost_tool()
async def create_app(
    name: str,
    slug: str | None = None,
    description: str | None = None,
    create_home_page: bool = True,
    context: ToolContext = None
) -> dict:
```

**Current behavior**: Always sets `organization_id=context.org_id`
**Gap**: No way to explicitly create a global app (`organization_id=None`) or specify a different org

### Forms (`create_form`)

**File**: `/api/src/services/mcp/tools/forms.py:348`

Similar pattern - uses `context.org_id` automatically.

### Tables (No MCP Tools)

**ORM File**: `/api/src/models/orm/tables.py`

```python
class Table(Base):
    __tablename__ = "tables"
    id: Mapped[uuid.UUID]
    name: Mapped[str]
    slug: Mapped[str]
    organization_id: Mapped[Optional[uuid.UUID]]  # Scoping field
    application_id: Mapped[Optional[uuid.UUID]]   # Can belong to an app
    scope: Mapped[str]  # 'global', 'organization', 'application'
    # ... columns, indexes, etc.
```

**Gap**: Tables are fully modeled but completely inaccessible via MCP.

### Table Scope Updates

**Finding**: Looking at the table update endpoints and UI, there appears to be no restriction on updating `scope` after creation. However, this could cause data integrity issues if records already exist with different visibility expectations.

---

## Documentation Locations

### 1. Bifrost SKILL.md (Vibecoding Debugger)

**File**: `/Users/jack/GitHub/bifrost-api/.claude/skills/bifrost_vibecode_debugger.md` (if exists, needs verification)

### 2. Coding Agent Prompt

**File**: `/api/src/services/coding_mode/prompts.py`

Contains the system prompt for the Coding Agent. This prompt guides how the agent interacts with users and should include multi-tenancy awareness.

### 3. System Agents

**File**: `/api/src/core/system_agents.py`

Defines the Coding Assistant and other system agents. Contains agent descriptions and capabilities.

### 4. Bifrost Docs - AI Coding Guide

**File**: `/Users/jack/GitHub/bifrost-docs/src/content/docs/how-to-guides/local-dev/ai-coding.md`

External documentation on using AI coding features with Bifrost.

---

## Key Code References

| Purpose | File | Line |
|---------|------|------|
| App creation | `api/src/services/mcp/tools/apps.py` | 167 |
| App listing/filtering | `api/src/services/mcp/tools/apps.py` | 192-193 |
| Form creation | `api/src/services/mcp/tools/forms.py` | 348 |
| Table ORM model | `api/src/models/orm/tables.py` | - |
| Organization ORM | `api/src/models/orm/organizations.py` | - |
| MCP Tool Registry | `api/src/services/mcp/tool_registry.py` | - |
| Coding Agent Prompt | `api/src/services/coding_mode/prompts.py` | - |

---

## Tool Access Control Model (Current Implementation)

### Tool Registry

**File**: `/api/src/services/mcp/tool_registry.py`

```python
class SystemToolMetadata:
    id: str
    name: str
    description: str
    category: ToolCategory  # workflow, file, form, app_builder, etc.
    default_enabled_for_coding_agent: bool = True
    input_schema: dict
    implementation: Callable

class ToolCategory(str, Enum):
    WORKFLOW = "workflow"
    FILE = "file"
    FORM = "form"
    APP_BUILDER = "app_builder"
    DATA_PROVIDER = "data_provider"
    KNOWLEDGE = "knowledge"
    INTEGRATION = "integration"
```

### Access Check Flow

1. **Agent Assignment**: `Agent.system_tools` (array of tool IDs)
2. **Access Service**: `/api/src/services/mcp/tool_access.py` - `MCPToolAccessService.get_accessible_tools()`
3. **Middleware**: `/api/src/services/mcp/middleware.py` - Filters `tools/list` and blocks unauthorized calls
4. **Context**: `MCPContext` carries `is_platform_admin`, `org_id`, `enabled_system_tools`

### Frontend Tool UI

**File**: `/client/src/components/agents/AgentDialog.tsx`

- Groups tools into "System Tools" and "Workflows"
- Uses `useToolsGrouped()` hook from `/client/src/hooks/useTools.ts`

### What's Missing

- No `is_restricted: bool` field on `SystemToolMetadata`
- No enforcement that ignores agent assignment for restricted tools
- No UI for "Restricted System Tools" section

---

## Documentation Files to Update

| File | Purpose |
|------|---------|
| `/api/src/services/coding_mode/prompts.py` | Coding Agent system prompt |
| `/api/.claude/skills/bifrost_vibecode_debugger/SKILL.md` | Claude Code skill for platform developers |
| `/bifrost-docs/src/content/docs/how-to-guides/local-dev/ai-coding.md` | External MCP/Coding Agent docs |

---

## Recommendations Summary

### New MCP Tools Needed

1. **Organization Tools**:
   - `list_organizations` - List all orgs (platform admin) or user's orgs
   - `create_organization` - Create new org (platform admin only?)
   - `get_organization` - Get org details by ID or slug

2. **Table Tools**:
   - `list_tables` - List tables with org filtering
   - `create_table` - Create table with explicit scope parameter
   - `get_table` - Get table details
   - `update_table` - Update table (with scope change warning)
   - `get_table_schema` - Documentation on table structure

### Modifications to Existing Tools

1. **`create_app`**: Add optional `scope` parameter (`global` | `organization`)
2. **`create_form`**: Add optional `scope` parameter

### Prompt Updates

1. **Coding Agent Prompt**: Add guidance to ask users which organization resources should be created in
2. **SKILL.md**: Add multi-tenancy awareness section
3. **Bifrost Docs**: Document organization scoping for AI workflows

---

## Confirmed Requirements

### Tool Access Model

**New "Restricted System Tools" category:**
- Scope-sensitive tools that can affect global/cross-org resources
- **Always platform-admin only**, regardless of agent assignment
- Hidden from non-admins even if enabled on agent
- UI shows section with note: "Never available to non-admins"

**Regular "System Tools" category:**
- Normal agent + role scoping
- Read-only and org-scoped operations

**Tool Classification:**

| Tool | Category | Reason |
|------|----------|--------|
| `create_organization` | Restricted | Cross-org, platform-admin only |
| `create_table` | Restricted | Can set global scope |
| `update_table` | Restricted | Can change scope |
| `create_app` (with scope param) | Restricted | Can set global scope |
| `list_organizations` | Restricted | No real need for org users |
| `get_organization` | Restricted | No real need for org users |
| `list_tables` | System | Read-only, uses org filter |
| `get_table` | System | Read-only |
| `search_knowledge` | System | Already unrestricted |

### Scope Handling
- Tools can create global resources (scope parameter)
- Table scope changes allowed but UI warns about implications
- No API/MCP blocking of scope changes

### UI Changes Needed
Agent tool assignment dropdown:
```
├── Restricted System Tools
│   └── (Note: "Never available to non-admins")
├── System Tools
└── Workflows
```
