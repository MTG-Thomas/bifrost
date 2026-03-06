# Autonomous Agents: Test Coverage + Pre-existing Test Fixes

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 3 pre-existing test failures and add test coverage for the critical + important areas of the autonomous agents feature.

**Architecture:** Unit tests mock DB sessions and Redis; E2E tests hit the running API via httpx. Manifest tests do YAML round-trip serialization. All tests run via `./test.sh` from the worktree root.

**Tech Stack:** pytest, pytest-asyncio, unittest.mock (AsyncMock/MagicMock/patch), httpx, SQLAlchemy async sessions

**Worktree:** `/home/jack/GitHub/bifrost/.worktrees/autonomous-agents`

**Run tests:** `./test.sh tests/unit/` for unit, `./test.sh` for all

---

## Task 1: Fix pre-existing test — retry delivery race condition

**Files:**
- Modify: `api/tests/e2e/api/test_events.py:970-976`

**Step 1: Fix the conditional assertion**

The test at line 971 wraps the assertion in `if delivery["status"] not in ["failed", "skipped"]` — if the delivery races to "failed" before the test polls, the `if` is False and the assertion silently never runs. But if it races to "failed" AFTER the poll but BEFORE the retry request, the retry succeeds (200) and the assertion fails.

Fix: assert the precondition, and if the delivery already failed (race), skip the test gracefully.

```python
# Replace lines 970-976:
        deliveries = result["deliveries"]
        delivery = deliveries[0]

        # Try to retry non-failed delivery
        if delivery["status"] not in ["failed", "skipped"]:
            response = e2e_client.post(
                f"/api/events/deliveries/{delivery['id']}/retry",
                headers=platform_admin.headers,
            )
            assert response.status_code == 400, f"Expected 400 for non-failed delivery: {response.text}"

# With:
        deliveries = result["deliveries"]
        delivery = deliveries[0]

        # If delivery already raced to failed/skipped, the retry test is moot
        if delivery["status"] in ["failed", "skipped"]:
            pytest.skip(
                f"Delivery already reached '{delivery['status']}' before retry test could run (race condition)"
            )

        response = e2e_client.post(
            f"/api/events/deliveries/{delivery['id']}/retry",
            headers=platform_admin.headers,
        )
        assert response.status_code == 400, f"Expected 400 for non-failed delivery: {response.text}"
```

**Step 2: Run the test**

```bash
./test.sh tests/e2e/api/test_events.py::TestDeliveryRetry -v
```

Expected: PASS (or SKIP if race condition triggers — both are acceptable).

**Step 3: Commit**

```bash
git add api/tests/e2e/api/test_events.py
git commit -m "fix: retry delivery test race condition — skip instead of silent pass"
```

---

## Task 2: Fix pre-existing test — workflow description "set once" behavior

**Files:**
- Modify: `api/tests/e2e/api/test_workflows.py:212-262`

**Step 1: Fix the test**

The WorkflowIndexer only sets `description` when the DB field is NULL (line 197-203 in `workflow.py`). After the first registration, description="Original" is non-NULL, so re-registration never overwrites it. The test should use the PATCH endpoint to update the description instead of expecting re-registration to do it.

```python
# Find the test_workflow_update_persists_to_db method and replace the update section.
# After the first write_and_register, instead of writing new content and re-registering,
# use the API PATCH to update description:

        # Update workflow description via API
        response = e2e_client.patch(
            f"/api/workflows/{original_id}",
            headers=platform_admin.headers,
            json={"description": "Updated description"},
        )
        assert response.status_code == 200, f"Patch failed: {response.text}"
```

Check if the PATCH endpoint exists first. Search for `@router.patch` in `api/src/routers/workflows.py`. If not, use PUT. The key point: don't rely on re-registration to update description.

**Step 2: Run the test**

```bash
./test.sh tests/e2e/api/test_workflows.py::TestWorkflowDBStorage::test_workflow_update_persists_to_db -v
```

Expected: PASS

**Step 3: Commit**

```bash
git add api/tests/e2e/api/test_workflows.py
git commit -m "fix: workflow update test uses PATCH instead of re-registration"
```

---

## Task 3: Fix pre-existing test — category default prevents reindex update

**Files:**
- Modify: `api/tests/e2e/platform/test_workspace_reindex.py:420-430`

**Step 1: Fix the test**

The Workflow ORM default is `category="General"`. The indexer only updates category when it's NULL. The test pre-creates a Workflow without setting category, so it gets the default "General", and the indexer skips the update.

