# Improve App Design Quality Through Better Docs & Guidance

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix false sandbox docs, remove prescriptive layout guidance, and rewrite the design workflow to produce higher-quality app UIs.

**Architecture:** Three independent edits to two files (`llms.txt.md` and `SKILL.md`), plus a parallel subagent for external docs. No code changes, no tests — purely documentation.

**Tech Stack:** Markdown templates, MDX docs

---

### Task 1: Fix False Sandbox Claims in llms.txt

**Files:**
- Modify: `api/src/templates/llms.txt.md:270-277`

**Context:** The "Sandbox Constraints" section claims apps cannot access `window`, `document`, `fetch`, `XMLHttpRequest`. This is wrong. Apps run via `new Function(...)` which doesn't shadow browser globals — only the symbols explicitly passed as arguments are in scope. Browser globals like `window`, `document`, `fetch`, `ResizeObserver`, `MutationObserver`, `IntersectionObserver`, `setTimeout`, etc. are all accessible. This false claim prevents the LLM from using DOM-dependent libraries like tiptap, dnd-kit, prosemirror, or making direct fetch calls.

**Step 1: Replace the Sandbox Constraints section**

Replace lines 270-277 with:

```markdown
### Runtime Environment

Apps run inside the Bifrost shell (not in an iframe). Browser globals (`window`, `document`, `fetch`, `ResizeObserver`, `MutationObserver`, etc.) are accessible — use them directly when needed. External npm packages that depend on DOM APIs (rich text editors, drag-and-drop libraries, charting with DOM measurement) work normally.

**Cannot use:**
- ES dynamic `import()` — all dependencies must be declared in `app.yaml`
- Node.js APIs (`fs`, `path`, `process`, etc.)

Use `useWorkflowQuery`/`useWorkflowMutation` for calling backend workflows. Use `fetch` directly for external HTTP calls that don't need backend logic.
```

**Step 2: Commit**

```bash
git add api/src/templates/llms.txt.md
git commit -m "fix: correct false sandbox claims in llms.txt — browser globals are accessible"
```

---

### Task 2: Simplify Layout Guidance in llms.txt and SKILL.md

**Files:**
- Modify: `api/src/templates/llms.txt.md:279-282`
- Modify: `.claude/skills/bifrost-build/SKILL.md:168`

**Context:** The "Layout Tips" section prescribes a rigid 3-layer `overflow-hidden` pattern for every page. The only actual platform constraint is that the app renders in a fixed-height container — the shell wraps everything in `<div class="h-full w-full overflow-hidden">`. Everything else is standard CSS that an LLM already knows.

**Step 1: Replace Layout Tips in llms.txt**

Replace lines 279-282 with:

```markdown
### Layout

Your app renders in a fixed-height container. The platform does not scroll the page for you — if a page needs scrolling, add `overflow-auto` to the element that should scroll.
```

**Step 2: Remove scrollable content rule from SKILL.md**

In `.claude/skills/bifrost-build/SKILL.md`, replace line 168:

```
4. **Scrollable content:** parent `flex flex-col h-full`, child `flex-1 overflow-auto`
```

with:

```
4. **Fixed-height container:** Your app renders in a fixed-height box — manage your own scrolling
```

**Step 3: Commit**

```bash
git add api/src/templates/llms.txt.md .claude/skills/bifrost-build/SKILL.md
git commit -m "fix: replace prescriptive layout rules with single platform constraint"
```

---

### Task 3: Rewrite Design Workflow in SKILL.md

**Files:**
- Modify: `.claude/skills/bifrost-build/SKILL.md:145-161`

**Context:** The current design workflow asks "what should this feel like?" then immediately jumps to implementation (components, shadcn, styles.css). This produces apps that are structurally correct but visually mediocre — the LLM never actually designs the UI. The Braytel CRM email campaign page is a clear example: functional but cramped, flat, and generic compared to its HubSpot inspiration.

**Step 1: Replace the Design Workflow section**

Replace lines 145-161 (from `### Design Workflow` through the component strategy paragraph) with:

```markdown
### Design Workflow

Before writing any app code, design what you're building.

**New app:**
1. Ask: "What should this app feel like? Any products you'd like it inspired by?"
2. If a product is named, **describe the specific visual patterns** that define it — not abstract qualities ("clean", "modern") but concrete observations: "full-height dark sidebar with icon+label nav items, content area with a sticky toolbar row above the main editor, right panel for live preview with a simulated email client frame, generous whitespace between sections, muted borders instead of heavy dividers."
3. Write a visual spec for each key screen: what elements exist, their spatial relationships, which are fixed vs. scrollable, where the visual weight sits, how the eye flows. This is the design — get it right before writing code.
4. Plan `styles.css` for visual identity — color palette, typography scale, spacing rhythm, dark mode variants.
5. Decide what's a custom component vs. pre-included shadcn. shadcn is for standard interactions (settings forms, confirmation dialogs, data tables). Custom components are for the interactions that define the app's identity — a project management app needs a custom kanban board, not a `<Table>`; an email tool needs a simulated inbox, not a textarea in a split pane.
6. Then start building.

**Existing app:**
1. Read existing `styles.css` and `components/` first
2. Match established design patterns
```

**Step 2: Commit**

```bash
git add .claude/skills/bifrost-build/SKILL.md
git commit -m "fix: rewrite app design workflow to require visual design before code"
```

---

### Task 4: Update External Docs (Parallel Subagent)

**Files:**
- Modify: `../bifrost-integrations-docs/src/content/docs/sdk-reference/app-builder/code-apps.mdx`
- Modify: `../bifrost-integrations-docs/src/content/docs/core-concepts/app-builder.mdx`

**Context:** These external docs are wrong in multiple ways:
1. They say "NO IMPORT STATEMENTS" — wrong for SDK-first mode which uses `import { X } from "bifrost"`
2. They document `useWorkflow` hook — the actual API is `useWorkflowQuery` and `useWorkflowMutation`
3. They don't mention `styles.css`, custom CSS, `_providers.tsx`, or `modules/` directory
4. They have false sandbox claims matching the llms.txt ones we're fixing
5. They don't mention `toast`, `format`, `useAppState`, or other available utilities

**This task should be assigned to a subagent** that:
1. Reads the current llms.txt.md (after Tasks 1-2 are applied) as the source of truth
2. Rewrites `code-apps.mdx` to match reality: `import from "bifrost"`, `useWorkflowQuery`/`useWorkflowMutation`, correct component list, `styles.css`, runtime environment (not sandbox), layout info
3. Rewrites `app-builder.mdx` to match: remove "NO IMPORT STATEMENTS", update hook names, add styles.css and custom CSS mention
4. Preserves the Astro/Starlight MDX format (`<Aside>` components, frontmatter, imports)
5. Commits in the bifrost-integrations-docs repo

**Source of truth for what's correct:** `api/src/templates/llms.txt.md` (the llms.txt template)
