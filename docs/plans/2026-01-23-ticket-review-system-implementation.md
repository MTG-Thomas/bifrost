# Ticket Review System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enhance HaloPSA ticket indexing with better metadata, reference data tools, and consistent naming for performance reviews.

**Architecture:** Extend existing `ai_ticketing` feature with new metadata fields (`agents_involved`, `sla_met`, `complexity_score`), add reference data workflows wrapping HaloPSA SDK, rename existing workflows to action-first convention, and add `review_tickets` for aggregated metrics.

**Tech Stack:** Python 3.11, Pydantic, Bifrost SDK (workflow, knowledge, ai modules), HaloPSA API

---

## Task 1: Update TicketMetadata Model

**Files:**
- Modify: `/Users/jack/GitHub/gocovi-bifrost-workspace/features/ai_ticketing/models.py`

**Step 1: Add new fields to TicketMetadata**

Add `agents_involved`, `sla_met`, and `complexity_score` fields:

```python
class TicketMetadata(BaseModel):
    """
    Metadata schema for indexed tickets.

    Used for filtering in knowledge store searches. All fields are optional
    to handle cases where HaloPSA data may be incomplete.
    """

    # Core identifiers
    ticket_id: int
    client_id: int | None = None
    client_name: str | None = None
    user_id: int | None = None
    user_name: str | None = None

    # Status & classification
    status_id: int | None = None
    status_name: str | None = None
    tickettype_id: int | None = None
    tickettype_name: str | None = None
    priority_id: int | None = None
    category_1: str | None = None
    category_2: str | None = None
    category_3: str | None = None
    category_4: str | None = None

    # Assignment
    team_id: int | None = None
    team_name: str | None = None
    agent_id: int | None = None
    agent_name: str | None = None  # Last assigned agent (kept for backwards compat)
    agents_involved: list[str] | None = None  # NEW: All agents who touched the ticket
    sla_id: int | None = None
    sla_met: bool | None = None  # NEW: True if SLA met, False if breached, None if N/A
    site_id: int | None = None
    site_name: str | None = None

    # Time tracking
    timetaken: float | None = None
    dateoccurred: str | None = None  # ISO format
    dateclosed: str | None = None    # ISO format

    # Quality scoring (AI-assessed at index time)
    quality_score: int | None = None          # 0-100 overall quality
    quality_flags: list[str] | None = None    # Issues found
    complexity_score: int | None = None       # NEW: 1-5 complexity rating
```

**Step 2: Add complexity_score to TicketSummaryResult**

```python
class TicketSummaryResult(BaseModel):
    """
    Result from AI ticket summarization.

    Contains both the structured summary (for embedding) and
    quality assessment (for metadata filtering).
    """

    summary: str           # Markdown-formatted summary for embedding
    quality_score: int     # 0-100 overall quality score
    quality_flags: list[str]  # Issues found during assessment
    complexity_score: int  # NEW: 1-5 complexity rating
```

**Step 3: Add TicketSearchResult model**

```python
class TicketSearchResult(BaseModel):
    """Result from searching indexed tickets."""

    query: str
    count: int
    results: list[dict]


class TicketMetrics(BaseModel):
    """Aggregated metrics for ticket review."""

    avg_quality_score: float
    quality_distribution: dict[str, int]  # {"excellent": 5, "good": 10, ...}
    sla_compliance_rate: float  # 0.0 - 1.0
    avg_complexity: float
    tickets_by_agent: dict[str, int]
    common_quality_flags: list[tuple[str, int]]  # [("missing_resolution", 15), ...]


class TicketReviewResult(BaseModel):
    """Result from ticket review with optional metrics."""

    tickets: list[dict]
    total_count: int
    metrics: TicketMetrics | None = None
```

**Step 4: Verify changes**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -c "from features.ai_ticketing.models import TicketMetadata, TicketSummaryResult, TicketMetrics, TicketReviewResult; print('Models OK')"`

Expected: `Models OK`

**Step 5: Commit**

```bash
git add features/ai_ticketing/models.py
git commit -m "feat(ai_ticketing): add agents_involved, sla_met, complexity_score to metadata"
```

---

## Task 2: Update HaloPSA Extension for New Metadata

**Files:**
- Modify: `/Users/jack/GitHub/gocovi-bifrost-workspace/modules/extensions/halopsa.py`

**Step 1: Add extract_agents_involved helper**

```python
def extract_agents_involved(actions: list[dict]) -> list[str]:
    """
    Extract unique agent names from ticket actions.

    Args:
        actions: List of action dicts from HaloPSA API

    Returns:
        List of unique agent names who touched the ticket
    """
    agents = set()
    for action in actions:
        who_agentid = action.get("who_agentid", 0)
        who = action.get("who", "")
        if who_agentid and who_agentid > 0 and who:
            agents.add(who)
    return list(agents)
```

**Step 2: Update extract_metadata to include sla_met**

```python
def extract_metadata(ticket: dict, actions: list[dict] | None = None) -> TicketMetadata:
    """
    Extract and normalize metadata from a ticket for filtering.

    Args:
        ticket: Raw ticket dict from HaloPSA API
        actions: Optional list of actions to extract agents_involved

    Returns:
        TicketMetadata with normalized fields
    """
    # Parse SLA state
    sla_state = ticket.get("slastate")
    if sla_state == "I":
        sla_met = True
    elif sla_state == "O":
        sla_met = False
    else:
        sla_met = None

    # Extract agents from actions if provided
    agents_involved = extract_agents_involved(actions) if actions else None

    return TicketMetadata(
        ticket_id=ticket.get("id"),
        client_id=ticket.get("client_id"),
        client_name=ticket.get("client_name"),
        user_id=ticket.get("user_id"),
        user_name=ticket.get("user_name"),
        status_id=ticket.get("status_id"),
        status_name=ticket.get("status_name"),
        tickettype_id=ticket.get("tickettype_id"),
        tickettype_name=ticket.get("tickettype_name"),
        priority_id=ticket.get("priority_id"),
        category_1=ticket.get("category_1"),
        category_2=ticket.get("category_2"),
        category_3=ticket.get("category_3"),
        category_4=ticket.get("category_4"),
        team_id=ticket.get("team_id"),
        team_name=ticket.get("team"),
        agent_id=ticket.get("agent_id"),
        agent_name=ticket.get("agent_name"),
        agents_involved=agents_involved,
        sla_id=ticket.get("sla_id"),
        sla_met=sla_met,
        site_id=ticket.get("site_id"),
        site_name=ticket.get("site_name"),
        timetaken=ticket.get("timetaken"),
        dateoccurred=ticket.get("dateoccurred"),
        dateclosed=ticket.get("dateclosed"),
    )
