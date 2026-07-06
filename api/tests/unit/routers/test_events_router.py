from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from src.models.contracts.events import DynamicValuesRequest, EmitEventRequest
from src.models.contracts.events import EventSourceCreate, ScheduleSourceConfig
from src.models.enums import (
    EventDeliveryStatus,
    EventSourceType,
    EventStatus,
    ScheduleOverlapPolicy,
)
from src.routers import events


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user() -> SimpleNamespace:
    return SimpleNamespace(user_id=uuid4(), email="admin@example.com")


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(org_id=uuid4(), solution_id=None, user=_user())


def _source(source_type: EventSourceType, **overrides: object) -> SimpleNamespace:
    source = SimpleNamespace(
        id=uuid4(),
        name="Source",
        source_type=source_type,
        event_type=None,
        organization_id=None,
        organization=None,
        is_active=True,
        error_message=None,
        created_by="admin@example.com",
        created_at=_now(),
        updated_at=_now(),
        webhook_source=None,
        schedule_source=None,
    )
    for key, value in overrides.items():
        setattr(source, key, value)
    return source


def _db_execute_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.unique.return_value.scalar_one_or_none.return_value = value
    return result


def _event(source_id=None, **overrides: object) -> SimpleNamespace:
    event = SimpleNamespace(
        id=uuid4(),
        event_source_id=source_id or uuid4(),
        event_source=SimpleNamespace(name="Source"),
        event_type="ticket.created",
        received_at=_now(),
        headers={"x-event": "1"},
        data={"ticket": "T1"},
        source_ip="203.0.113.10",
        status=EventStatus.RECEIVED,
        created_at=_now(),
    )
    for key, value in overrides.items():
        setattr(event, key, value)
    return event


class TestEventResponseBuilders:
    @pytest.mark.asyncio
    async def test_build_event_source_response_includes_webhook_details(self):
        integration_id = uuid4()
        webhook_source = SimpleNamespace(
            adapter_name="generic",
            integration_id=integration_id,
            integration=SimpleNamespace(name="NinjaOne"),
            config={"ticket": "created"},
            external_id="sub-1",
            expires_at=_now(),
            rate_limit_per_minute=30,
            rate_limit_window_seconds=60,
            rate_limit_enabled=True,
        )
        source = _source(
            EventSourceType.WEBHOOK,
            webhook_source=webhook_source,
            organization=SimpleNamespace(name="MTG"),
            organization_id=uuid4(),
        )

        with (
            patch.object(events, "_build_callback_url", return_value="https://hooks/source"),
            patch.object(events, "_get_rate_limited_count", AsyncMock(return_value=2)),
            patch.object(events, "EventSubscriptionRepository") as sub_repo_cls,
            patch.object(events, "EventRepository") as event_repo_cls,
        ):
            sub_repo_cls.return_value.count_by_source = AsyncMock(return_value=4)
            event_repo_cls.return_value.count_by_source = AsyncMock(return_value=9)

            result = await events._build_event_source_response(source, AsyncMock())

        assert result.subscription_count == 4
        assert result.event_count_24h == 9
        assert result.organization_name == "MTG"
        assert result.webhook.adapter_name == "generic"
        assert result.webhook.integration_name == "NinjaOne"
        assert result.webhook.callback_url == "https://hooks/source"
        assert result.webhook.rate_limited_count_24h == 2

    @pytest.mark.asyncio
    async def test_build_event_source_response_includes_schedule_details(self):
        schedule_source = SimpleNamespace(
            cron_expression="0 9 * * *",
            timezone="America/Indianapolis",
            enabled=False,
            overlap_policy=ScheduleOverlapPolicy.REPLACE,
        )
        source = _source(EventSourceType.SCHEDULE, schedule_source=schedule_source)

        with (
            patch.object(events, "EventSubscriptionRepository") as sub_repo_cls,
            patch.object(events, "EventRepository") as event_repo_cls,
        ):
            sub_repo_cls.return_value.count_by_source = AsyncMock(return_value=0)
            event_repo_cls.return_value.count_by_source = AsyncMock(return_value=1)

            result = await events._build_event_source_response(source, AsyncMock())

        assert result.webhook is None
        assert result.schedule.cron_expression == "0 9 * * *"
        assert result.schedule.timezone == "America/Indianapolis"
        assert result.schedule.enabled is False

    @pytest.mark.asyncio
    async def test_build_event_subscription_response_counts_deliveries(self):
        subscription = SimpleNamespace(
            id=uuid4(),
            event_source_id=uuid4(),
            target_type="agent",
            workflow_id=None,
            agent_id=uuid4(),
            agent=SimpleNamespace(name="Dispatcher"),
            workflow=None,
            event_type="ticket.created",
            filter_expression="$.priority == 'high'",
            input_mapping={"ticket": "{{ event.data.id }}"},
            is_active=True,
            created_by="admin@example.com",
            created_at=_now(),
            updated_at=_now(),
        )

        with patch.object(events, "EventDeliveryRepository") as delivery_repo_cls:
            repo = delivery_repo_cls.return_value
            repo.count_by_subscription = AsyncMock(side_effect=[5, 3, 2])

            result = await events._build_event_subscription_response(
                subscription, AsyncMock()
            )

        assert result.target_type == "agent"
        assert result.agent_name == "Dispatcher"
        assert result.delivery_count == 5
        assert result.success_count == 3
        assert result.failed_count == 2
        repo.count_by_subscription.assert_any_await(
            subscription.id, status=EventDeliveryStatus.SUCCESS
        )
        repo.count_by_subscription.assert_any_await(
            subscription.id, status=EventDeliveryStatus.FAILED
        )


