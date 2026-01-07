# Multi-tenancy in MCP Tools - Implementation Plan

## Overview

Add multi-tenancy awareness to MCP tools so AI agents can properly ask which organization resources should be created in, and add "Restricted System Tools" that are always platform-admin only regardless of agent assignment.

---

## Phase 1: Tool Registry Enhancement

### 1.1 Add `is_restricted` field to SystemToolMetadata

**File**: `api/src/services/mcp/tool_registry.py`

```python
@dataclass
class SystemToolMetadata:
    # ... existing fields ...
    is_restricted: bool = False  # NEW: Platform-admin only regardless of agent
```

### 1.2 Update `@system_tool` decorator

**File**: `api/src/services/mcp/tool_decorator.py`

Add `is_restricted` parameter to the decorator.

### 1.3 Update ToolInfo contract

**File**: `api/src/models/contracts/agents.py`

```python
class ToolInfo(BaseModel):
    # ... existing fields ...
    is_restricted: bool = False  # NEW
```

### 1.4 Update tools endpoint

**File**: `api/src/routers/tools.py`

Include `is_restricted` in the response.

---

## Phase 2: Access Control Enforcement

### 2.1 Update MCPToolAccessService

**File**: `api/src/services/mcp/tool_access.py`

Modify `get_accessible_tools()`:
- Restricted tools: Only include if `context.is_platform_admin == True`
- Regular tools: Existing logic (agent assignment + role filtering)

### 2.2 Update Middleware

**File**: `api/src/services/mcp/middleware.py`

In `on_call_tool()`, add check:
- If tool `is_restricted` and user is not platform admin → block with clear error

---

## Phase 3: New Organization Tools

**File**: `api/src/services/mcp/tools/organizations.py` (NEW)

### 3.1 list_organizations (Restricted)

```python
@system_tool(
    name="list_organizations",
    description="List all organizations in the platform",
    category=ToolCategory.INTEGRATION,
    is_restricted=True
)
async def list_organizations(context: ToolContext) -> dict:
    """List organizations. Platform admin only."""
```

### 3.2 get_organization (Restricted)

```python
@system_tool(
    name="get_organization",
    description="Get organization details by ID or slug",
    category=ToolCategory.INTEGRATION,
    is_restricted=True
)
async def get_organization(
    organization_id: str | None = None,
    slug: str | None = None,
    context: ToolContext = None
) -> dict:
```

### 3.3 create_organization (Restricted)

```python
@system_tool(
    name="create_organization",
    description="Create a new organization",
    category=ToolCategory.INTEGRATION,
    is_restricted=True
)
async def create_organization(
    name: str,
    slug: str | None = None,
    description: str | None = None,
    context: ToolContext = None
) -> dict:
```

---

## Phase 4: New Table Tools

**File**: `api/src/services/mcp/tools/tables.py` (NEW)

### 4.1 list_tables (Regular System Tool)

```python
@system_tool(
    name="list_tables",
    description="List tables in the platform",
    category=ToolCategory.DATA_PROVIDER,
    is_restricted=False
)
async def list_tables(
    scope: str | None = None,  # 'global', 'organization', 'application'
    context: ToolContext = None
) -> dict:
    """List tables. Respects org filtering for non-admins."""
```

### 4.2 get_table (Regular System Tool)

```python
@system_tool(
    name="get_table",
    description="Get table details including columns and indexes",
    category=ToolCategory.DATA_PROVIDER,
    is_restricted=False
)
async def get_table(
    table_id: str | None = None,
    slug: str | None = None,
    context: ToolContext = None
) -> dict:
```

### 4.3 create_table (Restricted)

```python
@system_tool(
    name="create_table",
    description="Create a new table with specified scope",
    category=ToolCategory.DATA_PROVIDER,
    is_restricted=True
)
async def create_table(
    name: str,
    slug: str | None = None,
    scope: str = "organization",  # 'global', 'organization', 'application'
    organization_id: str | None = None,  # Required if scope='organization'
    application_id: str | None = None,   # Required if scope='application'
    columns: list[dict] | None = None,
    context: ToolContext = None
) -> dict:
```

### 4.4 update_table (Restricted)

```python
@system_tool(
    name="update_table",
    description="Update table properties including scope",
    category=ToolCategory.DATA_PROVIDER,
    is_restricted=True
)
async def update_table(
    table_id: str,
    name: str | None = None,
    scope: str | None = None,
    organization_id: str | None = None,
    columns: list[dict] | None = None,
    context: ToolContext = None
) -> dict:
```

### 4.5 get_table_schema (Regular System Tool)

```python
@system_tool(
    name="get_table_schema",
    description="Get documentation about table structure and column types",
    category=ToolCategory.DATA_PROVIDER,
    is_restricted=False
)
async def get_table_schema(context: ToolContext = None) -> str:
    """Return markdown documentation about tables."""
```

---

## Phase 5: Update Existing Tools with Scope

### 5.1 Update create_app

**File**: `api/src/services/mcp/tools/apps.py`