```

**Step 3: Update get_enriched_ticket to pass actions to extract_metadata**

```python
async def get_enriched_ticket(ticket_id: int) -> EnrichedTicket:
    """
    Fetch a ticket with full details and all associated notes/actions.

    Args:
        ticket_id: The HaloPSA ticket ID to fetch

    Returns:
        EnrichedTicket with full ticket data, actions, and extracted metadata
    """
    from modules import halopsa

    # Get full ticket details
    logger.debug(f"Fetching ticket {ticket_id}")
    ticket = await halopsa.get_tickets(str(ticket_id))
    ticket_dict = ticket if isinstance(ticket, dict) else dict(ticket)

    # Get all actions (notes) for this ticket
    logger.debug(f"Fetching actions for ticket {ticket_id}")
    try:
        actions_result = await halopsa.list_actions(ticket_id=ticket_id)

        if hasattr(actions_result, "actions"):
            actions = actions_result.actions or []
        elif isinstance(actions_result, dict):
            actions = actions_result.get("actions", [])
        else:
            actions = []
    except Exception as e:
        logger.warning(f"Failed to fetch actions for ticket {ticket_id}: {e}")
        actions = []

    actions_list = [a if isinstance(a, dict) else dict(a) for a in actions]

    # Extract metadata (now includes agents_involved from actions)
    metadata = extract_metadata(ticket_dict, actions_list)

    return EnrichedTicket(
        ticket=ticket_dict,
        actions=actions_list,
        metadata=metadata,
    )
```

**Step 4: Verify changes**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -c "from modules.extensions.halopsa import extract_agents_involved, extract_metadata; print('Extension OK')"`

Expected: `Extension OK`

**Step 5: Commit**

```bash
git add modules/extensions/halopsa.py
git commit -m "feat(halopsa): extract agents_involved and sla_met from ticket data"
```

---

## Task 3: Update AI Prompt for Complexity Scoring

**Files:**
- Modify: `/Users/jack/GitHub/gocovi-bifrost-workspace/features/ai_ticketing/services/indexer.py`

**Step 1: Update AITicketAnalysis model**

```python
class AITicketAnalysis(BaseModel):
    """
    Structured output format for AI ticket analysis.

    The AI returns this structure containing both the summary
    (for embedding) and quality assessment (for filtering).
    """

    summary: str = Field(
        description="Markdown summary with sections: Summary, Key Symptoms, Actions Taken, Resolution, Key Words"
    )
    quality_score: int = Field(
        ge=0, le=100,
        description="Overall documentation quality score 0-100"
    )
    quality_flags: list[str] = Field(
        default_factory=list,
        description="List of quality issue flags found"
    )
    complexity_score: int = Field(
        ge=1, le=5,
        description="Ticket complexity rating 1-5"
    )
```

**Step 2: Update build_summary_prompt to include complexity instructions**

Add this section before the closing `"""` in the prompt:

```python
    prompt = f"""Analyze this IT support ticket and provide:
1. A structured summary optimized for semantic search
2. A quality assessment of the ticket documentation
3. A complexity assessment of the technical issue

## Ticket Information

- **ID**: {ticket.get('id')}
- **Summary**: {ticket.get('summary', 'No summary')}
- **Client**: {ticket.get('client_name', 'Unknown')}
- **User**: {ticket.get('user_name', 'Unknown')}
- **Status**: {ticket.get('status_name', 'Unknown')}
- **Type**: {ticket.get('tickettype_name', 'Unknown')}
- **Priority**: {ticket.get('priority_name', 'Unknown')}
- **Categories**: {category_path}
- **Time Logged**: {ticket.get('timetaken', 0)} hours
- **Date Opened**: {ticket.get('dateoccurred', 'Unknown')}
- **Date Closed**: {ticket.get('dateclosed', 'Still Open')}

## Details

{clean_html(ticket.get('details', 'No details provided'))[:2500]}

## Resolution/Clearance

{resolution[:1000] if resolution else 'No resolution documented'}

## Actions/Notes

{actions_text if actions_text else 'No actions recorded'}

---

Please analyze this ticket and return your response in the required JSON format.

For the **summary**, use this markdown structure:
- **Summary**: A 2-4 sentence story of the ticket (who, what, when, why, how)
- **Key Symptoms**: Bulleted list of symptoms or error messages
- **Actions Taken**: Bulleted list of diagnostic/resolution steps taken
- **Resolution**: How the issue was resolved (or current status)
- **Key Words**: Bulleted list of relevant keywords (technologies, systems, issue types)

For **quality_score** (0-100):
- 90-100: Excellent documentation with clear problem, steps, and resolution
- 70-89: Good documentation with most key information
- 50-69: Adequate but missing some important details
- 30-49: Poor documentation, hard to understand what happened
- 0-29: Minimal or no useful documentation

For **quality_flags**, include any that apply:
- "missing_resolution": Closed without clear resolution documented
- "no_time_logged": No time recorded (timetaken = 0)
- "minimal_notes": Very few or no actions/notes documented
- "vague_summary": Summary doesn't clearly describe the issue
- "escalation_needed": Issue may need follow-up or escalation
- "recurring_issue": Appears to be a frequently occurring problem
- "excellent_documentation": Exceptionally well-documented ticket (positive flag)

For **complexity_score** (1-5):
- 1 = Trivial: Password reset, simple how-to, single quick action
- 2 = Simple: Known fix, minimal troubleshooting, one system involved
- 3 = Moderate: Some investigation needed, multiple steps, coordination required
- 4 = Complex: Research required, multiple systems, escalation or vendor involvement
- 5 = Critical: Major incident, outage, extensive troubleshooting, multiple teams

Consider: technical depth, number of systems involved, troubleshooting steps required, whether it required research or was a known fix.
"""
    return prompt
```

**Step 3: Update generate_ticket_summary return**

```python
async def generate_ticket_summary(enriched: EnrichedTicket) -> TicketSummaryResult:
    """
    Generate an AI summary and quality assessment for a ticket.

    Args:
        enriched: EnrichedTicket with full ticket data and notes

    Returns:
        TicketSummaryResult with summary text, quality metrics, and complexity
    """
    prompt = build_summary_prompt(enriched)

    try:
        result = await ai.complete(
            prompt,
            system=SYSTEM_PROMPT,
            response_format=AITicketAnalysis,
            temperature=0.3,
            max_tokens=1000,
        )

        return TicketSummaryResult(
            summary=result.summary,
            quality_score=result.quality_score,
            quality_flags=result.quality_flags,
            complexity_score=result.complexity_score,
        )

    except Exception as e:
        logger.warning(f"AI analysis failed, using fallback: {e}")
        return generate_fallback_summary(enriched)
```

**Step 4: Update generate_fallback_summary**

```python
def generate_fallback_summary(enriched: EnrichedTicket) -> TicketSummaryResult:
    """
    Generate a basic summary without AI when AI fails.

    Creates a simple structured summary from raw ticket data.
    """
    ticket = enriched.ticket

    summary_parts = [
        "# Summary",
        f"{ticket.get('summary', 'No summary')}",
        "",
        "# Key Symptoms",
        f"- {clean_html(ticket.get('details', 'No details'))[:500]}",
        "",
        "# Actions Taken",
        "- Unable to generate AI summary",
        "",
        "# Resolution",
        clean_html(ticket.get('clearance_note', 'No resolution documented'))[:300] or "Not documented",
        "",
        "# Key Words",
        f"- {ticket.get('tickettype_name', 'Unknown')}",
        f"- {ticket.get('category_1', 'Uncategorized')}",
    ]

    return TicketSummaryResult(
        summary="\n".join(summary_parts),
        quality_score=0,  # No quality score for fallback
        quality_flags=["ai_fallback"],
        complexity_score=3,  # Default to moderate for fallback
    )
```