class TestWebhookAdapterEndpoints:
    @pytest.mark.asyncio
    async def test_list_adapters_returns_registry_entries(self):
        registry = MagicMock()
        registry.list_adapters.return_value = [
            {
                "name": "generic",
                "display_name": "Generic Webhook",
                "description": "HTTP webhook",
                "requires_integration": None,
                "config_schema": {"type": "object"},
                "supports_renewal": False,
            }
        ]

        with patch.object(events, "get_adapter_registry", return_value=registry):
            result = await events.list_adapters(_ctx(), _user())

        assert result.adapters[0].name == "generic"
        assert result.adapters[0].config_schema == {"type": "object"}

    @pytest.mark.asyncio
    async def test_get_dynamic_values_calls_adapter_with_integration(self):
        integration = SimpleNamespace(id=uuid4())
        result = MagicMock()
        result.scalar_one_or_none.return_value = integration
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        adapter = MagicMock()
        adapter.get_dynamic_values = AsyncMock(
            return_value=[{"value": "p1", "label": "Project 1"}]
        )
        registry = MagicMock()
        registry.get.return_value = adapter
        request = DynamicValuesRequest(
            operation="list_projects",
            integration_id=integration.id,
            current_config={"tenant": "mtg"},
        )

        with patch.object(events, "get_adapter_registry", return_value=registry):
            response = await events.get_dynamic_values(
                "generic", request, _ctx(), _user(), db
            )

        assert response.items == [{"value": "p1", "label": "Project 1"}]
        adapter.get_dynamic_values.assert_awaited_once_with(
            operation="list_projects",
            integration=integration,
            current_config={"tenant": "mtg"},
        )

    @pytest.mark.asyncio
    async def test_get_dynamic_values_maps_adapter_errors_to_http(self):
        registry = MagicMock()
        registry.get.return_value = None
        request = DynamicValuesRequest(operation="missing")

        with patch.object(events, "get_adapter_registry", return_value=registry):
            with pytest.raises(HTTPException) as exc:
                await events.get_dynamic_values(
                    "missing", request, _ctx(), _user(), AsyncMock()
                )

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

        adapter = MagicMock()
        adapter.get_dynamic_values = AsyncMock(
            side_effect=NotImplementedError("not supported")
        )
        registry.get.return_value = adapter

        with patch.object(events, "get_adapter_registry", return_value=registry):
            with pytest.raises(HTTPException) as exc:
                await events.get_dynamic_values(
                    "generic", request, _ctx(), _user(), AsyncMock()
                )

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


