# Design: Unified LLMs.txt + Design-First App Workflow

**Date**: 2026-02-24
**Status**: Draft

## Problem

Coding agents building on Bifrost produce mediocre UX. An agent asked to build an email campaign page reaches for `<Textarea>` because:

1. **Documentation is component-catalog-shaped, not design-shaped.** Six separate docs endpoints, a 1500-line Python component registry, and an MCP tool with 3 modes — all teaching the agent about component props, none teaching it to think about design.
2. **The docs accidentally define a ceiling.** By exhaustively documenting pre-included shadcn components, we imply that's all you can use. Agents don't realize they can build custom components, use arbitrary Tailwind, or add npm packages for richer UI.
3. **No design conversation.** The skill workflow goes straight from "user wants an app" to "start writing TSX." There's no step where the agent asks what the app should look like or feel like.
4. **CSS is unnecessarily constrained.** Apps can't define custom CSS files, forcing complex styles into inline `style` props — a format LLMs handle poorly compared to plain CSS.

Meanwhile, the grader project (standalone React) produced a beautiful Google Classroom-inspired UI because the agent had a design plan, built custom components, and used CSS variables freely.

## Design Decisions

### 1. Single `llms.txt` replaces all docs endpoints

**What**: One publicly accessible markdown document at `/api/llms.txt` containing everything an LLM needs to build on Bifrost — workflows, forms, agents, apps, tables, data providers, integrations, events.

**Why**:
- LLMs work best with a single well-structured document, not 6+ fragmented fetches
- Follows the emerging `llms.txt` convention — any tool can fetch it, not just our MCP
- One URL to remember: `{BIFROST_URL}/api/llms.txt`

**How**: A template file (`api/src/templates/llms.txt.md`) with hand-written prose and a few auto-generated token insertions:

```
# Bifrost Platform Documentation

{authored prose: platform overview, concepts, how things connect}

## Workflows & Tools

{authored prose: decorators, patterns, error handling, testing}

### SDK Module Reference
{sdk_module_docs}        ← auto-generated from SDK source via reflection

## Forms

{authored prose: field types, workflow linking, file uploads, data providers}

### Form Models
{form_model_docs}        ← auto-generated from Pydantic models

## Agents

{authored prose: channels, tools, delegation, knowledge sources}

### Agent Models
{agent_model_docs}       ← auto-generated from Pydantic models

## Apps

{authored prose: file structure, imports, sandbox constraints, custom components,
 dark mode, CSS support, dependencies, design philosophy}

### Pre-included Components
{pre_included_components} ← auto-generated from components.ts exports

## Tables
{authored prose: scope, column types, visibility rules}

### Table Models
{table_model_docs}       ← auto-generated from Pydantic models

## Data Providers
{authored prose: return format, form integration}

## Events
{authored prose: sources, subscriptions, webhooks, schedules}
```

**Token generation**: An endpoint handler reads the template, calls the existing `_generate_module_docs()`, `models_to_markdown()` to fill auto-generated tokens, and returns plain text. Regenerated on each request (no caching). The pre-included component list is manually maintained in the template — we rarely add/remove components, and there's no clean way to extract it from the client bundle at API runtime.

**Three delivery mechanisms** (same content, different access patterns):

| Channel | How | When to use |
|---------|-----|-------------|
| **MCP tool** | `get_docs` → returns `llms.txt` content directly | Claude Desktop, MCP-only mode, any MCP client |
| **CLI** | `bifrost api GET /api/llms.txt` | SDK-first mode, skill downloads docs once per session |
| **HTTP** | `GET {BIFROST_URL}/api/llms.txt` (public, no auth) | Any tool, browser, curl, external agents |

The MCP `get_docs` tool is a thin wrapper — calls the same generation function as the HTTP endpoint. Zero extra maintenance.

**What we kill**:
- `/api/docs/sdk`, `/api/docs/apps`, `/api/docs/forms`, `/api/docs/agents`, `/api/docs/tables`, `/api/docs/data-providers` (6 endpoints)
- `get_app_schema` MCP tool
- `get_component_docs` MCP tool
- `component_docs.py` (1500-line component registry)
- `get_sdk_schema`, `get_form_schema`, `get_agent_schema`, `get_table_schema`, `get_data_provider_schema` MCP tools

**What we keep**: The auto-generation utilities (`schema_utils.py`, `_generate_module_docs`, `_generate_decorator_docs`, etc.) — they now feed into the template instead of standalone endpoints.

### 2. Per-app CSS file support

**What**: Apps can include a `styles.css` file that gets injected into the page when the app renders.

**Why**: LLMs produce better CSS when writing plain `.css` files vs inline `style` props. CSS variables, complex gradients, custom animations, and `.dark` selectors are natural in CSS and awkward inline.

