# Autonomous Agent Runs — Simplified Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable agents to run autonomously from events, schedules, and the SDK, with full execution tracing for observability.

**Architecture:** Build a new `AutonomousAgentExecutor` alongside the existing `AgentExecutor` (chat stays untouched). Autonomous runs execute in RabbitMQ workers via a new `AgentRunConsumer`. Every LLM call, tool call, and tool result is recorded as an `AgentRunStep` for full replay/observability. Shared helpers (tool resolution, system prompt building, tool execution) are extracted so both executors reuse the same building blocks.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, PostgreSQL, RabbitMQ (aio-pika), Redis, React, TypeScript, shadcn/ui

---

## Task 1: Database Migration — AgentRun, AgentRunStep, Budget Columns

**Files:**
- Create: `api/alembic/versions/20260305_autonomous_agents.py`
- Create: `api/src/models/orm/agent_runs.py`
- Modify: `api/src/models/orm/agents.py`
- Modify: `api/src/models/orm/events.py`

**Step 1: Create the migration file**

Create `api/alembic/versions/20260305_autonomous_agents.py`:

```python
"""autonomous_agents

Add AgentRun and AgentRunStep tables, budget columns on agents,
agent_id on event_subscriptions, make workflow_id nullable.

Revision ID: 20260305_autonomous_agents
Revises: 20260302_api_key_ondelete
Create Date: 2026-03-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260305_autonomous_agents"
down_revision: Union[str, None] = "20260302_api_key_ondelete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agent budget columns
    op.add_column("agents", sa.Column("max_iterations", sa.Integer(), nullable=True, server_default="50"))
    op.add_column("agents", sa.Column("max_token_budget", sa.Integer(), nullable=True, server_default="100000"))

    # AgentRun table
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("trigger_type", sa.String(50), nullable=False),
        sa.Column("trigger_source", sa.String(500), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_delivery_id", UUID(as_uuid=True), sa.ForeignKey("event_deliveries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("output_schema", JSONB, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("caller_user_id", sa.String(255), nullable=True),
        sa.Column("caller_email", sa.String(255), nullable=True),
        sa.Column("caller_name", sa.String(255), nullable=True),
        sa.Column("iterations_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_max_iterations", sa.Integer(), nullable=True),
        sa.Column("budget_max_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("llm_model", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_trigger_type", "agent_runs", ["trigger_type"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])

    # AgentRunStep table
    op.create_table(
        "agent_run_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("content", JSONB, nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # EventSubscription: add agent_id + target_type, make workflow_id nullable
    op.add_column("event_subscriptions", sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True))
    op.add_column("event_subscriptions", sa.Column("target_type", sa.String(50), nullable=True, server_default="workflow"))
    op.alter_column("event_subscriptions", "workflow_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.create_index("ix_event_subscriptions_agent_id", "event_subscriptions", ["agent_id"])

    # Backfill target_type for existing rows
    op.execute("UPDATE event_subscriptions SET target_type = 'workflow' WHERE target_type IS NULL")
    op.alter_column("event_subscriptions", "target_type", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_event_subscriptions_agent_id", table_name="event_subscriptions")
    op.drop_column("event_subscriptions", "target_type")
    op.drop_column("event_subscriptions", "agent_id")
    op.alter_column("event_subscriptions", "workflow_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.drop_table("agent_run_steps")
    op.drop_table("agent_runs")
    op.drop_column("agents", "max_token_budget")
    op.drop_column("agents", "max_iterations")
```

**Step 2: Create AgentRun ORM models**

Create `api/src/models/orm/agent_runs.py`:

```python
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from src.models.orm.base import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_type = Column(String(50), nullable=False)  # chat, event, schedule, sdk
    trigger_source = Column(String(500), nullable=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    event_delivery_id = Column(UUID(as_uuid=True), ForeignKey("event_deliveries.id", ondelete="SET NULL"), nullable=True)
    input = Column(JSONB, nullable=True)
    output = Column(JSONB, nullable=True)
    output_schema = Column(JSONB, nullable=True)
    status = Column(String(50), nullable=False, default="queued")  # queued, running, completed, failed, budget_exceeded
    error = Column(Text, nullable=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    caller_user_id = Column(String(255), nullable=True)
    caller_email = Column(String(255), nullable=True)
    caller_name = Column(String(255), nullable=True)
    iterations_used = Column(Integer, nullable=False, default=0)
    tokens_used = Column(Integer, nullable=False, default=0)
    budget_max_iterations = Column(Integer, nullable=True)
    budget_max_tokens = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    llm_model = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    agent = relationship("Agent", lazy="joined")
    steps = relationship("AgentRunStep", back_populates="run", cascade="all, delete-orphan", order_by="AgentRunStep.step_number")
    conversation = relationship("Conversation", lazy="select")


class AgentRunStep(Base):
    __tablename__ = "agent_run_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    type = Column(String(50), nullable=False)  # llm_request, llm_response, tool_call, tool_result, budget_warning, error
    content = Column(JSONB, nullable=True)
    tokens_used = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    run = relationship("AgentRun", back_populates="steps")
```

**Step 3: Add budget columns to Agent ORM**

In `api/src/models/orm/agents.py`, add after the `llm_temperature` field (around line 60):

```python
max_iterations: Mapped[int | None] = mapped_column(Integer, default=50)
max_token_budget: Mapped[int | None] = mapped_column(Integer, default=100000)
```

**Step 4: Update EventSubscription ORM**

In `api/src/models/orm/events.py`, add to `EventSubscription` class (around line 220):

```python
target_type: Mapped[str] = mapped_column(String(50), nullable=False, default="workflow")  # workflow, agent
agent_id: Mapped[UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), default=None)
```