**Step 5: Update index_ticket to include complexity_score in metadata and add logging**

In the `index_ticket` function, update the metadata copy and add detailed logging:

```python
async def index_ticket(ticket_id: int) -> IndexResult:
    """
    Index a single HaloPSA ticket into the knowledge store.
    ...
    """
    logger.info(f"=== Indexing ticket {ticket_id} ===")

    # Fetch enriched ticket
    try:
        logger.info(f"Fetching ticket {ticket_id} from HaloPSA...")
        enriched = await get_enriched_ticket(ticket_id)
        logger.info(f"  Summary: {enriched.ticket.get('summary', 'No summary')[:60]}...")
        logger.info(f"  Client: {enriched.ticket.get('client_name', 'Unknown')}")
        logger.info(f"  Actions: {len(enriched.actions)} found")
        logger.info(f"  Agents involved: {enriched.metadata.agents_involved}")
        logger.info(f"  SLA met: {enriched.metadata.sla_met}")
    except Exception as e:
        logger.error(f"Failed to fetch ticket {ticket_id}: {e}")
        return IndexResult(
            success=False,
            ticket_id=ticket_id,
            error=f"Failed to fetch ticket: {e}",
        )

    # Generate AI summary and quality assessment
    try:
        logger.info(f"Generating AI summary for ticket {ticket_id}...")
        summary_result = await generate_ticket_summary(enriched)
        logger.info(f"  Quality score: {summary_result.quality_score}")
        logger.info(f"  Complexity score: {summary_result.complexity_score}")
        logger.info(f"  Quality flags: {summary_result.quality_flags}")
    except Exception as e:
        logger.error(f"Failed to generate summary for ticket {ticket_id}: {e}")
        return IndexResult(
            success=False,
            ticket_id=ticket_id,
            error=f"Failed to generate summary: {e}",
        )

    # Update metadata with quality scores and complexity
    metadata = enriched.metadata.model_copy()
    metadata.quality_score = summary_result.quality_score
    metadata.quality_flags = summary_result.quality_flags
    metadata.complexity_score = summary_result.complexity_score

    # Store in knowledge base
    try:
        logger.info(f"Storing ticket {ticket_id} in knowledge base...")
        doc_id = await knowledge.store(
            content=summary_result.summary,
            namespace=NAMESPACE,
            key=f"halopsa-ticket-{ticket_id}",
            metadata=metadata.model_dump(),
        )
        logger.info(f"  Stored with document_id: {doc_id}")
    except Exception as e:
        logger.error(f"Failed to store ticket {ticket_id}: {e}")
        return IndexResult(
            success=False,
            ticket_id=ticket_id,
            error=f"Failed to store in knowledge base: {e}",
        )

    logger.info(f"=== Successfully indexed ticket {ticket_id} ===")

    return IndexResult(
        success=True,
        ticket_id=ticket_id,
        document_id=doc_id,
        summary_preview=summary_result.summary[:200] + "..." if len(summary_result.summary) > 200 else summary_result.summary,
        quality_score=summary_result.quality_score,
        quality_flags=summary_result.quality_flags,
    )
```

**Step 6: Update batch indexing to include complexity_score**

In the `index_tickets_batch` function, update the metadata copy:

```python
            # Update metadata with quality scores and complexity
            metadata = enriched.metadata.model_copy()
            metadata.quality_score = summary_result.quality_score
            metadata.quality_flags = summary_result.quality_flags
            metadata.complexity_score = summary_result.complexity_score
```

**Step 7: Verify changes**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -c "from features.ai_ticketing.services.indexer import AITicketAnalysis; print('Indexer OK')"`

Expected: `Indexer OK`

**Step 8: Commit**

```bash
git add features/ai_ticketing/services/indexer.py
git commit -m "feat(ai_ticketing): add complexity_score to AI analysis prompt"
```

---

## Task 4: Create Reference Data Workflows

**Files:**
- Create: `/Users/jack/GitHub/gocovi-bifrost-workspace/features/ai_ticketing/workflows/reference_data.py`

**Step 1: Create the reference data workflows file**

```python
"""
HaloPSA Reference Data Workflows

Lookup tools for agents, clients, teams, and statuses.
These help LLMs resolve names to IDs for filtering ticket searches.
"""

from __future__ import annotations

import logging

from bifrost import workflow

logger = logging.getLogger(__name__)


# =============================================================================
# Agent Lookups
# =============================================================================


@workflow(
    category="HaloPSA",
    tags=["halopsa", "reference", "agents"],
    is_tool=True,
    tool_description="""List all HaloPSA agents (technicians).

Use this to find an agent's name before filtering tickets.
Example: To find Jason's tickets, first call list_agents to get his exact name,
then use that name with search_tickets or review_tickets.""",
)
async def list_agents(
    include_inactive: bool = False,
) -> dict:
    """
    List all HaloPSA agents with their basic info.

    Args:
        include_inactive: Include inactive agents (default False)

    Returns:
        List of agents with id, name, email, team
    """
    from modules import halopsa

    result = await halopsa.list_agents()

    # Extract agents from response
    if hasattr(result, "agents"):
        agents = result.agents or []
    elif isinstance(result, list):
        agents = result
    else:
        agents = []

    # Normalize to list of dicts
    agents_list = [a if isinstance(a, dict) else dict(a) for a in agents]

    # Filter inactive if requested
    if not include_inactive:
        agents_list = [a for a in agents_list if a.get("is_active", True)]

    return {
        "count": len(agents_list),
        "agents": [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "email": a.get("email"),
                "team": a.get("team"),
                "is_active": a.get("is_active", True),
            }
            for a in agents_list
        ],
    }


@workflow(
    category="HaloPSA",
    tags=["halopsa", "reference", "agents"],
    is_tool=True,
    tool_description="""Get details for a specific agent by ID or name.

Use list_agents first if you need to find an agent by partial name.""",
)
async def get_agent(
    agent_id: int | None = None,
    name: str | None = None,
) -> dict:
    """
    Get agent details by ID or exact name.

    Args:
        agent_id: Agent ID (preferred)
        name: Agent name (exact match)

    Returns:
        Agent details or error if not found
    """
    from modules import halopsa
    from bifrost import UserError

    if agent_id:
        result = await halopsa.get_agent(str(agent_id))
        agent = result if isinstance(result, dict) else dict(result)
        return {
            "id": agent.get("id"),
            "name": agent.get("name"),
            "email": agent.get("email"),
            "team": agent.get("team"),
            "is_active": agent.get("is_active", True),
        }

    if name:
        # Search by name
        all_agents = await list_agents(include_inactive=True)
        for agent in all_agents["agents"]:
            if agent["name"] and agent["name"].lower() == name.lower():
                return agent
        raise UserError(f"Agent not found: {name}")

    raise UserError("Must provide agent_id or name")


# =============================================================================
# Client Lookups
# =============================================================================


