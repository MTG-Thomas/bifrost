# Bifrost SDK Skills Design

## Overview

Two user-level Claude Code skills for working with the Bifrost platform:

1. **`bifrost:setup`** - Install SDK, authenticate, configure environment
2. **`bifrost:build`** - Create and debug workflows, forms, apps using MCP tools

Plus a **SessionStart hook** for automatic environment detection.

## Architecture

```
~/.claude/
├── skills/
│   └── bifrost/
│       ├── setup.md      # SDK installation and login
│       └── build.md      # Building and debugging
└── hooks/
    └── bifrost-detect.sh # SessionStart detection
```

## Environment Variables

The hook exports these variables for skills to use:

| Variable | Description |
|----------|-------------|
| `BIFROST_HAS_SOURCE` | `true` if Bifrost source code detected via file markers |
| `BIFROST_SDK_INSTALLED` | `true` if `bifrost` CLI is in PATH |
| `BIFROST_LOGGED_IN` | `true` if credentials file exists |
| `BIFROST_MCP_CONFIGURED` | `true` if bifrost MCP server in Claude settings |
| `BIFROST_DEV_URL` | The Bifrost instance URL (from credentials if logged in) |
| `BIFROST_SOURCE_PATH` | Path to Bifrost source (if detected) |

## SessionStart Hook

**File:** `~/.claude/hooks/bifrost-detect.sh`

### Detection Logic

1. **Source access** - Check for Bifrost-specific file markers in current directory or parents:
   - `api/shared/models.py`
   - `docker-compose.dev.yml`
   - `api/src/main.py`

   If 2+ markers found, set `BIFROST_HAS_SOURCE=true` and `BIFROST_SOURCE_PATH`.

2. **SDK installed** - Check if `bifrost` command exists via `command -v bifrost`

3. **Logged in** - Check for credentials file:
   - Unix: `~/.bifrost/credentials.json`
   - Windows: `%APPDATA%/Bifrost/credentials.json`

   If found, extract and export `BIFROST_DEV_URL` from the credentials.

4. **MCP configured** - Check if `bifrost` server exists via `claude mcp list`

### Hook Registration

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/bifrost-detect.sh"
      }]
    }]
  }
}
```

## Skill: bifrost:setup

**Purpose:** Get a user from zero to working Bifrost environment.

### Resume Logic

The skill checks env vars from the hook to determine where to start:

- `BIFROST_SDK_INSTALLED=false` → Start at Python check
- `BIFROST_LOGGED_IN=false` → Start at login
- `BIFROST_MCP_CONFIGURED=false` → Start at MCP config
- All true → Inform user setup is complete

### Flow

1. **Check Python 3.11+** - If missing, provide OS-specific guidance:
   - macOS: `brew install python@3.11`
   - Ubuntu/Debian: `sudo apt install python3.11 python3-pip`
   - Windows: `winget install Python.Python.3.11`

2. **Get Bifrost URL** - Ask user for their instance URL (e.g., `https://app.gobifrost.com`)

3. **Validate URL** - Test that `{url}/api/cli/download` is accessible

4. **Install SDK** - `pip install --force-reinstall {url}/api/cli/download`

5. **Login** - Run `bifrost login --url {url}` (persists URL + tokens to credentials file)

6. **Configure MCP** - Check for existing config first:
   - If `bifrost` MCP exists with different URL → Ask user if they want to update
   - If not configured → Run `claude mcp add bifrost --transport http --url "{url}/mcp"`

7. **Restart prompt** - Tell user to restart Claude Code for MCP to take effect

### Success Criteria

- `bifrost` CLI works
- User authenticated
- MCP server configured

## Skill: bifrost:build

**Purpose:** Create and debug Bifrost artifacts using MCP tools.

### Prerequisite Check

Verify `BIFROST_SDK_INSTALLED=true` and `BIFROST_LOGGED_IN=true`. If not, direct user to run `/bifrost:setup` first.

### Questions Before Building

- Which organization? (or global scope)
- What triggers this? (webhook, form, schedule, manual)
- If webhook: sample payload?
- What integrations are involved?
- Error handling requirements?

### MCP Tools Reference

- **Discovery:** `list_workflows`, `get_workflow`, `get_workflow_schema`, `get_sdk_schema`
- **Creation:** `create_workflow`, `create_form`, `create_app` (auto-validating)
- **Editing:** `list_content`, `search_content`, `read_content_lines`, `patch_content`, `replace_content`
- **Execution:** `execute_workflow`, `list_executions`, `get_execution`
- **Organization:** `list_organizations`, `get_organization`, `list_tables`

### Creation Workflow

1. Understand the goal
2. Read relevant SDK docs via `get_workflow_schema`, `get_sdk_schema`
3. Create artifact via MCP (`create_workflow`, `create_form`, `create_app`)
4. Test via `execute_workflow` or access preview URL
5. Check logs via `get_execution` if issues
6. Iterate with `patch_content` or `replace_content`

### Debugging Behavior

When an error suggests a system bug (not user error):

- If `BIFROST_HAS_SOURCE=true` → Offer to debug and fix in the backend at `BIFROST_SOURCE_PATH`
- If `BIFROST_HAS_SOURCE=false` → Provide error details for user to report to platform team

### App URLs

- **Preview:** `{BIFROST_DEV_URL}/apps/{slug}/preview`
- **Live (after `publish_app`):** `{BIFROST_DEV_URL}/apps/{slug}`

## Implementation Order

1. Create hook script - `~/.claude/hooks/bifrost-detect.sh`
2. Create setup skill - `~/.claude/skills/bifrost/setup.md`
3. Create build skill - `~/.claude/skills/bifrost/build.md`
4. Update settings - Add hook registration to `~/.claude/settings.json`
5. Test full flow - Verify detection, setup, and build work end-to-end
6. Remove old skill - Delete `/home/jack/GitHub/bifrost/.claude/skills/bifrost_vibecode_debugger/`

## Testing Checklist

- [ ] Hook detects source access when in Bifrost repo
- [ ] Hook detects no source when in other directories
- [ ] Setup skill resumes at correct step based on env vars
- [ ] Setup detects existing MCP config and prompts before overwriting
- [ ] Build skill refuses to run without setup complete
- [ ] Build skill offers backend debugging when source available
- [ ] Build skill provides escalation path when source not available

## Migration

The existing project-level skill at `/home/jack/GitHub/bifrost/.claude/skills/bifrost_vibecode_debugger/` will be removed once these user-level skills are working.
