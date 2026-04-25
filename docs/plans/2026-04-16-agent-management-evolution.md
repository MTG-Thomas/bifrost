# Agent Management Evolution — From List to Living Dashboard

**Date:** 2026-04-16
**Status:** Draft — awaiting review
**Inspiration:** HyperAgent's agent-as-team-member paradigm, internal agent tuning skill concept

---

## Problem

Our agents page is a CRUD list. You create an agent, configure it in a dialog, close the dialog, and the card sits there with a name and a toggle. There's no sense of whether the agent is doing well, what it's been up to, or how to make it better. The "agent runs" page exists separately — you have to navigate away to see what happened. Training an agent means manually editing the system prompt based on vibes.

We need agents to feel **alive** — a page you glance at to know health, click into to understand behavior, and use to systematically improve quality.

---

## Design Overview

### Navigation Flow

```
/agents (Fleet Dashboard)
  └── /agents/:id (Agent Detail)
        ├── Overview tab (health + recent activity)
        ├── Runs tab (filtered run history)
        ├── Review tab (quality review workflow)
        └── Settings tab (current dialog content, promoted to full page)
```

---

## Phase 1: Agent Detail Page + Fleet Health

**Goal:** Replace the dialog with a full page. Add run stats to the list view so you can see health at a glance.

### 1A. Fleet Dashboard (Enhanced /agents)

The current grid/table view evolves. Each agent card/row gains:

```
┌──────────────────────────────────────────────────┐
│  🤖 Triage Agent                        ● Active │
│  Routes and prioritizes incoming tickets          │
│                                                   │
│  Last 7 days:                                     │
│  ██████████░░ 47 runs  │  93% success  │  $4.20  │
│                                                   │
│  Last run: 12 min ago — "Routed ticket #4821"     │
│  ─────────────────────────────────────────────── │
│  Chat │ Slack                        [View Agent] │
└──────────────────────────────────────────────────┘
```

**New data needed on AgentSummary:**
- `total_runs_7d` — count of runs in last 7 days
- `success_rate_7d` — completed / (completed + failed) over 7 days
- `total_cost_7d` — sum of AI costs over 7 days
- `last_run_at` — timestamp of most recent run
- `last_run_summary` — short text: what the agent last did (first ~80 chars of output or tool call summary)

**Backend:** Add a `GET /api/agents/stats` endpoint that returns per-agent stats for a time window. Query `agent_runs` with aggregates. Keep it separate from the agent list query so we don't slow down the list — fetch stats in parallel on the frontend.

**Frontend:** Cards link to `/agents/:id` instead of opening a dialog. The dialog goes away entirely for edit — settings live on the detail page.

### 1B. Agent Detail Page (/agents/:id)

Full page with four tabs:

#### Overview Tab

Two-column layout. Left: health metrics + activity. Right: agent identity.

```
┌─────────────────────────────────────────────────────────────────────┐
│  ← Agents    Triage Agent                              ● Active    │
│  Routes and prioritizes incoming tickets                           │
├─────────────────────────────────────────────────────────────────────┤
│  Overview │ Runs │ Review │ Settings                               │
├──────────────────────────────────┬──────────────────────────────────┤
│                                  │                                  │
│  HEALTH (7d / 30d toggle)        │  CONFIGURATION                  │
│  ┌────────┬────────┬────────┐   │  Model: claude-sonnet-4-6        │
│  │ 142    │  94%   │ $18.40 │   │  Budget: 50 iter / 100k tokens   │
│  │ runs   │success │ cost   │   │  Tools: 4 workflows, 2 system    │
│  └────────┴────────┴────────┘   │  Delegates to: Alert Responder   │
│                                  │  Knowledge: 2 sources            │
│  RUNS/DAY SPARKLINE              │  Channels: Chat, Slack           │
│  ▁▂▃▅▇█▆▅▃▂▁▂▃▅▆▇              │  Access: Role-based (3 roles)    │
│                                  │                                  │
│  RECENT ACTIVITY                 │  OWNER                           │
│  ┌───────────────────────────┐  │  Jack M. — created Apr 2         │
│  │ 12m ago  Routed #4821    │  │                                  │
│  │ 45m ago  Escalated #4819 │  │                                  │
│  │ 1h ago   Closed #4817    │  │                                  │
│  │ 2h ago   Routed #4815    │  │                                  │
│  └───────────────────────────┘  │                                  │
│                                  │                                  │
├──────────────────────────────────┴──────────────────────────────────┤
```

