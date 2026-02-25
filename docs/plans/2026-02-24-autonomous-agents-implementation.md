# Autonomous Agents Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable agents to run autonomously from events, schedules, and the SDK, with a unified execution model where all agent runs (including chat) execute in workers.

**Architecture:** Extend the existing `AgentExecutor` to support autonomous runs with structured I/O, move all agent execution to RabbitMQ-backed workers via a new `AgentRunConsumer`, and build an Agent Runs observability UI. Chat moves from synchronous API/WebSocket execution to worker-based execution with Redis pub/sub streaming.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, PostgreSQL, RabbitMQ (aio-pika), Redis, React, TypeScript, shadcn/ui

---

## Task 1: Database Migration — AgentRun, AgentRunStep, Agent Budget Columns, EventSubscription Agent Target

**Files:**
- Create: `api/alembic/versions/20260224_autonomous_agents.py`
- Modify: `api/src/models/orm/agents.py`
- Modify: `api/src/models/orm/events.py`

**Step 1: Create the migration file**

```python
"""autonomous_agents

Add AgentRun and AgentRunStep tables, budget columns on agents,
agent_id on event_subscriptions, make workflow_id nullable.

Revision ID: 20260224_autonomous_agents
Revises: 20260220_global_app_slugs
Create Date: 2026-02-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260224_autonomous_agents"
down_revision: Union[str, None] = "20260220_global_app_slugs"
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
        sa.Column("parent_run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("trigger_type", sa.String(50), nullable=False),  # chat, event, schedule, sdk, delegation
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
    op.create_index("ix_agent_runs_caller_user_id", "agent_runs", ["caller_user_id"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])

    # AgentRunStep table
    op.create_table(
        "agent_run_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),  # llm_response, tool_call, tool_result, budget_warning, error
        sa.Column("content", JSONB, nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # EventSubscription: add agent_id, make workflow_id nullable
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

**Step 2: Add ORM models**

In `api/src/models/orm/agents.py`, add `max_iterations` and `max_token_budget` columns to the `Agent` class (after `llm_temperature`, around line 60):

```python
max_iterations = Column(Integer, default=50)
max_token_budget = Column(Integer, default=100000)
```

Create a new file `api/src/models/orm/agent_runs.py`:

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
    parent_run_id = Column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    trigger_type = Column(String(50), nullable=False)  # chat, event, schedule, sdk, delegation
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
    parent_run = relationship("AgentRun", remote_side=[id], lazy="select")
    child_runs = relationship("AgentRun", lazy="select", order_by="AgentRun.created_at")
    steps = relationship("AgentRunStep", back_populates="run", cascade="all, delete-orphan", order_by="AgentRunStep.step_number")
    conversation = relationship("Conversation", lazy="select")


class AgentRunStep(Base):
    __tablename__ = "agent_run_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    type = Column(String(50), nullable=False)  # llm_response, tool_call, tool_result, budget_warning, error
    content = Column(JSONB, nullable=True)
    tokens_used = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    run = relationship("AgentRun", back_populates="steps")
```

**Step 3: Update EventSubscription ORM**

In `api/src/models/orm/events.py`, add to `EventSubscription` (around line 220):

```python
target_type = Column(String(50), nullable=False, default="workflow")  # workflow, agent
agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)
```

Make `workflow_id` nullable (change `nullable=False` to `nullable=True`).

Add relationship:

```python
agent = relationship("Agent", lazy="joined")
```

**Step 4: Run migration**

Run: `docker compose -f docker-compose.dev.yml restart api`
Expected: Migration applies on startup, tables created.

**Step 5: Commit**

```bash
git add api/alembic/versions/20260224_autonomous_agents.py api/src/models/orm/agent_runs.py api/src/models/orm/agents.py api/src/models/orm/events.py
git commit -m "feat: add AgentRun, AgentRunStep tables, agent budget columns, event subscription agent target"
```

---

## Task 2: Pydantic Contracts for Agent Runs and Updated Event Subscriptions

**Files:**
- Create: `api/src/models/contracts/agent_runs.py`
- Modify: `api/src/models/contracts/events.py`

**Step 1: Create AgentRun contracts**

Create `api/src/models/contracts/agent_runs.py`:

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AgentRunResponse(BaseModel):
    id: UUID
    agent_id: UUID
    agent_name: str | None = None
    parent_run_id: UUID | None = None
    trigger_type: str
    trigger_source: str | None = None
    conversation_id: UUID | None = None
    event_delivery_id: UUID | None = None
    input: dict | None = None
    output: dict | None = None
    output_schema: dict | None = None
    status: str
    error: str | None = None
    org_id: UUID | None = None
    org_name: str | None = None
    caller_user_id: str | None = None
    caller_email: str | None = None
    caller_name: str | None = None
    iterations_used: int
    tokens_used: int
    budget_max_iterations: int | None = None
    budget_max_tokens: int | None = None
    duration_ms: int | None = None
    llm_model: str | None = None
    child_run_count: int = 0
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentRunStepResponse(BaseModel):
    id: UUID
    run_id: UUID
    step_number: int
    type: str  # llm_response, tool_call, tool_result, budget_warning, error
    content: dict | None = None
    tokens_used: int | None = None
    duration_ms: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentRunDetailResponse(AgentRunResponse):
    steps: list[AgentRunStepResponse] = Field(default_factory=list)
    child_runs: list[AgentRunResponse] = Field(default_factory=list)


