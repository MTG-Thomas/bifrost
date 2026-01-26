# Ticket Review System Enhancement

## Overview

Redesign the HaloPSA ticket indexing system to support comprehensive ticket review and individual performance assessment. This includes enhanced metadata for filtering, reference data lookup tools, and consistent naming conventions.

## Goals

1. **Performance reviews** - Query tickets by agent contribution, quality scores, SLA compliance
2. **Targeted filtering** - Find specific tickets without relying solely on semantic search
3. **Reference data access** - Look up agents, clients, teams by name to get IDs for filtering
4. **Consistent tooling** - Action-first naming, clear tool descriptions that guide LLM usage

## Current State

**Existing workflows** (`features/ai_ticketing/workflows/ticket_indexer.py`):
- `index_halopsa_ticket` - Index single ticket
- `index_halopsa_tickets_batch` - Batch index with date range
- `index_halopsa_historical` - Initial historical load
- `search_halopsa_tickets` - Semantic search with filters
- `find_quality_issues` - Find tickets by quality flag

**Existing metadata** (`TicketMetadata`):
- Basic ticket info: `ticket_id`, `client_id`, `client_name`, `user_id`, `user_name`
- Classification: `status_name`, `tickettype_name`, `priority_id`, `category_1-4`
- Assignment: `team_name`, `agent_name` (last assigned only), `site_name`
- Time: `timetaken`, `dateoccurred`, `dateclosed`
- Quality: `quality_score` (0-100), `quality_flags` (list)

**Problems**:
1. `agent_name` only captures last assigned agent, not all contributors
2. No SLA status tracking
3. No complexity indicator
4. No reference data tools for looking up agent/client IDs
5. Inconsistent naming (`index_halopsa_ticket` vs action-first style)

## Solution

### Part 1: Enhanced Metadata

Add three new fields to `TicketMetadata`:

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `agents_involved` | `list[str]` | Actions where `who_agentid > 0` | Find all tickets an agent touched |
| `sla_met` | `bool \| None` | `slastate == "I"` → true, `"O"` → false | SLA compliance filtering |
| `complexity_score` | `int` (1-5) | LLM judgment during summarization | Filter by ticket difficulty |

**`agents_involved` extraction logic:**
```python
agents_involved = list(set(
    action["who"]
    for action in actions
    if action.get("who_agentid", 0) > 0
))
```

**`sla_met` extraction logic:**
```python
sla_state = ticket.get("slastate")
sla_met = True if sla_state == "I" else (False if sla_state == "O" else None)
```

**`complexity_score` LLM prompt addition:**
```
Assess ticket complexity (1-5):
1 = Trivial (password reset, simple how-to)
2 = Simple (known fix, one system)
3 = Moderate (investigation needed, multiple steps)
4 = Complex (research required, multiple systems, vendor involvement)
5 = Critical (major incident, extensive troubleshooting)
```

### Part 2: Reference Data Tools

Create tools that let an LLM look up identifiers before calling filter/search tools.

**Tool descriptions should guide usage**, e.g.:
> "To filter tickets by agent, first call `list_agents` to find the agent's name, then use that name with `search_tickets`."

| Tool | Purpose | Returns |
|------|---------|---------|
| `list_agents` | Get all HaloPSA agents | `[{id, name, email, team, is_active}, ...]` |
| `get_agent` | Get agent by ID or name | `{id, name, email, team, is_active}` |
| `list_clients` | Get all clients | `[{id, name, site_count}, ...]` |
| `get_client` | Get client by ID or name | `{id, name, sites: [...]}` |
| `list_teams` | Get all teams | `[{id, name, agent_count}, ...]` |
| `list_statuses` | Get ticket statuses | `[{id, name, is_closed}, ...]` |