The "Recent Activity" list is a compact view of the latest runs. Each row shows:
- Time ago
- **What was asked** — extracted from `input` (first meaningful line)
- **What was done** — extracted from `output` or final tool call (short summary)
- Status indicator (green dot, red dot, yellow for budget exceeded)

Clicking a row navigates to the existing run detail page.

#### Runs Tab

Embeds the existing `AgentRunsTable` component, pre-filtered to this agent. Same columns, same real-time updates, but scoped.

#### Review Tab — *This is the interesting one*

See Phase 2 below.

#### Settings Tab

The current `AgentDialog` form content, extracted into a full-page layout. Two columns:
- Left: System prompt (full height editor)
- Right: All configuration fields (tools, delegations, knowledge, model settings, access control)

Save button at top. No dialog, no modal.

---

## Phase 2: Quality Review Workflow

**Goal:** Make it easy to flip through agent runs, spot problems, flag them, and improve the agent's prompt — without needing a "judge model."

### The Core Insight

You said it well: a model can't judge whether "absolutely not billable" was right or wrong for a quarantine release ticket. Only a human with context knows. So we skip automated scoring entirely and instead make **human review fast and frictionless**.

### 2A. Review Tab — The Flipbook

The Review tab is a **card-based flipbook** for quickly scanning agent behavior.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Review │  Showing: All runs  │  ◀ 3 of 47 ▶  │  Filter ▼        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Apr 16, 2:34 PM — via Event (ticket.created)                      │
│                                                                     │
│  WHAT WAS ASKED                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ New ticket #4821: "Quarantine release not working"          │   │
│  │ Client: Acme Corp — Priority: High                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  WHAT WAS DONE                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Classified as: Infrastructure — Not billable                │   │
│  │ Routed to: Network Operations queue                        │   │
│  │ Set priority: Urgent (escalated from High)                 │   │
│  │ Note added: "Quarantine release is an infrastructure       │   │
│  │ issue, routing to NetOps for immediate attention"          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────┐  ┌───────────┐  ┌─────────────────┐                  │
│  │  ✓ Good │  │  ✗ Wrong  │  │  → View Details  │                  │
│  └─────────┘  └───────────┘  └─────────────────┘                  │
│                                                                     │
│  ────────────── Keyboard: ← Previous  → Next  G Good  B Bad ───── │
└─────────────────────────────────────────────────────────────────────┘
```

**Key UX decisions:**

1. **Two fields only: "What was asked" + "What was done."** These are extracted server-side from the run's `input` and `output`/steps. Not the raw JSON — human-readable summaries. This is the thing you scroll through quickly.

2. **Binary feedback: Good / Wrong.** No 1-5 scales, no rubrics, no categories. Just "did this agent do the right thing?" Fast to decide, fast to click.

3. **Keyboard navigation.** Arrow keys to flip, G for good, B for bad. You should be able to review 50 runs in a few minutes.

4. **Filter by verdict.** Dropdown filter: All / Unreviewed / Good / Wrong. The "Wrong" filter is the power feature — flip through only the failures to spot patterns.

5. **View Details** links to the existing run detail page for the full step-by-step.

### 2B. Run Verdicts — Data Model

Lightweight. We're storing a human judgment, not building a scoring system.

**New DB table: `agent_run_verdicts`**

```sql
agent_run_verdicts (
  id          UUID PRIMARY KEY,
  run_id      UUID FK -> agent_runs (unique — one verdict per run),
  agent_id    UUID FK -> agents,
  verdict     VARCHAR(20) NOT NULL,  -- 'good' | 'wrong'
  note        TEXT,                   -- optional comment
  reviewed_by VARCHAR(255),          -- user email
  reviewed_at TIMESTAMPTZ NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL
)
```

**Why a separate table instead of columns on agent_runs:**
- Keeps the hot run-tracking path clean — verdicts are a different lifecycle
- Easy to query "all wrong runs for agent X" without touching the runs table
- Can extend later (multiple reviewers, verdict history) without migrating runs

**New fields on AgentRunResponse:**
- `verdict: str | None` — joined from verdicts table
- `verdict_note: str | None`
- `reviewed_by: str | None`
- `reviewed_at: datetime | None`

**New endpoints:**
- `POST /api/agent-runs/{run_id}/verdict` — submit verdict (good/wrong + optional note)
- `GET /api/agents/{agent_id}/review-summary` — counts: total, reviewed, good, wrong, unreviewed

### 2C. Agent Stats Endpoint (Enhanced)

`GET /api/agents/{agent_id}/stats?days=7`

Returns:
```json
{
  "total_runs": 142,
  "completed": 135,
  "failed": 4,
  "budget_exceeded": 3,
  "success_rate": 0.94,
  "total_cost": 18.40,
  "avg_cost_per_run": 0.13,
  "avg_duration_ms": 4500,
  "avg_iterations": 3.2,
  "runs_by_day": [
    {"date": "2026-04-10", "count": 18, "success": 17, "failed": 1},
    ...
  ],
  "review": {
    "total_reviewed": 89,
    "good": 82,
    "wrong": 7,
    "unreviewed": 53
  }
}
```

### 2D. Review-Friendly Run Summaries

The "What was asked" / "What was done" fields need to be **useful at a glance**, not raw JSON dumps.

**Approach: server-side extraction, not LLM summarization.**

For `what_was_asked`:
- If `input` has a `message` or `prompt` key → use that
- If `input` has a `ticket` or `event` object → format as "{type}: {title/subject}"
- Fallback: first 200 chars of JSON-serialized input

For `what_was_done`:
- If `output` has a `summary` or `result` key → use that
- If run has tool calls → list the tools called and their key args ("Routed to: NetOps, Set priority: Urgent")
- If `output` is a string → first 200 chars
- Fallback: "{N} steps, {N} tool calls" with the final tool call's name

These are computed fields on the response, not stored. We add `summary_asked` and `summary_done` to `AgentRunResponse`.

**If extraction isn't good enough**, we can add an optional background job that uses a fast model (Haiku) to generate one-line summaries and caches them on the run record. But start with extraction — it's instant and free.

---

## Phase 3: Prompt Improvement Workflow

**Goal:** When you find wrong runs, make it easy to improve the agent and verify the fix.

### 3A. "Improve from Wrong Runs" Flow

From the Review tab, when filtered to "Wrong" runs:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Review │  Showing: Wrong (7)  │  ◀ 1 of 7 ▶                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  [... wrong run card as above ...]                                  │
│                                                                     │
│  YOUR NOTE                                                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ "This should have been billable — quarantine releases are   │   │
│  │  client-requested infrastructure changes, not internal ops" │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ─────────────────────────────────────────────────────────────────  │
│                                                                     │
│  7 wrong runs collected.                                            │
│  [Suggest Prompt Improvements]                                      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**"Suggest Prompt Improvements"** button:
1. Sends the current system prompt + all wrong runs (with notes) to a model
2. Model returns a **diff** of the system prompt — specific additions/changes with reasoning
3. Displayed as a side-by-side diff in the UI

```
┌─────────────────────────────────────────────────────────────────────┐
│  Suggested Prompt Changes                                           │
├─────────────────────┬───────────────────────────────────────────────┤
│  CURRENT            │  SUGGESTED                                    │
│                     │                                               │
│  When classifying   │  When classifying                             │
│  billability:       │  billability:                                  │
│  - Internal infra   │  - Internal infra                             │
│    = not billable   │    = not billable                              │
│                     │+ - Client-requested                           │
│                     │+   infrastructure changes                     │
│                     │+   (e.g., quarantine releases,                │
│                     │+   firewall rules) = billable                 │
│                     │                                               │
├─────────────────────┴───────────────────────────────────────────────┤
│                                                                     │
│  [Dry Run]  [Apply Changes]  [Dismiss]                              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 3B. Dry Run