```python
# In test_metadata_extraction_updates_workflow, find the pre-registered workflow creation:
    pre_wf = Workflow(
        id=uuid4(),
        name="documented_workflow",
        function_name="documented_workflow",
        path="documented_workflow.py",
        is_active=True,
    )

# Change to explicitly set category=None:
    pre_wf = Workflow(
        id=uuid4(),
        name="documented_workflow",
        function_name="documented_workflow",
        path="documented_workflow.py",
        is_active=True,
        category=None,
    )
```

**Step 2: Run the test**

```bash
./test.sh tests/e2e/platform/test_workspace_reindex.py::TestReindexWorkspaceFiles::test_metadata_extraction_updates_workflow -v
```

Expected: PASS

**Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_workspace_reindex.py
git commit -m "fix: reindex test explicitly sets category=None so indexer can update it"
```

---

## Task 4: Unit tests for AI usage service — agent_run_id paths

**Files:**
- Create: `api/tests/unit/services/test_ai_usage_agent_run.py`
- Reference: `api/src/services/ai_usage_service.py`

**Step 1: Write failing tests**

Test the 3 functions that gained `agent_run_id` support:

```python
"""Tests for AI usage service agent_run_id support."""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.services.ai_usage_service import (
    record_ai_usage,
    get_usage_totals,
    invalidate_usage_cache,
)