class TestTopicEndpoints:
    @pytest.mark.asyncio
    async def test_emit_topic_event_accepts_global_and_org_scopes(self):
        event_id = uuid4()

        with patch.object(
            events, "emit_event", AsyncMock(return_value=(event_id, 3))
        ) as emit_event:
            global_response = await events.emit_topic_event(
                EmitEventRequest(topic="ticket.created", data={"id": "T1"}, scope="GLOBAL"),
                _ctx(),
                _user(),
            )

        assert global_response.event_id == str(event_id)
        assert global_response.subscribers_notified == 3
        emit_event.assert_awaited_once()
        assert emit_event.await_args.kwargs["organization_id"] is None

        org_id = uuid4()
        with patch.object(
            events, "emit_event", AsyncMock(return_value=(event_id, 1))
        ) as emit_event:
            response = await events.emit_topic_event(
                EmitEventRequest(
                    topic="ticket.updated",
                    data={},
                    scope=str(org_id),
                ),
                _ctx(),
                _user(),
            )

        assert response.subscribers_notified == 1
        assert emit_event.await_args.kwargs["organization_id"] == org_id

    @pytest.mark.asyncio
    async def test_emit_topic_event_rejects_invalid_scope(self):
        with pytest.raises(HTTPException) as exc:
            await events.emit_topic_event(
                EmitEventRequest(topic="ticket.created", data={}, scope="not-a-uuid"),
                _ctx(),
                _user(),
            )

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid scope" in exc.value.detail

    @pytest.mark.asyncio
    async def test_list_topics_combines_curated_and_in_use_topics(self):
        db = AsyncMock()

        with patch.object(events, "EventSourceRepository") as repo_cls:
            repo_cls.return_value.get_distinct_topic_types = AsyncMock(
                return_value=["ticket.created", "agent.completed"]
            )

            response = await events.list_topics(db)

        assert response.curated
        assert response.in_use == ["ticket.created", "agent.completed"]


class TestEventListingEndpoints:
    @pytest.mark.asyncio
    async def test_list_events_applies_filters_and_counts_deliveries(self):
        source_id = uuid4()
        source = SimpleNamespace(id=source_id, name="Ticket Source")
        event = _event(source_id=source_id, status=EventStatus.COMPLETED)
        since = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        until = datetime(2026, 1, 3, 3, 4, tzinfo=timezone.utc)
        deliveries = [
            SimpleNamespace(status=EventDeliveryStatus.SUCCESS),
            SimpleNamespace(status=EventDeliveryStatus.FAILED),
            SimpleNamespace(status=EventDeliveryStatus.PENDING),
        ]

        with (
            patch.object(events, "EventSourceRepository") as source_repo_cls,
            patch.object(events, "EventRepository") as event_repo_cls,
            patch.object(events, "EventDeliveryRepository") as delivery_repo_cls,
        ):
            source_repo_cls.return_value.get_by_id = AsyncMock(return_value=source)
            event_repo = event_repo_cls.return_value
            event_repo.get_by_source = AsyncMock(return_value=[event])
            event_repo.count_by_source = AsyncMock(return_value=1)
            delivery_repo_cls.return_value.get_by_event = AsyncMock(
                return_value=deliveries
            )

            response = await events.list_events(
                source_id,
                _ctx(),
                _user(),
                AsyncMock(),
                event_status="completed",
                event_type="ticket.created",
                since=since,
                until=until,
                limit=25,
                offset=5,
            )

        assert response.total == 1
        assert response.items[0].event_source_name == "Ticket Source"
        assert response.items[0].delivery_count == 3
        assert response.items[0].success_count == 1
        assert response.items[0].failed_count == 1
        event_repo.get_by_source.assert_awaited_once_with(
            source_id,
            status=EventStatus.COMPLETED,
            event_type="ticket.created",
            since=since.replace(tzinfo=None),
            until=until.replace(tzinfo=None),
            limit=25,
            offset=5,
        )

    @pytest.mark.asyncio
    async def test_list_events_rejects_missing_source_and_invalid_status(self):
        source_id = uuid4()

        with patch.object(events, "EventSourceRepository") as source_repo_cls:
            source_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc:
                await events.list_events(source_id, _ctx(), _user(), AsyncMock())

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

        with patch.object(events, "EventSourceRepository") as source_repo_cls:
            source_repo_cls.return_value.get_by_id = AsyncMock(
                return_value=SimpleNamespace(id=source_id, name="Source")
            )

            with pytest.raises(HTTPException) as exc:
                await events.list_events(
                    source_id,
                    _ctx(),
                    _user(),
                    AsyncMock(),
                    event_status="not-real",
                )

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid status" in exc.value.detail


