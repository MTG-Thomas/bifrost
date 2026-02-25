# Autonomous Agents Design

## Overview

Enable agents to run autonomously — triggered by events, schedules, workflows, or the SDK — using a unified execution model where all agent runs (including chat) execute in workers via RabbitMQ.

**Core insight:** Every agent run is the same loop: receive input → LLM reasoning → tool calls → repeat → return output. The only differences are at the edges: what triggers the run, what shape the input takes, and where the output goes.

| | Chat | Autonomous |
|---|---|---|
| **Trigger** | User message via API | Event, schedule, SDK, workflow |
| **Input** | Natural language string | Structured data (JSON) |
| **Output** | Streamed text to Redis/WebSocket | Structured result (via output schema) |
| **Destination** | User's conversation UI | Agent Runs view, calling workflow, event delivery record |
| **Run record** | Yes | Yes |

---

## Unified Execution Model

All agent runs execute in workers. Chat moves from the API process to workers as part of this work.

**Chat flow (new):**
1. API receives chat message, saves it to DB, enqueues an `agent_run` job to RabbitMQ
2. `AgentRunConsumer` picks up the job in a worker
3. Worker runs the agent loop (LLM → tool calls → repeat)
4. Tokens stream to Redis pub/sub as they're generated
5. Client receives via WebSocket (no client-side changes)
6. On completion, worker writes the `AgentRun` record

**Autonomous flow:**
1. Trigger fires (event, schedule, SDK call)
2. `agent_run` job enqueued to RabbitMQ with structured input
3. Same worker path as chat — `AgentRunConsumer` runs the loop
4. Result returned via the appropriate channel (BLPOP for SDK, event delivery record for events)

**Workflow tool calls from within the agent loop:** When the agent calls a workflow tool, that workflow is enqueued to the existing workflow execution queue and runs in the isolated process pool. The agent worker blocks on Redis BLPOP for the result — same `execute_tool()` → `_enqueue_workflow_async(sync=True)` pattern as today. User-authored Python code stays sandboxed in workers. No change.

---

## Agent Configuration

### Agent file (portable artifact)

```yaml
# agents/service-manager.agent.yaml
name: Service Manager
description: Manages service operations and delegates to specialists
system_prompt: |
  You are a service operations manager. When you receive
  a ticket, analyze it, gather context, and decide on the
  appropriate action.
tools:
  - fetch_ticket_history
  - update_ticket
  - send_notification
delegated_agents:
  - halo_reporting
  - notification_agent
knowledge_sources:
  - service_procedures
system_tools:
  - execute_workflow
  - search_knowledge
```

### Instance config (`.bifrost/agents.yaml`)

```yaml
service_manager:
  id: c3d4e5f6-...
  path: agents/service-manager.agent.yaml
  organization_id: 9a3f2b1c-...
  roles: [b7e2a4d1-...]
  access_level: role_based
  max_iterations: 50
  max_token_budget: 100000
  llm_model: claude-sonnet-4-6
```

The agent file is the portable artifact — system prompt, tools, description. `.bifrost/agents.yaml` is the instance binding with runtime config (org, roles, budget, model). Same split as the workspace architecture.

### New DB columns on `agents` table

| Column | Type | Description |
|---|---|---|
| `max_iterations` | Integer, default 50 | Soft limit on tool call iterations per run |
| `max_token_budget` | Integer, default 100000 | Soft limit on total LLM tokens per run |

---

## Budget Enforcement

Each agent carries its own resource budget. The budget is per-run and applies regardless of how the agent is called.

**Soft limit:** When a run reaches ~80% of either limit (iterations or tokens), the system injects a message: "You are approaching your resource limit. Begin wrapping up and return your final output." The agent gets one more iteration to produce a clean result.

**Hard limit:** At 100%, the run is terminated and returns whatever partial result is available. Status is set to `budget_exceeded`.

**Delegation budget propagation:** When an agent delegates to another agent, the child receives a budget that is the minimum of its own configured limits and the parent's remaining budget. The child's usage counts against the parent's budget. Whichever limit hits first wins.

```
Parent: 50 max iterations, 20 used → 30 remaining
Child configured: 25 max iterations
Child receives: min(25, 30) = 25
Child uses 20 → parent's iterations_used increases by 20
```

---

## Input & Output

### Input

Every agent run receives input as structured data (JSON). No separate `task` parameter — the agent's system prompt defines its purpose.