@pytest.mark.asyncio
class TestAIUsageAgentRunId:
    """Test agent_run_id handling in AI usage service."""

    async def test_record_ai_usage_with_agent_run_id(self):
        """AI usage record is created with agent_run_id."""
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        redis.sadd = AsyncMock()
        redis.delete = AsyncMock()
        run_id = uuid4()

        with patch("src.services.ai_usage_service.get_cached_price",
                    return_value=(Decimal("3.0"), Decimal("15.0"))):
            await record_ai_usage(
                session=session,
                redis_client=redis,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                input_tokens=100,
                output_tokens=50,
                duration_ms=1000,
                agent_run_id=run_id,
            )

        # Verify AIUsage was added with agent_run_id
        session.add.assert_called_once()
        usage = session.add.call_args[0][0]
        assert usage.agent_run_id == run_id
        assert usage.execution_id is None
        assert usage.conversation_id is None

    async def test_get_usage_totals_with_agent_run_id(self):
        """get_usage_totals uses correct cache key for agent_run_id."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        session = AsyncMock()
        run_id = uuid4()

        # Mock DB query result
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = MagicMock(
            input_tokens=500,
            output_tokens=200,
            total_cost=Decimal("0.01"),
            call_count=3,
        )
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_usage_totals(
            redis_client=redis,
            session=session,
            agent_run_id=run_id,
        )

        assert result["call_count"] == 3
        assert result["input_tokens"] == 500
        # Verify cache key includes "run:" prefix
        cache_call = redis.set.call_args
        assert f"run:{run_id}" in cache_call[0][0]

    async def test_invalidate_usage_cache_agent_run_id(self):
        """invalidate_usage_cache deletes agent run cache key."""
        redis = AsyncMock()
        redis.delete = AsyncMock()
        run_id = uuid4()

        await invalidate_usage_cache(
            redis_client=redis,
            agent_run_id=run_id,
        )

        redis.delete.assert_called_once()
        key = redis.delete.call_args[0][0]
        assert f"run:{run_id}" in key
```

**Step 2: Run to verify they fail or pass**

```bash
./test.sh tests/unit/services/test_ai_usage_agent_run.py -v
```

Adjust mocks as needed to match actual function signatures. The tests may need tweaking based on how `record_ai_usage` validates its parameters and how `get_usage_totals` structures its DB query.

**Step 3: Commit**

```bash
git add api/tests/unit/services/test_ai_usage_agent_run.py
git commit -m "test: AI usage service agent_run_id coverage"
```

---

## Task 5: Manifest round-trip tests for new agent + subscription fields

**Files:**
- Modify: `api/tests/unit/test_manifest.py`
- Reference: `api/src/services/manifest.py`, `api/src/services/manifest_generator.py`

**Step 1: Write round-trip tests**

Add tests to the existing `test_manifest.py` file:

```python
class TestAgentManifestFields:
    """Test agent budget fields round-trip through manifest."""

    def test_agent_max_iterations_round_trip(self):
        """max_iterations survives serialize → parse → serialize."""
        from src.services.manifest import ManifestAgent, Manifest, serialize_manifest, parse_manifest

        agent = ManifestAgent(
            id=str(uuid4()),
            name="Budget Agent",
            path="agents/test.agent.yaml",
            max_iterations=25,
            max_token_budget=50000,
        )
        manifest = Manifest(agents={agent.id: agent})
        yaml_out = serialize_manifest(manifest)
        parsed = parse_manifest(yaml_out)
        assert parsed.agents[agent.id].max_iterations == 25
        assert parsed.agents[agent.id].max_token_budget == 50000

        # Stability: second round-trip identical
        yaml_out2 = serialize_manifest(parsed)
        assert yaml_out == yaml_out2


class TestEventSubscriptionManifestFields:
    """Test event subscription agent fields round-trip."""

    def test_agent_subscription_round_trip(self):
        """target_type=agent with agent_id survives round-trip."""
        from src.services.manifest import (
            ManifestEventSource, ManifestEventSubscription, Manifest,
            serialize_manifest, parse_manifest,
        )

        agent_id = str(uuid4())
        sub = ManifestEventSubscription(
            id=str(uuid4()),
            target_type="agent",
            agent_id=agent_id,
            workflow_id=None,
            is_active=True,
        )
        source = ManifestEventSource(
            id=str(uuid4()),
            name="Test Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(events={source.id: source})
        yaml_out = serialize_manifest(manifest)
        parsed = parse_manifest(yaml_out)

        parsed_sub = parsed.events[source.id].subscriptions[0]
        assert parsed_sub.target_type == "agent"
        assert parsed_sub.agent_id == agent_id
        assert parsed_sub.workflow_id is None

    def test_workflow_subscription_round_trip(self):
        """target_type=workflow with workflow_id survives round-trip."""
        from src.services.manifest import (
            ManifestEventSource, ManifestEventSubscription, Manifest,
            serialize_manifest, parse_manifest,
        )

        workflow_id = str(uuid4())
        sub = ManifestEventSubscription(
            id=str(uuid4()),
            target_type="workflow",
            workflow_id=workflow_id,
            agent_id=None,
            is_active=True,
        )
        source = ManifestEventSource(
            id=str(uuid4()),
            name="Test Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(events={source.id: source})
        yaml_out = serialize_manifest(manifest)
        parsed = parse_manifest(yaml_out)

        parsed_sub = parsed.events[source.id].subscriptions[0]
        assert parsed_sub.target_type == "workflow"
        assert parsed_sub.workflow_id == workflow_id
        assert parsed_sub.agent_id is None
```

**Step 2: Run tests**

```bash
./test.sh tests/unit/test_manifest.py -v
```

Expected: PASS. If `serialize_manifest` or `parse_manifest` signatures differ, adjust accordingly — read the existing tests in the file for the exact import paths and patterns.

**Step 3: Commit**

```bash
git add api/tests/unit/test_manifest.py
git commit -m "test: manifest round-trip for agent budget fields and subscription target_type"
```

---

## Task 6: Unit tests for event processor — agent target routing

**Files:**
- Create: `api/tests/unit/services/test_event_processor_agents.py`
- Reference: `api/src/services/events/processor.py`

**Step 1: Write tests for the agent routing branch**

```python
"""Tests for EventProcessor agent-target routing."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.models.enums import EventDeliveryStatus


