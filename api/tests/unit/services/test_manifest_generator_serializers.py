"""Serializer coverage for manifest generation helpers."""

from types import SimpleNamespace

from src.services import manifest_generator


ORG_ID = "11111111-1111-1111-1111-111111111111"
ROLE_ID = "22222222-2222-2222-2222-222222222222"
WORKFLOW_ID = "33333333-3333-3333-3333-333333333333"
FORM_ID = "44444444-4444-4444-4444-444444444444"
AGENT_ID = "55555555-5555-5555-5555-555555555555"
APP_ID = "66666666-6666-6666-6666-666666666666"
INTEGRATION_ID = "77777777-7777-7777-7777-777777777777"
CONFIG_ID = "88888888-8888-8888-8888-888888888888"
CLAIM_ID = "99999999-9999-9999-9999-999999999999"
RULE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TABLE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
FILE_POLICY_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
EVENT_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
SUB_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
MCP_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"
MCP_CONN_ID = "12121212-1212-1212-1212-121212121212"


def test_basic_manifest_serializers_preserve_portable_fields():
    org = manifest_generator.serialize_organization(
        SimpleNamespace(id=ORG_ID, name="Midtown", is_active=False)
    )
    role = manifest_generator.serialize_role(SimpleNamespace(id=ROLE_ID, name="Operator"))
    workflow = manifest_generator.serialize_workflow(
        SimpleNamespace(
            id=WORKFLOW_ID,
            name="Do Work",
            path="workflows/do_work.py",
            function_name="do_work",
            type=None,
            description="Run a task",
            tool_description=None,
            organization_id=ORG_ID,
            access_level=None,
            endpoint_enabled=True,
            timeout_seconds=0,
            public_endpoint=True,
            category=None,
            tags=["ops"],
        ),
        roles=[ROLE_ID],
    )

    assert org.name == "Midtown"
    assert org.is_active is False
    assert role.id == ROLE_ID
    assert workflow.type == "workflow"
    assert workflow.timeout_seconds == 0
    assert workflow.category == "General"
    assert workflow.roles == [ROLE_ID]


def test_form_serializer_inlines_ordered_field_schema():
    form = SimpleNamespace(
        id=FORM_ID,
        name="Ticket Form",
        organization_id=ORG_ID,
        access_level=SimpleNamespace(value="role_based"),
        description="Collect ticket detail",
        workflow_id=WORKFLOW_ID,
        launch_workflow_id=None,
        default_launch_params={"priority": "normal"},
        allowed_query_params=["ticket_id"],
    )
    field = SimpleNamespace(
        name="ticket_id",
        type="text",
        required=True,
        label="Ticket",
        placeholder=None,
        help_text="Paste a ticket id",
        default_value=None,
        options=None,
        data_provider_id=WORKFLOW_ID,
        data_provider_inputs={"q": "ticket"},
        visibility_expression=None,
        validation={"minLength": 1},
        allowed_types=None,
        multiple=None,
        max_size_mb=None,
        content=None,
        allow_as_query_param=True,
        auto_fill=None,
    )

    manifest_form = manifest_generator.serialize_form(form, roles=[ROLE_ID], fields=[field])

    assert manifest_form.roles == [ROLE_ID]
    assert manifest_form.form_schema == {
        "fields": [
            {
                "name": "ticket_id",
                "type": "text",
                "required": True,
                "label": "Ticket",
                "help_text": "Paste a ticket id",
                "data_provider_id": WORKFLOW_ID,
                "data_provider_inputs": {"q": "ticket"},
                "validation": {"minLength": 1},
                "allow_as_query_param": True,
            }
        ]
    }


def test_form_field_schema_helper_omits_unset_optional_values():
    field = SimpleNamespace(
        name="notes",
        type="textarea",
        required=False,
        label=None,
        placeholder=None,
        help_text=None,
        default_value=None,
        options=None,
        data_provider_id=None,
        data_provider_inputs=None,
        visibility_expression=None,
        validation=None,
        allowed_types=None,
        multiple=None,
        max_size_mb=None,
        content=None,
        allow_as_query_param=None,
        auto_fill=None,
    )

    assert manifest_generator._form_field_to_schema_dict(field) == {
        "name": "notes",
        "type": "textarea",
        "required": False,
    }