| Trigger | Input |
|---|---|
| Chat | `{"message": "user's text", "conversation_id": "..."}` |
| Webhook | Webhook payload, mapped via `input_mapping` on the event subscription |
| Schedule | Schedule metadata, mapped via `input_mapping` |
| SDK | Whatever the caller provides |
| Delegation | Whatever the parent agent provides as tool input |

### Output

Callers can specify an `output_schema` (JSON Schema). When provided, the final LLM call includes the schema as a response format constraint. The run result is validated against the schema before being returned.

- **Chat:** Streamed text, no schema enforcement
- **SDK call:** Caller provides `output_schema`, gets structured result back
- **Event-triggered:** No schema by default (agent runs and results are logged)
- **Delegation:** Parent agent defines expected output via the delegation tool definition

When no schema is provided, the agent returns free-form text (still captured in the run record).

---

## Triggers

### Event subscriptions

Extend `EventSubscription` to support agent targets:

| Field | Type | Description |
|---|---|---|
| `target_type` | enum: `workflow`, `agent` | NEW — what the subscription triggers |
| `agent_id` | FK → agents, nullable | NEW — target agent when `target_type="agent"` |
| `workflow_id` | FK → workflows, nullable | Existing — now nullable when `target_type="agent"` |
| `input_mapping` | JSONB | Existing — works the same for agents |

The `input_mapping` template system (already built for workflows) applies identically. `{{ payload.ticket.id }}` in a webhook subscription maps payload fields into the agent's input.

`EventProcessor` dispatches to the `agent_runs` queue instead of the workflow execution queue when `target_type == "agent"`.

### SDK

```python
from bifrost import agents

result = await agents.run(
    "service-manager",
    input={"ticket": ticket_data},
    output_schema={
        "action": {"type": "string", "enum": ["escalate", "resolve", "reassign"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"}
    }
)

result["action"]      # "escalate"
result["confidence"]  # 0.87
```

Under the hood, `agents.run()` enqueues an `agent_run` job to RabbitMQ and blocks on Redis BLPOP for the result — same sync pattern as `execute_tool()` for workflow tools.

### Schedules

No new mechanism. Schedules fire through the existing event system: `ScheduleSource` → `Event` → `EventSubscription` (with `target_type="agent"`) → `agent_run` job.

---

## Scope & Identity

### Organization scope

The agent's `organization_id` is the execution scope. This propagates to all tool calls (workflow executions, knowledge searches, config access, integration lookups). Same principle as existing workflow scope resolution — org-scoped agent always runs as its org.

### Caller identity

The original caller identity propagates through the entire chain.

| Trigger | caller |
|---|---|
| Chat | The user who sent the message |
| Webhook | `SYSTEM_USER_ID` / `Event System` |
| Schedule | `SYSTEM_USER_ID` / `Event System` |
| SDK (from workflow) | The caller of the parent workflow (passed through) |

When Service Manager agent (triggered by a user in chat) calls a workflow tool, that workflow sees the original human caller. Workflows can make decisions based on who asked.

---

## Deep Delegation

Flat single-turn delegation is replaced with full agent runs.

When an agent delegates, it's modeled as a tool call:

```
Tool: delegate_to_halo_reporting
Input: {"ticket_id": "123", "lookback_days": 30}
```

The parent agent's worker enqueues a new `agent_run` job for the delegated agent, then BLPOP waits for the structured result — same synchronous pattern as workflow tool calls.

Each delegation creates its own `AgentRun` record with `parent_run_id` pointing to the caller. This gives a tree of runs:

```
AgentRun: Service Manager (trigger: webhook)
  ├── AgentRun: Halo Reporting (trigger: delegation)
  │     └── WorkflowExecution: fetch_ticket_history
  ├── WorkflowExecution: update_ticket
  └── AgentRun: Notification Agent (trigger: delegation)
        └── WorkflowExecution: send_teams_message
```

No explicit depth limit. The budget is the guardrail — total iterations and tokens across the full delegation chain.

---

## Worker Infrastructure

### AgentRunConsumer

A new consumer alongside `WorkflowExecutionConsumer`. Reads from an `agent_runs` queue.

**Message format:**