"Dry Run" replays the wrong runs against the **modified prompt** without executing any tools (tool calls are simulated/skipped). Shows what the agent *would have done* differently.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Dry Run Results — 7 wrong runs replayed                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Run #4821 (Quarantine release)                                     │
│  Before: Not billable → NetOps                                      │
│  After:  Billable → Client Services               ✓ CHANGED        │
│                                                                     │
│  Run #4799 (Password reset)                                         │
│  Before: Billable → Help Desk                                       │
│  After:  Billable → Help Desk                      — SAME           │
│                                                                     │
│  Run #4756 (Firewall rule request)                                  │
│  Before: Not billable → NetOps                                      │
│  After:  Billable → Client Services               ✓ CHANGED        │
│                                                                     │
│  ... 4 more                                                         │
│                                                                     │
│  Summary: 5/7 runs changed behavior. 2 unchanged.                  │
│                                                                     │
│  [Apply Changes]  [Edit Further]  [Cancel]                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Implementation:**
- New endpoint: `POST /api/agents/{agent_id}/dry-run`
- Body: `{ "system_prompt": "...", "run_ids": [...] }`
- Replays each run's input through the agent executor with the new prompt
- Tool calls are captured but **not executed** — the LLM's intent (which tool, what args) is the signal
- Returns the LLM's output/tool calls for comparison