class TestEventDeliveryEndpoints:
    @pytest.mark.asyncio
    async def test_get_event_counts_delivery_statuses(self):
        event_id = uuid4()
        event = _event(
            source_id=uuid4(),
            id=event_id,
            event_source=SimpleNamespace(name="Ticket Source"),
            status=EventStatus.FAILED,
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_db_execute_result(event))

        with patch.object(events, "EventDeliveryRepository") as delivery_repo_cls:
            delivery_repo_cls.return_value.get_by_event = AsyncMock(
                return_value=[
                    SimpleNamespace(status=EventDeliveryStatus.SUCCESS),
                    SimpleNamespace(status=EventDeliveryStatus.FAILED),
                ]
            )

            response = await events.get_event(event_id, _ctx(), _user(), db)

        assert response.event_source_name == "Ticket Source"
        assert response.status == EventStatus.FAILED
        assert response.delivery_count == 2
        assert response.success_count == 1
        assert response.failed_count == 1

    @pytest.mark.asyncio
    async def test_get_event_returns_404_when_missing(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_db_execute_result(None))

        with pytest.raises(HTTPException) as exc:
            await events.get_event(uuid4(), _ctx(), _user(), db)

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_list_deliveries_includes_existing_and_not_delivered_subscriptions(self):
        source_id = uuid4()
        event_id = uuid4()
        delivered_subscription_id = uuid4()
        undelivered_subscription_id = uuid4()
        event = _event(source_id=source_id, id=event_id)
        delivery = SimpleNamespace(
            id=uuid4(),
            event_id=event_id,
            event_subscription_id=delivered_subscription_id,
            workflow_id=uuid4(),
            workflow=SimpleNamespace(name="Existing workflow"),
            subscription=SimpleNamespace(
                target_type=None,
                agent_id=None,
                agent=None,
            ),
            execution_id=uuid4(),
            agent_run_id=None,
            status=EventDeliveryStatus.SUCCESS,
            error_message=None,
            attempt_count=2,
            next_retry_at=None,
            completed_at=_now(),
            created_at=_now(),
        )
        undelivered_subscription = SimpleNamespace(
            id=undelivered_subscription_id,
            target_type="agent",
            agent_id=uuid4(),
            agent=SimpleNamespace(name="Dispatcher"),
            workflow_id=None,
            workflow=None,
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_db_execute_result(event))

        with (
            patch.object(events, "EventDeliveryRepository") as delivery_repo_cls,
            patch.object(events, "EventSubscriptionRepository") as sub_repo_cls,
        ):
            delivery_repo_cls.return_value.get_by_event = AsyncMock(
                return_value=[delivery]
            )
            sub_repo_cls.return_value.get_active_for_event = AsyncMock(
                return_value=[
                    SimpleNamespace(id=delivered_subscription_id),
                    undelivered_subscription,
                ]
            )

            response = await events.list_deliveries(event_id, _ctx(), _user(), db)

        assert response.total == 2
        assert response.items[0].status == "success"
        assert response.items[0].workflow_name == "Existing workflow"
        assert response.items[0].target_type == "workflow"
        assert response.items[1].id is None
        assert response.items[1].status == "not_delivered"
        assert response.items[1].target_type == "agent"
        assert response.items[1].agent_name == "Dispatcher"
        sub_repo_cls.return_value.get_active_for_event.assert_awaited_once_with(
            source_id=source_id,
            event_type="ticket.created",
        )

    @pytest.mark.asyncio
    async def test_list_deliveries_returns_404_when_event_missing(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_db_execute_result(None))

        with pytest.raises(HTTPException) as exc:
            await events.list_deliveries(uuid4(), _ctx(), _user(), db)

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_retry_delivery_rejects_missing_or_non_failed_delivery(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_db_execute_result(None))

        with pytest.raises(HTTPException) as exc:
            await events.retry_delivery(uuid4(), _ctx(), _user(), db)

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

        delivery = SimpleNamespace(status=EventDeliveryStatus.SUCCESS)
        db.execute = AsyncMock(return_value=_db_execute_result(delivery))

        with pytest.raises(HTTPException) as exc:
            await events.retry_delivery(uuid4(), _ctx(), _user(), db)

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_retry_delivery_marks_failed_when_queueing_raises(self):
        delivery_id = uuid4()
        event_id = uuid4()
        delivery = SimpleNamespace(
            id=delivery_id,
            event_id=event_id,
            status=EventDeliveryStatus.FAILED,
            error_message="previous failure",
            execution_id=uuid4(),
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_db_execute_result(delivery))

        processor = MagicMock()
        processor.queue_event_deliveries = AsyncMock(side_effect=RuntimeError("queue down"))
        with patch("src.services.events.processor.EventProcessor", return_value=processor):
            response = await events.retry_delivery(delivery_id, _ctx(), _user(), db)

        assert response.delivery_id == delivery_id
        assert response.status == "failed"
        assert response.message == "Failed to queue retry: queue down"
        assert delivery.status == EventDeliveryStatus.FAILED
        assert delivery.error_message == "queue down"
        assert delivery.execution_id is None
        assert db.flush.await_count == 2
        processor.queue_event_deliveries.assert_awaited_once_with(event_id)


