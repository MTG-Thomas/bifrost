"""Focused coverage for manifest import helper behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from bifrost.manifest import (
    Manifest,
    ManifestAgent,
    ManifestApp,
    ManifestConfig,
    ManifestEventSource,
    ManifestForm,
    ManifestIntegration,
    ManifestIntegrationMapping,
    ManifestMCPConnection,
    ManifestMCPServer,
    ManifestOrganization,
    ManifestPolicyRule,
    ManifestRole,
    ManifestTable,
    ManifestWorkflow,
)
from src.services import manifest_import


ORG_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ORG_ID = "22222222-2222-2222-2222-222222222222"
WORKFLOW_ID = "33333333-3333-3333-3333-333333333333"
FORM_ID = "44444444-4444-4444-4444-444444444444"
AGENT_ID = "55555555-5555-5555-5555-555555555555"
DELEGATE_ID = "66666666-6666-6666-6666-666666666666"
APP_ID = "77777777-7777-7777-7777-777777777777"
INTEGRATION_ID = "88888888-8888-8888-8888-888888888888"
CONFIG_ID = "99999999-9999-9999-9999-999999999999"
MCP_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ROLE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
EXISTING_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_diff_collect_ignores_oauth_token_mapping_noise_and_cascades_configs():
    current = Manifest(
        organizations=[ManifestOrganization(id=ORG_ID, name="Midtown")],
        integrations={
            "psa": ManifestIntegration(
                id=INTEGRATION_ID,
                name="Halo",
                mappings=[
                    ManifestIntegrationMapping(
                        organization_id=ORG_ID,
                        entity_id="tenant-1",
                        entity_name="Tenant",
                        oauth_token_id="old-token",
                    ),
                ],
            ),
        },
        configs={
            "psa.api_url": ManifestConfig(
                id=CONFIG_ID,
                integration_id=INTEGRATION_ID,
                key="api_url",
                organization_id=ORG_ID,
                value="https://old.example",
            ),
        },
    )
    incoming = current.model_copy(deep=True)
    incoming.integrations["psa"].mappings[0].oauth_token_id = "new-token"

    changes, changed_ids = manifest_import._diff_and_collect(incoming, current)

    assert changes == []
    assert changed_ids == set()

    incoming.integrations["psa"].name = "Halo PSA"
    changes, changed_ids = manifest_import._diff_and_collect(incoming, current)

    assert changes == [
        {
            "id": INTEGRATION_ID,
            "action": "update",
            "entity_type": "integrations",
            "name": "Halo PSA",
            "organization": "Global",
        },
    ]
    assert changed_ids == {INTEGRATION_ID, CONFIG_ID}


def test_filter_manifest_to_scope_keeps_repo_owned_graph_and_scoped_metadata():
    manifest = Manifest(
        organizations=[
            ManifestOrganization(id=ORG_ID, name="In scope"),
            ManifestOrganization(id=OTHER_ORG_ID, name="Out of scope"),
        ],
        roles=[
            ManifestRole(id="role-1", name="Operator"),
            ManifestRole(id="role-2", name="Auditor"),
        ],
        workflows={
            "owned": ManifestWorkflow(
                id=WORKFLOW_ID,
                name="Owned",
                path="workflows/owned.py",
                function_name="owned",
                organization_id=ORG_ID,
            ),
            "missing": ManifestWorkflow(
                id="missing-wf",
                name="Missing",
                path="workflows/missing.py",
                function_name="missing",
                organization_id=OTHER_ORG_ID,
            ),
        },
        forms={
            "by-id": ManifestForm(id=FORM_ID, name="Form", workflow_id=WORKFLOW_ID),
            "outside": ManifestForm(id="outside-form", name="Outside", workflow_id="missing-wf"),
        },
        agents={
            "worker": ManifestAgent(
                id=AGENT_ID,
                name="Worker",
                tool_ids=[WORKFLOW_ID],
                delegated_agent_ids=[DELEGATE_ID],
            ),
            "delegate": ManifestAgent(id=DELEGATE_ID, name="Delegate", system_prompt="help"),
            "outside": ManifestAgent(id="outside-agent", name="Outside", tool_ids=["missing-wf"]),
        },
        apps={
            "ok": ManifestApp(id=APP_ID, path="apps/portal", slug="portal", name="Portal"),
            "bad": ManifestApp(id="bad-app", path="../escape", slug="escape", name="Escape"),
            "absent": ManifestApp(id="absent-app", path="apps/absent", slug="absent", name="Absent"),
        },
        configs={
            "in": ManifestConfig(id=CONFIG_ID, key="url", organization_id=ORG_ID),
            "out": ManifestConfig(id="out-config", key="url", organization_id=OTHER_ORG_ID),
        },
        mcp_servers={
            "in": ManifestMCPServer(
                id=MCP_ID,
                name="MCP",
                server_url="https://mcp.example",
                connections={
                    "conn": ManifestMCPConnection(
                        organization_id=ORG_ID,
                        client_id="client",
                    ),
                },
            ),
            "out": ManifestMCPServer(
                id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                name="Other MCP",
                server_url="https://other.example",
                organization_id=OTHER_ORG_ID,
            ),
        },
    )
    scope_manifest = Manifest(
        organizations=[ManifestOrganization(id=ORG_ID, name="In scope")],
        roles=[ManifestRole(id="role-1", name="Operator")],
        workflows={"owned": manifest.workflows["owned"]},
        apps={"ok": manifest.apps["ok"]},
        configs={"in": manifest.configs["in"]},
        mcp_servers={"in": manifest.mcp_servers["in"]},
    )

    manifest_import._filter_manifest_to_scope(
        manifest,
        path_exists=lambda path: path == "workflows/owned.py",
        dir_exists=lambda path: path == "apps/portal",
        scope_manifest=scope_manifest,
    )

    assert set(manifest.workflows) == {"owned"}
    assert set(manifest.forms) == {"by-id"}
    assert set(manifest.agents) == {"worker", "delegate"}
    assert set(manifest.apps) == {"ok"}
    assert manifest.apps["ok"].path == "apps/portal"
    assert [org.id for org in manifest.organizations] == [ORG_ID]
    assert [role.id for role in manifest.roles] == ["role-1"]
    assert set(manifest.configs) == {"in"}
    assert set(manifest.mcp_servers) == {"in"}


def test_filter_manifest_to_scope_keeps_forms_referenced_by_workflow_paths():
    manifest = Manifest(
        workflows={
            "owned": ManifestWorkflow(
                id=WORKFLOW_ID,
                name="Owned",
                path="workflows/owned.py",
                function_name="owned",
            ),
        },
    )
    manifest.forms = {
        "by-workflow-path": SimpleNamespace(
            id=FORM_ID,
            name="By workflow path",
            workflow_path="workflows/owned.py",
        ),
        "by-launch-path": SimpleNamespace(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            name="By launch path",
            launch_workflow_path="workflows/owned.py",
        ),
        "outside": SimpleNamespace(
            id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            name="Outside",
            workflow_path="workflows/outside.py",
        ),
    }

    manifest_import._filter_manifest_to_scope(
        manifest,
        path_exists=lambda path: path == "workflows/owned.py",
        dir_exists=lambda path: False,
    )

    assert set(manifest.forms) == {"by-workflow-path", "by-launch-path"}


def test_filter_manifest_to_scope_keeps_standalone_agents_and_clears_empty_scope():
    manifest = Manifest(
        organizations=[ManifestOrganization(id=ORG_ID, name="Out of scope")],
        roles=[ManifestRole(id=ROLE_ID, name="Out of scope")],
        workflows={
            "owned": ManifestWorkflow(
                id=WORKFLOW_ID,
                name="Owned",
                path="workflows/owned.py",
                function_name="owned",
            ),
        },
        agents={
            "standalone": ManifestAgent(
                id=AGENT_ID,
                name="Standalone",
                system_prompt="Handle requests without workflow tools",
            ),
            "outside": ManifestAgent(
                id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                name="Outside",
                tool_ids=["eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"],
            ),
        },
        configs={"out": ManifestConfig(id=CONFIG_ID, key="url", organization_id=ORG_ID)},
        tables={
            "out": ManifestTable(
                id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                name="Tickets",
                organization_id=ORG_ID,
            ),
        },
    )

    manifest_import._filter_manifest_to_scope(
        manifest,
        path_exists=lambda path: path == "workflows/owned.py",
        dir_exists=lambda path: False,
        scope_manifest=Manifest(),
    )

    assert set(manifest.workflows) == {"owned"}
    assert set(manifest.agents) == {"standalone"}
    assert manifest.organizations == []
    assert manifest.roles == []
    assert manifest.configs == {}
    assert manifest.tables == {}


def test_diff_collect_displays_config_changes_with_integration_and_org_names():
    current = Manifest(
        organizations=[ManifestOrganization(id=ORG_ID, name="Midtown")],
        integrations={
            "psa": ManifestIntegration(id=INTEGRATION_ID, name="Halo"),
        },
        configs={
            "psa.api_url": ManifestConfig(
                id=CONFIG_ID,
                integration_id=INTEGRATION_ID,
                key="api_url",
                organization_id=ORG_ID,
                value="https://old.example",
            ),
        },
    )
    incoming = current.model_copy(deep=True)
    incoming.configs["psa.api_url"].value = "https://new.example"

    changes, changed_ids = manifest_import._diff_and_collect(incoming, current)

    assert changes == [
        {
            "id": CONFIG_ID,
            "action": "update",
            "entity_type": "configs",
            "name": "Halo/api_url",
            "organization": "Midtown",
        },
    ]
    assert changed_ids == {CONFIG_ID}


@pytest.mark.parametrize(
    ("app", "expected"),
    [
        (SimpleNamespace(id=APP_ID, path="", slug="portal"), "apps/portal"),
        (SimpleNamespace(id=APP_ID, path="apps\\portal\\", slug="portal"), "apps/portal"),
    ],
)
def test_safe_app_repo_path_normalizes_slug_paths(app, expected):
    assert manifest_import._safe_app_repo_path(app) == expected


def test_safe_app_repo_path_treats_whitespace_path_as_missing():
    assert manifest_import._safe_app_repo_path(
        SimpleNamespace(id=APP_ID, path="   ", slug="portal")
    ) == "apps/portal"


@pytest.mark.parametrize(
    "app",
    [
        SimpleNamespace(id=APP_ID, path="", slug=None),
        SimpleNamespace(id=APP_ID, path="/apps/portal", slug="portal"),
        SimpleNamespace(id=APP_ID, path="apps/portal/build", slug="portal"),
        SimpleNamespace(id=APP_ID, path="apps/.", slug="."),
        SimpleNamespace(id=APP_ID, path="apps/..", slug=".."),
        SimpleNamespace(id=APP_ID, path="apps/portal", slug="other"),
    ],
)
def test_safe_app_repo_path_rejects_unsafe_or_ambiguous_paths(app):
    with pytest.raises(ValueError):
        manifest_import._safe_app_repo_path(app)


@pytest.mark.asyncio
async def test_resolve_form_and_agent_content_prefers_inline_then_legacy(caplog):
    form = ManifestForm(
        id=FORM_ID,
        name="Ticket",
        workflow_id=WORKFLOW_ID,
        allowed_query_params=["ticket_id"],
    )
    agent = ManifestAgent(
        id=AGENT_ID,
        name="Dispatcher",
        system_prompt="Route the request",
        channels=["chat"],
    )

    assert b"workflow_id" in await manifest_import._resolve_form_content(form, _unused_read)
    assert b"system_prompt" in await manifest_import._resolve_agent_content(agent, _unused_read)

    async def read_legacy(path):
        return f"name: legacy\npath: {path}\n".encode()

    legacy_form = ManifestForm(id="legacy-form", name="Legacy", path="forms/legacy.form.yaml")
    legacy_agent = ManifestAgent(id="legacy-agent", name="Legacy", path="agents/legacy.agent.yaml")

    assert await manifest_import._resolve_form_content(legacy_form, read_legacy) == (
        b"name: legacy\npath: forms/legacy.form.yaml\n"
    )
    assert await manifest_import._resolve_agent_content(legacy_agent, read_legacy) == (
        b"name: legacy\npath: agents/legacy.agent.yaml\n"
    )
    assert "Form content in separate file is deprecated" in caplog.text
    assert "Agent content in separate file is deprecated" in caplog.text

    assert await manifest_import._resolve_form_content(
        ManifestForm(id="empty-form", name="Empty"),
        _missing_read,
    ) is None
    assert await manifest_import._resolve_agent_content(
        ManifestAgent(id="empty-agent", name="Empty"),
        _missing_read,
    ) is None


async def _unused_read(path):
    raise AssertionError(f"inline content should not read {path}")


async def _missing_read(path):
    return None


def test_collect_removed_entity_ids_groups_only_deletes_with_ids():
    changes = [
        {"action": "delete", "entity_type": "workflows", "id": WORKFLOW_ID},
        {"action": "delete", "entity_type": "workflows", "id": DELEGATE_ID},
        {"action": "delete", "entity_type": "apps", "id": APP_ID},
        {"action": "update", "entity_type": "forms", "id": FORM_ID},
        {"action": "delete", "entity_type": "", "id": "ignored"},
        {"action": "delete", "entity_type": "agents", "id": ""},
        {"action": "delete", "entity_type": "agents"},
        {"entity_type": "tables", "id": "ignored"},
    ]

    assert manifest_import._collect_removed_entity_ids(changes) == {
        "workflows": {WORKFLOW_ID, DELEGATE_ID},
        "apps": {APP_ID},
    }


def test_manifest_org_scope_collects_direct_and_nested_org_references():
    manifest = Manifest(
        organizations=[ManifestOrganization(id=ORG_ID, name="Primary")],
        workflows={
            "wf": ManifestWorkflow(
                id=WORKFLOW_ID,
                name="Scoped workflow",
                path="workflows/scoped.py",
                function_name="run",
                organization_id=OTHER_ORG_ID,
            ),
        },
        tables={
            "table": ManifestTable(
                id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                name="Tickets",
                organization_id="33333333-3333-3333-3333-333333333333",
            ),
        },
        events={
            "event": ManifestEventSource(
                id="cccccccc-cccc-cccc-cccc-cccccccccccc",
                name="Webhook",
                source_type="webhook",
                organization_id="66666666-6666-6666-6666-666666666666",
            ),
        },
        integrations={
            "psa": ManifestIntegration(
                id=INTEGRATION_ID,
                name="Halo",
                mappings=[
                    ManifestIntegrationMapping(
                        organization_id="44444444-4444-4444-4444-444444444444",
                        entity_id="tenant",
                        entity_name="Tenant",
                    ),
                ],
            ),
        },
        mcp_servers={
            "mcp": ManifestMCPServer(
                id=MCP_ID,
                name="MCP",
                server_url="https://mcp.example",
                connections={
                    "conn": ManifestMCPConnection(
                        organization_id="55555555-5555-5555-5555-555555555555",
                        client_id="client",
                    ),
                },
            ),
        },
    )

    assert manifest_import._manifest_org_scope(manifest) == {
        ORG_ID,
        OTHER_ORG_ID,
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444",
        "55555555-5555-5555-5555-555555555555",
        "66666666-6666-6666-6666-666666666666",
    }


def test_filter_non_file_entities_keeps_scoped_configs_tables_and_events():
    manifest = Manifest(
        organizations=[
            ManifestOrganization(id=ORG_ID, name="In scope"),
            ManifestOrganization(id=OTHER_ORG_ID, name="Out"),
        ],
        integrations={
            "mapped": ManifestIntegration(
                id=INTEGRATION_ID,
                name="Mapped",
                mappings=[
                    ManifestIntegrationMapping(
                        organization_id=ORG_ID,
                        entity_id="tenant",
                        entity_name="Tenant",
                    ),
                ],
            ),
            "out": ManifestIntegration(
                id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                name="Out",
                mappings=[
                    ManifestIntegrationMapping(
                        organization_id=OTHER_ORG_ID,
                        entity_id="other",
                        entity_name="Other",
                    ),
                ],
            ),
        },
        configs={
            "mapped-url": ManifestConfig(
                id=CONFIG_ID,
                integration_id=INTEGRATION_ID,
                key="url",
            ),
            "out-url": ManifestConfig(
                id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                integration_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                key="url",
            ),
        },
        tables={
            "in": ManifestTable(id="table-in", name="Tickets", organization_id=ORG_ID),
            "out": ManifestTable(id="table-out", name="Devices", organization_id=OTHER_ORG_ID),
        },
        events={
            "in": ManifestEventSource(
                id="ffffffff-ffff-ffff-ffff-ffffffffffff",
                name="In",
                source_type="webhook",
                organization_id=ORG_ID,
            ),
            "out": ManifestEventSource(
                id="abababab-abab-abab-abab-abababababab",
                name="Out",
                source_type="webhook",
                organization_id=OTHER_ORG_ID,
            ),
        },
    )
    scope_manifest = Manifest(
        organizations=[ManifestOrganization(id=ORG_ID, name="In scope")],
        events={"in": manifest.events["in"]},
        integrations={},
        configs={"mapped-url": manifest.configs["mapped-url"]},
        tables={"in": manifest.tables["in"]},
    )

    manifest_import._filter_non_file_entities_to_scope(manifest, scope_manifest)

    assert set(manifest.integrations) == set()
    assert set(manifest.configs) == {"mapped-url"}
    assert set(manifest.tables) == {"in"}
    assert set(manifest.events) == {"in"}


def test_entity_in_org_scope_requires_matching_explicit_org():
    assert manifest_import._entity_in_org_scope(
        SimpleNamespace(organization_id=ORG_ID),
        {ORG_ID},
    ) is True
    assert manifest_import._entity_in_org_scope(
        SimpleNamespace(organization_id=OTHER_ORG_ID),
        {ORG_ID},
    ) is False
    assert manifest_import._entity_in_org_scope(
        SimpleNamespace(organization_id=None),
        {ORG_ID},
    ) is False


def test_inline_content_detection_and_manifest_access_level_defaults():
    assert manifest_import._form_has_inline_content(
        ManifestForm(id=FORM_ID, name="Ticket", workflow_id=WORKFLOW_ID)
    ) is True
    assert manifest_import._form_has_inline_content(
        ManifestForm(id="empty-inline-form", name="Empty", default_launch_params={})
    ) is True
    assert manifest_import._form_has_inline_content(
        ManifestForm(id="empty-form", name="Empty")
    ) is False

    assert manifest_import._agent_has_inline_content(
        ManifestAgent(id=AGENT_ID, name="Agent", system_prompt="Help")
    ) is True
    assert manifest_import._agent_has_inline_content(
        ManifestAgent(id="tool-agent", name="Agent", tool_ids=[WORKFLOW_ID])
    ) is True
    assert manifest_import._agent_has_inline_content(
        ManifestAgent(id="token-agent", name="Agent", llm_max_tokens=0)
    ) is False
    assert manifest_import._agent_has_inline_content(
        ManifestAgent(id="empty-agent", name="Agent")
    ) is False

    assert manifest_import._manifest_access_level("public", ["Support"]) == "public"
    assert manifest_import._manifest_access_level(None, ["Support"]) == "role_based"
    assert manifest_import._manifest_access_level(None, []) is None
    assert manifest_import._manifest_access_level(None, [""]) == "role_based"
    assert manifest_import._manifest_access_level(None, None) is None


def test_resolve_workflow_realigns_natural_key_and_clears_explicit_empty_roles():
    resolver = manifest_import.ManifestResolver(AsyncMock())
    workflow = ManifestWorkflow(
        id=WORKFLOW_ID,
        name="Renamed in manifest",
        path="workflows/ticket.py",
        function_name="run",
        organization_id=ORG_ID,
        roles=[],
    )

    ops = resolver._resolve_workflow(
        "Canonical Name",
        workflow,
        {"wf_by_natural": {("workflows/ticket.py", "run"): UUID(EXISTING_ID)}},
    )

    assert len(ops) == 2
    upsert, sync_roles = ops
    assert upsert.id == UUID(EXISTING_ID)
    assert upsert.values["id"] == UUID(WORKFLOW_ID)
    assert upsert.values["name"] == "Canonical Name"
    assert upsert.values["organization_id"] == UUID(ORG_ID)
    assert sync_roles.entity_fk == "workflow_id"
    assert sync_roles.entity_id == UUID(WORKFLOW_ID)
    assert sync_roles.role_ids == set()


def test_resolve_app_derives_repo_path_realigns_slug_and_syncs_roles():
    resolver = manifest_import.ManifestResolver(AsyncMock())
    app = ManifestApp(
        id=APP_ID,
        path="",
        slug="portal",
        name="Portal",
        organization_id=ORG_ID,
        roles=[ROLE_ID],
        access_level="authenticated",
    )

    ops = resolver._resolve_app(
        app,
        {"app_by_slug": {"portal": UUID(EXISTING_ID)}},
    )

    assert len(ops) == 2
    upsert, sync_roles = ops
    assert upsert.id == UUID(EXISTING_ID)
    assert upsert.values["id"] == UUID(APP_ID)
    assert upsert.values["slug"] == "portal"
    assert upsert.values["repo_path"] == "apps/portal"
    assert upsert.values["organization_id"] == UUID(ORG_ID)
    assert sync_roles.entity_fk == "app_id"
    assert sync_roles.role_ids == {UUID(ROLE_ID)}


def test_resolve_app_skips_manifest_entry_without_slug_or_path(caplog):
    resolver = manifest_import.ManifestResolver(AsyncMock())
    app = SimpleNamespace(id=APP_ID, path="", slug=None)

    assert resolver._resolve_app(app, {"app_by_slug": {}}) == []
    assert f"App {APP_ID} has no slug or path, skipping" in caplog.text


def test_resolve_config_preserves_existing_secret_and_tracks_plain_global_config():
    resolver = manifest_import.ManifestResolver(AsyncMock())
    secret = ManifestConfig(
        id=CONFIG_ID,
        key="api_key",
        config_type="secret",
        organization_id=ORG_ID,
        value=None,
    )

    assert resolver._resolve_config(
        secret,
        {
            "config_by_natural": {
                ("api_key", None, UUID(ORG_ID)): (UUID(EXISTING_ID), "encrypted", None),
            },
        },
    ) == []
    assert resolver.configs_touched == {(ORG_ID, "api_key")}

    plain = ManifestConfig(
        id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        key="feature_flags",
        config_type="json",
        value=None,
    )
    ops = resolver._resolve_config(
        plain,
        {"config_by_natural": {}, "integ_cs": {}},
    )

    assert len(ops) == 1
    assert ops[0].id == UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    assert ops[0].values["key"] == "feature_flags"
    assert ops[0].values["value"] == {}
    assert (None, "feature_flags") in resolver.configs_touched


def test_resolve_config_links_integration_schema_without_marking_cache_touch():
    resolver = manifest_import.ManifestResolver(AsyncMock())
    schema = SimpleNamespace(id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"))
    config = ManifestConfig(
        id=CONFIG_ID,
        integration_id=INTEGRATION_ID,
        key="tenant_id",
        config_type="string",
        organization_id=ORG_ID,
        value="tenant-1",
    )

    ops = resolver._resolve_config(
        config,
        {
            "config_by_natural": {
                ("tenant_id", UUID(INTEGRATION_ID), UUID(ORG_ID)): (
                    UUID(EXISTING_ID),
                    "old-tenant",
                    None,
                ),
            },
            "integ_cs": {UUID(INTEGRATION_ID): {"tenant_id": schema}},
        },
    )

    assert len(ops) == 1
    assert ops[0].id == UUID(EXISTING_ID)
    assert ops[0].values["id"] == UUID(CONFIG_ID)
    assert ops[0].values["config_schema_id"] == schema.id
    assert "value" in ops[0].values
    assert resolver.configs_touched == set()


def test_resolve_policy_rule_uses_natural_key_or_supplies_insert_timestamps():
    resolver = manifest_import.ManifestResolver(AsyncMock())
    rule = ManifestPolicyRule(
        id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        name="Support only",
        domain="table",
        description="Only support can read",
        body={"actions": ["read"], "when": {"claim": "role", "equals": "support"}},
        organization_id=ORG_ID,
    )

    existing_ops = resolver._resolve_policy_rule(
        rule,
        {"policy_rule_by_natural": {("Support only", "table", UUID(ORG_ID)): UUID(EXISTING_ID)}},
    )
    assert existing_ops[0].id == UUID(EXISTING_ID)
    assert existing_ops[0].values["id"] == UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

    insert_ops = resolver._resolve_policy_rule(rule, {"policy_rule_by_natural": {}})
    assert insert_ops[0].id == UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    assert "created_at" in insert_ops[0].values
    assert "updated_at" in insert_ops[0].values


def test_resolve_form_and_agent_emit_metadata_and_authoritative_role_clears():
    resolver = manifest_import.ManifestResolver(AsyncMock())
    form = ManifestForm(
        id=FORM_ID,
        name="Ticket Form",
        organization_id=ORG_ID,
        roles=[],
        access_level="role_based",
    )
    agent = ManifestAgent(
        id=AGENT_ID,
        name="Dispatcher",
        organization_id=ORG_ID,
        roles=[],
        access_level="role_based",
    )

    form_ops = resolver._resolve_form(form, b"name: Ticket Form\n")
    agent_ops = resolver._resolve_agent(
        agent,
        b"name: Dispatcher\nsystem_prompt: Route tickets\nmax_iterations: 4\n",
    )

    assert len(form_ops) == 2
    assert form_ops[0].values["name"] == "Ticket Form"
    assert form_ops[0].values["access_level"] == "role_based"
    assert form_ops[1].entity_fk == "form_id"
    assert form_ops[1].role_ids == set()
    assert form_ops[1].extra_fields == {"assigned_by": "git-sync"}

    assert len(agent_ops) == 2
    assert agent_ops[0].values["system_prompt"] == "Route tickets"
    assert agent_ops[0].values["max_iterations"] == 4
    assert agent_ops[1].entity_fk == "agent_id"
    assert agent_ops[1].role_ids == set()
    assert agent_ops[1].extra_fields == {"assigned_by": "git-sync"}


def test_resolve_form_and_agent_ignore_empty_content_and_global_metadata():
    resolver = manifest_import.ManifestResolver(AsyncMock())

    assert resolver._resolve_form(ManifestForm(id=FORM_ID, name="Empty"), b"") == []
    assert resolver._resolve_agent(ManifestAgent(id=AGENT_ID, name="Empty"), b"") == []

    form_ops = resolver._resolve_form(
        ManifestForm(id=FORM_ID, name="Global", roles=[ROLE_ID]),
        b"name: Global\n",
    )
    agent_ops = resolver._resolve_agent(
        ManifestAgent(id=AGENT_ID, name="Global", roles=[ROLE_ID]),
        b"name: Global\nsystem_prompt: Help\n",
    )

    assert len(form_ops) == 1
    assert form_ops[0].entity_fk == "form_id"
    assert form_ops[0].role_ids == {UUID(ROLE_ID)}
    assert len(agent_ops) == 1
    assert agent_ops[0].entity_fk == "agent_id"
    assert agent_ops[0].role_ids == {UUID(ROLE_ID)}


@pytest.mark.asyncio
async def test_resolve_role_names_preserves_order_and_creates_missing_roles():
    existing_id = uuid4()
    created_id = uuid4()
    added_roles = []

    class Result:
        def all(self):
            return [(existing_id, "Existing")]

    class Db:
        async def execute(self, _stmt):
            return Result()

        def add(self, role):
            added_roles.append(role)

        async def flush(self):
            added_roles[-1].id = created_id

    created_names = set()

    resolved = await manifest_import._resolve_role_names(
        Db(),
        ["Existing", "Missing", "Missing"],
        create_missing=True,
        created_out=created_names,
    )

    assert resolved == [str(existing_id), str(created_id), str(created_id)]
    assert [role.name for role in added_roles] == ["Missing"]
    assert added_roles[0].created_by == "solution-install"
    assert created_names == {"Missing"}


@pytest.mark.asyncio
async def test_resolve_role_names_fails_unknown_role_without_creation():
    class Result:
        def all(self):
            return []

    class Db:
        async def execute(self, _stmt):
            return Result()

    with pytest.raises(ValueError, match="unknown role: Missing"):
        await manifest_import._resolve_role_names(Db(), ["Missing"])


@pytest.mark.asyncio
async def test_resolve_ref_field_updates_scalar_and_list_portable_refs(caplog):
    resolver = manifest_import.ManifestResolver(AsyncMock())
    resolved = {
        "workflows/ticket.py::run": WORKFLOW_ID,
        "workflows/escalate.py::run": DELEGATE_ID,
    }

    async def resolve_portable_ref(ref):
        return resolved.get(ref)

    resolver._resolve_portable_ref = resolve_portable_ref

    data = {"workflow_id": "workflows/ticket.py::run"}
    await resolver._resolve_ref_field(data, "workflow_id")
    assert data["workflow_id"] == WORKFLOW_ID

    data = {
        "tool_ids": [
            "workflows/escalate.py::run",
            "workflows/missing.py::run",
            "plain-tool-id",
        ],
    }
    await resolver._resolve_ref_field(data, "tool_ids")

    assert data["tool_ids"] == [
        DELEGATE_ID,
        "workflows/missing.py::run",
        "plain-tool-id",
    ]
    assert "Could not resolve portable ref" not in caplog.text


@pytest.mark.asyncio
async def test_resolve_ref_field_leaves_unresolved_scalar_portable_ref(caplog):
    resolver = manifest_import.ManifestResolver(AsyncMock())

    async def resolve_portable_ref(_ref):
        return None

    resolver._resolve_portable_ref = resolve_portable_ref

    data = {"workflow_id": "workflows/missing.py::run"}
    await resolver._resolve_ref_field(data, "workflow_id")

    assert data["workflow_id"] == "workflows/missing.py::run"
    assert "Could not resolve portable ref 'workflows/missing.py::run'" in caplog.text


@pytest.mark.asyncio
async def test_resolve_workflow_ref_tries_uuid_path_function_then_name():
    found_id = UUID(WORKFLOW_ID)
    missing_id = UUID(DELEGATE_ID)

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Db:
        def __init__(self, values):
            self.values = list(values)
            self.calls = 0

        async def execute(self, _stmt):
            self.calls += 1
            return Result(self.values.pop(0))

    uuid_db = Db([found_id])
    assert await manifest_import.ManifestResolver(uuid_db)._resolve_workflow_ref(WORKFLOW_ID) == found_id
    assert uuid_db.calls == 1

    path_db = Db([None, found_id])
    assert (
        await manifest_import.ManifestResolver(path_db)._resolve_workflow_ref(
            "workflows/ticket.py::run"
        )
        == found_id
    )
    assert path_db.calls == 2

    name_db = Db([missing_id])
    assert await manifest_import.ManifestResolver(name_db)._resolve_workflow_ref("Ticket") == missing_id
    assert name_db.calls == 1


@pytest.mark.asyncio
async def test_apply_ops_stamps_dry_run_upserts_and_executes_real_ops():
    from src.services.sync_ops import Upsert

    class Model:
        __tablename__ = "fake"

    existing_id = uuid4()
    new_id = uuid4()
    resolver = manifest_import.ManifestResolver(AsyncMock())
    all_ops = []
    existing = Upsert(Model, existing_id, {"name": "Existing"})
    new = Upsert(Model, new_id, {"name": "New"})

    await resolver._apply_ops(
        [existing, new],
        all_ops,
        dry_run=True,
        existing_ids={str(existing_id), existing_id},
    )

    assert existing.action_taken == "updated"
    assert new.action_taken == "inserted"
    assert all_ops == [existing, new]

    executed = []

    class FakeOp:
        async def execute(self, db):
            executed.append(db)

    real_ops = [FakeOp()]
    await resolver._apply_ops(real_ops, [], dry_run=False, existing_ids=set())

    assert executed == [resolver.db]
