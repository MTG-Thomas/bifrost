"""Focused coverage for event contract models."""

from datetime import datetime, timezone
from uuid import uuid4

from src.models.contracts.events import (
    CreateDeliveryRequest,
    DynamicValuesRequest,
    DynamicValuesResponse,
    EmitEventRequest,
    EmitEventResponse,
    EventDeliveryListResponse,
    EventDeliveryResponse,
    EventListResponse,
    EventResponse,
    EventSourceCreate,
    EventSourceListResponse,
    EventSourceResponse,
    EventSubscriptionCreate,
    EventSubscriptionListResponse,
    EventSubscriptionResponse,
    RetryDeliveryRequest,
    RetryDeliveryResponse,
    ScheduleSourceConfig,
    ScheduleSourceResponse,
    TopicRegistryEntry,
    TopicsRegistryResponse,
    WebhookAdapterInfo,
    WebhookAdapterListResponse,
    WebhookReceivedResponse,
    WebhookSourceConfig,
    WebhookSourceResponse,
)
from src.models.enums import EventSourceType, EventStatus, ScheduleOverlapPolicy


def test_event_source_create_defaults_for_webhook_schedule_and_topic():
    integration_id = uuid4()
    webhook = EventSourceCreate(
        name="Halo Webhook",
        source_type=EventSourceType.WEBHOOK,
        webhook=WebhookSourceConfig(
            adapter_name="halo",
            integration_id=integration_id,
            config={"secret": "ref"},
        ),
    )
    schedule = EventSourceCreate(
        name="Daily",
        source_type=EventSourceType.SCHEDULE,
        schedule=ScheduleSourceConfig(cron_expression="0 9 * * *"),
    )
    topic = EventSourceCreate(
        name="Ticket Created",
        source_type=EventSourceType.TOPIC,
        event_type="ticket.created",
    )

    assert webhook.webhook.rate_limit_per_minute == 60
    assert webhook.webhook.rate_limit_enabled is True
    assert webhook.webhook.integration_id == integration_id
    assert schedule.schedule.timezone == "UTC"
    assert schedule.schedule.enabled is True
    assert schedule.schedule.overlap_policy is ScheduleOverlapPolicy.SKIP
    assert topic.event_type == "ticket.created"


def test_event_subscription_defaults_and_mapping_shape():
    workflow_id = uuid4()
    agent_id = uuid4()

    workflow_sub = EventSubscriptionCreate(workflow_id=workflow_id)
    agent_sub = EventSubscriptionCreate(
        target_type="agent",
        agent_id=agent_id,
        input_mapping={"ticket_id": "{{ event.data.id }}"},
    )

    assert workflow_sub.target_type == "workflow"
    assert workflow_sub.workflow_id == workflow_id
    assert agent_sub.agent_id == agent_id
    assert agent_sub.input_mapping == {"ticket_id": "{{ event.data.id }}"}


def test_event_source_response_nests_webhook_schedule_and_counts():
    now = datetime.now(timezone.utc)
    source_id = uuid4()
    response = EventSourceResponse(
        id=source_id,
        name="Daily",
        source_type=EventSourceType.SCHEDULE,
        is_active=True,
        subscription_count=2,
        event_count_24h=5,
        created_by=str(uuid4()),
        created_at=now,
        updated_at=now,
        webhook=WebhookSourceResponse(
            adapter_name="halo",
            callback_url="https://example.test/hooks/abc",
            rate_limited_count_24h=3,
        ),
        schedule=ScheduleSourceResponse(
            cron_expression="0 9 * * *",
            timezone="America/Indianapolis",
            enabled=False,
            overlap_policy=ScheduleOverlapPolicy.REPLACE,
        ),
    )
    listed = EventSourceListResponse(items=[response], total=1)

    assert listed.items[0].id == source_id
    assert listed.items[0].webhook.callback_url.endswith("/hooks/abc")
    assert listed.items[0].webhook.rate_limited_count_24h == 3
    assert listed.items[0].schedule.enabled is False
    assert listed.items[0].schedule.overlap_policy is ScheduleOverlapPolicy.REPLACE


def test_event_subscription_event_and_delivery_responses():
    now = datetime.now(timezone.utc)
    event_id = uuid4()
    subscription_id = uuid4()
    source_id = uuid4()
    workflow_id = uuid4()
    subscription = EventSubscriptionResponse(
        id=subscription_id,
        event_source_id=source_id,
        workflow_id=workflow_id,
        workflow_name="Triage",
        is_active=True,
        delivery_count=4,
        success_count=3,
        failed_count=1,
        created_by=str(uuid4()),
        created_at=now,
        updated_at=now,
    )
    event = EventResponse(
        id=event_id,
        event_source_id=source_id,
        event_source_name="Halo",
        event_type="ticket.created",
        received_at=now,
        headers={"x-request-id": "abc"},
        data={"id": "T1"},
        status=EventStatus.COMPLETED,
        success_count=1,
        created_at=now,
    )
    delivery = EventDeliveryResponse(
        event_id=event_id,
        event_subscription_id=subscription_id,
        workflow_id=workflow_id,
        workflow_name="Triage",
        status="success",
        attempt_count=1,
        completed_at=now,
    )

    assert EventSubscriptionListResponse(items=[subscription], total=1).total == 1
    assert EventListResponse(items=[event], total=1).items[0].data == {"id": "T1"}
    assert EventDeliveryListResponse(items=[delivery], total=1).items[0].id is None
    assert delivery.created_at is None


def test_webhook_adapter_dynamic_values_emit_and_topic_models():
    integration_id = uuid4()
    adapter = WebhookAdapterInfo(
        name="halo",
        display_name="Halo",
        requires_integration="Halo",
        config_schema={"type": "object"},
        supports_renewal=True,
    )
    dynamic_request = DynamicValuesRequest(
        operation="list_boards",
        integration_id=integration_id,
        current_config={"tenant": "mtg"},
    )
    topic = TopicRegistryEntry(
        topic="ticket.created",
        description="Ticket created",
        category="tickets",
        emitted_by="Halo adapter",
        example_body={"id": "T1"},
    )

    assert WebhookAdapterListResponse(adapters=[adapter]).adapters[0].supports_renewal
    assert dynamic_request.current_config == {"tenant": "mtg"}
    assert DynamicValuesResponse(items=[{"value": "1", "label": "Board"}]).items[0]["label"] == "Board"
    assert EmitEventRequest(topic="ticket.created").data == {}
    assert EmitEventResponse(event_id=str(uuid4()), subscribers_notified=2).subscribers_notified == 2
    assert TopicsRegistryResponse(curated=[topic], in_use=["ticket.created"]).curated[0].example_body == {
        "id": "T1"
    }


def test_delivery_command_models_and_default_empty_requests():
    delivery_id = uuid4()
    subscription_id = uuid4()

    assert RetryDeliveryRequest().model_dump() == {}
    assert CreateDeliveryRequest(subscription_id=subscription_id).subscription_id == subscription_id
    assert RetryDeliveryResponse(
        delivery_id=delivery_id,
        status="queued",
        message="retry queued",
    ).message == "retry queued"
    assert WebhookReceivedResponse(event_id=uuid4(), subscriptions=3).status == "accepted"