def test_agent_and_app_serializers_coerce_relationship_ids_and_defaults():
    agent = manifest_generator.serialize_agent(
        SimpleNamespace(
            id=AGENT_ID,
            name="Dispatcher",
            organization_id=ORG_ID,
            access_level=SimpleNamespace(value="authenticated"),
            description="Route requests",
            system_prompt="Help users",
            channels=("chat", "email"),
            knowledge_sources=("kb",),
            system_tools=("execute_workflow",),
            llm_model="gpt-test",
            llm_max_tokens=2048,
            max_iterations=8,
            max_token_budget=9000,
        ),
        roles=[ROLE_ID],
        tool_ids=[WORKFLOW_ID],
        delegated_agent_ids=[AGENT_ID],
        mcp_connection_ids=[MCP_CONN_ID],
    )
    app = manifest_generator.serialize_app(
        SimpleNamespace(
            id=APP_ID,
            repo_path="apps/desk/",
            slug="desk",
            name="Desk",
            description=None,
            dependencies={},
            organization_id=ORG_ID,
            access_level=None,
            app_model=None,
        ),
        roles=[ROLE_ID],
    )

    assert agent.channels == ["chat", "email"]
    assert agent.tool_ids == [WORKFLOW_ID]
    assert agent.delegated_agent_ids == [AGENT_ID]
    assert agent.mcp_connection_ids == [MCP_CONN_ID]
    assert app.path == "apps/desk"
    assert app.access_level == "authenticated"
    assert app.app_model == "inline_v1"


def test_integration_serializer_filters_invalid_schema_and_redacts_oauth_secret():
    config_schema = [
        SimpleNamespace(
            key="api_url",
            type="string",
            required=True,
            description="Base URL",
            options=None,
            position=1,
        ),
        SimpleNamespace(
            key="bad",
            type="unsupported",
            required=False,
            description=None,
            options=None,
            position=2,
        ),
    ]
    oauth = SimpleNamespace(
        provider_name="halo",
        display_name="Halo",
        oauth_flow_type="authorization_code",
        client_id="client-id",
        authorization_url="https://auth.example",
        token_url="https://token.example",
        token_url_defaults={"audience": "halo"},
        scopes=["read"],
        provider_metadata={"pkce": True},
        redirect_uri="https://bifrost.example/callback",
    )
    mapping = SimpleNamespace(
        organization_id=ORG_ID,
        entity_id="tenant-1",
        entity_name="Tenant",
        oauth_token_id="secret-token",
    )

    integration = manifest_generator.serialize_integration(
        SimpleNamespace(
            id=INTEGRATION_ID,
            name="Halo",
            entity_id="tenant",
            entity_id_name="Tenant",
            default_entity_id="tenant-1",
            list_entities_data_provider_id=WORKFLOW_ID,
        ),
        config_schema=config_schema,
        oauth_provider=oauth,
        mappings=[mapping],
    )

    assert [item.key for item in integration.config_schema] == ["api_url"]
    assert integration.oauth_provider.client_id == "client-id"
    assert integration.mappings[0].oauth_token_id is None


def test_config_claim_policy_table_and_file_policy_serializers():
    config = manifest_generator.serialize_config(
        SimpleNamespace(
            id=CONFIG_ID,
            integration_id=INTEGRATION_ID,
            key="api_key",
            config_type="secret",
            description="API key",
            organization_id=ORG_ID,
            value="should-redact",
        )
    )
    claim = manifest_generator.serialize_custom_claim(
        SimpleNamespace(
            id=CLAIM_ID,
            name="customer_id",
            description="Customer",
            organization_id=ORG_ID,
            type="scalar",
            query={"table": "customers", "where": {"eq": ["id", "$user.id"]}, "select": "id"},
            is_active=True,
        )
    )
    policy = manifest_generator.serialize_policy_rule(
        SimpleNamespace(
            id=RULE_ID,
            name="read-own",
            domain="table",
            description=None,
            body={"actions": ["read"]},
            organization_id=ORG_ID,
        )
    )
    table = manifest_generator.serialize_table(
        SimpleNamespace(
            id=TABLE_ID,
            name="Tickets",
            description="Tickets",
            organization_id=ORG_ID,
            schema={"columns": [{"name": "id"}]},
            access={"policies": [{"$ref": "read-own"}]},
        )
    )
    file_policy = manifest_generator.serialize_file_policy(
        SimpleNamespace(
            id=FILE_POLICY_ID,
            organization_id=ORG_ID,
            location="workspace",
            path="docs",
            policies={"policies": [{"allow": "read"}]},
            solution_id=None,
        )
    )

    assert config.value is None
    assert claim.name == "customer_id"
    assert claim.query.table == "customers"
    assert policy.body == {"actions": ["read"]}
    assert table.policies[0].ref == "read-own"
    assert file_policy.policies == [{"allow": "read"}]