@workflow(
    category="HaloPSA",
    tags=["halopsa", "reference", "clients"],
    is_tool=True,
    tool_description="""List all HaloPSA clients.

Use this to find a client's ID before filtering tickets.
Example: To find tickets for "Acme Corp", first call list_clients to get the client_id,
then use that ID with search_tickets or review_tickets.""",
)
async def list_clients() -> dict:
    """
    List all HaloPSA clients with their basic info.

    Returns:
        List of clients with id, name
    """
    from modules import halopsa

    result = await halopsa.list_clients()

    # Extract clients from response
    if hasattr(result, "clients"):
        clients = result.clients or []
    elif isinstance(result, list):
        clients = result
    else:
        clients = []

    # Normalize to list of dicts
    clients_list = [c if isinstance(c, dict) else dict(c) for c in clients]

    return {
        "count": len(clients_list),
        "clients": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "is_active": c.get("inactive", False) is False,
            }
            for c in clients_list
        ],
    }


@workflow(
    category="HaloPSA",
    tags=["halopsa", "reference", "clients"],
    is_tool=True,
    tool_description="""Get details for a specific client by ID or name.

Use list_clients first if you need to find a client by partial name.""",
)
async def get_client(
    client_id: int | None = None,
    name: str | None = None,
) -> dict:
    """
    Get client details by ID or exact name.

    Args:
        client_id: Client ID (preferred)
        name: Client name (exact match)

    Returns:
        Client details or error if not found
    """
    from modules import halopsa
    from bifrost import UserError

    if client_id:
        result = await halopsa.get_client(str(client_id))
        client = result if isinstance(result, dict) else dict(result)
        return {
            "id": client.get("id"),
            "name": client.get("name"),
            "is_active": client.get("inactive", False) is False,
        }

    if name:
        # Search by name
        all_clients = await list_clients()
        for client in all_clients["clients"]:
            if client["name"] and client["name"].lower() == name.lower():
                return client
        raise UserError(f"Client not found: {name}")

    raise UserError("Must provide client_id or name")


# =============================================================================
# Team Lookups
# =============================================================================


@workflow(
    category="HaloPSA",
    tags=["halopsa", "reference", "teams"],
    is_tool=True,
    tool_description="""List all HaloPSA teams.

Use this to find team names for filtering tickets by team.""",
)
async def list_teams() -> dict:
    """
    List all HaloPSA teams.

    Returns:
        List of teams with id, name
    """
    from modules import halopsa

    result = await halopsa.list_teams()

    # Extract teams from response
    if hasattr(result, "teams"):
        teams = result.teams or []
    elif isinstance(result, list):
        teams = result
    else:
        teams = []

    # Normalize to list of dicts
    teams_list = [t if isinstance(t, dict) else dict(t) for t in teams]

    return {
        "count": len(teams_list),
        "teams": [
            {
                "id": t.get("id"),
                "name": t.get("name"),
            }
            for t in teams_list
        ],
    }


# =============================================================================
# Status Lookups
# =============================================================================


@workflow(
    category="HaloPSA",
    tags=["halopsa", "reference", "statuses"],
    is_tool=True,
    tool_description="""List all HaloPSA ticket statuses.

Use this to understand available statuses for filtering.""",
)
async def list_statuses() -> dict:
    """
    List all HaloPSA ticket statuses.

    Returns:
        List of statuses with id, name, is_closed
    """
    from modules import halopsa

    result = await halopsa.list_statuses()

    # Extract statuses from response
    if hasattr(result, "statuses"):
        statuses = result.statuses or []
    elif isinstance(result, list):
        statuses = result
    else:
        statuses = []

    # Normalize to list of dicts
    statuses_list = [s if isinstance(s, dict) else dict(s) for s in statuses]

    return {
        "count": len(statuses_list),
        "statuses": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "is_closed": s.get("is_closed", False),
            }
            for s in statuses_list
        ],
    }
```

**Step 2: Verify the file is valid Python**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -c "from features.ai_ticketing.workflows.reference_data import list_agents, list_clients, list_teams, list_statuses; print('Reference data workflows OK')"`

Expected: `Reference data workflows OK`

**Step 3: Commit**

```bash
git add features/ai_ticketing/workflows/reference_data.py
git commit -m "feat(ai_ticketing): add reference data workflows for agents, clients, teams, statuses"
```

---

## Task 5: Rename and Enhance Ticket Workflows

**Files:**
- Modify: `/Users/jack/GitHub/gocovi-bifrost-workspace/features/ai_ticketing/workflows/ticket_indexer.py`

**Important: All workflows must include logging** so progress can be monitored in real-time. Use `logger.info()` for key milestones and `logger.debug()` for detailed progress.

**Step 1: Rename index_halopsa_ticket to index_ticket**

Change the function name and decorator:

```python
@workflow(
    category="HaloPSA",
    tags=["halopsa", "indexing", "knowledge", "tickets", "ai"],
    is_tool=True,
    tool_description="Index a single HaloPSA ticket into the knowledge base for semantic search and quality assessment",
)
async def index_ticket(ticket_id: int) -> dict:
    """
    Index a single HaloPSA ticket into the knowledge store.
    ...
    """
```

**Step 2: Rename index_halopsa_tickets_batch to index_tickets**

```python
@workflow(
    category="HaloPSA",
    tags=["halopsa", "indexing", "knowledge", "batch"],
    timeout_seconds=3600,
)
async def index_tickets(
    start_date: str,
    end_date: str | None = None,
    batch_size: int = 10,
    closed_only: bool = False,
    max_tickets: int | None = None,
) -> dict:
    """
    Batch index HaloPSA tickets within a date range.
    ...
    """
```

**Step 3: Rename index_halopsa_historical to index_tickets_historical**

```python
@workflow(
    category="HaloPSA",
    tags=["halopsa", "indexing", "historical"],
    timeout_seconds=7200,
)
async def index_tickets_historical(
    months_back: int = 6,
    batch_size: int = 10,
) -> dict:
    """
    Index historical HaloPSA tickets for initial knowledge base population.
    ...
    """
```

**Step 4: Rename search_halopsa_tickets to search_tickets with enhanced filters**