class AgentRunListResponse(BaseModel):
    items: list[AgentRunResponse]
    total: int
    next_cursor: str | None = None


class AgentRunFilters(BaseModel):
    agent_id: UUID | None = None
    trigger_type: str | None = None
    status: str | None = None
    org_id: UUID | None = None
    caller_user_id: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


class ExplainReasoningRequest(BaseModel):
    run_id: UUID


class ExplainReasoningResponse(BaseModel):
    reasoning: str
```

**Step 2: Update event subscription contracts**

In `api/src/models/contracts/events.py`:

Update `EventSubscriptionCreate` (line 147) to add:

```python
target_type: str = "workflow"  # "workflow" or "agent"
agent_id: UUID | None = None
```

Make `workflow_id` optional:

```python
workflow_id: UUID | None = None  # required when target_type="workflow"
```

Update `EventSubscriptionResponse` (line 316) to add:

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

## Task 3: Agent Run Enqueue Function

**Files:**
- Create: `api/src/services/execution/agent_run_executor.py`

**Step 1: Write unit test**

Create `api/tests/unit/services/test_agent_run_executor.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from src.services.execution.agent_run_executor import enqueue_agent_run


class TestEnqueueAgentRun:
    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_executor.publish_message")
    @patch("src.services.execution.agent_run_executor.redis_client")
    async def test_enqueue_agent_run_chat(self, mock_redis, mock_publish):
        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="chat",
            trigger_source="user: jack@example.com",
            input_data={"message": "hello", "conversation_id": str(uuid4())},
            org_id=str(uuid4()),
            caller_user_id="user-123",
            caller_email="jack@example.com",
            caller_name="Jack",
        )

        assert run_id is not None
        mock_redis.set.assert_called_once()
        mock_publish.assert_called_once()

        # Verify queue name
        call_args = mock_publish.call_args
        assert call_args[0][0] == "agent-runs"

        # Verify message shape
        message = call_args[0][1]
        assert message["run_id"] == run_id
        assert message["agent_id"] is not None
        assert message["trigger_type"] == "chat"

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_executor.publish_message")
    @patch("src.services.execution.agent_run_executor.redis_client")
    async def test_enqueue_agent_run_with_output_schema(self, mock_redis, mock_publish):
        schema = {"action": {"type": "string"}, "confidence": {"type": "number"}}
        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            input_data={"ticket": {"id": 123}},
            output_schema=schema,
        )

        assert run_id is not None
        # Verify Redis payload includes output_schema
        redis_call = mock_redis.set.call_args
        import json
        stored = json.loads(redis_call[0][1])
        assert stored["output_schema"] == schema

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_executor.publish_message")
    @patch("src.services.execution.agent_run_executor.redis_client")
    async def test_enqueue_agent_run_with_budget(self, mock_redis, mock_publish):
        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="delegation",
            input_data={"task": "analyze"},
            parent_run_id=str(uuid4()),
            budget_max_iterations=25,
            budget_max_tokens=50000,
            budget_iterations_used=10,
            budget_tokens_used=20000,
        )

        assert run_id is not None
        import json
        stored = json.loads(mock_redis.set.call_args[0][1])
        assert stored["budget"]["max_iterations"] == 25
        assert stored["budget"]["max_tokens"] == 50000
        assert stored["budget"]["iterations_used"] == 10
        assert stored["budget"]["tokens_used"] == 20000
```

**Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/services/test_agent_run_executor.py -v`
Expected: FAIL — module not found.

**Step 3: Implement enqueue function**

Create `api/src/services/execution/agent_run_executor.py`:

```python
"""Enqueue agent runs to RabbitMQ for worker processing."""
import json
import logging
from uuid import uuid4

from src.core.redis_client import redis_client
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
    conversation_id: str | None = None,
    event_delivery_id: str | None = None,
    parent_run_id: str | None = None,
    budget_max_iterations: int | None = None,
    budget_max_tokens: int | None = None,
    budget_iterations_used: int = 0,
    budget_tokens_used: int = 0,
    sync: bool = False,
    run_id: str | None = None,
) -> str:
    """Enqueue an agent run for worker processing.

    Returns the run_id.
    """
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
        "conversation_id": conversation_id,
        "event_delivery_id": event_delivery_id,
        "parent_run_id": parent_run_id,
        "budget": {
            "max_iterations": budget_max_iterations,
            "max_tokens": budget_max_tokens,
            "iterations_used": budget_iterations_used,
            "tokens_used": budget_tokens_used,
        },
        "sync": sync,
    }

    # Store full context in Redis
    redis_key = f"{REDIS_PREFIX}:{run_id}:context"
    await redis_client.set(redis_key, json.dumps(context), ex=3600)

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
    """Block until agent run completes and return the result. Used for sync calls (SDK, delegation)."""
    result_key = f"{REDIS_PREFIX}:{run_id}:result"
    result = await redis_client.blpop(result_key, timeout=timeout)
    if result:
        return json.loads(result[1])
    return None
```

**Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/services/test_agent_run_executor.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/execution/agent_run_executor.py api/tests/unit/services/test_agent_run_executor.py
git commit -m "feat: add agent run enqueue function with Redis context storage"
```

---

## Task 4: Refactor AgentExecutor Core Loop for Worker Compatibility

This is the most critical task. The current `AgentExecutor.chat()` method (in `api/src/services/agent_executor.py`) runs in the API process, streams chunks directly on the WebSocket, and manages conversation persistence inline. We need to refactor it so the core loop can run in a worker.

**Files:**
- Modify: `api/src/services/agent_executor.py`

**Step 1: Extract the core agent loop into a separate method**

Add a new method `run()` to `AgentExecutor` that contains the core loop logic without WebSocket/streaming assumptions. The existing `chat()` method becomes a thin wrapper that calls `run()` and translates the results into streaming chunks.

The `run()` method:
- Accepts structured input or a chat message
- Runs the LLM → tool call loop
- Records `AgentRunStep` entries for each step
- Supports budget enforcement (soft + hard limits)
- Publishes streaming chunks to Redis pub/sub (for chat mode)
- Returns structured output when `output_schema` is provided
- Supports deep delegation (full agent run instead of single-turn LLM call)

```python
async def run(
    self,
    agent: Agent,
    *,
    input_data: dict | None = None,
    output_schema: dict | None = None,
    conversation: Conversation | None = None,
    user_message: str | None = None,
    run_id: str | None = None,
    budget: dict | None = None,
    caller: dict | None = None,
    stream_channel: str | None = None,  # Redis pub/sub channel for streaming
    is_platform_admin: bool = False,
    local_id: str | None = None,
) -> dict:
    """Core agent execution loop. Runs in worker process.

    For chat: provide conversation + user_message + stream_channel.
    For autonomous: provide input_data + optional output_schema.

    Returns: {"output": ..., "iterations_used": int, "tokens_used": int, "status": str}
    """
```

Key changes inside the loop:
- Replace direct `yield ChatStreamChunk(...)` with `await self._publish_chunk(stream_channel, chunk)` — publishes to Redis when `stream_channel` is set, no-ops for autonomous runs
- Replace `self._save_message()` calls — still save messages for chat mode (conversation is provided), skip for autonomous
- Add `AgentRunStep` recording for every LLM call, tool call, and tool result
- Add budget tracking: increment `iterations_used` and `tokens_used` after each LLM call
- At 80% of budget, inject wrap-up system message
- At 100%, break loop and return partial result with `status="budget_exceeded"`
- Replace `_execute_delegation()` — instead of single-turn LLM call, enqueue a child agent run via `enqueue_agent_run(sync=True)` and `wait_for_agent_run_result()`
- When `output_schema` is provided, add it to the final LLM call as a response format constraint

**Step 2: Update `chat()` to become a thin wrapper**

The `chat()` method:
1. Handles @mention routing (keep as-is)
2. Saves the user message
3. Calls `self.run(agent=agent, conversation=conversation, user_message=msg, stream_channel=channel, ...)`
4. The return value is used to finalize the conversation (save final message, record AI usage)

**Step 3: Add `_publish_chunk()` method**

```python
async def _publish_chunk(self, channel: str | None, chunk: ChatStreamChunk) -> None:
    """Publish a streaming chunk to Redis pub/sub for WebSocket delivery."""
    if channel is None:
        return
    from src.core.pubsub import manager
    await manager._publish_to_redis(channel, chunk.model_dump(exclude_none=True))
```

**Step 4: Update `_execute_delegation()` for deep delegation**

Replace the single-turn `llm_client.complete()` call with:

```python
async def _execute_delegation(self, agent_name_slug: str, task_input: dict, budget: dict) -> str:
    """Execute delegation as a full child agent run."""
    # Look up delegated agent
    delegated_agent = ...  # existing lookup logic

    # Calculate child budget
    child_max_iter = min(
        delegated_agent.max_iterations or 50,
        budget["max_iterations"] - budget["iterations_used"],
    )
    child_max_tokens = min(
        delegated_agent.max_token_budget or 100000,
        budget["max_tokens"] - budget["tokens_used"],
    )

    # Enqueue child run synchronously
    child_run_id = await enqueue_agent_run(
        agent_id=str(delegated_agent.id),
        trigger_type="delegation",
        input_data=task_input,
        parent_run_id=self._current_run_id,
        org_id=self._org_id,
        caller_user_id=self._caller_user_id,
        caller_email=self._caller_email,
        caller_name=self._caller_name,
        budget_max_iterations=child_max_iter,
        budget_max_tokens=child_max_tokens,
        sync=True,
    )

    # Wait for result
    result = await wait_for_agent_run_result(child_run_id, timeout=1800)

    # Update parent budget
    if result:
        budget["iterations_used"] += result.get("iterations_used", 0)
        budget["tokens_used"] += result.get("tokens_used", 0)

    return json.dumps(result.get("output", "Delegation failed"))