Add `scope` parameter:
```python
async def create_app(
    name: str,
    slug: str | None = None,
    description: str | None = None,
    scope: str = "organization",  # 'global' or 'organization'
    organization_id: str | None = None,  # Override context.org_id
    create_home_page: bool = True,
    context: ToolContext = None
) -> dict:
```

Mark as restricted since it can set global scope.

### 5.2 Update create_form

**File**: `api/src/services/mcp/tools/forms.py`

Add `scope` parameter (same pattern as create_app).

Mark as restricted since it can set global scope.

---

## Phase 6: Frontend UI Updates

### 6.1 Update useTools hook

**File**: `client/src/hooks/useTools.ts`

Group into three categories:
```typescript
const grouped = {
  restricted: [] as ToolInfo[],
  system: [] as ToolInfo[],
  workflow: [] as ToolInfo[],
};

for (const tool of data.tools) {
  if (tool.is_restricted) {
    grouped.restricted.push(tool);
  } else if (tool.type === "system") {
    grouped.system.push(tool);
  } else {
    grouped.workflow.push(tool);
  }
}
```

### 6.2 Update AgentDialog

**File**: `client/src/components/agents/AgentDialog.tsx`

Add third CommandGroup:
```tsx
<CommandGroup heading="Restricted System Tools">
  <p className="text-xs text-muted-foreground px-2 pb-2">
    Never available to non-admins
  </p>
  {toolsGrouped.restricted.map(tool => (
    <CommandItem key={tool.id} ...>
      {tool.name}
    </CommandItem>
  ))}
</CommandGroup>
<CommandGroup heading="System Tools">
  {/* existing system tools */}
</CommandGroup>
<CommandGroup heading="Workflows">
  {/* existing workflows */}
</CommandGroup>
```

---

## Phase 7: Documentation Updates

### 7.1 Coding Agent Prompt

**File**: `api/src/services/coding_mode/prompts.py`

Add section:
```markdown
## Multi-tenancy Awareness

Before creating any resource, ask the user:
1. **Which organization?** Use `list_organizations` to show options
2. **Global or org-specific?** Clarify scope requirements

If user says "global", explain this makes the resource visible to all organizations.

### Tools for Multi-tenancy
- `list_organizations` - See available organizations
- `get_organization` - Get org details by ID or slug
- `create_table` - Create tables with explicit scope
- `list_tables` - View tables (filtered by org for non-admins)
```

### 7.2 SKILL.md

**File**: `api/.claude/skills/bifrost_vibecode_debugger/SKILL.md`

Add section covering:
- New organization tools
- New table tools
- Scope parameter on create_app/create_form
- Multi-tenancy patterns

### 7.3 External Docs

**File**: `bifrost-docs/src/content/docs/how-to-guides/local-dev/ai-coding.md`

Add to External MCP Prompt section:
- New tools reference
- Multi-tenancy guidance

---

## Phase 8: UI Warning for Table Scope Changes

### 8.1 Table Edit Component

Find the table edit component and add warning when scope is changed:

```tsx
{scopeChanged && (
  <Alert variant="warning">
    <AlertTriangle className="h-4 w-4" />
    <AlertDescription>
      Changing table scope affects which users can access this data.
      Existing records will remain but may become visible/hidden to different users.
    </AlertDescription>
  </Alert>
)}
```

---

## File Summary

| File | Action |
|------|--------|
| `api/src/services/mcp/tool_registry.py` | Add `is_restricted` field |
| `api/src/services/mcp/tool_decorator.py` | Add `is_restricted` param |
| `api/src/models/contracts/agents.py` | Add `is_restricted` to ToolInfo |
| `api/src/routers/tools.py` | Include `is_restricted` in response |
| `api/src/services/mcp/tool_access.py` | Enforce restricted tool access |
| `api/src/services/mcp/middleware.py` | Block restricted tools for non-admins |
| `api/src/services/mcp/tools/organizations.py` | NEW - 3 tools |
| `api/src/services/mcp/tools/tables.py` | NEW - 5 tools |
| `api/src/services/mcp/tools/apps.py` | Add scope param, mark restricted |
| `api/src/services/mcp/tools/forms.py` | Add scope param, mark restricted |
| `client/src/hooks/useTools.ts` | Three-way grouping |
| `client/src/components/agents/AgentDialog.tsx` | Three sections in dropdown |
| `api/src/services/coding_mode/prompts.py` | Multi-tenancy guidance |
| `api/.claude/skills/bifrost_vibecode_debugger/SKILL.md` | New tools + patterns |
| `bifrost-docs/...ai-coding.md` | External docs update |
| Table edit component (TBD) | Scope change warning |

---

## Testing Requirements

1. **Unit tests**: New tool implementations
2. **Integration tests**: Access control enforcement
3. **E2E tests**: Agent tool assignment UI with three sections
4. **Manual testing**: Verify restricted tools hidden from non-admins

---

## Execution Order

1. Phase 1 (Registry) + Phase 2 (Access Control) - Foundation
2. Phase 3 (Org Tools) + Phase 4 (Table Tools) - New tools
3. Phase 5 (Scope params) - Update existing tools
4. Phase 6 (Frontend) - UI changes
5. Phase 7 (Docs) - Documentation
6. Phase 8 (UI Warning) - Table scope warning