**Cost note:** Dry runs cost LLM tokens. Show estimated cost before running ("~$0.35 for 7 runs").

### 3C. Apply Changes

"Apply Changes" updates the agent's `system_prompt` via the existing `PUT /api/agents/{agent_id}` endpoint. The only new thing is we store the change context:

**New DB table: `agent_prompt_changes`**

```sql
agent_prompt_changes (
  id              UUID PRIMARY KEY,
  agent_id        UUID FK -> agents,
  previous_prompt TEXT NOT NULL,
  new_prompt      TEXT NOT NULL,
  reason          TEXT,            -- auto-generated summary of what changed and why
  source_run_ids  JSONB,           -- the wrong run IDs that motivated the change
  dry_run_results JSONB,           -- summary of dry run outcomes
  applied_by      VARCHAR(255),
  applied_at      TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL
)
```

This gives you an audit trail: what changed, why, which runs motivated it, and what the dry run showed. Useful for "why does the prompt say this?" questions later.

---

## Phase 2.5: Run Detail Redesign — Narrative View

**Goal:** The current run detail page is a technical timeline — LLM request/response, tool call, tool result, step by step. Great for debugging, terrible for understanding. Redesign it into a **narrative view** with the technical details tucked away.

### The Problem Today

The current left column is a vertical stack of step cards. Each card shows:
- Step type icon (LLM request/response, tool call, tool result, error)
- Step number, type label, token count, duration
- Raw content of that step

For a 20-step run, you scroll through 20 technical cards. If you're trying to understand "did this agent do the right thing?" you have to mentally reconstruct the story from raw pieces. The step-by-step view is a **debugger UI shown to a user**.

### The New Design

Two views, toggle between them:

#### 1. Narrative View (default)

One flowing story of what the agent did. Think: the agent's internal monologue rendered as readable prose.