**Changes** (6 touchpoints):

| Layer | File | Change |
|-------|------|--------|
| Path validation | `app_code_files.py` | Allow `.css` extension |
| Render endpoint | `app_code_files.py` | Skip compilation for `.css`, return in `styles` field |
| Response model | `AppRenderResponse` | Add `styles: dict[str, str]` |
| Compiler | `app_compiler/__init__.py` | Skip `.css` in batch compile |
| Client runtime | `JsxAppShell.tsx` | Inject CSS via scoped `<style>` tag |
| Route builder | `app-code-router.ts` | Filter `.css` from page routes |

**Scoping**: CSS is injected in a `<style data-app="{appId}">` tag. Apps should use a wrapper class or CSS nesting to avoid leaking styles into the platform UI.

**Dark mode**: Works naturally — `.dark .my-class { ... }` inherits from the platform's root toggle.

### 3. App section of llms.txt: "build anything" philosophy

The app documentation section shifts from "here's our component API" to "you have full creative control":

**Pre-included components** are listed by name only (no props docs). They're standard shadcn/ui — every LLM already knows them. The list tells the agent what's available without installation.

**Custom components are encouraged**: "Need something not pre-included? Build it in `components/`. shadcn/ui components are just TSX files — recreate any shadcn component, customize it, or build entirely new ones from scratch with React and Tailwind."

**The docs explicitly state the creative surface**:
- Full Tailwind CSS v4 with all utilities, arbitrary values, responsive breakpoints
- Custom CSS via `styles.css` with CSS variables, animations, gradients
- Dark mode via `.dark` selector and `dark:` Tailwind variants
- npm dependencies for specialized libraries (recharts, tiptap, etc.)
- Lucide icons (full set)
- date-fns for date formatting

### 4. SKILL.md: design-first workflow

The build skill gets a new workflow for all artifact types (not just apps):

**For apps — design conversation before code**:

1. **New app**: Ask what it should look like. "What should this feel like? Any products you'd like it inspired by?" Explore the key screens and interactions. Decide what's custom vs pre-included. If a distinct visual identity is desired, plan the `styles.css` theme (colors, typography, spacing).
2. **Existing app**: Read `styles.css` and existing components first. Match the established patterns.
3. **Component strategy**: Decide upfront — pre-included shadcn for standard UI, custom components for anything distinctive. Don't default to the simplest component that technically works.

**For workflows/forms/agents — clarification before building**:

The existing "Before Building" section in the skill already covers this (organization, triggers, integrations). This stays and gets reinforced.

**Documentation fetch** (auto-detect delivery channel):

```
If $BIFROST_MCP_CONFIGURED → get_docs MCP tool, save to /tmp/bifrost-docs/llms.txt
If $BIFROST_SDK_INSTALLED  → bifrost api GET /api/llms.txt > /tmp/bifrost-docs/llms.txt
Fallback                   → ask user for Bifrost URL, curl it
```

One fetch. One file. Grep as needed. Same content regardless of channel.

### 5. MCP tool cleanup

**Add**: `get_docs` — returns the full `llms.txt` content. Single tool replaces all schema tools.

**Keep** (operations):
- Apps: `list_apps`, `create_app`, `get_app`, `update_app`, `publish_app`, `validate_app`, `push_files`, `get_app_dependencies`, `update_app_dependencies`
- All other CRUD/operation tools across domains

**Remove** (documentation):
- Apps: `get_app_schema`, `get_component_docs`
- SDK: `get_sdk_schema`
- Forms: `get_form_schema`
- Agents: `get_agent_schema`
- Tables: `get_table_schema`
- Data providers: `get_data_provider_schema`

All documentation is now served via `get_docs` (MCP) or `/api/llms.txt` (HTTP). MCP tools are for operations only.

## Implementation Summary

| Change | Effort | Files |
|--------|--------|-------|
| Template-based `llms.txt` endpoint | Medium | New template file, new endpoint, token generators |
| `get_docs` MCP tool | Small | Thin wrapper calling same generation function |
| Remove 6 docs endpoints + 7 MCP schema tools | Small | Delete code, update registrations |
| Delete `component_docs.py` | Small | Delete file, remove imports |
| Per-app CSS support | Medium | 6 files across API + client |
| SKILL.md redesign | Small | Rewrite skill file with doc-fetch detection + design workflow |
| Author `llms.txt` template prose | Medium | Migrate + consolidate existing endpoint content into template |

## Resolved Decisions

1. **Component list**: Manually maintained in the template. Rarely changes, no clean way to extract from client bundle at API runtime.
2. **Old endpoints**: Removed entirely, no redirect period. Update skill and CLI references in the same PR.
3. **Caching**: Regenerated per request. SDK reflection is cheap enough and avoids stale docs after deployments.