```

**Step 5: Run existing agent tests**

Run: `./test.sh tests/unit/services/test_agent_executor_tools.py tests/unit/services/test_agent_executor_context.py -v`
Expected: Existing tests still pass (the `chat()` wrapper preserves behavior).

**Step 6: Commit**

```bash
git add api/src/services/agent_executor.py
git commit -m "refactor: extract core agent loop into run() method with budget enforcement and deep delegation"
```

---

## Task 5: AgentRunConsumer — Worker-Side Agent Execution

**Files:**
- Create: `api/src/jobs/consumers/agent_run.py`
- Modify: `api/src/worker/main.py`

**Step 1: Create the consumer**

Create `api/src/jobs/consumers/agent_run.py`:

```python
"""RabbitMQ consumer for agent runs. Processes all agent execution (chat + autonomous)."""
import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.redis_client import redis_client
from src.core.settings import settings
from src.jobs.rabbitmq import BaseConsumer
from src.models.orm.agents import Agent
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.services.agent_executor import AgentExecutor

logger = logging.getLogger(__name__)

QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"


class AgentRunConsumer(BaseConsumer):
    def __init__(self):
        super().__init__(queue_name=QUEUE_NAME, prefetch_count=settings.agent_run_concurrency or 5)
        self._engine = None
        self._session_factory = None

    async def start(self):
        await super().start()
        self._engine = create_async_engine(settings.database_url, pool_size=10)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        logger.info("AgentRunConsumer started")

    async def stop(self):
        if self._engine:
            await self._engine.dispose()
        await super().stop()

    async def process_message(self, body: dict) -> None:
        run_id = body["run_id"]
        agent_id = body["agent_id"]
        trigger_type = body["trigger_type"]
        sync = body.get("sync", False)

        logger.info(f"Processing agent run {run_id} (agent={agent_id}, trigger={trigger_type})")

        # Read full context from Redis
        redis_key = f"{REDIS_PREFIX}:{run_id}:context"
        context_raw = await redis_client.get(redis_key)
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
                    parent_run_id=UUID(context["parent_run_id"]) if context.get("parent_run_id") else None,
                    trigger_type=trigger_type,
                    trigger_source=context.get("trigger_source"),
                    conversation_id=UUID(context["conversation_id"]) if context.get("conversation_id") else None,
                    event_delivery_id=UUID(context["event_delivery_id"]) if context.get("event_delivery_id") else None,
                    input=context.get("input"),
                    output_schema=context.get("output_schema"),
                    status="running",
                    org_id=UUID(context["org_id"]) if context.get("org_id") else None,
                    caller_user_id=context["caller"].get("user_id") if context.get("caller") else None,
                    caller_email=context["caller"].get("email") if context.get("caller") else None,
                    caller_name=context["caller"].get("name") if context.get("caller") else None,
                    budget_max_iterations=context["budget"].get("max_iterations") if context.get("budget") else None,
                    budget_max_tokens=context["budget"].get("max_tokens") if context.get("budget") else None,
                    started_at=datetime.now(timezone.utc),
                )
                db.add(agent_run)
                await db.commit()

                # Determine stream channel for chat runs
                stream_channel = None
                if trigger_type == "chat" and context.get("conversation_id"):
                    stream_channel = f"chat:{context['conversation_id']}"

                # Load conversation for chat mode
                conversation = None
                if trigger_type == "chat" and context.get("conversation_id"):
                    from src.models.orm.agents import Conversation
                    conv_result = await db.execute(
                        select(Conversation).where(Conversation.id == UUID(context["conversation_id"]))
                    )
                    conversation = conv_result.scalar_one_or_none()

                # Run the agent
                executor = AgentExecutor(db)
                run_result = await executor.run(
                    agent=agent,
                    input_data=context.get("input"),
                    output_schema=context.get("output_schema"),
                    conversation=conversation,
                    user_message=context["input"].get("message") if context.get("input") else None,
                    run_id=run_id,
                    budget=context.get("budget"),
                    caller=context.get("caller"),
                    stream_channel=stream_channel,
                )

                # Update run record
                duration_ms = int((time.time() - start_time) * 1000)
                agent_run.status = run_result.get("status", "completed")
                agent_run.output = run_result.get("output")
                agent_run.error = run_result.get("error")
                agent_run.iterations_used = run_result.get("iterations_used", 0)
                agent_run.tokens_used = run_result.get("tokens_used", 0)
                agent_run.llm_model = run_result.get("llm_model")
                agent_run.duration_ms = duration_ms
                agent_run.completed_at = datetime.now(timezone.utc)
                await db.commit()

                # If sync, push result for BLPOP waiter
                if sync:
                    result_key = f"{REDIS_PREFIX}:{run_id}:result"
                    await redis_client.lpush(result_key, json.dumps({
                        "output": run_result.get("output"),
                        "status": run_result.get("status", "completed"),
                        "iterations_used": run_result.get("iterations_used", 0),
                        "tokens_used": run_result.get("tokens_used", 0),
                        "error": run_result.get("error"),
                    }))
                    await redis_client.expire(result_key, 300)

            except Exception as e:
                logger.exception(f"Agent run {run_id} failed: {e}")
                # Update run record with failure
                agent_run.status = "failed"
                agent_run.error = str(e)
                agent_run.duration_ms = int((time.time() - start_time) * 1000)
                agent_run.completed_at = datetime.now(timezone.utc)
                await db.commit()

                if sync:
                    result_key = f"{REDIS_PREFIX}:{run_id}:result"
                    await redis_client.lpush(result_key, json.dumps({
                        "output": None,
                        "status": "failed",
                        "error": str(e),
                    }))
                    await redis_client.expire(result_key, 300)

            finally:
                # Cleanup Redis context
                await redis_client.delete(f"{REDIS_PREFIX}:{run_id}:context")