```json
{
    "run_id": "uuid",
    "agent_id": "uuid",
    "trigger_type": "chat | event | schedule | sdk | delegation",
    "input": {},
    "output_schema": null,
    "org_id": "uuid | null",
    "conversation_id": "uuid | null",
    "caller": { "user_id": "...", "email": "...", "name": "..." },
    "parent_run_id": "uuid | null",
    "budget": {
        "max_iterations": 50,
        "max_token_budget": 100000,
        "iterations_used": 0,
        "tokens_used": 0
    }
}
```

The consumer loads the agent config from DB, builds the tool set (system tools, workflow tools, knowledge, delegation — same logic as today), and runs the loop.

### The loop

Largely unchanged from `AgentExecutor.chat()`:

1. Build message history (for chat: load from DB; for autonomous: system prompt + input)
2. Call LLM with tools
3. If tool calls → execute each, record as run step, append results, loop
4. If budget soft limit hit → inject wrap-up instruction, allow one more iteration
5. If budget hard limit hit → force return with partial result
6. If no tool calls → done, return final response
7. Write `AgentRun` record with all steps

### LLM infrastructure

Same `llm_client`, same multi-provider infrastructure, same `agent.llm_model` override. No new LLM plumbing.

---

## Agent Run Records & Observability

### AgentRun model

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `agent_id` | FK → agents | Which agent ran |
| `parent_run_id` | FK → agent_runs, nullable | For delegated runs |
| `trigger_type` | enum | `chat`, `event`, `schedule`, `sdk`, `delegation` |
| `trigger_source` | String | Human-readable: `"webhook: new_ticket"`, `"user: jack@..."` |
| `conversation_id` | FK → conversations, nullable | For chat-triggered runs |
| `event_delivery_id` | FK → event_deliveries, nullable | For event-triggered runs |
| `input` | JSONB | The structured input or chat message |
| `output` | JSONB, nullable | The structured result |
| `output_schema` | JSONB, nullable | The requested output schema |
| `status` | enum | `queued`, `running`, `completed`, `failed`, `budget_exceeded` |
| `error` | Text, nullable | Error message if failed |
| `org_id` | FK → organizations, nullable | Execution scope |
| `caller_user_id` | String, nullable | Original human who initiated the chain |
| `iterations_used` | Integer | Tool call iterations consumed |
| `tokens_used` | Integer | Total LLM tokens consumed |
| `budget_max_iterations` | Integer | Budget this run started with |
| `budget_max_tokens` | Integer | Token budget this run started with |
| `duration_ms` | Integer | Wall clock time |
| `llm_model` | String | Model used |
| `created_at` | DateTime | When queued |
| `started_at` | DateTime, nullable | When worker picked it up |
| `completed_at` | DateTime, nullable | When finished |

### AgentRunStep model (reasoning trace)

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `run_id` | FK → agent_runs | Parent run |
| `step_number` | Integer | Ordering |
| `type` | enum | `llm_response`, `tool_call`, `tool_result`, `budget_warning`, `error` |
| `content` | JSONB | LLM text, tool name + args, tool result, etc. |
| `tokens_used` | Integer, nullable | Tokens for this LLM call |
| `duration_ms` | Integer, nullable | Time for this step |
| `created_at` | DateTime | |

---

## UI

### Agent Runs table (list view)

Follows the same patterns as the existing executions history table — same filtering, ordering, and column customization.

| Column | Filterable | Sortable |
|---|---|---|
| Agent | Yes (dropdown) | Yes |
| Trigger | Yes (chat / event / schedule / sdk / delegation) | Yes |
| Caller | Yes (user dropdown) | Yes |
| Status | Yes (completed / failed / running / budget_exceeded) | Yes |
| Org | Yes (dropdown) | Yes |
| Duration | No | Yes |
| Iterations / Tokens | No | Yes |
| Started | Yes (date range) | Yes (default) |

### Run detail view (activity map)

A vertical node map showing each step in the agent's reasoning chain. Each step is a node showing:

- **LLM response nodes:** The agent's reasoning text, token count, duration, and a "Show rationale" button (on-demand LLM call explaining the decision, cached after first generation)
- **Tool call nodes:** Tool name, input, result, duration. Links to the workflow execution record.
- **Delegation nodes:** Child agent name, input, result, duration. Expandable inline to show the child agent's full activity map nested within the parent, or click-through to open as a separate run view.
- **Output node:** Final result with total duration, iterations used, and tokens used.

### Chat integration

For chat-triggered runs, the conversation UI works as it does today (streaming messages). A "View run details" link on agent responses takes the user to the Agent Runs detail view for that run.