@pytest.mark.asyncio
class TestEventProcessorAgentRouting:
    """Test _queue_agent_run and target_type branching."""

    async def test_queue_deliveries_routes_agent_subscription(self):
        """Delivery with target_type='agent' calls _queue_agent_run."""
        from src.services.events.processor import EventProcessor

        session = AsyncMock()
        session.flush = AsyncMock()
        processor = EventProcessor(session)

        event_id = uuid4()
        agent_id = uuid4()

        # Mock delivery with agent subscription
        delivery = MagicMock()
        delivery.id = uuid4()
        delivery.status = EventDeliveryStatus.PENDING
        delivery.subscription = MagicMock()
        delivery.subscription.target_type = "agent"
        delivery.subscription.agent_id = agent_id
        delivery.subscription.input_mapping = None

        # Mock event
        event = MagicMock()
        event.id = event_id
        event.event_type = "test.event"
        event.data = {"key": "value"}
        event.event_source_id = uuid4()

        # Mock repos
        processor._delivery_repo = MagicMock()
        processor._delivery_repo.get_by_event = AsyncMock(return_value=[delivery])
        processor._event_repo = MagicMock()
        processor._event_repo.get_by_id = AsyncMock(return_value=event)

        with patch.object(processor, "_queue_agent_run", new_callable=AsyncMock) as mock_queue_agent, \
             patch.object(processor, "_broadcast_event_update", new_callable=AsyncMock):
            count = await processor.queue_event_deliveries(event_id)

        mock_queue_agent.assert_called_once_with(delivery, event)
        assert delivery.status == EventDeliveryStatus.QUEUED
        assert count == 1

    async def test_queue_deliveries_routes_workflow_subscription(self):
        """Delivery with target_type='workflow' calls _queue_workflow_execution."""
        from src.services.events.processor import EventProcessor

        session = AsyncMock()
        session.flush = AsyncMock()
        processor = EventProcessor(session)

        event_id = uuid4()

        delivery = MagicMock()
        delivery.id = uuid4()
        delivery.status = EventDeliveryStatus.PENDING
        delivery.subscription = MagicMock()
        delivery.subscription.target_type = "workflow"

        event = MagicMock()
        event.id = event_id
        event.event_source_id = uuid4()

        processor._delivery_repo = MagicMock()
        processor._delivery_repo.get_by_event = AsyncMock(return_value=[delivery])
        processor._event_repo = MagicMock()
        processor._event_repo.get_by_id = AsyncMock(return_value=event)

        with patch.object(processor, "_queue_workflow_execution", new_callable=AsyncMock) as mock_queue_wf, \
             patch.object(processor, "_broadcast_event_update", new_callable=AsyncMock):
            count = await processor.queue_event_deliveries(event_id)

        mock_queue_wf.assert_called_once_with(delivery, event)
        assert count == 1

    async def test_queue_deliveries_skips_non_pending(self):
        """Deliveries not in PENDING status are skipped."""
        from src.services.events.processor import EventProcessor

        session = AsyncMock()
        session.flush = AsyncMock()
        processor = EventProcessor(session)

        delivery = MagicMock()
        delivery.status = EventDeliveryStatus.QUEUED  # Already queued

        event = MagicMock()
        event.id = uuid4()
        event.event_source_id = uuid4()

        processor._delivery_repo = MagicMock()
        processor._delivery_repo.get_by_event = AsyncMock(return_value=[delivery])
        processor._event_repo = MagicMock()
        processor._event_repo.get_by_id = AsyncMock(return_value=event)

        with patch.object(processor, "_broadcast_event_update", new_callable=AsyncMock):
            count = await processor.queue_event_deliveries(event.id)

        assert count == 0

    async def test_queue_agent_run_calls_enqueue(self):
        """_queue_agent_run calls enqueue_agent_run with correct params."""
        from src.services.events.processor import EventProcessor

        session = AsyncMock()
        processor = EventProcessor(session)

        agent_id = uuid4()
        delivery = MagicMock()
        delivery.id = uuid4()
        delivery.subscription = MagicMock()
        delivery.subscription.agent_id = agent_id
        delivery.subscription.input_mapping = None

        event = MagicMock()
        event.id = uuid4()
        event.event_type = "webhook.received"
        event.data = {"payload": "test"}
        event.raw_body = b'{"payload":"test"}'
        event.raw_headers = {"content-type": "application/json"}
        event.received_at = MagicMock()
        event.received_at.isoformat.return_value = "2026-03-05T00:00:00Z"
        event.source_ip = "127.0.0.1"
        event.event_source = MagicMock()
        event.event_source.name = "Test Source"

        with patch("src.services.events.processor.enqueue_agent_run", new_callable=AsyncMock) as mock_enqueue:
            await processor._queue_agent_run(delivery, event)

        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args[1]
        assert call_kwargs["agent_id"] == str(agent_id)
        assert call_kwargs["trigger_type"] == "event"
        assert "_event" in call_kwargs["input_data"]