```

**Step 2: Register consumer in worker**

In `api/src/worker/main.py`, add to the `_start_consumers()` method (around line 88):

```python
from src.jobs.consumers.agent_run import AgentRunConsumer

# In _start_consumers():
self._consumers = [
    WorkflowExecutionConsumer(),
    PackageInstallConsumer(),
    AgentRunConsumer(),  # NEW
]
```

**Step 3: Commit**

```bash
git add api/src/jobs/consumers/agent_run.py api/src/worker/main.py
git commit -m "feat: add AgentRunConsumer for worker-based agent execution"
```

---

## Task 6: Migrate Chat to Worker Path

**Files:**
- Modify: `api/src/routers/websocket.py` (the `_process_chat_message` function, line 542)
- Modify: `api/src/routers/chat.py` (the `send_message` handler, line 314)

**Step 1: Update WebSocket chat handler**

In `api/src/routers/websocket.py`, replace the direct `AgentExecutor.chat()` call in `_process_chat_message` (lines 590-603) with an enqueue call:

```python
# Instead of running executor.chat() inline:
from src.services.execution.agent_run_executor import enqueue_agent_run

run_id = await enqueue_agent_run(
    agent_id=str(conversation.agent_id) if conversation.agent_id else None,
    trigger_type="chat",
    trigger_source=f"user: {user.email}",
    input_data={"message": message, "conversation_id": conversation_id, "local_id": local_id},
    org_id=str(conversation.agent.organization_id) if conversation.agent and conversation.agent.organization_id else None,
    caller_user_id=user.user_id,
    caller_email=user.email,
    caller_name=user.name,
    conversation_id=conversation_id,
)
```

The streaming chunks will now come from the worker via Redis pub/sub to the `chat:{conversation_id}` channel. The WebSocket handler subscribes to this channel (it already subscribes for other events) and forwards chunks to the client.

Update the WebSocket subscription logic to listen for chat chunks on the `chat:{conversation_id}` channel and forward them to the client WebSocket.

**Step 2: Update REST chat handler**

In `api/src/routers/chat.py`, update `send_message` (line 314) to enqueue and wait for result:

```python
from src.services.execution.agent_run_executor import enqueue_agent_run, wait_for_agent_run_result

run_id = await enqueue_agent_run(
    agent_id=str(conversation.agent_id),
    trigger_type="chat",
    input_data={"message": request.message},
    caller_user_id=user.user_id,
    caller_email=user.email,
    caller_name=user.name,
    conversation_id=str(conversation_id),
    sync=True,
)
result = await wait_for_agent_run_result(run_id, timeout=120)
```

**Step 3: Test chat still works**

Start the dev stack with `./debug.sh`, open the chat UI, send a message, verify streaming works.

**Step 4: Commit**

```bash
git add api/src/routers/websocket.py api/src/routers/chat.py
git commit -m "feat: migrate chat to worker-based execution via AgentRunConsumer"
```

---

## Task 7: Event Processor — Dispatch Agent Runs from Events and Schedules

**Files:**
- Modify: `api/src/services/events/processor.py`
- Modify: `api/src/routers/events.py`

**Step 1: Update EventProcessor to dispatch agent runs**

In `api/src/services/events/processor.py`, modify `_queue_workflow_execution()` (line 492) to handle agent targets. Rename to `_queue_execution()` or add a new `_queue_agent_run()` method:

```python
async def _queue_agent_run(self, delivery: EventDelivery, event: Event) -> None:
    """Queue an agent run for an event subscription targeting an agent."""
    subscription = delivery.subscription
    agent = subscription.agent

    # Process input mapping (same as workflow path)
    parameters = {}
    if subscription.input_mapping:
        parameters = self._process_input_mapping(subscription.input_mapping, event)
    else:
        parameters = event.data or {}

    # Always include event context
    parameters["_event"] = {
        "event_id": str(event.id),
        "source_id": str(event.event_source_id),
        "event_type": event.event_type,
        "received_at": event.received_at.isoformat() if event.received_at else None,
    }

    org_id = str(agent.organization_id) if agent.organization_id else None

    from src.services.execution.agent_run_executor import enqueue_agent_run

    run_id = await enqueue_agent_run(
        agent_id=str(agent.id),
        trigger_type="event",
        trigger_source=f"event: {event.event_type or 'webhook'}",
        input_data=parameters,
        org_id=org_id,
        event_delivery_id=str(delivery.id),
    )

    delivery.execution_id = run_id