```
┌─────────────────────────────────────────────────────────────────────┐
│  ← Triage Agent     Run completed · 4.2s · $0.08                   │
│                                                                     │
│  Narrative  │  Timeline  │  Raw                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  REQUEST                                                            │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Triage ticket #4821: "Quarantine release not working"       │   │
│  │ Client: Acme Corp, Priority: High                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ─────────────────────────────────────────────────────────────     │
│                                                                     │
│  The agent read the ticket and recognized it as a quarantine       │
│  release issue. It checked the client's contract type (Acme Corp   │
│  — MSP Managed) and reviewed recent similar tickets.               │
│                                                                     │
│    ▸ read_ticket  ticket_id=4821                                   │
│    ▸ get_client_contract  client="Acme Corp"                       │
│    ▸ search_similar_tickets  query="quarantine release"            │
│                                                                     │
│  Based on the client's managed contract and the nature of the      │
│  request, it classified the ticket as infrastructure work and      │
│  routed it to Network Operations. It also escalated the priority   │
│  from High to Urgent, noting that quarantine releases block        │
│  email delivery.                                                   │
│                                                                     │
│    ▸ classify_ticket  category="infrastructure", billable=false   │
│    ▸ route_ticket  queue="NetOps"                                  │
│    ▸ set_priority  priority="Urgent"                               │
│    ▸ add_note  "Routing to NetOps for immediate attention..."     │
│                                                                     │
│  ─────────────────────────────────────────────────────────────     │
│                                                                     │
│  RESULT                                                             │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Ticket #4821 classified as Infrastructure (not billable)    │   │
│  │ Routed to NetOps queue, priority escalated to Urgent       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Structure:**
- **Request** — the input, formatted as a compact card (same extraction as the review flipbook)
- **Narrative paragraphs** — short prose describing what the agent was doing
- **Tool call chips** — inline, between paragraphs, showing which tools were called with key args
- **Result** — the final output

**Generation:** The narrative prose is Haiku-generated, cached on the run record. One call per run, ~$0.0001 each. Done post-hoc when the run completes (or on first view for existing runs).

Clicking a tool call chip expands it to show args + result inline (without navigating away).

#### 2. Streaming View (for running agents)

When a run is in progress, we can't have a narrative yet — the story isn't written. So the streaming view shows a simpler live feed:

```
┌─────────────────────────────────────────────────────────────────────┐
│  ● Running · 8s elapsed · 2,340 tokens                             │
│                                                                     │
│  Narrative  │  Timeline  │  Raw                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  REQUEST                                                            │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Triage ticket #4821: "Quarantine release not working"       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ─────────────────────────────────────────────────────────────     │
│                                                                     │
│  Thinking...                                                        │
│  ✓ read_ticket  ticket_id=4821                                     │
│  ✓ get_client_contract  client="Acme Corp"                         │
│  ⟳ search_similar_tickets  query="quarantine release"              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**What's live:**
- Token count ticks up
- Tool calls appear with a spinner, turn into a checkmark when they resolve
- A "Thinking..." indicator when the LLM is generating but no tool call yet
- When the run completes, the view automatically switches to the Narrative view

No individual steps, no raw request/response dumps. Just the essence of what's happening.

#### 3. Timeline View (optional, for power users)

A button toggle that shows the current detailed timeline — step cards with tokens, durations, raw content. Preserved for debugging, just not the default.

#### 4. Raw View

Third tab: the raw JSON of input, output, and all steps. For when you need to see exactly what was sent/received.

### What this changes

- **Narrative View becomes the default** for completed runs
- **Streaming View is the default** for in-progress runs (auto-promotes to Narrative when done)
- **Timeline View** is the current experience, relegated to a tab
- **Raw View** added for deep debugging

The review flipbook (Phase 2) shows an even shorter version of the narrative — just the input summary and result summary — so someone scrolling through verdicts sees the essentials without the tool-call detail.

---

## Phase 4: Fleet-Level Insights (Future)

Not in initial scope, but the data model supports it:

- **Cross-agent dashboard:** Aggregate stats across all agents (total runs, costs, failure rates)
- **Agent comparison:** Side-by-side stats for agents in same org
- **Cost trends:** Track spending over time, alert on spikes
- **Tool usage analytics:** Which tools are most called, which fail most
- **Wrong-run patterns:** Cluster wrong runs across agents to find systemic issues

---

## Execution Plan

### Phase 1 — Agent Detail Page + Fleet Health