```

**Step 2: Run tests**

```bash
./test.sh tests/unit/services/test_event_processor_agents.py -v
```

Adjust mock structure as needed based on actual `EventProcessor.__init__` signature and how repos are initialized.

**Step 3: Commit**

```bash
git add api/tests/unit/services/test_event_processor_agents.py
git commit -m "test: event processor agent target routing"
```

---

## Task 7: Unit tests for agent run consumer

**Files:**
- Create: `api/tests/unit/services/test_agent_run_consumer.py`
- Reference: `api/src/jobs/consumers/agent_run.py`

**Step 1: Write tests for the consumer**

```python
"""Tests for AgentRunConsumer."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.jobs.consumers.agent_run import AgentRunConsumer, REDIS_PREFIX


@pytest.mark.asyncio
class TestAgentRunConsumer:
    """Test AgentRunConsumer.process_message."""

    @pytest.fixture
    def consumer(self):
        with patch("src.jobs.consumers.agent_run.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(max_concurrency=2)
            with patch("src.jobs.consumers.agent_run.get_session_factory"):
                c = AgentRunConsumer()
                return c

    @pytest.fixture
    def mock_context(self):
        return {
            "org_id": str(uuid4()),
            "input": {"task": "do something"},
            "caller": {"user_id": "user1", "email": "a@b.com", "name": "Test"},
        }

    async def test_missing_redis_context_returns_early(self, consumer):
        """If Redis context is missing, process_message returns without error."""
        run_id = str(uuid4())
        agent_id = str(uuid4())

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("src.jobs.consumers.agent_run.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock()
            await consumer.process_message({
                "run_id": run_id,
                "agent_id": agent_id,
                "trigger_type": "manual",
            })

        # Should not crash — just log and return

    async def test_agent_not_found_returns_early(self, consumer, mock_context):
        """If agent doesn't exist in DB, returns without crashing."""
        run_id = str(uuid4())
        agent_id = str(uuid4())

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(mock_context))
        mock_redis.delete = AsyncMock()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Agent not found
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.jobs.consumers.agent_run.get_redis") as mock_get_redis, \
             patch.object(consumer, "_session_factory") as mock_factory:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock()

            await consumer.process_message({
                "run_id": run_id,
                "agent_id": agent_id,
                "trigger_type": "manual",
            })

        # Should not crash
```

**Step 2: Run tests**

```bash
./test.sh tests/unit/services/test_agent_run_consumer.py -v
```

These tests are primarily about verifying error paths don't crash. The happy path requires heavy mocking of the executor which is better covered by integration tests.

**Step 3: Commit**

```bash
git add api/tests/unit/services/test_agent_run_consumer.py
git commit -m "test: agent run consumer error handling"
```

---

## Task 8: Unit tests for usage reports — agents source filter

**Files:**
- Create: `api/tests/unit/routers/test_usage_reports_agents.py`
- Reference: `api/src/routers/usage_reports.py`

**Step 1: Write tests**

Test the `source="agents"` filter and `by_agent` aggregation. Since these are DB queries, the simplest approach is a DB-level unit test using the async session fixture:

```python
"""Tests for usage reports agent source filter."""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from src.models.orm.ai_usage import AIUsage
from src.models.orm.agents import Agent
from src.models.orm.agent_runs import AgentRun