These wrap the existing HaloPSA SDK functions (`halopsa.list_agents()`, etc.) with:
- Simplified response schemas (only fields useful for lookups)
- Name-based search/filtering where the SDK only supports ID
- Caching where appropriate (agents/teams don't change often)

### Part 3: Ticket Review Tools

Rename and consolidate ticket tools with action-first naming:

| Old Name | New Name | Purpose |
|----------|----------|---------|
| `index_halopsa_ticket` | `index_ticket` | Index single ticket by ID |
| `index_halopsa_tickets_batch` | `index_tickets` | Batch index with date range |
| `index_halopsa_historical` | `index_tickets_historical` | Initial historical load |
| `search_halopsa_tickets` | `search_tickets` | Semantic search with metadata filters |
| `find_quality_issues` | `find_tickets_by_flag` | Find tickets by quality flag |
| (new) | `review_tickets` | High-level ticket review with aggregations |

**`search_tickets` enhanced parameters:**
```python
@workflow.register(
    name="search_tickets",
    description="""Search indexed tickets with optional filters.

    To filter by agent: First call list_agents to get the agent name,
    then pass it to agents_involved filter.

    To filter by client: First call list_clients to get the client ID,
    then pass it to client_id filter.
    """,
)
async def search_tickets(
    query: str = "",  # Semantic search query (optional)
    # Metadata filters (exact match at DB level)
    client_id: int | None = None,
    agent_name: str | None = None,  # Checks agents_involved array
    team_name: str | None = None,
    sla_met: bool | None = None,
    quality_flag: str | None = None,
    # Post-query filters (applied in Python)
    min_quality_score: int | None = None,
    max_quality_score: int | None = None,
    min_complexity: int | None = None,
    max_complexity: int | None = None,
    start_date: str | None = None,  # YYYY-MM-DD
    end_date: str | None = None,
    # Pagination
    limit: int = 20,
) -> TicketSearchResult:
    ...
```

**`review_tickets` - new high-level tool:**
```python
@workflow.register(
    name="review_tickets",
    description="""Review tickets with filtering and optional metrics.

    Returns ticket list plus optional aggregations (avg quality score,
    SLA compliance rate, tickets by agent, etc.).

    To review a specific agent's performance: First call list_agents
    to get their name, then pass it to agent_name filter.
    """,
)
async def review_tickets(
    # Filters (same as search_tickets)
    agent_name: str | None = None,
    client_id: int | None = None,
    team_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    min_quality_score: int | None = None,
    max_quality_score: int | None = None,
    sla_met: bool | None = None,
    # Options
    include_metrics: bool = True,
    limit: int = 50,
) -> TicketReviewResult:
    ...
```

**`TicketReviewResult` schema:**
```python
class TicketReviewResult(BaseModel):
    tickets: list[TicketSummary]
    total_count: int
    metrics: TicketMetrics | None = None

class TicketMetrics(BaseModel):
    avg_quality_score: float
    quality_distribution: dict[str, int]  # {"excellent": 5, "good": 10, ...}
    sla_compliance_rate: float  # 0.0 - 1.0
    avg_complexity: float
    tickets_by_agent: dict[str, int]
    common_quality_flags: list[tuple[str, int]]  # [("missing_resolution", 15), ...]
```

### Part 4: SDK Filtering Limitations

**Important constraint:** The Bifrost knowledge store only supports exact equality matching via PostgreSQL JSONB containment. No `>`, `<`, `>=`, `<=` operators at the database level.

**Implications:**
- `client_id`, `agent_name`, `sla_met`, `quality_flag` → DB-level filtering ✅
- `min_quality_score`, `max_quality_score`, date ranges → Post-query Python filtering
- When using post-query filters with `limit`, we must fetch more results than requested, then filter and truncate

**Workaround pattern:**
```python
# Fetch extra results to account for post-filtering
fetch_limit = limit * 3 if has_post_filters else limit

results = await knowledge.search(
    query=query,
    namespace="halopsa-tickets",
    limit=fetch_limit,
    metadata_filter=db_filters,  # Only exact-match filters
)

# Apply post-query filters
if min_quality_score:
    results = [r for r in results if r.metadata.get("quality_score", 0) >= min_quality_score]
# ... other post-filters

return results[:limit]
```

## File Changes

### `features/ai_ticketing/models.py`
- Add `agents_involved`, `sla_met`, `complexity_score` to `TicketMetadata`
- Add `complexity_score` to `TicketSummaryResult`
- Add `TicketReviewResult`, `TicketMetrics` models

### `modules/extensions/halopsa.py`
- Update `extract_metadata()` to parse `slastate` → `sla_met`
- Add `extract_agents_involved(actions)` helper
- Update `get_enriched_ticket()` to populate `agents_involved`

### `features/ai_ticketing/services/indexer.py`
- Update AI prompt to include complexity scoring
- Parse `complexity_score` from AI response
- Update `generate_ticket_summary()` return type

### `features/ai_ticketing/workflows/ticket_indexer.py`
- Rename all workflows to action-first naming
- Add reference data tools: `list_agents`, `get_agent`, `list_clients`, etc.
- Enhance `search_tickets` with new filters and post-query filtering
- Add `review_tickets` workflow with metrics aggregation

### Migration
- Re-index all historical tickets to populate new metadata fields
- No schema migration needed (metadata is schemaless JSONB)

## Example Usage

**"Show me Jason's low-quality tickets from last month":**
```
1. LLM calls list_agents() → finds "Jason Zimanski"
2. LLM calls search_tickets(
     agent_name="Jason Zimanski",
     max_quality_score=50,
     start_date="2025-12-23",
     end_date="2026-01-23"
   )
3. Returns filtered ticket list
```

**"Review SLA compliance for Busenbark Clark":**
```
1. LLM calls list_clients() → finds client_id=167
2. LLM calls review_tickets(
     client_id=167,
     include_metrics=True
   )
3. Returns tickets + metrics including sla_compliance_rate
```

**"Find complex tickets with poor documentation":**
```
1. LLM calls search_tickets(
     min_complexity=4,
     max_quality_score=50
   )
2. Returns high-complexity, low-quality tickets
```

## Success Criteria

- [ ] Can query tickets by any agent who contributed (not just assignee)
- [ ] Can filter by SLA met/breached status
- [ ] Can filter by complexity level
- [ ] Reference data tools enable LLM to resolve names → IDs
- [ ] Tool descriptions guide LLM on which tools to call first
- [ ] Consistent action-first naming across all tools
- [ ] `review_tickets` returns useful aggregated metrics
- [ ] Historical tickets re-indexed with new metadata