| Task | Files | Depends On |
|------|-------|------------|
| 1.1 Agent stats API endpoint | `api/src/routers/agents.py`, new `api/src/services/agent_stats.py` | — |
| 1.2 Add summary fields to AgentSummary | `api/src/models/contracts/agents.py`, `api/src/routers/agents.py` | 1.1 |
| 1.3 Agent detail page shell + routing | `client/src/pages/AgentDetail.tsx`, `client/src/App.tsx` | — |
| 1.4 Overview tab (stats + recent activity) | `client/src/pages/AgentDetail.tsx`, `client/src/hooks/useAgentStats.ts` | 1.1, 1.3 |
| 1.5 Runs tab (embed existing table) | `client/src/pages/AgentDetail.tsx` | 1.3 |
| 1.6 Settings tab (extract from dialog) | `client/src/components/agents/AgentSettings.tsx` | 1.3 |
| 1.7 Update fleet list cards with stats | `client/src/pages/Agents.tsx` | 1.1 |
| 1.8 Remove AgentDialog, update all references | `client/src/components/agents/AgentDialog.tsx` | 1.6 |

### Phase 2 — Quality Review Workflow

| Task | Files | Depends On |
|------|-------|------------|
| 2.0a Add `background_model` to LLM config | `api/src/routers/llm_config.py`, `client/src/pages/settings/LLMConfig.tsx` | — |
| 2.0b Add `llm_background_model` to Agent ORM/contracts/Settings form | `api/src/models/orm/agents.py`, `api/src/models/contracts/agents.py`, `client/src/components/agents/AgentSettings.tsx` | 2.0a |
| 2.0c Background model resolver (agent override → system default → main model) | `api/src/services/llm/factory.py` | 2.0a, 2.0b |
| 2.1 Migration: agent_run_verdicts table, summary columns on agent_runs | `api/alembic/versions/` | — |
| 2.2 Verdict API endpoints | `api/src/routers/agent_runs.py` | 2.1 |
| 2.3 Run summary generation (background model, post-run) | `api/src/services/agent_run_summarizer.py` | 2.0c, 2.1 |
| 2.4 Add summary + verdict to run responses | `api/src/models/contracts/agent_runs.py` | 2.2, 2.3 |
| 2.5 Review summary endpoint | `api/src/routers/agents.py` | 2.1 |
| 2.6 Review tab — flipbook UI | `client/src/components/agents/AgentReview.tsx` | 2.4 |
| 2.7 Keyboard navigation | `client/src/components/agents/AgentReview.tsx` | 2.6 |
| 2.8 Review stats on overview tab | `client/src/pages/AgentDetail.tsx` | 2.5 |

### Phase 2.5 — Run Detail Redesign

| Task | Files | Depends On |
|------|-------|------------|
| 2.5.1 Add narrative columns to agent_runs | `api/alembic/versions/`, `api/src/models/orm/agent_runs.py` | — |
| 2.5.2 Narrative generation service (Haiku) | `api/src/services/agent_run_narrator.py` | 2.5.1 |
| 2.5.3 Trigger narrative on run completion | `api/src/services/agent_executor.py` | 2.5.2 |
| 2.5.4 Backfill job for existing runs (lazy on first view) | `api/src/routers/agent_runs.py` | 2.5.2 |
| 2.5.5 Narrative view component | `client/src/components/agent-runs/NarrativeView.tsx` | 2.5.1 |
| 2.5.6 Streaming view component (live) | `client/src/components/agent-runs/StreamingView.tsx` | — |
| 2.5.7 View toggle + Timeline/Raw tabs | `client/src/pages/AgentRunDetail.tsx` | 2.5.5, 2.5.6 |
| 2.5.8 Inline tool-chip expansion | `client/src/components/agent-runs/ToolChip.tsx` | 2.5.5 |
| 2.5.9 Inline verdict buttons (👍/👎 + note) on run detail | `client/src/pages/AgentRunDetail.tsx` | Phase 2 |
| 2.5.10 "Suggest Fix" drawer on run detail (reuses Phase 3 components) | `client/src/pages/AgentRunDetail.tsx` | Phase 3 |

### Phase 3 — Prompt Improvement Workflow