def test_event_and_mcp_serializers_include_nested_children():
    event = manifest_generator.serialize_event_source(
        SimpleNamespace(
            id=EVENT_ID,
            name="Ticket Created",
            source_type=SimpleNamespace(value="webhook"),
            event_type="ticket.created",
            organization_id=ORG_ID,
            is_active=True,
        ),
        webhook=SimpleNamespace(
            adapter_name="halo",
            integration_id=INTEGRATION_ID,
            config={"secret": "ref"},
            rate_limit_per_minute=None,
            rate_limit_window_seconds=120,
            rate_limit_enabled=False,
        ),
        subscriptions=[
            SimpleNamespace(
                id=SUB_ID,
                target_type="workflow",
                workflow_id=WORKFLOW_ID,
                agent_id=None,
                event_type="ticket.created",
                filter_expression="$.priority == 'high'",
                input_mapping={"ticket": "$.id"},
                is_active=True,
            )
        ],
    )
    tool = SimpleNamespace(
        tool_name="search",
        tool_schema={"type": "object"},
        enabled=False,
        disabled_reason="admin",
    )
    connection = SimpleNamespace(
        organization_id=ORG_ID,
        client_id="client",
        server_url_override=None,
        available_in_chat=True,
        available_to_autonomous=False,
        service_oauth_token_id=None,
    )
    server = manifest_generator.serialize_mcp_server(
        SimpleNamespace(
            id=MCP_ID,
            name="Docs MCP",
            server_url="https://mcp.example",
            oauth_provider_id=None,
            redirect_url=None,
            discovery_metadata={"issuer": "mcp"},
            organization_id=None,
            is_active=True,
        ),
        connections_by_id={MCP_CONN_ID: connection},
        tools_by_connection={MCP_CONN_ID: [tool]},
    )

    assert event.adapter_name == "halo"
    assert event.subscriptions[0].input_mapping == {"ticket": "$.id"}
    assert server.connections[MCP_CONN_ID].tools[0].disabled_reason == "admin"


def test_schedule_event_serializer_sets_schedule_fields_and_webhook_defaults():
    event = manifest_generator.serialize_event_source(
        SimpleNamespace(
            id=EVENT_ID,
            name="Daily Sync",
            source_type="schedule",
            event_type=None,
            organization_id=None,
            is_active=False,
        ),
        schedule=SimpleNamespace(
            cron_expression="0 8 * * *",
            timezone="America/Indianapolis",
            enabled=False,
            overlap_policy=SimpleNamespace(value="replace"),
        ),
    )

    assert event.source_type == "schedule"
    assert event.organization_id is None
    assert event.is_active is False
    assert event.cron_expression == "0 8 * * *"
    assert event.timezone == "America/Indianapolis"
    assert event.schedule_enabled is False
    assert event.overlap_policy == "replace"
    assert event.rate_limit_per_minute == 60
    assert event.rate_limit_window_seconds == 60
    assert event.rate_limit_enabled is True
    assert event.subscriptions == []


def test_mcp_connection_serializer_includes_tools_and_omits_secret_material():
    tool = manifest_generator.serialize_mcp_connection_tool(
        SimpleNamespace(
            tool_name="lookup_ticket",
            tool_schema=None,
            enabled=True,
            disabled_reason=None,
        )
    )
    connection = manifest_generator.serialize_mcp_connection(
        SimpleNamespace(
            organization_id=ORG_ID,
            client_id="client-id",
            encrypted_client_secret="do-not-export",
            server_url_override="https://regional.example/mcp",
            available_in_chat=False,
            available_to_autonomous=True,
            service_oauth_token_id=CONFIG_ID,
        ),
        tools=[
            SimpleNamespace(
                tool_name="lookup_ticket",
                tool_schema={"type": "object"},
                enabled=False,
                disabled_reason="disabled by admin",
            )
        ],
    )

    assert tool.tool_schema == {}
    assert connection.organization_id == ORG_ID
    assert connection.server_url_override == "https://regional.example/mcp"
    assert connection.available_in_chat is False
    assert connection.available_to_autonomous is True
    assert connection.service_oauth_token_id == CONFIG_ID
    assert connection.tools[0].tool_schema == {"type": "object"}
    assert "secret" not in connection.model_dump()