```python
@workflow(
    category="HaloPSA",
    tags=["halopsa", "search", "knowledge"],
    is_tool=True,
    tool_description="""Search indexed HaloPSA tickets with filters.

To filter by agent: First call list_agents to get the agent's exact name,
then pass it to the agent_name parameter.

To filter by client: First call list_clients to get the client_id,
then pass it to the client_id parameter.""",
)
async def search_tickets(
    query: str = "",
    limit: int = 20,
    # DB-level filters (exact match)
    client_id: int | None = None,
    agent_name: str | None = None,
    team_name: str | None = None,
    sla_met: bool | None = None,
    quality_flag: str | None = None,
    # Post-query filters
    min_quality_score: int | None = None,
    max_quality_score: int | None = None,
    min_complexity: int | None = None,
    max_complexity: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """
    Search indexed HaloPSA tickets using semantic search with filters.

    Args:
        query: Search query (optional - can filter without semantic search)
        limit: Maximum results to return
        client_id: Filter by client ID
        agent_name: Filter by agent name (checks agents_involved)
        team_name: Filter by team name
        sla_met: Filter by SLA status (True=met, False=breached)
        quality_flag: Filter by quality flag
        min_quality_score: Minimum quality score (0-100)
        max_quality_score: Maximum quality score (0-100)
        min_complexity: Minimum complexity (1-5)
        max_complexity: Maximum complexity (1-5)
        start_date: Filter tickets on/after this date (YYYY-MM-DD)
        end_date: Filter tickets on/before this date (YYYY-MM-DD)

    Returns:
        Search results with matching tickets
    """
    # Log search parameters
    logger.info(f"Searching tickets: query='{query}', limit={limit}")
    if agent_name:
        logger.info(f"  Filtering by agent: {agent_name}")
    if client_id:
        logger.info(f"  Filtering by client_id: {client_id}")
    if sla_met is not None:
        logger.info(f"  Filtering by sla_met: {sla_met}")

    # Determine if we have post-query filters
    has_post_filters = any([
        min_quality_score, max_quality_score,
        min_complexity, max_complexity,
        start_date, end_date,
    ])

    # Fetch extra results if we have post-filters
    fetch_limit = limit * 3 if has_post_filters else limit
    logger.debug(f"Fetching up to {fetch_limit} results (has_post_filters={has_post_filters})")

    # Build metadata filter (DB-level, exact match only)
    metadata_filter: dict = {}

    if client_id:
        metadata_filter["client_id"] = client_id

    if agent_name:
        metadata_filter["agents_involved"] = [agent_name]

    if team_name:
        metadata_filter["team_name"] = team_name

    if sla_met is not None:
        metadata_filter["sla_met"] = sla_met

    if quality_flag:
        metadata_filter["quality_flags"] = [quality_flag]

    # Search knowledge store
    search_query = query if query else "ticket"  # Need some query for semantic search
    logger.debug(f"Querying knowledge store with filter: {metadata_filter}")
    results = await knowledge.search(
        search_query,
        namespace=NAMESPACE,
        limit=fetch_limit,
        metadata_filter=metadata_filter if metadata_filter else None,
    )
    logger.info(f"Knowledge store returned {len(results)} results")

    # Apply post-query filters
    filtered_results = []
    for r in results:
        meta = r.metadata

        # Quality score range
        score = meta.get("quality_score") or 0
        if min_quality_score is not None and score < min_quality_score:
            continue
        if max_quality_score is not None and score > max_quality_score:
            continue

        # Complexity range
        complexity = meta.get("complexity_score") or 3
        if min_complexity is not None and complexity < min_complexity:
            continue
        if max_complexity is not None and complexity > max_complexity:
            continue

        # Date range
        occurred = meta.get("dateoccurred", "")
        if start_date and occurred and occurred < start_date:
            continue
        if end_date and occurred and occurred > end_date:
            continue

        filtered_results.append(r)

    # Truncate to requested limit
    filtered_results = filtered_results[:limit]
    logger.info(f"After filtering: {len(filtered_results)} results")

    return {
        "query": query,
        "count": len(filtered_results),
        "results": [
            {
                "ticket_id": r.metadata.get("ticket_id"),
                "client": r.metadata.get("client_name"),
                "status": r.metadata.get("status_name"),
                "agents_involved": r.metadata.get("agents_involved"),
                "sla_met": r.metadata.get("sla_met"),
                "summary": r.content[:300] + "..." if len(r.content) > 300 else r.content,
                "score": round(r.score, 3) if r.score else None,
                "quality_score": r.metadata.get("quality_score"),
                "complexity_score": r.metadata.get("complexity_score"),
                "quality_flags": r.metadata.get("quality_flags"),
                "dateoccurred": r.metadata.get("dateoccurred"),
            }
            for r in filtered_results
        ],
    }
```

**Step 5: Rename find_quality_issues to find_tickets_by_flag**

```python
@workflow(
    category="HaloPSA",
    tags=["halopsa", "quality", "review"],
    is_tool=True,
    tool_description="Find tickets with specific quality issues like missing resolutions or no time logged",
)
async def find_tickets_by_flag(
    quality_flag: Literal[
        "missing_resolution",
        "no_time_logged",
        "minimal_notes",
        "vague_summary",
        "escalation_needed",
        "excellent_documentation",
    ],
    limit: int = 50,
    client_id: int | None = None,
    agent_name: str | None = None,
) -> dict:
    """
    Find tickets with specific quality issues.

    Args:
        quality_flag: The quality flag to filter by
        limit: Maximum results to return
        client_id: Optional client ID filter
        agent_name: Optional agent name filter (checks agents_involved)

    Returns:
        List of tickets with the specified quality issue
    """
    metadata_filter: dict = {"quality_flags": [quality_flag]}

    if client_id:
        metadata_filter["client_id"] = client_id

    if agent_name:
        metadata_filter["agents_involved"] = [agent_name]

    results = await knowledge.search(
        "ticket",
        namespace=NAMESPACE,
        limit=limit,
        metadata_filter=metadata_filter,
    )

    return {
        "quality_flag": quality_flag,
        "count": len(results),
        "tickets": [
            {
                "ticket_id": r.metadata.get("ticket_id"),
                "client": r.metadata.get("client_name"),
                "status": r.metadata.get("status_name"),
                "agents_involved": r.metadata.get("agents_involved"),
                "summary_preview": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                "quality_score": r.metadata.get("quality_score"),
                "complexity_score": r.metadata.get("complexity_score"),
                "all_flags": r.metadata.get("quality_flags"),
            }
            for r in results
        ],
    }
```

**Step 6: Add missing import for knowledge**

At the top of the file, ensure the import includes knowledge:

```python
from bifrost import workflow, knowledge, UserError
```

**Step 7: Verify changes**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -c "from features.ai_ticketing.workflows.ticket_indexer import index_ticket, index_tickets, search_tickets, find_tickets_by_flag; print('Renamed workflows OK')"`

Expected: `Renamed workflows OK`

**Step 8: Commit**

```bash
git add features/ai_ticketing/workflows/ticket_indexer.py
git commit -m "refactor(ai_ticketing): rename workflows to action-first convention, enhance search_tickets"
```

---

## Task 6: Add review_tickets Workflow with Metrics

**Files:**
- Modify: `/Users/jack/GitHub/gocovi-bifrost-workspace/features/ai_ticketing/workflows/ticket_indexer.py`

**Step 1: Add the review_tickets workflow**

Add this new workflow at the end of the file:

```python
# =============================================================================
# Ticket Review with Metrics
# =============================================================================