Make `workflow_id` nullable (change `nullable=False` to `nullable=True` if it's currently required).

Add relationship:

```python
agent: Mapped["Agent | None"] = relationship("Agent", lazy="joined")
```

**Step 5: Apply migration**

Run: `docker compose -f docker-compose.dev.yml restart api`
Expected: Migration applies on startup, tables created.

**Step 6: Commit**

```bash
git add api/alembic/versions/20260305_autonomous_agents.py api/src/models/orm/agent_runs.py api/src/models/orm/agents.py api/src/models/orm/events.py
git commit -m "feat: add AgentRun, AgentRunStep tables, agent budget columns, event subscription agent target"
```

---

## Task 2: Pydantic Contracts

**Files:**
- Create: `api/src/models/contracts/agent_runs.py`
- Modify: `api/src/models/contracts/events.py`

**Step 1: Create AgentRun contracts**

Create `api/src/models/contracts/agent_runs.py`:

```python
"""Agent run contract models."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentRunStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    step_number: int
    type: str
    content: dict | None = None
    tokens_used: int | None = None
    duration_ms: int | None = None
    created_at: datetime


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    agent_name: str | None = None
    trigger_type: str
    trigger_source: str | None = None
    conversation_id: UUID | None = None
    event_delivery_id: UUID | None = None
    input: dict | None = None
    output: dict | None = None
    status: str
    error: str | None = None
    org_id: UUID | None = None
    caller_user_id: str | None = None
    caller_email: str | None = None
    caller_name: str | None = None
    iterations_used: int
    tokens_used: int
    budget_max_iterations: int | None = None
    budget_max_tokens: int | None = None
    duration_ms: int | None = None
    llm_model: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AgentRunDetailResponse(AgentRunResponse):
    steps: list[AgentRunStepResponse] = Field(default_factory=list)


class AgentRunListResponse(BaseModel):
    items: list[AgentRunResponse]
    total: int
    next_cursor: str | None = None


class AgentRunCreateRequest(BaseModel):
    agent_name: str
    input: dict | None = None
    output_schema: dict | None = None
    timeout: int = 1800
```

**Step 2: Update event subscription contracts**

In `api/src/models/contracts/events.py`:

Update `EventSubscriptionCreate` to add:

```python
target_type: str = "workflow"  # "workflow" or "agent"
agent_id: UUID | None = None
```

Make `workflow_id` optional:

```python
workflow_id: UUID | None = None  # required when target_type="workflow"
```

Update `EventSubscriptionResponse` to add:

```python
target_type: str
agent_id: UUID | None = None
agent_name: str | None = None
```

**Step 3: Commit**

```bash
git add api/src/models/contracts/agent_runs.py api/src/models/contracts/events.py
git commit -m "feat: add AgentRun contracts, update event subscription contracts for agent targets"
```

---

## Task 3: Shared Agent Helpers

Extract reusable helpers from `AgentExecutor` so both chat and autonomous execution share the same building blocks.

**Files:**
- Create: `api/src/services/execution/agent_helpers.py`
- Modify: `api/src/services/agent_executor.py` (use shared helpers)

**Step 1: Write test for shared helpers**

Create `api/tests/unit/services/test_agent_helpers.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.services.execution.agent_helpers import resolve_agent_tools, build_agent_system_prompt


class TestResolveAgentTools:
    @pytest.mark.asyncio
    async def test_returns_tool_definitions(self):
        """resolve_agent_tools returns tool definitions from agent config."""
        mock_session = AsyncMock()
        mock_agent = MagicMock()
        mock_agent.id = uuid4()
        mock_agent.tools = []
        mock_agent.system_tools = ["execute_workflow"]
        mock_agent.knowledge_sources = []
        mock_agent.delegated_agents = []

        tools, id_map = await resolve_agent_tools(mock_agent, mock_session)
        assert isinstance(tools, list)
        assert isinstance(id_map, dict)

    @pytest.mark.asyncio
    async def test_adds_search_knowledge_when_sources_exist(self):
        """Auto-adds search_knowledge tool when agent has knowledge sources."""
        mock_session = AsyncMock()
        mock_agent = MagicMock()
        mock_agent.id = uuid4()
        mock_agent.tools = []
        mock_agent.system_tools = []
        mock_agent.knowledge_sources = ["docs"]
        mock_agent.delegated_agents = []

        tools, _ = await resolve_agent_tools(mock_agent, mock_session)
        tool_names = [t.name for t in tools]
        assert "search_knowledge" in tool_names


class TestBuildAgentSystemPrompt:
    def test_uses_agent_system_prompt(self):
        """Uses the agent's configured system prompt."""
        mock_agent = MagicMock()
        mock_agent.system_prompt = "You are a helpful assistant."

        result = build_agent_system_prompt(mock_agent)
        assert result == "You are a helpful assistant."
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/services/test_agent_helpers.py -v`
Expected: FAIL — module not found.

**Step 3: Create shared helpers**

Create `api/src/services/execution/agent_helpers.py`:

```python
"""Shared helpers for agent execution (used by both chat and autonomous executors)."""
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent
from src.services.llm import ToolDefinition
from src.services.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def build_agent_system_prompt(agent: Agent) -> str:
    """Build the system prompt from agent configuration."""
    return agent.system_prompt


async def resolve_agent_tools(
    agent: Agent,
    session: AsyncSession,
) -> tuple[list[ToolDefinition], dict[str, UUID]]:
    """Resolve tool definitions for an agent.

    Returns (tool_definitions, tool_workflow_id_map).
    The id_map maps normalized tool names to workflow UUIDs.
    """
    tool_registry = ToolRegistry(session)
    tool_definitions: list[ToolDefinition] = []
    tool_workflow_id_map: dict[str, UUID] = {}

    # System tools
    if agent.system_tools:
        for tool_name in agent.system_tools:
            tool_def = tool_registry.get_system_tool_definition(tool_name)
            if tool_def:
                tool_definitions.append(tool_def)

    # Knowledge search (auto-add if agent has knowledge sources)
    if agent.knowledge_sources:
        search_def = tool_registry.get_knowledge_search_definition(agent.knowledge_sources)
        if search_def:
            tool_definitions.append(search_def)

    # Workflow tools (from agent.tools many-to-many)
    if agent.tools:
        for workflow in agent.tools:
            tool_def = tool_registry.get_workflow_tool_definition(workflow)
            if tool_def:
                tool_definitions.append(tool_def)
                tool_workflow_id_map[tool_def.name] = workflow.id

    # Delegation tools (from agent.delegated_agents many-to-many)
    if agent.delegated_agents:
        for delegated_agent in agent.delegated_agents:
            tool_def = ToolDefinition(
                name=f"delegate_to_{delegated_agent.name}",
                description=f"Delegate task to {delegated_agent.name}: {delegated_agent.description or 'No description'}",
                parameters={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "The task to delegate"},
                    },
                    "required": ["task"],
                },
            )
            tool_definitions.append(tool_def)

    return tool_definitions, tool_workflow_id_map
```

Note: This is a starting point. During implementation, examine `AgentExecutor._get_agent_tools()` (line 497) and `ToolRegistry` to match the exact patterns. The helpers should delegate to `ToolRegistry` methods that already exist — don't duplicate logic.

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/services/test_agent_helpers.py -v`
Expected: Tests should pass (may need mock adjustments for ToolRegistry).

**Step 5: Update AgentExecutor to use shared helpers**

In `api/src/services/agent_executor.py`, update `_get_agent_tools()` to delegate to the shared helper:

```python
from src.services.execution.agent_helpers import resolve_agent_tools

async def _get_agent_tools(self, agent: Agent) -> list[ToolDefinition]:
    tools, self._tool_workflow_id_map = await resolve_agent_tools(agent, self.session)
    return tools
```

**Step 6: Run existing agent tests to verify no regressions**

Run: `./test.sh tests/unit/services/test_agent_executor_tools.py tests/unit/services/test_agent_executor_context.py -v`
Expected: All existing tests pass unchanged.

**Step 7: Commit**

```bash
git add api/src/services/execution/agent_helpers.py api/src/services/agent_executor.py api/tests/unit/services/test_agent_helpers.py
git commit -m "refactor: extract shared agent helpers for tool resolution and system prompt building"
```

---

## Task 4: Autonomous Agent Executor

**Files:**
- Create: `api/src/services/execution/autonomous_agent_executor.py`
- Create: `api/tests/unit/services/test_autonomous_agent_executor.py`

**Step 1: Write failing test**

Create `api/tests/unit/services/test_autonomous_agent_executor.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Test Agent"
    agent.system_prompt = "You are a test agent."
    agent.tools = []
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.delegated_agents = []
    agent.max_iterations = 10
    agent.max_token_budget = 50000
    agent.llm_model = None
    agent.llm_max_tokens = None
    agent.llm_temperature = None
    agent.organization_id = uuid4()
    return agent


class TestAutonomousAgentExecutor:
    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_returns_structured_result(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run returns output, iterations_used, tokens_used, status."""
        # Mock tool resolution
        mock_resolve_tools.return_value = ([], {})

        # Mock LLM client that returns a simple response (no tool calls)
        mock_llm = AsyncMock()
        mock_chunk_delta = MagicMock(type="delta", content="Hello world", tool_call=None)
        mock_chunk_done = MagicMock(type="done", content=None, tool_call=None, input_tokens=100, output_tokens=50, finish_reason="end_turn")
        mock_llm.stream = MagicMock(return_value=_async_iter([mock_chunk_delta, mock_chunk_done]))
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            input_data={"message": "hello"},
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        assert result["output"] is not None
        assert result["iterations_used"] >= 1
        assert result["tokens_used"] > 0

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_records_steps(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run records AgentRunStep entries for each LLM call."""
        mock_resolve_tools.return_value = ([], {})

        mock_llm = AsyncMock()
        mock_llm.stream = MagicMock(return_value=_async_iter([
            MagicMock(type="delta", content="Response", tool_call=None),
            MagicMock(type="done", content=None, tool_call=None, input_tokens=100, output_tokens=50, finish_reason="end_turn"),
        ]))
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            input_data={"task": "analyze"},
            run_id=str(uuid4()),
        )

        # Verify steps were recorded (session.add called for AgentRunStep)
        add_calls = mock_session.add.call_args_list
        step_adds = [c for c in add_calls if hasattr(c[0][0], 'step_number')]
        assert len(step_adds) >= 1  # At least one LLM response step


async def _async_iter(items):
    for item in items:
        yield item
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/services/test_autonomous_agent_executor.py -v`
Expected: FAIL — module not found.

**Step 3: Implement AutonomousAgentExecutor**

Create `api/src/services/execution/autonomous_agent_executor.py`:

```python
"""Autonomous agent executor — runs agents without chat/streaming concerns.

Used for event-triggered, schedule-triggered, and SDK-triggered agent runs.
Records every step as an AgentRunStep for full observability.
"""
import json
import logging
import time
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent
from src.models.orm.agent_runs import AgentRunStep
from src.services.execution.agent_helpers import build_agent_system_prompt, resolve_agent_tools
from src.services.llm import LLMMessage, ToolCallRequest, get_llm_client

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 50  # Hard ceiling, agent.max_iterations is the configurable limit


class AutonomousAgentExecutor:
    def __init__(self, session: AsyncSession):
        self.session = session
        self._tool_workflow_id_map: dict = {}

    async def run(
        self,
        agent: Agent,
        *,
        input_data: dict | None = None,
        output_schema: dict | None = None,
        run_id: str | None = None,
        caller: dict | None = None,
    ) -> dict:
        """Execute an autonomous agent run.

        Returns: {"output": ..., "iterations_used": int, "tokens_used": int, "status": str, "llm_model": str}
        """
        run_id = run_id or str(uuid4())
        step_number = 0
        iterations_used = 0
        tokens_used = 0
        max_iterations = min(agent.max_iterations or 50, MAX_ITERATIONS)
        max_tokens = agent.max_token_budget or 100000

        # Resolve tools
        tool_definitions, self._tool_workflow_id_map = await resolve_agent_tools(agent, self.session)

        # Build initial messages
        system_prompt = build_agent_system_prompt(agent)
        user_content = json.dumps(input_data) if input_data else "Run your task."
        if output_schema:
            user_content += f"\n\nRespond with JSON matching this schema:\n{json.dumps(output_schema)}"

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_content),
        ]

        # Get LLM client
        llm_client = await get_llm_client(self.session)
        model = agent.llm_model

        # Record initial request step
        step_number += 1
        await self._record_step(run_id, step_number, "llm_request", {
            "messages_count": len(messages),
            "tools_count": len(tool_definitions),
            "model": model,
        })

        # Main loop
        final_content = ""
        status = "completed"

        while iterations_used < max_iterations:
            iterations_used += 1
            start_time = time.time()

            # Check budget — soft warning at 80%
            if iterations_used == int(max_iterations * 0.8):
                messages.append(LLMMessage(
                    role="system",
                    content="You are approaching your iteration budget. Please wrap up your work and provide your final output.",
                ))
                step_number += 1
                await self._record_step(run_id, step_number, "budget_warning", {
                    "iterations_used": iterations_used,
                    "max_iterations": max_iterations,
                })

            # Call LLM
            collected_content = ""
            collected_tool_calls: list[ToolCallRequest] = []
            chunk_input_tokens = 0
            chunk_output_tokens = 0

            async for chunk in llm_client.stream(
                messages=messages,
                tools=tool_definitions if tool_definitions else None,
                model=model,
                max_tokens=agent.llm_max_tokens,
                temperature=agent.llm_temperature,
            ):
                if chunk.type == "delta" and chunk.content:
                    collected_content += chunk.content
                elif chunk.type == "tool_call" and chunk.tool_call:
                    collected_tool_calls.append(chunk.tool_call)
                elif chunk.type == "done":
                    chunk_input_tokens = chunk.input_tokens or 0
                    chunk_output_tokens = chunk.output_tokens or 0

            duration_ms = int((time.time() - start_time) * 1000)
            tokens_used += chunk_input_tokens + chunk_output_tokens

            # Record LLM response step
            step_number += 1
            await self._record_step(run_id, step_number, "llm_response", {
                "content": collected_content[:2000],  # Truncate for storage
                "tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in collected_tool_calls],
            }, tokens_used=chunk_input_tokens + chunk_output_tokens, duration_ms=duration_ms)

            # No tool calls = done
            if not collected_tool_calls:
                final_content = collected_content
                break

            # Add assistant message with tool calls to history
            messages.append(LLMMessage(
                role="assistant",
                content=collected_content if collected_content else None,
                tool_calls=collected_tool_calls,
            ))

            # Execute tools
            for tc in collected_tool_calls:
                step_number += 1
                await self._record_step(run_id, step_number, "tool_call", {
                    "tool_name": tc.name,
                    "arguments": tc.arguments,
                })

                tool_start = time.time()
                try:
                    result = await self._execute_tool(tc, agent)
                    tool_duration = int((time.time() - tool_start) * 1000)

                    step_number += 1
                    await self._record_step(run_id, step_number, "tool_result", {
                        "tool_name": tc.name,
                        "result": str(result)[:2000],
                    }, duration_ms=tool_duration)

                    messages.append(LLMMessage(
                        role="tool",
                        content=str(result),
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                    ))
                except Exception as e:
                    tool_duration = int((time.time() - tool_start) * 1000)
                    step_number += 1
                    await self._record_step(run_id, step_number, "error", {
                        "tool_name": tc.name,
                        "error": str(e),
                    }, duration_ms=tool_duration)

                    messages.append(LLMMessage(
                        role="tool",
                        content=f"Error: {e}",
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                    ))

            # Check token budget
            if tokens_used >= max_tokens:
                status = "budget_exceeded"
                step_number += 1
                await self._record_step(run_id, step_number, "budget_warning", {
                    "tokens_used": tokens_used,
                    "max_tokens": max_tokens,
                    "reason": "token_budget_exceeded",
                })
                break
        else:
            # Loop exhausted without break = budget exceeded
            status = "budget_exceeded"

        # Parse output
        output = final_content
        if output_schema and final_content:
            try:
                output = json.loads(final_content)
            except json.JSONDecodeError:
                pass

        return {
            "output": output,
            "iterations_used": iterations_used,
            "tokens_used": tokens_used,
            "status": status,
            "llm_model": model,
        }

    async def _execute_tool(self, tool_call: ToolCallRequest, agent: Agent) -> str:
        """Execute a tool call. Delegates to the same infrastructure as AgentExecutor."""
        # Import here to avoid circular imports
        from src.services.execution.service import execute_tool

        workflow_id = self._tool_workflow_id_map.get(tool_call.name)
        if not workflow_id:
            return f"Unknown tool: {tool_call.name}"

        response = await execute_tool(
            workflow_id=str(workflow_id),
            workflow_name=tool_call.name,
            parameters=tool_call.arguments or {},
            user_id="system",
            user_email="agent@internal.gobifrost.com",
            user_name=agent.name,
            org_id=str(agent.organization_id) if agent.organization_id else None,
            is_platform_admin=False,
        )

        return str(response.result) if response.result else "Tool executed successfully"

    async def _record_step(
        self,
        run_id: str,
        step_number: int,
        step_type: str,
        content: dict | None = None,
        *,
        tokens_used: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Record an AgentRunStep in the database."""
        from uuid import UUID
        step = AgentRunStep(
            id=uuid4(),
            run_id=UUID(run_id),
            step_number=step_number,
            type=step_type,
            content=content,
            tokens_used=tokens_used,
            duration_ms=duration_ms,
        )
        self.session.add(step)
        await self.session.flush()
```

Note: The `_execute_tool` method above is simplified. During implementation, examine `AgentExecutor._execute_tool()` (line 1111) for handling delegation, knowledge search, and system tools — adapt those patterns as needed. The key difference: autonomous runs don't need to save Message records or stream chunks.

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/services/test_autonomous_agent_executor.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/execution/autonomous_agent_executor.py api/tests/unit/services/test_autonomous_agent_executor.py
git commit -m "feat: add AutonomousAgentExecutor with budget enforcement and step recording"
```

---

## Task 5: Agent Run Enqueue Function + Consumer

**Files:**
- Create: `api/src/services/execution/agent_run_service.py`
- Create: `api/src/jobs/consumers/agent_run.py`
- Modify: `api/src/worker/main.py`

**Step 1: Write test for enqueue function**

Create `api/tests/unit/services/test_agent_run_service.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.services.execution.agent_run_service import enqueue_agent_run


class TestEnqueueAgentRun:
    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_returns_run_id(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=False)

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="event",
            input_data={"ticket_id": 123},
        )

        assert run_id is not None
        mock_publish.assert_called_once()

        # Verify queue name
        call_args = mock_publish.call_args
        assert call_args[0][0] == "agent-runs"

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_stores_context_in_redis(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=False)

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            input_data={"task": "analyze"},
            output_schema={"action": {"type": "string"}},
        )

        mock_redis.set.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/services/test_agent_run_service.py -v`
Expected: FAIL — module not found.

**Step 3: Implement enqueue function**

Create `api/src/services/execution/agent_run_service.py`:

```python
"""Agent run enqueue and result waiting."""
import json
import logging
from uuid import uuid4

from src.core.cache.redis_client import get_redis
from src.jobs.rabbitmq import publish_message

logger = logging.getLogger(__name__)

QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"


async def enqueue_agent_run(
    agent_id: str,
    trigger_type: str,
    input_data: dict | None = None,
    *,
    trigger_source: str | None = None,
    output_schema: dict | None = None,
    org_id: str | None = None,
    caller_user_id: str | None = None,
    caller_email: str | None = None,
    caller_name: str | None = None,
    event_delivery_id: str | None = None,
    sync: bool = False,
    run_id: str | None = None,
) -> str:
    """Enqueue an agent run for worker processing. Returns run_id."""
    if run_id is None:
        run_id = str(uuid4())

    context = {
        "run_id": run_id,
        "agent_id": agent_id,
        "trigger_type": trigger_type,
        "trigger_source": trigger_source,
        "input": input_data,
        "output_schema": output_schema,
        "org_id": org_id,
        "caller": {
            "user_id": caller_user_id,
            "email": caller_email,
            "name": caller_name,
        },
        "event_delivery_id": event_delivery_id,
        "sync": sync,
    }

    # Store full context in Redis
    redis_key = f"{REDIS_PREFIX}:{run_id}:context"
    async with get_redis() as redis:
        await redis.set(redis_key, json.dumps(context), ex=3600)

    # Publish lightweight message to queue
    message = {
        "run_id": run_id,
        "agent_id": agent_id,
        "trigger_type": trigger_type,
        "sync": sync,
    }
    await publish_message(QUEUE_NAME, message)

    logger.info(f"Enqueued agent run {run_id} for agent {agent_id} (trigger={trigger_type})")
    return run_id


async def wait_for_agent_run_result(run_id: str, timeout: int = 1800) -> dict | None:
    """Block until agent run completes. Used for sync SDK calls."""
    result_key = f"{REDIS_PREFIX}:{run_id}:result"
    async with get_redis() as redis:
        result = await redis.blpop(result_key, timeout=timeout)
        if result:
            return json.loads(result[1])
    return None
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/services/test_agent_run_service.py -v`
Expected: PASS

**Step 5: Create the AgentRunConsumer**

Create `api/src/jobs/consumers/agent_run.py`:

```python
"""RabbitMQ consumer for autonomous agent runs."""
import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.core.cache.redis_client import get_redis
from src.core.database import get_session_factory
from src.jobs.rabbitmq import BaseConsumer
from src.models.orm.agents import Agent
from src.models.orm.agent_runs import AgentRun
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor

logger = logging.getLogger(__name__)

QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"


class AgentRunConsumer(BaseConsumer):
    def __init__(self):
        settings = get_settings()
        super().__init__(
            queue_name=QUEUE_NAME,
            prefetch_count=settings.max_concurrency,
        )
        self._session_factory = get_session_factory()

    async def process_message(self, message_data: dict) -> None:
        run_id = message_data["run_id"]
        agent_id = message_data["agent_id"]
        trigger_type = message_data["trigger_type"]
        sync = message_data.get("sync", False)

        logger.info(f"Processing agent run {run_id} (agent={agent_id}, trigger={trigger_type})")

        # Read full context from Redis
        redis_key = f"{REDIS_PREFIX}:{run_id}:context"
        async with get_redis() as redis:
            context_raw = await redis.get(redis_key)

        if not context_raw:
            logger.error(f"Agent run {run_id}: context not found in Redis")
            return

        context = json.loads(context_raw)
        start_time = time.time()

        async with self._session_factory() as db:
            try:
                # Load agent with relationships
                result = await db.execute(
                    select(Agent)
                    .options(
                        selectinload(Agent.tools),
                        selectinload(Agent.delegated_agents),
                        selectinload(Agent.roles),
                    )
                    .where(Agent.id == UUID(agent_id))
                )
                agent = result.scalar_one_or_none()
                if not agent:
                    logger.error(f"Agent run {run_id}: agent {agent_id} not found")
                    return

                # Create AgentRun record
                agent_run = AgentRun(
                    id=UUID(run_id),
                    agent_id=agent.id,
                    trigger_type=trigger_type,
                    trigger_source=context.get("trigger_source"),
                    event_delivery_id=UUID(context["event_delivery_id"]) if context.get("event_delivery_id") else None,
                    input=context.get("input"),
                    output_schema=context.get("output_schema"),
                    status="running",
                    org_id=UUID(context["org_id"]) if context.get("org_id") else None,
                    caller_user_id=context["caller"].get("user_id") if context.get("caller") else None,
                    caller_email=context["caller"].get("email") if context.get("caller") else None,
                    caller_name=context["caller"].get("name") if context.get("caller") else None,
                    budget_max_iterations=agent.max_iterations,
                    budget_max_tokens=agent.max_token_budget,
                    started_at=datetime.now(timezone.utc),
                )
                db.add(agent_run)
                await db.commit()

                # Run the agent
                executor = AutonomousAgentExecutor(db)
                run_result = await executor.run(
                    agent=agent,
                    input_data=context.get("input"),
                    output_schema=context.get("output_schema"),
                    run_id=run_id,
                    caller=context.get("caller"),
                )

                # Update run record
                duration_ms = int((time.time() - start_time) * 1000)
                agent_run.status = run_result.get("status", "completed")
                agent_run.output = run_result.get("output")
                agent_run.iterations_used = run_result.get("iterations_used", 0)
                agent_run.tokens_used = run_result.get("tokens_used", 0)
                agent_run.llm_model = run_result.get("llm_model")
                agent_run.duration_ms = duration_ms
                agent_run.completed_at = datetime.now(timezone.utc)
                await db.commit()

                # If sync, push result for BLPOP waiter
                if sync:
                    result_key = f"{REDIS_PREFIX}:{run_id}:result"
                    async with get_redis() as redis:
                        await redis.lpush(result_key, json.dumps({
                            "output": run_result.get("output"),
                            "status": run_result.get("status", "completed"),
                            "iterations_used": run_result.get("iterations_used", 0),
                            "tokens_used": run_result.get("tokens_used", 0),
                        }))
                        await redis.expire(result_key, 300)

            except Exception as e:
                logger.exception(f"Agent run {run_id} failed: {e}")
                agent_run.status = "failed"
                agent_run.error = str(e)
                agent_run.duration_ms = int((time.time() - start_time) * 1000)
                agent_run.completed_at = datetime.now(timezone.utc)
                await db.commit()

                if sync:
                    result_key = f"{REDIS_PREFIX}:{run_id}:result"
                    async with get_redis() as redis:
                        await redis.lpush(result_key, json.dumps({
                            "output": None,
                            "status": "failed",
                            "error": str(e),
                        }))
                        await redis.expire(result_key, 300)

            finally:
                async with get_redis() as redis:
                    await redis.delete(f"{REDIS_PREFIX}:{run_id}:context")
```

**Step 6: Register consumer in worker**

In `api/src/worker/main.py`, add to the consumer list (around line 88):

```python
from src.jobs.consumers.agent_run import AgentRunConsumer

# In the consumers list:
self._consumers = [
    WorkflowExecutionConsumer(),
    PackageInstallConsumer(),
    AgentRunConsumer(),  # NEW
]
```

**Step 7: Commit**

```bash
git add api/src/services/execution/agent_run_service.py api/src/jobs/consumers/agent_run.py api/src/worker/main.py api/tests/unit/services/test_agent_run_service.py
git commit -m "feat: add agent run enqueue function and AgentRunConsumer worker"
```

---

## Task 6: Event Processor + SDK Agent Dispatch

**Files:**
- Modify: `api/src/services/events/processor.py`
- Modify: `api/src/routers/events.py`
- Create: `api/bifrost/agents.py`
- Modify: `api/bifrost/__init__.py`

**Step 1: Update EventProcessor to dispatch agent runs**

In `api/src/services/events/processor.py`, add a new method `_queue_agent_run()` (after `_queue_workflow_execution`, around line 571):

```python
async def _queue_agent_run(self, delivery: EventDelivery, event: Event) -> None:
    """Queue an agent run for an event subscription targeting an agent."""
    subscription = delivery.subscription
    agent = subscription.agent

    # Process input mapping
    parameters = {}
    if subscription.input_mapping:
        parameters = self._process_input_mapping(subscription.input_mapping, event)
    else:
        parameters = event.data or {}

    # Include event context
    parameters["_event"] = {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "received_at": event.received_at.isoformat() if event.received_at else None,
    }

    org_id = str(agent.organization_id) if agent.organization_id else None

    from src.services.execution.agent_run_service import enqueue_agent_run

    run_id = await enqueue_agent_run(
        agent_id=str(agent.id),
        trigger_type="event",
        trigger_source=f"event: {event.event_type or 'webhook'}",
        input_data=parameters,
        org_id=org_id,
        event_delivery_id=str(delivery.id),
    )

    delivery.execution_id = uuid.UUID(run_id)
```

In `queue_event_deliveries()` (around line 422), add a branch based on `subscription.target_type`:

```python
if subscription.target_type == "agent":
    await self._queue_agent_run(delivery, event)
else:
    await self._queue_workflow_execution(delivery, event)
```

**Step 2: Update event subscription creation endpoint**

In `api/src/routers/events.py`, update the `create_subscription` endpoint to accept `target_type` and `agent_id`. Add validation:

```python
# In create_subscription handler:
if request.target_type == "agent":
    if not request.agent_id:
        raise HTTPException(status_code=400, detail="agent_id required when target_type is 'agent'")
    # Verify agent exists
    agent = await db.get(Agent, request.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
elif request.target_type == "workflow":
    if not request.workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id required when target_type is 'workflow'")
```

**Step 3: Create SDK agents module**

Create `api/bifrost/agents.py`:

```python
"""Bifrost SDK — Agent invocation from workflows."""
import json
import logging
from typing import Any

from .client import get_client, raise_for_status_with_detail

logger = logging.getLogger(__name__)


class agents:
    """Agent execution operations."""

    @staticmethod
    async def run(
        agent_name: str,
        input: dict[str, Any] | None = None,
        *,
        output_schema: dict[str, Any] | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any] | str:
        """Run an agent and wait for the result.

        Args:
            agent_name: Name of the agent to run.
            input: Structured input data for the agent.
            output_schema: JSON Schema for the expected output.
            timeout: Maximum seconds to wait (default 30 min).

        Returns:
            Structured dict if output_schema was provided, otherwise string.

        Raises:
            RuntimeError: If the agent run fails.
            ValueError: If the agent is not found.

        Example:
            result = await agents.run(
                "ticket-classifier",
                input={"ticket": {"subject": "Password reset", "body": "..."}},
                output_schema={"category": {"type": "string"}, "priority": {"type": "string"}},
            )
            print(result["category"])  # "access_management"
        """
        client = get_client()
        response = await client.post(
            "/api/agent-runs/execute",
            json={
                "agent_name": agent_name,
                "input": input or {},
                "output_schema": output_schema,
                "timeout": timeout,
            },
        )
        raise_for_status_with_detail(response)
        data = response.json()

        if data.get("error"):
            raise RuntimeError(f"Agent run failed: {data['error']}")

        output = data.get("output")
        if output_schema and isinstance(output, str):
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return output
        return output
```

**Step 4: Export from SDK `__init__.py`**

In `api/bifrost/__init__.py`, add the import (around line 15 with the other module imports):

```python
from .agents import agents
```

**Step 5: Commit**

```bash
git add api/src/services/events/processor.py api/src/routers/events.py api/bifrost/agents.py api/bifrost/__init__.py
git commit -m "feat: dispatch agent runs from events, add SDK agents.run()"
```

---

## Task 7: Agent Runs API Endpoints

**Files:**
- Create: `api/src/routers/agent_runs.py`
- Modify: `api/src/main.py`

**Step 1: Create the router**

Create `api/src/routers/agent_runs.py`:

```python
"""Agent Runs API endpoints."""
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser, CurrentSuperuser
from src.core.database import DbSession
from src.models.contracts.agent_runs import (
    AgentRunCreateRequest,
    AgentRunDetailResponse,
    AgentRunListResponse,
    AgentRunResponse,
    AgentRunStepResponse,
)
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.agents import Agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-runs", tags=["agent-runs"])


@router.get("", response_model=AgentRunListResponse)
async def list_agent_runs(
    db: DbSession,
    user: CurrentActiveUser,
    agent_id: UUID | None = Query(None),
    trigger_type: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    org_id: UUID | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
) -> AgentRunListResponse:
    """List agent runs with filtering."""
    query = select(AgentRun).options(selectinload(AgentRun.agent))

    if agent_id:
        query = query.where(AgentRun.agent_id == agent_id)
    if trigger_type:
        query = query.where(AgentRun.trigger_type == trigger_type)
    if status_filter:
        query = query.where(AgentRun.status == status_filter)
    if org_id:
        query = query.where(AgentRun.org_id == org_id)
    if start_date:
        query = query.where(AgentRun.created_at >= start_date)
    if end_date:
        query = query.where(AgentRun.created_at <= end_date)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Fetch page
    query = query.order_by(AgentRun.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    runs = result.scalars().all()

    items = [
        AgentRunResponse(
            **{c.key: getattr(run, c.key) for c in AgentRun.__table__.columns},
            agent_name=run.agent.name if run.agent else None,
        )
        for run in runs
    ]

    return AgentRunListResponse(items=items, total=total)


@router.get("/{run_id}", response_model=AgentRunDetailResponse)
async def get_agent_run(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentRunDetailResponse:
    """Get agent run detail with steps."""
    result = await db.execute(
        select(AgentRun)
        .options(
            selectinload(AgentRun.agent),
            selectinload(AgentRun.steps),
        )
        .where(AgentRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")

    return AgentRunDetailResponse(
        **{c.key: getattr(run, c.key) for c in AgentRun.__table__.columns},
        agent_name=run.agent.name if run.agent else None,
        steps=[AgentRunStepResponse.model_validate(s) for s in run.steps],
    )


@router.post("/execute", status_code=status.HTTP_200_OK)
async def execute_agent_run(
    request: AgentRunCreateRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Execute an agent run synchronously (used by SDK)."""
    from src.services.execution.agent_run_service import enqueue_agent_run, wait_for_agent_run_result

    # Look up agent by name
    result = await db.execute(
        select(Agent).where(Agent.name == request.agent_name, Agent.is_active == True)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{request.agent_name}' not found")

    run_id = await enqueue_agent_run(
        agent_id=str(agent.id),
        trigger_type="sdk",
        trigger_source=f"sdk: {user.email}",
        input_data=request.input,
        output_schema=request.output_schema,
        org_id=str(agent.organization_id) if agent.organization_id else None,
        caller_user_id=user.user_id,
        caller_email=user.email,
        caller_name=user.name,
        sync=True,
    )

    result = await wait_for_agent_run_result(run_id, timeout=request.timeout)
    if result is None:
        raise HTTPException(status_code=504, detail="Agent run timed out")

    return result
```

**Step 2: Register router in main.py**

In `api/src/main.py`, add import and include:

```python
from src.routers.agent_runs import router as agent_runs_router

# In the router registration section:
app.include_router(agent_runs_router)
```

**Step 3: Commit**

```bash
git add api/src/routers/agent_runs.py api/src/main.py
git commit -m "feat: add Agent Runs API endpoints (list, detail, execute)"
```

---

## Task 8: Manifest — Budget Fields on Agent

**Files:**
- Modify: `api/src/services/manifest.py`
- Modify: `api/src/services/manifest_generator.py`
- Modify: `api/src/services/github_sync.py`

**Step 1: Update ManifestAgent model**

In `api/src/services/manifest.py`, add fields to `ManifestAgent` (around line 87):

```python
class ManifestAgent(BaseModel):
    id: str
    name: str = ""
    path: str
    organization_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    access_level: str = "role_based"
    max_iterations: int | None = None       # NEW
    max_token_budget: int | None = None     # NEW
```

**Step 2: Update manifest generator**

In `api/src/services/manifest_generator.py` (around line 327), add new fields to agent serialization:

```python
ManifestAgent(
    id=str(agent.id),
    name=agent.name,
    path=f"agents/{agent.id}.agent.yaml",
    organization_id=str(agent.organization_id) if agent.organization_id else None,
    roles=agent_roles_by_agent.get(str(agent.id), []),
    access_level=agent.access_level.value if agent.access_level else "role_based",
    max_iterations=agent.max_iterations,          # NEW
    max_token_budget=agent.max_token_budget,      # NEW
)
```

**Step 3: Update github_sync.py**

In `_resolve_agent()`, add the new fields to the upsert values:

```python
max_iterations=magent.max_iterations,
max_token_budget=magent.max_token_budget,
```

**Step 4: Commit**

```bash
git add api/src/services/manifest.py api/src/services/manifest_generator.py api/src/services/github_sync.py
git commit -m "feat: add budget fields to agent manifest serialization"
```

---

## Task 9: Frontend — Agent Runs Service + Hook

**Files:**
- Create: `client/src/services/agent-runs.ts`
- Create: `client/src/hooks/useAgentRuns.ts`

**Step 1: Create API service**

Create `client/src/services/agent-runs.ts`:

```typescript
import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Types will be available after running npm run generate:types
export type AgentRun = components["schemas"]["AgentRunResponse"];
export type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];
export type AgentRunStep = components["schemas"]["AgentRunStepResponse"];

export function useAgentRuns(params?: {
  agentId?: string;
  triggerType?: string;
  status?: string;
  orgId?: string;
  startDate?: string;
  endDate?: string;
  limit?: number;
  offset?: number;
}) {
  return $api.useQuery("get", "/api/agent-runs", {
    params: {
      query: {
        agent_id: params?.agentId,
        trigger_type: params?.triggerType,
        status: params?.status,
        org_id: params?.orgId,
        start_date: params?.startDate,
        end_date: params?.endDate,
        limit: params?.limit ?? 50,
        offset: params?.offset ?? 0,
      },
    },
  });
}

export function useAgentRun(runId: string | undefined) {
  return $api.useQuery(
    "get",
    "/api/agent-runs/{run_id}",
    { params: { path: { run_id: runId! } } },
    {
      enabled: !!runId,
      staleTime: 5000,
      refetchInterval: (query) => {
        const status = query.state.data?.status;
        return status === "queued" || status === "running" ? 2000 : false;
      },
    }
  );
}
```

Note: Types won't exist in `v1.d.ts` until you run `npm run generate:types` after the API endpoints are deployed. You may need to use temporary inline types during development.

**Step 2: Commit**

```bash
git add client/src/services/agent-runs.ts
git commit -m "feat: add agent runs API service and hooks"
```

---

## Task 10: Frontend — Agent Runs List Page

**Files:**
- Create: `client/src/pages/AgentRuns.tsx`
- Modify: `client/src/App.tsx` (add route)

**Step 1: Create the page**

Create `client/src/pages/AgentRuns.tsx` following the pattern in `ExecutionHistory.tsx`:

- Filter bar: Agent dropdown, Trigger type select, Status select, Date range
- DataTable with columns: Agent, Trigger, Status, Duration, Tokens, Created
- Clickable rows → navigate to `/agent-runs/{id}`
- URL-synced filters via `useSearchParams()`
- Use `useAgentRuns()` hook from the service

**Step 2: Add route in App.tsx**

```typescript
const AgentRuns = lazyWithReload(() =>
  import("@/pages/AgentRuns").then((m) => ({ default: m.AgentRuns }))
);

// In routes:
<Route
  path="agent-runs"
  element={
    <ProtectedRoute requirePlatformAdmin>
      <AgentRuns />
    </ProtectedRoute>
  }
/>
```

**Step 3: Run frontend checks**

Run: `cd client && npm run tsc && npm run lint`

**Step 4: Commit**

```bash
git add client/src/pages/AgentRuns.tsx client/src/App.tsx
git commit -m "feat: add Agent Runs list page with filtering"
```

---

## Task 11: Frontend — Agent Run Detail Page (Activity Map)

**Files:**
- Create: `client/src/pages/AgentRunDetail.tsx`
- Create: `client/src/components/agent-runs/ActivityMap.tsx`
- Create: `client/src/components/agent-runs/StepCard.tsx`

**Step 1: Create StepCard component**

A card that renders a single `AgentRunStep` with different styles per type:
- `llm_response`: Shows response text, token count, duration
- `tool_call`: Shows tool name + input arguments
- `tool_result`: Shows result data, duration
- `budget_warning`: Warning banner (amber)
- `error`: Error banner (red)

Use `Card` from shadcn/ui, `Badge` for step type, `cn()` for conditional styling.

**Step 2: Create ActivityMap component**

A vertical timeline of `StepCard` components connected by a line (CSS `border-left` on a wrapper div). Accepts `steps: AgentRunStep[]`.

**Step 3: Create AgentRunDetail page**

- Header: Agent name, trigger badge, status badge, duration, budget usage bars
- Body: `ActivityMap` with all steps
- Use `useAgentRun(runId)` hook
- Route param: `/agent-runs/:runId`

**Step 4: Add route in App.tsx**

```typescript
const AgentRunDetail = lazyWithReload(() =>
  import("@/pages/AgentRunDetail").then((m) => ({ default: m.AgentRunDetail }))
);

<Route path="agent-runs/:runId" element={<ProtectedRoute requirePlatformAdmin><AgentRunDetail /></ProtectedRoute>} />
```

**Step 5: Run frontend checks**

Run: `cd client && npm run tsc && npm run lint`

**Step 6: Commit**

```bash
git add client/src/pages/AgentRunDetail.tsx client/src/components/agent-runs/ActivityMap.tsx client/src/components/agent-runs/StepCard.tsx client/src/App.tsx
git commit -m "feat: add Agent Run detail page with activity map"
```

---

## Task 12: Frontend — Event Subscription + Agent Settings Updates

**Files:**
- Modify: `client/src/components/events/CreateSubscriptionDialog.tsx`
- Modify: `client/src/components/agents/AgentDialog.tsx`

**Step 1: Update CreateSubscriptionDialog**

Add a target type toggle (Workflow / Agent). When "Agent" is selected, show an agent dropdown instead of the workflow dropdown. Wire `target_type` and `agent_id` into the create mutation.

**Step 2: Update AgentDialog**

Add `max_iterations` and `max_token_budget` number inputs to the agent create/edit form. Show defaults as placeholders (50 iterations, 100000 tokens).

**Step 3: Regenerate types and run checks**

Run: `cd client && npm run generate:types && npm run tsc && npm run lint`

**Step 4: Commit**

```bash
git add client/src/components/events/CreateSubscriptionDialog.tsx client/src/components/agents/AgentDialog.tsx
git commit -m "feat: support agent targets in event subscriptions, add budget settings to agent dialog"
```

---

## Task 13: E2E Tests

**Files:**
- Create: `api/tests/e2e/platform/test_autonomous_agent_run.py`

**Step 1: Write E2E tests**

```python
import pytest
import uuid

@pytest.mark.e2e
class TestAutonomousAgentRun:
    def test_agent_run_list_endpoint(self, e2e_client, platform_admin):
        """List agent runs returns empty initially."""
        response = e2e_client.get(
            "/api/agent-runs",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_create_agent_subscription(self, e2e_client, platform_admin, test_agent, test_event_source):
        """Create an event subscription targeting an agent."""
        response = e2e_client.post(
            f"/api/events/sources/{test_event_source['id']}/subscriptions",
            json={
                "target_type": "agent",
                "agent_id": str(test_agent["id"]),
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["target_type"] == "agent"
        assert data["agent_id"] == str(test_agent["id"])

    def test_agent_run_detail_not_found(self, e2e_client, platform_admin):
        """Get non-existent run returns 404."""
        fake_id = str(uuid.uuid4())
        response = e2e_client.get(
            f"/api/agent-runs/{fake_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404
```

Note: Full integration tests (webhook → agent run → verify output) require a running worker and real LLM. Add those once the basic CRUD tests pass.

**Step 2: Run tests**

Run: `./test.sh tests/e2e/platform/test_autonomous_agent_run.py -v`

**Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_autonomous_agent_run.py
git commit -m "test: add E2E tests for autonomous agent runs"
```

---

## Task 14: Pre-Completion Verification

**Step 1: Backend checks**

```bash
cd api
pyright
ruff check .
```

**Step 2: Regenerate frontend types**

```bash
cd client
npm run generate:types
```

**Step 3: Frontend checks**

```bash
npm run tsc
npm run lint
```

**Step 4: Full test suite**

```bash
cd /home/jack/GitHub/bifrost
./test.sh
```

**Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve type errors and lint issues from autonomous agents implementation"
```
