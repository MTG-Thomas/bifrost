"""Behavior-focused coverage for ManifestResolver pure resolver helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from bifrost.manifest import ManifestAgent, ManifestConfig, ManifestForm, ManifestPolicyRule
from src.services.manifest_import import ManifestResolver


ORG_ID = "11111111-1111-1111-1111-111111111111"
FORM_ID = "22222222-2222-2222-2222-222222222222"
AGENT_ID = "33333333-3333-3333-3333-333333333333"
ROLE_ID = "44444444-4444-4444-4444-444444444444"
CONFIG_ID = "55555555-5555-5555-5555-555555555555"
EXISTING_ID = "66666666-6666-6666-6666-666666666666"
INTEGRATION_ID = "77777777-7777-7777-7777-777777777777"
POLICY_RULE_ID = "88888888-8888-8888-8888-888888888888"
WORKFLOW_ID = "99999999-9999-9999-9999-999999999999"


@pytest.mark.asyncio
async def test_resolve_ref_field_rewrites_portable_strings_and_lists_in_place():
    resolver = ManifestResolver(AsyncMock())
    resolver._resolve_portable_ref = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda ref: {
            "workflows/a.py::run": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "workflows/b.py::run": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }.get(ref)
    )
    data = {
        "workflow_id": "workflows/a.py::run",
        "tool_ids": [
            "workflows/b.py::run",
            "workflows/missing.py::run",
            UUID(WORKFLOW_ID),
            "plain-id",
        ],
        "untouched": "workflows/a.py::run",
    }

    await resolver._resolve_ref_field(data, "workflow_id")
    await resolver._resolve_ref_field(data, "tool_ids")
    await resolver._resolve_ref_field(data, "missing_field")

    assert data["workflow_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert data["tool_ids"] == [
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "workflows/missing.py::run",
        UUID(WORKFLOW_ID),
        "plain-id",
    ]
    assert data["untouched"] == "workflows/a.py::run"
    assert resolver._resolve_portable_ref.await_args_list[0].args == ("workflows/a.py::run",)


@pytest.mark.asyncio
async def test_resolve_workflow_ref_tries_uuid_path_function_then_name():
    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeDb:
        def __init__(self, values):
            self.values = list(values)
            self.execute_count = 0

        async def execute(self, _stmt):
            self.execute_count += 1
            return Result(self.values.pop(0))

    uuid_db = FakeDb([UUID(WORKFLOW_ID)])
    assert await ManifestResolver(uuid_db)._resolve_workflow_ref(WORKFLOW_ID) == UUID(WORKFLOW_ID)
    assert uuid_db.execute_count == 1

    path_db = FakeDb([UUID(WORKFLOW_ID)])
    assert (
        await ManifestResolver(path_db)._resolve_workflow_ref("workflows/ticket.py::triage")
        == UUID(WORKFLOW_ID)
    )
    assert path_db.execute_count == 1

    name_db = FakeDb([UUID(WORKFLOW_ID)])
    assert await ManifestResolver(name_db)._resolve_workflow_ref("Ticket triage") == UUID(WORKFLOW_ID)
    assert name_db.execute_count == 1

    missing_db = FakeDb([None])
    assert await ManifestResolver(missing_db)._resolve_workflow_ref("Missing") is None
    assert missing_db.execute_count == 1


def test_resolve_config_uses_natural_key_cache_and_tracks_only_global_configs():
    resolver = ManifestResolver(AsyncMock())
    global_config = ManifestConfig(
        id=CONFIG_ID,
        key="feature_flags",
        config_type="json",
        organization_id=ORG_ID,
        value={"enabled": True},
    )

    global_ops = resolver._resolve_config(
        global_config,
        {
            "config_by_natural": {
                ("feature_flags", None, UUID(ORG_ID)): (UUID(EXISTING_ID), {"enabled": False}, None)
            },
            "integ_cs": {},
        },
    )

    assert global_ops[0].id == UUID(EXISTING_ID)
    assert global_ops[0].values["id"] == UUID(CONFIG_ID)
    assert global_ops[0].values["value"] == {"enabled": True}
    assert resolver.configs_touched == {(ORG_ID, "feature_flags")}

    schema = SimpleNamespace(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    integration_config = ManifestConfig(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        key="tenant_id",
        config_type="string",
        integration_id=INTEGRATION_ID,
        organization_id=ORG_ID,
        value="tenant-2",
    )

    integration_ops = resolver._resolve_config(
        integration_config,
        {
            "config_by_natural": {},
            "integ_cs": {UUID(INTEGRATION_ID): {"tenant_id": schema}},
        },
    )

    assert integration_ops[0].id == UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    assert integration_ops[0].values["config_schema_id"] == schema.id
    assert integration_ops[0].values["value"] == "tenant-2"
    assert resolver.configs_touched == {(ORG_ID, "feature_flags")}


def test_resolve_policy_rule_updates_by_natural_key_and_timestamps_new_rows():
    resolver = ManifestResolver(AsyncMock())
    rule = ManifestPolicyRule(
        id=POLICY_RULE_ID,
        name="Support read",
        domain="table",
        description="Support users can read records",
        body={"actions": ["read"], "where": {"claim": "role", "equals": "support"}},
        organization_id=ORG_ID,
    )

    update_ops = resolver._resolve_policy_rule(
        rule,
        {"policy_rule_by_natural": {("Support read", "table", UUID(ORG_ID)): UUID(EXISTING_ID)}},
    )
    insert_ops = resolver._resolve_policy_rule(rule, {"policy_rule_by_natural": {}})

    assert update_ops[0].id == UUID(EXISTING_ID)
    assert update_ops[0].values["id"] == UUID(POLICY_RULE_ID)
    assert update_ops[0].values["body"]["actions"] == ["read"]
    assert "created_at" not in update_ops[0].values
    assert insert_ops[0].id == UUID(POLICY_RULE_ID)
    assert insert_ops[0].values["created_at"].tzinfo is not None
    assert insert_ops[0].values["updated_at"].tzinfo is not None


def test_resolve_form_and_agent_emit_metadata_ops_from_inline_yaml():
    resolver = ManifestResolver(AsyncMock())
    form = ManifestForm(
        id=FORM_ID,
        name="Ticket form",
        organization_id=ORG_ID,
        roles=[ROLE_ID],
        access_level="role_based",
    )
    agent = ManifestAgent(
        id=AGENT_ID,
        name="Ticket dispatcher",
        organization_id=ORG_ID,
        roles=[],
        access_level="role_based",
    )

    form_ops = resolver._resolve_form(form, b"name: Ticket intake\n")
    agent_ops = resolver._resolve_agent(
        agent,
        (
            b"name: Ticket dispatcher\n"
            b"system_prompt: Route tickets by customer and severity\n"
            b"max_iterations: 5\n"
            b"max_token_budget: 12000\n"
        ),
    )

    assert form_ops[0].id == UUID(FORM_ID)
    assert form_ops[0].values == {
        "name": "Ticket intake",
        "is_active": True,
        "created_by": "git-sync",
        "organization_id": UUID(ORG_ID),
        "access_level": "role_based",
    }
    assert form_ops[1].entity_fk == "form_id"
    assert form_ops[1].role_ids == {UUID(ROLE_ID)}
    assert form_ops[1].extra_fields == {"assigned_by": "git-sync"}

    assert agent_ops[0].id == UUID(AGENT_ID)
    assert agent_ops[0].values["name"] == "Ticket dispatcher"
    assert agent_ops[0].values["system_prompt"] == "Route tickets by customer and severity"
    assert agent_ops[0].values["max_iterations"] == 5
    assert agent_ops[0].values["max_token_budget"] == 12000
    assert agent_ops[0].values["organization_id"] == UUID(ORG_ID)
    assert agent_ops[0].values["access_level"] == "role_based"
    assert agent_ops[1].entity_fk == "agent_id"
    assert agent_ops[1].role_ids == set()
    assert agent_ops[1].extra_fields == {"assigned_by": "git-sync"}