@workflow(
    category="HaloPSA",
    tags=["halopsa", "review", "metrics", "performance"],
    is_tool=True,
    tool_description="""Review tickets with filtering and aggregated metrics.

Returns ticket list plus metrics like average quality score, SLA compliance rate,
tickets by agent, etc.

To review a specific agent's performance: First call list_agents to get their
exact name, then pass it to the agent_name parameter.""",
)
async def review_tickets(
    # Filters
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
) -> dict:
    """
    Review tickets with optional aggregated metrics.

    Args:
        agent_name: Filter by agent name (checks agents_involved)
        client_id: Filter by client ID
        team_name: Filter by team name
        start_date: Filter tickets on/after this date (YYYY-MM-DD)
        end_date: Filter tickets on/before this date (YYYY-MM-DD)
        min_quality_score: Minimum quality score (0-100)
        max_quality_score: Maximum quality score (0-100)
        sla_met: Filter by SLA status
        include_metrics: Calculate and return aggregated metrics
        limit: Maximum tickets to return

    Returns:
        Tickets and optional metrics for review
    """
    # Log review parameters
    logger.info(f"Starting ticket review (include_metrics={include_metrics}, limit={limit})")
    if agent_name:
        logger.info(f"  Filtering by agent: {agent_name}")
    if client_id:
        logger.info(f"  Filtering by client_id: {client_id}")
    if start_date or end_date:
        logger.info(f"  Date range: {start_date or 'any'} to {end_date or 'any'}")

    # Fetch more results for metrics calculation
    fetch_limit = max(limit, 200) if include_metrics else limit
    logger.debug(f"Fetching up to {fetch_limit} results for metrics calculation")

    # Build metadata filter
    metadata_filter: dict = {}

    if client_id:
        metadata_filter["client_id"] = client_id

    if agent_name:
        metadata_filter["agents_involved"] = [agent_name]

    if team_name:
        metadata_filter["team_name"] = team_name

    if sla_met is not None:
        metadata_filter["sla_met"] = sla_met

    # Search knowledge store
    logger.debug(f"Querying knowledge store with filter: {metadata_filter}")
    results = await knowledge.search(
        "ticket",
        namespace=NAMESPACE,
        limit=fetch_limit,
        metadata_filter=metadata_filter if metadata_filter else None,
    )
    logger.info(f"Knowledge store returned {len(results)} results")

    # Apply post-query filters
    filtered_results = []
    for r in results:
        meta = r.metadata

        # Quality score range
        score = meta.get("quality_score") or 0
        if min_quality_score is not None and score < min_quality_score:
            continue
        if max_quality_score is not None and score > max_quality_score:
            continue

        # Date range
        occurred = meta.get("dateoccurred", "")
        if start_date and occurred and occurred < start_date:
            continue
        if end_date and occurred and occurred > end_date:
            continue

        filtered_results.append(r)

    logger.info(f"After post-query filtering: {len(filtered_results)} results")

    # Calculate metrics if requested
    metrics = None
    if include_metrics and filtered_results:
        logger.info("Calculating aggregated metrics...")
        metrics = _calculate_metrics(filtered_results)
        logger.info(f"  Avg quality: {metrics['avg_quality_score']}, SLA compliance: {metrics['sla_compliance_rate']}")

    # Truncate for response
    response_results = filtered_results[:limit]
    logger.info(f"Returning {len(response_results)} tickets (total matching: {len(filtered_results)})")

    return {
        "total_count": len(filtered_results),
        "returned_count": len(response_results),
        "tickets": [
            {
                "ticket_id": r.metadata.get("ticket_id"),
                "client": r.metadata.get("client_name"),
                "status": r.metadata.get("status_name"),
                "agents_involved": r.metadata.get("agents_involved"),
                "sla_met": r.metadata.get("sla_met"),
                "summary_preview": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                "quality_score": r.metadata.get("quality_score"),
                "complexity_score": r.metadata.get("complexity_score"),
                "quality_flags": r.metadata.get("quality_flags"),
                "dateoccurred": r.metadata.get("dateoccurred"),
            }
            for r in response_results
        ],
        "metrics": metrics,
    }


def _calculate_metrics(results: list) -> dict:
    """Calculate aggregated metrics from search results."""
    from collections import Counter

    quality_scores = []
    complexity_scores = []
    sla_statuses = []
    agents_counter: Counter = Counter()
    flags_counter: Counter = Counter()

    for r in results:
        meta = r.metadata

        # Quality scores
        if meta.get("quality_score") is not None:
            quality_scores.append(meta["quality_score"])

        # Complexity scores
        if meta.get("complexity_score") is not None:
            complexity_scores.append(meta["complexity_score"])

        # SLA status
        if meta.get("sla_met") is not None:
            sla_statuses.append(meta["sla_met"])

        # Agents involved
        for agent in meta.get("agents_involved") or []:
            agents_counter[agent] += 1

        # Quality flags
        for flag in meta.get("quality_flags") or []:
            flags_counter[flag] += 1

    # Calculate averages
    avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
    avg_complexity = sum(complexity_scores) / len(complexity_scores) if complexity_scores else 0

    # Quality distribution
    quality_dist = {"excellent": 0, "good": 0, "adequate": 0, "poor": 0, "minimal": 0}
    for score in quality_scores:
        if score >= 90:
            quality_dist["excellent"] += 1
        elif score >= 70:
            quality_dist["good"] += 1
        elif score >= 50:
            quality_dist["adequate"] += 1
        elif score >= 30:
            quality_dist["poor"] += 1
        else:
            quality_dist["minimal"] += 1

    # SLA compliance rate
    sla_met_count = sum(1 for s in sla_statuses if s is True)
    sla_compliance = sla_met_count / len(sla_statuses) if sla_statuses else None

    return {
        "avg_quality_score": round(avg_quality, 1),
        "quality_distribution": quality_dist,
        "sla_compliance_rate": round(sla_compliance, 3) if sla_compliance is not None else None,
        "sla_sample_size": len(sla_statuses),
        "avg_complexity": round(avg_complexity, 1),
        "tickets_by_agent": dict(agents_counter.most_common(20)),
        "common_quality_flags": flags_counter.most_common(10),
    }
```

**Step 2: Verify changes**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -c "from features.ai_ticketing.workflows.ticket_indexer import review_tickets; print('review_tickets OK')"`

Expected: `review_tickets OK`

**Step 3: Commit**

```bash
git add features/ai_ticketing/workflows/ticket_indexer.py
git commit -m "feat(ai_ticketing): add review_tickets workflow with aggregated metrics"
```

---

## Task 7: Update Feature __init__.py

**Files:**
- Modify: `/Users/jack/GitHub/gocovi-bifrost-workspace/features/ai_ticketing/__init__.py`

**Step 1: Check current contents and add reference_data import**

First read the current file, then update to include reference_data workflows:

```python
"""
AI Ticketing Feature

Provides AI-powered ticket indexing, quality assessment, and semantic search
for HaloPSA tickets.
"""

from features.ai_ticketing.workflows import ticket_indexer
from features.ai_ticketing.workflows import reference_data

__all__ = ["ticket_indexer", "reference_data"]
```

**Step 2: Verify changes**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -c "from features import ai_ticketing; print('Feature init OK')"`

Expected: `Feature init OK`

**Step 3: Commit**

```bash
git add features/ai_ticketing/__init__.py
git commit -m "feat(ai_ticketing): export reference_data workflows from feature"
```

---

## Task 8: Write Unit Tests for New Functionality

**Files:**
- Create: `/Users/jack/GitHub/gocovi-bifrost-workspace/tests/unit/features/ai_ticketing/test_metadata_extraction.py`

**Step 1: Create test file for metadata extraction**

```python
"""
Unit tests for enhanced ticket metadata extraction.
"""