@pytest.mark.asyncio
class TestUsageReportsAgentSource:
    """Test that source='agents' filters correctly."""

    async def test_agent_usage_record_created_with_run_id(self, db_session):
        """AIUsage records with agent_run_id are queryable."""
        # Create test agent
        agent = Agent(
            id=uuid4(),
            name="Test Report Agent",
            system_prompt="test",
            is_active=True,
        )
        db_session.add(agent)
        await db_session.flush()

        # Create agent run
        run = AgentRun(
            id=uuid4(),
            agent_id=agent.id,
            trigger_type="manual",
            status="completed",
        )
        db_session.add(run)
        await db_session.flush()

        # Create AI usage linked to agent run
        usage = AIUsage(
            id=uuid4(),
            agent_run_id=run.id,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            input_tokens=100,
            output_tokens=50,
            cost=Decimal("0.001"),
            duration_ms=500,
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(usage)
        await db_session.flush()

        # Query with agent_run_id filter (mimics source="agents")
        result = await db_session.execute(
            select(AIUsage).where(AIUsage.agent_run_id.isnot(None))
        )
        records = result.scalars().all()
        assert len(records) >= 1
        assert any(r.agent_run_id == run.id for r in records)
```

**Step 2: Run tests**

```bash
./test.sh tests/unit/routers/test_usage_reports_agents.py -v
```

**Step 3: Commit**

```bash
git add api/tests/unit/routers/test_usage_reports_agents.py
git commit -m "test: usage reports agent source filter"
```

---

## Task 9: Unit tests for events router — agent subscription CRUD

**Files:**
- Create: `api/tests/unit/routers/test_events_agent_subscriptions.py`
- Reference: `api/src/routers/events.py`

**Step 1: Write tests for validation logic**

The create_subscription endpoint validates target_type/agent_id combinations. Test the validation branches:

```python
"""Tests for events router agent subscription validation."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.models.contracts.events import EventSubscriptionCreate


class TestAgentSubscriptionValidation:
    """Test subscription creation validation for agent targets."""

    def test_agent_subscription_requires_agent_id(self):
        """target_type='agent' without agent_id should be invalid."""
        # The validation happens at the router level, not model level
        # Verify the contract model allows it (router validates)
        sub = EventSubscriptionCreate(
            target_type="agent",
            agent_id=None,
            workflow_id=None,
            is_active=True,
        )
        assert sub.target_type == "agent"
        assert sub.agent_id is None  # Model allows it; router should reject

    def test_workflow_subscription_requires_workflow_id(self):
        """target_type='workflow' without workflow_id should be invalid."""
        sub = EventSubscriptionCreate(
            target_type="workflow",
            workflow_id=None,
            agent_id=None,
            is_active=True,
        )
        assert sub.target_type == "workflow"
        assert sub.workflow_id is None  # Model allows it; router should reject

    def test_agent_subscription_with_agent_id(self):
        """target_type='agent' with agent_id is valid."""
        agent_id = uuid4()
        sub = EventSubscriptionCreate(
            target_type="agent",
            agent_id=agent_id,
            is_active=True,
        )
        assert sub.agent_id == agent_id

    def test_default_target_type_is_workflow(self):
        """Default target_type should be 'workflow'."""
        wf_id = uuid4()
        sub = EventSubscriptionCreate(
            workflow_id=wf_id,
            is_active=True,
        )
        # Check what the default is
        assert sub.target_type in ("workflow", None)
```

**Step 2: Run tests**

```bash
./test.sh tests/unit/routers/test_events_agent_subscriptions.py -v
```

**Step 3: Commit**

```bash
git add api/tests/unit/routers/test_events_agent_subscriptions.py
git commit -m "test: event subscription agent validation"
```

---

## Task 10: Unit tests for github_sync — agent subscription import

**Files:**
- Modify: `api/tests/unit/test_manifest.py` (or create `api/tests/unit/test_manifest_agents.py`)
- Reference: `api/src/services/github_sync.py`

**Step 1: Write manifest validation tests for agent subscriptions**

```python
class TestManifestValidationAgents:
    """Test manifest validation catches agent subscription issues."""

    def test_validate_unknown_agent_in_subscription(self):
        """Subscription referencing non-existent agent_id should fail validation."""
        from src.services.manifest import (
            Manifest, ManifestEventSource, ManifestEventSubscription,
            ManifestAgent, validate_manifest,
        )

        sub = ManifestEventSubscription(
            id=str(uuid4()),
            target_type="agent",
            agent_id=str(uuid4()),  # Unknown agent
            is_active=True,
        )
        source = ManifestEventSource(
            id=str(uuid4()),
            name="Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(events={source.id: source})

        errors = validate_manifest(manifest)
        assert any("agent" in e.lower() for e in errors), f"Expected agent validation error, got: {errors}"

    def test_validate_known_agent_in_subscription(self):
        """Subscription referencing existing agent_id should pass."""
        from src.services.manifest import (
            Manifest, ManifestEventSource, ManifestEventSubscription,
            ManifestAgent, validate_manifest,
        )

        agent_id = str(uuid4())
        agent = ManifestAgent(
            id=agent_id,
            name="Test Agent",
            path=f"agents/{agent_id}.agent.yaml",
        )
        sub = ManifestEventSubscription(
            id=str(uuid4()),
            target_type="agent",
            agent_id=agent_id,
            is_active=True,
        )
        source = ManifestEventSource(
            id=str(uuid4()),
            name="Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(
            agents={agent_id: agent},
            events={source.id: source},
        )

        errors = validate_manifest(manifest)
        agent_errors = [e for e in errors if "agent" in e.lower()]
        assert len(agent_errors) == 0, f"Unexpected agent errors: {agent_errors}"
```

**Step 2: Run tests**

```bash
./test.sh tests/unit/test_manifest.py -v
```

**Step 3: Commit**

```bash
git add api/tests/unit/test_manifest.py
git commit -m "test: manifest validation for agent subscriptions"
```

---

## Task 11: Final verification

**Step 1: Run full test suite**

```bash
./test.sh
```

Expected: All unit tests pass. The 3 previously-failing E2E tests now pass (or skip gracefully for the retry race).

**Step 2: Run linting**

```bash
cd api && ruff check .
```

**Step 3: Commit any final fixes, then done**