```

In `queue_event_deliveries()` (line 422), add a branch based on `subscription.target_type`:

```python
if subscription.target_type == "agent":
    await self._queue_agent_run(delivery, event)
else:
    await self._queue_workflow_execution(delivery, event)
```

**Step 2: Update event subscription CRUD**

In `api/src/routers/events.py`, update the subscription creation endpoint to accept `target_type` and `agent_id`. Add validation: if `target_type == "agent"`, require `agent_id` and allow `workflow_id` to be null; if `target_type == "workflow"`, require `workflow_id`.

**Step 3: Write E2E test for agent-targeted event subscription**

Create `api/tests/e2e/platform/test_agent_run_events.py`:

```python
class TestAgentRunFromEvent:
    def test_create_agent_subscription(self, e2e_client, platform_admin, test_agent, test_event_source):
        """Create an event subscription targeting an agent."""
        response = e2e_client.post(
            f"/api/events/sources/{test_event_source.id}/subscriptions",
            json={
                "target_type": "agent",
                "agent_id": str(test_agent.id),
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["target_type"] == "agent"
        assert data["agent_id"] == str(test_agent.id)
        assert data.get("workflow_id") is None
```

**Step 4: Commit**

```bash
git add api/src/services/events/processor.py api/src/routers/events.py api/tests/e2e/platform/test_agent_run_events.py
git commit -m "feat: dispatch agent runs from event subscriptions and schedules"
```

---

## Task 8: SDK `agents.run()` Method

**Files:**
- Create: `api/bifrost/agents.py`
- Modify: `api/bifrost/__init__.py`

**Step 1: Create the SDK module**

Create `api/bifrost/agents.py`:

```python
"""Bifrost SDK — Agent invocation from workflows."""
import json
import logging
from typing import Any

from bifrost._context import get_execution_context
from bifrost._http import sdk_request

logger = logging.getLogger(__name__)


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
        output_schema: JSON Schema for the expected output. When provided,
            the agent's response will be structured to match this schema.
        timeout: Maximum seconds to wait for the result (default 30 min).

    Returns:
        Structured dict if output_schema was provided, otherwise the agent's
        text response as a string.
    """
    ctx = get_execution_context()

    response = await sdk_request(
        "POST",
        "/api/agents/run",
        json={
            "agent_name": agent_name,
            "input": input or {},
            "output_schema": output_schema,
            "timeout": timeout,
        },
    )

    if response.get("error"):
        raise RuntimeError(f"Agent run failed: {response['error']}")

    output = response.get("output")
    if output_schema and isinstance(output, str):
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output
    return output
```

**Step 2: Add API endpoint for SDK agent runs**

Create a new handler in the agents router (or a new file) that:
1. Looks up the agent by name
2. Calls `enqueue_agent_run(sync=True)` with the calling workflow's context
3. Calls `wait_for_agent_run_result()` with the timeout
4. Returns the result

**Step 3: Export from `__init__.py`**

In `api/bifrost/__init__.py`, add `agents` to the imports (around line 85):

```python
from bifrost import agents
```

**Step 4: Commit**

```bash
git add api/bifrost/agents.py api/bifrost/__init__.py
git commit -m "feat: add SDK agents.run() for invoking agents from workflows"
```

---

## Task 9: Agent Runs API Endpoints

**Files:**
- Create: `api/src/routers/agent_runs.py`
- Modify: `api/src/main.py` (register router)

**Step 1: Create the router**

Create `api/src/routers/agent_runs.py` with these endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agent-runs` | List runs with filtering/pagination |
| GET | `/api/agent-runs/{run_id}` | Get run detail with steps and child runs |
| POST | `/api/agent-runs/{run_id}/explain` | Generate reasoning explanation (on-demand LLM call) |

The list endpoint should support query params: `agent_id`, `trigger_type`, `status`, `org_id`, `caller_user_id`, `start_date`, `end_date`, `cursor`, `limit`.

The explain endpoint takes the full run trace (steps), sends it to the LLM with a prompt like "Explain step by step why this agent made these decisions and arrived at this output," caches the result on the run record, and returns it.

**Step 2: Register router**

In `api/src/main.py`, add:

```python
from src.routers.agent_runs import router as agent_runs_router
app.include_router(agent_runs_router)
```

**Step 3: Write E2E tests**

Create `api/tests/e2e/api/test_agent_runs.py` with tests for list, detail, and filtering.

**Step 4: Commit**

```bash
git add api/src/routers/agent_runs.py api/src/main.py api/tests/e2e/api/test_agent_runs.py
git commit -m "feat: add Agent Runs API endpoints with filtering and explain reasoning"
```

---

## Task 10: Update Manifest — agents.yaml Budget Fields

**Files:**
- Modify: `api/src/services/manifest.py`
- Modify: `api/src/services/manifest_generator.py`
- Modify: `api/src/services/github_sync.py`
- Modify: `api/src/services/file_storage/indexers/agent.py`

**Step 1: Update ManifestAgent**

In `api/src/services/manifest.py`, add to `ManifestAgent` (line 87):

```python
class ManifestAgent(BaseModel):
    id: str
    path: str
    organization_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    access_level: str = "role_based"
    max_iterations: int | None = None       # NEW
    max_token_budget: int | None = None     # NEW
    llm_model: str | None = None            # NEW
```

**Step 2: Update manifest generator**

In `api/src/services/manifest_generator.py` (around line 322), include the new fields when serializing:

```python
ManifestAgent(
    id=str(agent.id),
    path=f"agents/{agent.id}.agent.yaml",
    organization_id=str(agent.organization_id) if agent.organization_id else None,
    roles=[str(r) for r in agent_roles_by_agent.get(agent.id, [])],
    access_level=agent.access_level.value if agent.access_level else "role_based",
    max_iterations=agent.max_iterations,
    max_token_budget=agent.max_token_budget,
    llm_model=agent.llm_model,
)
```

**Step 3: Update github_sync.py**

In `_resolve_agent()` (line 2534), add the new fields to the upsert values:

```python
max_iterations=magent.max_iterations,
max_token_budget=magent.max_token_budget,
llm_model=magent.llm_model,
```

**Step 4: Commit**

```bash
git add api/src/services/manifest.py api/src/services/manifest_generator.py api/src/services/github_sync.py
git commit -m "feat: add budget and model fields to agent manifest serialization"
```

---

## Task 11: Frontend — Agent Runs Page (List View)

**Files:**
- Create: `client/src/pages/AgentRuns.tsx`
- Create: `client/src/hooks/useAgentRuns.ts`
- Create: `client/src/services/agent-runs.ts`
- Modify: routing config to add the page

**Step 1: Create API service**

Create `client/src/services/agent-runs.ts` following the pattern in existing service files. Define types from the generated `v1.d.ts` (after running `npm run generate:types`).

**Step 2: Create hook**

Create `client/src/hooks/useAgentRuns.ts` with `useAgentRuns(filters)` and `useAgentRun(runId)` hooks using React Query, following the pattern in `useExecutions.ts`.

**Step 3: Create the page**

Create `client/src/pages/AgentRuns.tsx` following the pattern in `ExecutionHistory.tsx`:
- Filter bar: Agent dropdown, Trigger type, Status, Caller, Org, Date range
- Data table with sortable columns
- Click row → navigate to run detail page
- URL-synced filters via `useSearchParams()`

**Step 4: Add route**

Add the route in the app routing config (wherever routes are defined).

**Step 5: Run frontend checks**

Run: `cd client && npm run tsc && npm run lint`

**Step 6: Commit**

```bash
git add client/src/pages/AgentRuns.tsx client/src/hooks/useAgentRuns.ts client/src/services/agent-runs.ts
git commit -m "feat: add Agent Runs list page with filtering and sorting"
```

---

## Task 12: Frontend — Agent Run Detail View (Activity Map)

**Files:**
- Create: `client/src/pages/AgentRunDetail.tsx`
- Create: `client/src/components/agent-runs/ActivityMap.tsx`
- Create: `client/src/components/agent-runs/ActivityNode.tsx`

**Step 1: Create ActivityNode component**

A card component that renders a single step in the activity map. Different styles per step type:
- `llm_response`: Shows reasoning text, token count, duration, "Show rationale" button
- `tool_call`: Shows tool name, input, links to workflow execution record
- `tool_result`: Shows result data, duration
- `delegation`: Shows child agent name, expandable to show nested ActivityMap
- `budget_warning`: Warning banner
- `error`: Error banner

**Step 2: Create ActivityMap component**

A vertical list of `ActivityNode` components connected by lines (CSS `border-left` or thin SVG). Accepts a list of `AgentRunStep` items and optional child runs.

For delegation steps, render a nested `ActivityMap` inline (expandable/collapsible) using the child run's steps.

**Step 3: Create AgentRunDetail page**

Header: Agent name, trigger, status badge, duration, budget usage (iterations/tokens as progress bars).

Body: `ActivityMap` component with all steps.

Sidebar or header action: "Explain Reasoning" button → calls `/api/agent-runs/{id}/explain`, shows result in a panel.

Link to conversation (for chat-triggered runs).

**Step 4: Add route**

Add route for `/agent-runs/:id`.

**Step 5: Run frontend checks**

Run: `cd client && npm run tsc && npm run lint`

**Step 6: Commit**

```bash
git add client/src/pages/AgentRunDetail.tsx client/src/components/agent-runs/ActivityMap.tsx client/src/components/agent-runs/ActivityNode.tsx
git commit -m "feat: add Agent Run detail page with activity map visualization"
```

---

## Task 13: Frontend — Chat Integration (View Run Details Link)

**Files:**
- Modify: `client/src/components/chat/ChatMessage.tsx`
- Modify: `client/src/hooks/useChatStream.ts`

**Step 1: Update chat streaming to handle run_id**

The worker will now include `run_id` in the `done` chunk. Update `useChatStream.ts` to capture this and store it on the message.

**Step 2: Add "View run details" link to ChatMessage**

When a message has an associated `run_id`, show a small link/button that navigates to `/agent-runs/{run_id}`.

**Step 3: Commit**

```bash
git add client/src/components/chat/ChatMessage.tsx client/src/hooks/useChatStream.ts
git commit -m "feat: add 'View run details' link to chat messages"
```

---

## Task 14: Update Event Subscription UI for Agent Targets

**Files:**
- Modify: `client/src/components/events/CreateSubscriptionDialog.tsx`
- Modify: `client/src/components/events/EventSourceDetail.tsx`

**Step 1: Add target type selector**

In `CreateSubscriptionDialog.tsx`, add a radio/toggle for "Workflow" vs "Agent" target type. When "Agent" is selected, show an agent dropdown instead of the workflow dropdown.

**Step 2: Update subscription display**

In `EventSourceDetail.tsx`, show the agent name when `target_type == "agent"`.

**Step 3: Regenerate types**

Run: `cd client && npm run generate:types`

**Step 4: Run frontend checks**

Run: `cd client && npm run tsc && npm run lint`

**Step 5: Commit**

```bash
git add client/src/components/events/CreateSubscriptionDialog.tsx client/src/components/events/EventSourceDetail.tsx
git commit -m "feat: support agent targets in event subscription UI"
```

---

## Task 15: Agent Settings UI — Budget Fields

**Files:**
- Modify: `client/src/components/agents/AgentDialog.tsx`
- Modify: `client/src/hooks/useAgents.ts`

**Step 1: Add budget fields to AgentDialog**

Add `max_iterations` and `max_token_budget` fields to the agent create/edit dialog. Use number inputs with sensible defaults shown as placeholders (50 iterations, 100000 tokens).

**Step 2: Update mutation**

In `useAgents.ts`, ensure the create/update mutation sends the new fields.

**Step 3: Regenerate types**

Run: `cd client && npm run generate:types`

**Step 4: Commit**

```bash
git add client/src/components/agents/AgentDialog.tsx client/src/hooks/useAgents.ts
git commit -m "feat: add budget configuration to agent settings dialog"
```

---

## Task 16: End-to-End Tests

**Files:**
- Create: `api/tests/e2e/platform/test_autonomous_agent_run.py`

**Step 1: Write comprehensive E2E tests**

```python
class TestAutonomousAgentRun:
    """E2E tests for agent runs triggered by events, SDK, and delegation."""

    def test_agent_run_from_webhook(self):
        """Webhook event → agent subscription → agent run → structured output."""

    def test_agent_run_from_sdk(self):
        """Workflow calls agents.run() → agent executes → returns structured result."""

    def test_agent_delegation(self):
        """Parent agent delegates to child → child runs full loop → result returned to parent."""

    def test_agent_budget_enforcement(self):
        """Agent hits budget soft limit → wraps up gracefully."""

    def test_agent_run_records(self):
        """Verify AgentRun and AgentRunStep records are created correctly."""

    def test_agent_run_list_filtering(self):
        """List endpoint filters by agent, trigger, status, caller."""

    def test_chat_creates_run_record(self):
        """Chat message creates an AgentRun record alongside the conversation."""
```

**Step 2: Run tests**

Run: `./test.sh tests/e2e/platform/test_autonomous_agent_run.py -v`

**Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_autonomous_agent_run.py
git commit -m "test: add E2E tests for autonomous agent runs"
```

---

## Task 17: Pre-Completion Verification

**Step 1: Run full backend checks**

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

**Step 3: Run full frontend checks**

```bash
npm run tsc
npm run lint
```

**Step 4: Run full test suite**

```bash
cd /home/jack/GitHub/bifrost
./test.sh
```

**Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve type errors and lint issues from autonomous agents implementation"
```