class TestEventSourceMutationEndpoints:
    def _db_that_reloads_added_source(self):
        added = []
        db = AsyncMock()

        def add(row):
            if getattr(row, "id", None) is None:
                row.id = uuid4()
            added.append(row)

        db.add = MagicMock(side_effect=add)
        db.flush = AsyncMock()
        result = MagicMock()
        result.unique.return_value.scalar_one.side_effect = lambda: added[0]
        db.execute = AsyncMock(return_value=result)
        return db, added

    @pytest.mark.asyncio
    async def test_create_topic_source_defaults_to_caller_org(self):
        db, added = self._db_that_reloads_added_source()
        ctx = _ctx()
        request = EventSourceCreate(
            name="Ticket Created",
            source_type=EventSourceType.TOPIC,
            event_type="ticket.created",
        )
        response = SimpleNamespace(id="source-response")

        with patch.object(
            events,
            "_build_event_source_response",
            AsyncMock(return_value=response),
        ) as build_response:
            result = await events.create_source(request, ctx, _user(), db)

        assert result is response
        assert added[0].name == "Ticket Created"
        assert added[0].source_type == EventSourceType.TOPIC
        assert added[0].event_type == "ticket.created"
        assert added[0].organization_id == ctx.org_id
        db.flush.assert_awaited_once()
        build_response.assert_awaited_once_with(added[0], db)

    @pytest.mark.asyncio
    async def test_create_topic_source_rejects_missing_or_invalid_topic(self):
        db, _added = self._db_that_reloads_added_source()

        with pytest.raises(HTTPException) as exc:
            await events.create_source(
                EventSourceCreate(
                    name="Bad Topic",
                    source_type=EventSourceType.TOPIC,
                ),
                _ctx(),
                _user(),
                db,
            )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        db.add.assert_not_called()

        with pytest.raises(HTTPException) as exc:
            await events.create_source(
                EventSourceCreate(
                    name="Bad Topic",
                    source_type=EventSourceType.TOPIC,
                    event_type="not valid",
                ),
                _ctx(),
                _user(),
                db,
            )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_create_schedule_source_adds_schedule_row(self):
        db, added = self._db_that_reloads_added_source()
        explicit_org = uuid4()
        request = EventSourceCreate(
            name="Daily Sync",
            source_type=EventSourceType.SCHEDULE,
            organization_id=explicit_org,
            schedule=ScheduleSourceConfig(
                cron_expression="0 9 * * *",
                timezone="America/Indianapolis",
                enabled=True,
                overlap_policy=ScheduleOverlapPolicy.QUEUE,
            ),
        )
        response = SimpleNamespace(id="schedule-response")

        with patch.object(
            events,
            "_build_event_source_response",
            AsyncMock(return_value=response),
        ):
            result = await events.create_source(request, _ctx(), _user(), db)

        assert result is response
        source, schedule = added
        assert source.organization_id == explicit_org
        assert schedule.event_source_id == source.id
        assert schedule.cron_expression == "0 9 * * *"
        assert schedule.timezone == "America/Indianapolis"
        assert schedule.overlap_policy == ScheduleOverlapPolicy.QUEUE
        assert db.flush.await_count == 2

    @pytest.mark.asyncio
    async def test_create_schedule_source_requires_schedule_config(self):
        db, _added = self._db_that_reloads_added_source()

        with pytest.raises(HTTPException) as exc:
            await events.create_source(
                EventSourceCreate(
                    name="Daily Sync",
                    source_type=EventSourceType.SCHEDULE,
                ),
                _ctx(),
                _user(),
                db,
            )

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Schedule configuration required" in exc.value.detail