import pytest

from features.ai_ticketing.models import TicketMetadata
from modules.extensions.halopsa import extract_agents_involved, extract_metadata


class TestExtractAgentsInvolved:
    """Tests for extract_agents_involved helper."""

    def test_extracts_unique_agents(self):
        """Should extract unique agent names from actions."""
        actions = [
            {"who": "Jason Zimanski", "who_agentid": 23},
            {"who": "Ethan Waymire", "who_agentid": 15},
            {"who": "Jason Zimanski", "who_agentid": 23},  # Duplicate
        ]

        result = extract_agents_involved(actions)

        assert len(result) == 2
        assert "Jason Zimanski" in result
        assert "Ethan Waymire" in result

    def test_excludes_non_agents(self):
        """Should exclude actions where who_agentid is 0 or missing."""
        actions = [
            {"who": "Jason Zimanski", "who_agentid": 23},
            {"who": "Jennifer Cisco", "who_agentid": 0},  # Not an agent
            {"who": "System", "who_agentid": None},  # Missing
            {"who": "API", },  # No who_agentid key
        ]

        result = extract_agents_involved(actions)

        assert result == ["Jason Zimanski"]

    def test_empty_actions(self):
        """Should return empty list for empty actions."""
        result = extract_agents_involved([])
        assert result == []

    def test_no_valid_agents(self):
        """Should return empty list when no valid agents."""
        actions = [
            {"who": "Customer", "who_agentid": 0},
        ]

        result = extract_agents_involved(actions)
        assert result == []


class TestExtractMetadata:
    """Tests for extract_metadata with SLA parsing."""

    def test_sla_met_when_in_progress(self):
        """Should set sla_met=True when slastate is 'I'."""
        ticket = {"id": 123, "slastate": "I"}

        result = extract_metadata(ticket)

        assert result.sla_met is True

    def test_sla_not_met_when_overdue(self):
        """Should set sla_met=False when slastate is 'O'."""
        ticket = {"id": 123, "slastate": "O"}

        result = extract_metadata(ticket)

        assert result.sla_met is False

    def test_sla_none_when_missing(self):
        """Should set sla_met=None when slastate is missing or other."""
        ticket = {"id": 123}

        result = extract_metadata(ticket)

        assert result.sla_met is None

    def test_sla_none_when_other_value(self):
        """Should set sla_met=None when slastate is unknown value."""
        ticket = {"id": 123, "slastate": "X"}

        result = extract_metadata(ticket)

        assert result.sla_met is None

    def test_includes_agents_involved(self):
        """Should include agents_involved when actions provided."""
        ticket = {"id": 123, "slastate": "I"}
        actions = [
            {"who": "Jason Zimanski", "who_agentid": 23},
        ]

        result = extract_metadata(ticket, actions)

        assert result.agents_involved == ["Jason Zimanski"]

    def test_agents_involved_none_without_actions(self):
        """Should set agents_involved=None when no actions provided."""
        ticket = {"id": 123}

        result = extract_metadata(ticket)

        assert result.agents_involved is None
```

**Step 2: Run the tests**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -m pytest tests/unit/features/ai_ticketing/test_metadata_extraction.py -v`

Expected: All tests pass

**Step 3: Commit**

```bash
git add tests/unit/features/ai_ticketing/test_metadata_extraction.py
git commit -m "test(ai_ticketing): add unit tests for metadata extraction"
```

---

## Task 9: Write Unit Tests for Metrics Calculation

**Files:**
- Create: `/Users/jack/GitHub/gocovi-bifrost-workspace/tests/unit/features/ai_ticketing/test_metrics.py`

**Step 1: Create test file for metrics calculation**

```python
"""
Unit tests for ticket review metrics calculation.
"""

import pytest
from unittest.mock import MagicMock

from features.ai_ticketing.workflows.ticket_indexer import _calculate_metrics


class TestCalculateMetrics:
    """Tests for _calculate_metrics helper."""

    def _make_result(self, **metadata):
        """Create a mock search result with given metadata."""
        result = MagicMock()
        result.metadata = metadata
        return result

    def test_calculates_avg_quality_score(self):
        """Should calculate average quality score."""
        results = [
            self._make_result(quality_score=90),
            self._make_result(quality_score=70),
            self._make_result(quality_score=50),
        ]

        metrics = _calculate_metrics(results)

        assert metrics["avg_quality_score"] == 70.0

    def test_calculates_quality_distribution(self):
        """Should categorize quality scores into distribution buckets."""
        results = [
            self._make_result(quality_score=95),  # excellent
            self._make_result(quality_score=75),  # good
            self._make_result(quality_score=55),  # adequate
            self._make_result(quality_score=35),  # poor
            self._make_result(quality_score=15),  # minimal
        ]

        metrics = _calculate_metrics(results)

        assert metrics["quality_distribution"]["excellent"] == 1
        assert metrics["quality_distribution"]["good"] == 1
        assert metrics["quality_distribution"]["adequate"] == 1
        assert metrics["quality_distribution"]["poor"] == 1
        assert metrics["quality_distribution"]["minimal"] == 1

    def test_calculates_sla_compliance_rate(self):
        """Should calculate SLA compliance rate."""
        results = [
            self._make_result(sla_met=True),
            self._make_result(sla_met=True),
            self._make_result(sla_met=False),
            self._make_result(sla_met=None),  # Should be excluded
        ]

        metrics = _calculate_metrics(results)

        # 2 met out of 3 with sla_met set = 0.667
        assert metrics["sla_compliance_rate"] == 0.667
        assert metrics["sla_sample_size"] == 3

    def test_calculates_avg_complexity(self):
        """Should calculate average complexity score."""
        results = [
            self._make_result(complexity_score=1),
            self._make_result(complexity_score=3),
            self._make_result(complexity_score=5),
        ]

        metrics = _calculate_metrics(results)

        assert metrics["avg_complexity"] == 3.0

    def test_counts_tickets_by_agent(self):
        """Should count tickets by agent involvement."""
        results = [
            self._make_result(agents_involved=["Jason", "Ethan"]),
            self._make_result(agents_involved=["Jason"]),
            self._make_result(agents_involved=["Cory"]),
        ]

        metrics = _calculate_metrics(results)

        assert metrics["tickets_by_agent"]["Jason"] == 2
        assert metrics["tickets_by_agent"]["Ethan"] == 1
        assert metrics["tickets_by_agent"]["Cory"] == 1

    def test_counts_quality_flags(self):
        """Should count occurrences of quality flags."""
        results = [
            self._make_result(quality_flags=["missing_resolution", "no_time_logged"]),
            self._make_result(quality_flags=["missing_resolution"]),
            self._make_result(quality_flags=["excellent_documentation"]),
        ]

        metrics = _calculate_metrics(results)

        # Returns list of tuples (flag, count)
        flags_dict = dict(metrics["common_quality_flags"])
        assert flags_dict["missing_resolution"] == 2
        assert flags_dict["no_time_logged"] == 1
        assert flags_dict["excellent_documentation"] == 1

    def test_handles_empty_results(self):
        """Should handle empty results gracefully."""
        metrics = _calculate_metrics([])

        assert metrics["avg_quality_score"] == 0
        assert metrics["avg_complexity"] == 0
        assert metrics["sla_compliance_rate"] is None
        assert metrics["tickets_by_agent"] == {}
        assert metrics["common_quality_flags"] == []

    def test_handles_missing_metadata(self):
        """Should handle results with missing metadata fields."""
        results = [
            self._make_result(),  # No metadata fields set
        ]

        metrics = _calculate_metrics(results)

        # Should not raise, should return empty/zero values
        assert metrics["avg_quality_score"] == 0
        assert metrics["tickets_by_agent"] == {}
```