| Task | Files | Depends On |
|------|-------|------------|
| 3.1 Migration: agent_prompt_changes table | `api/alembic/versions/` | — |
| 3.2 Create `agent_tuning` service module | `api/src/services/agent_tuning.py` | Phase 2 |
| 3.3 Prompt suggestion (uses wrong runs + notes) | `api/src/services/agent_tuning.py` | 3.2 |
| 3.4 Post-hoc dry-run evaluation | `api/src/services/agent_tuning.py` | 3.2 |
| 3.5 HTTP endpoints (thin wrappers over service) | `api/src/routers/agent_runs.py` | 3.3, 3.4 |
| 3.6 MCP tools (thin wrappers over service) | `api/src/services/mcp_server/tools/agent_tuning.py` | 3.3, 3.4 |
| 3.7 Prompt diff + apply UI | `client/src/components/agents/PromptDiff.tsx` | 3.3 |
| 3.8 Dry-run results UI (before/after per run) | `client/src/components/agents/DryRunResults.tsx` | 3.4 |
| 3.9 Prompt change history (audit trail) | `client/src/components/agents/PromptHistory.tsx` | 3.1 |

---

## Decisions (resolved 2026-04-16)

1. **Run summaries use the configured background model.** Every run, on completion, gets a short "asked" summary and "done" summary. Cached on the run record (`summary_asked`, `summary_done` columns). Used by both the review flipbook and the narrative run detail view. Model selection follows this priority:
   - Agent's `llm_background_model` override (new field)
   - System default `background_model` (new field in `system_configs.llm.provider_config`)
   - Falls back to the main `model` from LLM config
   - Extraction-only fallback if the model call fails

   Both new config fields default to null (= use main model). Small teams can leave them alone; teams with high volume can point them at a cheaper model like Haiku.

2. **Dry run uses post-hoc evaluation, not replay.** Given the original input, the original output, and the new system prompt, ask the configured evaluation model: "Would the agent have produced this same output under the new prompt? If not, what would differ and why?" One LLM call per run. No tool execution, no tool replay. Much cheaper, much safer, good enough for a preview. Full replay can be added later as an optional "deep dry run" if needed. Uses the same `background_model` setting as summarization (we don't split this further unless needed).

3. **Verdicts start binary.** Thumbs up / thumbs down. Optional note. No categories, no rubrics. Extend only if patterns emerge that justify more structure.

4. **Agent create uses the full page, simplified.** `/agents/new` opens the detail page in create mode. Only the Settings tab is active; Overview/Runs/Review tabs are disabled (grayed out) until first save. The Settings component is reused between create and edit — same form, same validation, just a different save endpoint.

5. **MCP tools and HTTP routes share a service layer.** All Phase 2-3 logic (verdict submission, prompt suggestion, dry-run evaluation, apply changes) lives in `api/src/services/agent_tuning.py`. The HTTP router in `api/src/routers/agent_runs.py` and the MCP tools in `api/src/services/mcp_server/tools/agent_tuning.py` are both thin wrappers that call into the service. One implementation, two interfaces. Means reviewing runs via Claude chat behaves identically to the UI.

6. **Run detail gets three views.** Narrative (default, model-generated prose + tool chips), Streaming (live feed for in-progress runs, auto-promotes to Narrative when done), Timeline (current experience, kept for debugging), Raw (full JSON). See Phase 2.5.

7. **Tuning is accessible from every run detail, not just the Review tab.** Two entry points:
   - **Inline verdict:** 👍/👎 buttons on any run detail page. Same shortcuts (G/B). Thumbs-down reveals a note field. No navigation.
   - **"Suggest Fix" button:** Runs the full Phase 3 flow scoped to just this one run — suggest a prompt change, show the diff, optionally dry-run against related runs, apply or dismiss. Opens in a drawer/sheet on the same page, reusing the `PromptDiff` and `DryRunResults` components.

   The Review tab flipbook is the "batch through the backlog" workflow; the run detail is the "I found one bad one, fix it now" workflow. Same underlying service, two entry points.