**Step 2: Run the tests**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -m pytest tests/unit/features/ai_ticketing/test_metrics.py -v`

Expected: All tests pass

**Step 3: Commit**

```bash
git add tests/unit/features/ai_ticketing/test_metrics.py
git commit -m "test(ai_ticketing): add unit tests for metrics calculation"
```

---

## Task 10: Integration Test - End-to-End Workflow

**Files:**
- Create: `/Users/jack/GitHub/gocovi-bifrost-workspace/tests/integration/features/ai_ticketing/test_ticket_review.py`

**Step 1: Create integration test file**

```python
"""
Integration tests for ticket review system.

These tests require a running Bifrost environment with HaloPSA integration.
"""

import pytest

pytestmark = pytest.mark.integration


class TestReferenceDataWorkflows:
    """Integration tests for reference data lookup workflows."""

    @pytest.mark.asyncio
    async def test_list_agents_returns_agents(self):
        """Should return list of agents from HaloPSA."""
        from features.ai_ticketing.workflows.reference_data import list_agents

        result = await list_agents()

        assert "count" in result
        assert "agents" in result
        assert result["count"] > 0
        # Verify agent structure
        agent = result["agents"][0]
        assert "id" in agent
        assert "name" in agent

    @pytest.mark.asyncio
    async def test_list_clients_returns_clients(self):
        """Should return list of clients from HaloPSA."""
        from features.ai_ticketing.workflows.reference_data import list_clients

        result = await list_clients()

        assert "count" in result
        assert "clients" in result
        assert result["count"] > 0

    @pytest.mark.asyncio
    async def test_list_teams_returns_teams(self):
        """Should return list of teams from HaloPSA."""
        from features.ai_ticketing.workflows.reference_data import list_teams

        result = await list_teams()

        assert "count" in result
        assert "teams" in result

    @pytest.mark.asyncio
    async def test_list_statuses_returns_statuses(self):
        """Should return list of statuses from HaloPSA."""
        from features.ai_ticketing.workflows.reference_data import list_statuses

        result = await list_statuses()

        assert "count" in result
        assert "statuses" in result


class TestTicketSearchWorkflow:
    """Integration tests for enhanced ticket search."""

    @pytest.mark.asyncio
    async def test_search_tickets_with_agent_filter(self):
        """Should filter tickets by agent_name."""
        from features.ai_ticketing.workflows.ticket_indexer import search_tickets

        # First get an agent name
        from features.ai_ticketing.workflows.reference_data import list_agents
        agents_result = await list_agents()
        if agents_result["count"] == 0:
            pytest.skip("No agents available")

        agent_name = agents_result["agents"][0]["name"]

        # Search with agent filter
        result = await search_tickets(agent_name=agent_name, limit=5)

        assert "count" in result
        assert "results" in result
        # If results exist, verify they include the agent
        for ticket in result["results"]:
            if ticket.get("agents_involved"):
                assert agent_name in ticket["agents_involved"]

    @pytest.mark.asyncio
    async def test_search_tickets_with_quality_range(self):
        """Should filter tickets by quality score range."""
        from features.ai_ticketing.workflows.ticket_indexer import search_tickets

        result = await search_tickets(
            min_quality_score=70,
            max_quality_score=100,
            limit=10,
        )

        assert "count" in result
        for ticket in result["results"]:
            score = ticket.get("quality_score")
            if score is not None:
                assert 70 <= score <= 100


class TestReviewTicketsWorkflow:
    """Integration tests for ticket review with metrics."""

    @pytest.mark.asyncio
    async def test_review_tickets_returns_metrics(self):
        """Should return tickets with aggregated metrics."""
        from features.ai_ticketing.workflows.ticket_indexer import review_tickets

        result = await review_tickets(include_metrics=True, limit=10)

        assert "total_count" in result
        assert "tickets" in result
        assert "metrics" in result

        if result["metrics"]:
            assert "avg_quality_score" in result["metrics"]
            assert "quality_distribution" in result["metrics"]
            assert "sla_compliance_rate" in result["metrics"]
            assert "tickets_by_agent" in result["metrics"]

    @pytest.mark.asyncio
    async def test_review_tickets_without_metrics(self):
        """Should return tickets without metrics when disabled."""
        from features.ai_ticketing.workflows.ticket_indexer import review_tickets

        result = await review_tickets(include_metrics=False, limit=10)

        assert "total_count" in result
        assert "tickets" in result
        assert result["metrics"] is None
```

**Step 2: Run integration tests (if environment available)**

Run: `cd /Users/jack/GitHub/gocovi-bifrost-workspace && python -m pytest tests/integration/features/ai_ticketing/test_ticket_review.py -v -m integration`

Note: These tests require the Bifrost environment with HaloPSA integration to be running.

**Step 3: Commit**

```bash
git add tests/integration/features/ai_ticketing/test_ticket_review.py
git commit -m "test(ai_ticketing): add integration tests for ticket review system"
```

---

## Task 11: Re-index Historical Tickets

**Files:**
- No new files - uses existing workflow

**Step 1: Verify the updated indexing works on a single ticket**

Run the index_ticket workflow on a known ticket to verify new metadata is captured:

```bash
# Using Bifrost CLI or form - index a single ticket
# Then verify the metadata in the knowledge store includes:
# - agents_involved
# - sla_met
# - complexity_score
```

**Step 2: Run historical re-indexing**

Use the `index_tickets_historical` workflow to re-index all historical tickets with the new metadata:

```bash
# Via Bifrost form or CLI:
# index_tickets_historical(months_back=6, batch_size=10)
```

**Step 3: Verify sample tickets have new metadata**

Use `search_tickets` to verify tickets now have the new fields populated.

**Step 4: Document completion**

No commit needed - this is a data migration step.

---

## Summary

This plan implements:

1. **Enhanced metadata** - `agents_involved`, `sla_met`, `complexity_score`
2. **Reference data tools** - `list_agents`, `get_agent`, `list_clients`, `get_client`, `list_teams`, `list_statuses`
3. **Renamed workflows** - Action-first naming (`index_ticket`, `search_tickets`, etc.)
4. **Enhanced search** - New filters for agent, SLA, complexity, date ranges
5. **Review workflow** - `review_tickets` with aggregated metrics
6. **Tests** - Unit tests for metadata extraction and metrics, integration tests for workflows

Total commits: 10
Estimated implementation time: The tasks are structured to be completed sequentially with frequent commits.

---

Plan complete and saved to `docs/plans/2026-01-23-ticket-review-system-implementation.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
