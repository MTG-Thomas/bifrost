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
    ManifestEventSubscription,
    ManifestForm,
    ManifestIntegration,
    ManifestIntegrationConfigSchema,
    ManifestIntegrationMapping,
    ManifestMCPConnection,
    ManifestFilePolicy,
    ManifestMCPConnectionTool,
    ManifestMCPServer,
    ManifestOAuthProvider,
    ManifestOrganization,
    ManifestPolicyRule,
    ManifestPolicyRef,
    ManifestRole,
    ManifestSolutionFile,
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
EVENT_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
SUBSCRIPTION_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
MCP_CONNECTION_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"


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


@pytest.mark.asyncio
async def test_resolve_event_source_upserts_schedule_and_valid_subscriptions_only():
    db = AsyncMock()
    resolver = manifest_import.ManifestResolver(db)
    resolver._resolve_workflow_ref = AsyncMock(return_value=UUID(DELEGATE_ID))
    event = ManifestEventSource(
        id=EVENT_ID,
        name="Daily ticket sweep",
        source_type="schedule",
        event_type="ticket.sweep",
        organization_id=ORG_ID,
        cron_expression="0 6 * * *",
        timezone="America/New_York",
        schedule_enabled=None,
        subscriptions=[
            ManifestEventSubscription(
                id=SUBSCRIPTION_ID,
                workflow_id=WORKFLOW_ID,
                event_type="ticket.created",
                input_mapping={"ticket_id": "$.id"},
            ),
            ManifestEventSubscription(
                id="abababab-abab-abab-abab-abababababab",
                workflow_id="workflows/escalate.py::run",
                event_type="ticket.escalated",
            ),
            ManifestEventSubscription(
                id="cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd",
                target_type="agent",
                agent_id=AGENT_ID,
                event_type="ticket.assigned",
            ),
            ManifestEventSubscription(
                id="edededed-eded-eded-eded-edededededed",
                target_type="agent",
                agent_id="not-a-uuid",
            ),
            ManifestEventSubscription(
                id="fafafafa-fafa-fafa-fafa-fafafafafafa",
                workflow_id=None,
            ),
        ],
    )

    await resolver._resolve_event_source(
        "daily-ticket-sweep",
        event,
        imported_wf_ids={WORKFLOW_ID},
    )

    assert db.execute.await_count == 5
    source_params = db.execute.call_args_list[0][0][0].compile().params
    assert source_params["id"] == UUID(EVENT_ID)
    assert source_params["name"] == "daily-ticket-sweep"
    assert source_params["organization_id"] == UUID(ORG_ID)
    schedule_params = db.execute.call_args_list[1][0][0].compile().params
    assert schedule_params["event_source_id"] == UUID(EVENT_ID)
    assert schedule_params["cron_expression"] == "0 6 * * *"
    assert schedule_params["timezone"] == "America/New_York"
    assert schedule_params["enabled"] is True
    assert schedule_params["overlap_policy"] == "skip"

    subscription_params = [
        call[0][0].compile().params for call in db.execute.call_args_list[2:]
    ]
    assert [params["id"] for params in subscription_params] == [
        UUID(SUBSCRIPTION_ID),
        UUID("abababab-abab-abab-abab-abababababab"),
        UUID("cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd"),
    ]
    assert subscription_params[0]["workflow_id"] == UUID(WORKFLOW_ID)
    assert subscription_params[1]["workflow_id"] == UUID(DELEGATE_ID)
    assert subscription_params[2]["target_type"] == "agent"
    assert subscription_params[2]["agent_id"] == UUID(AGENT_ID)
    resolver._resolve_workflow_ref.assert_awaited_once_with("workflows/escalate.py::run")


@pytest.mark.asyncio
async def test_resolve_event_source_upserts_webhook_and_skips_unimported_workflow():
    db = AsyncMock()
    resolver = manifest_import.ManifestResolver(db)
    event = ManifestEventSource(
        id=EVENT_ID,
        name="Halo webhook",
        source_type="webhook",
        organization_id=None,
        adapter_name="halopsa",
        webhook_integration_id=INTEGRATION_ID,
        webhook_config={"path": "/tickets"},
        rate_limit_per_minute=None,
        rate_limit_window_seconds=30,
        rate_limit_enabled=False,
        subscriptions=[
            ManifestEventSubscription(id=SUBSCRIPTION_ID, workflow_id=WORKFLOW_ID),
        ],
    )

    await resolver._resolve_event_source(
        "halo-webhook",
        event,
        imported_wf_ids={DELEGATE_ID},
    )

    assert db.execute.await_count == 2
    webhook_params = db.execute.call_args_list[1][0][0].compile().params
    assert webhook_params["event_source_id"] == UUID(EVENT_ID)
    assert webhook_params["adapter_name"] == "halopsa"
    assert webhook_params["integration_id"] == UUID(INTEGRATION_ID)
    assert webhook_params["config"] == {"path": "/tickets"}
    assert webhook_params["rate_limit_per_minute"] is None
    assert webhook_params["rate_limit_window_seconds"] == 30
    assert webhook_params["rate_limit_enabled"] is False


@pytest.mark.asyncio
async def test_resolve_mcp_connection_imports_connection_and_tool_catalog():
    db = AsyncMock()
    resolver = manifest_import.ManifestResolver(db)
    connection = ManifestMCPConnection(
        organization_id=ORG_ID,
        client_id="client-1",
        server_url_override="https://region.example/mcp",
        available_in_chat=True,
        available_to_autonomous=True,
        service_oauth_token_id=CONFIG_ID,
        tools=[
            ManifestMCPConnectionTool(
                tool_name="ticket_lookup",
                tool_schema={"inputSchema": {"type": "object"}},
                enabled=False,
                disabled_reason="Needs review",
            )
        ],
    )

    await resolver._resolve_mcp_connection(
        MCP_CONNECTION_ID,
        connection,
        {MCP_ID},
        server_id=MCP_ID,
    )

    assert db.execute.await_count == 2
    connection_params = db.execute.call_args_list[0][0][0].compile().params
    assert connection_params["id"] == UUID(MCP_CONNECTION_ID)
    assert connection_params["server_id"] == UUID(MCP_ID)
    assert connection_params["organization_id"] == UUID(ORG_ID)
    assert connection_params["client_id"] == "client-1"
    assert connection_params["encrypted_client_secret"] == ""
    assert connection_params["service_oauth_token_id"] == UUID(CONFIG_ID)
    tool_params = db.execute.call_args_list[1][0][0].compile().params
    assert tool_params["connection_id"] == UUID(MCP_CONNECTION_ID)
    assert tool_params["tool_name"] == "ticket_lookup"
    assert tool_params["enabled"] is False
    assert tool_params["disabled_reason"] == "Needs review"


@pytest.mark.asyncio
async def test_resolve_mcp_connection_skips_when_parent_server_not_imported():
    db = AsyncMock()
    resolver = manifest_import.ManifestResolver(db)
    connection = ManifestMCPConnection(
        organization_id=ORG_ID,
        client_id="client-1",
    )

    await resolver._resolve_mcp_connection(
        MCP_CONNECTION_ID,
        connection,
        imported_server_ids=set(),
        server_id=MCP_ID,
    )

    db.execute.assert_not_called()


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ScalarRowsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _ScalarsResult(self._values)


class _SequenceDb:
    def __init__(self, *results):
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if self._results:
            return self._results.pop(0)
        return _RowsResult([])


@pytest.mark.asyncio
async def test_prefetch_existing_entities_builds_all_resolver_caches():
    integration_uuid = UUID(INTEGRATION_ID)
    org_uuid = UUID(ORG_ID)
    workflow_uuid = UUID(WORKFLOW_ID)
    app_uuid = UUID(APP_ID)
    table_uuid = UUID("abababab-abab-abab-abab-abababababab")
    file_policy_uuid = UUID("bcbcbcbc-bcbc-bcbc-bcbc-bcbcbcbcbcbc")
    policy_rule_uuid = UUID("dededede-dede-dede-dede-dededededede")
    claim_uuid = UUID("cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd")
    schema = SimpleNamespace(integration_id=integration_uuid, key="api_url")
    mapping = SimpleNamespace(
        integration_id=integration_uuid,
        organization_id=org_uuid,
    )
    db = _SequenceDb(
        _RowsResult([(org_uuid, "Midtown")]),
        _RowsResult([(UUID(ROLE_ID), "Operator")]),
        _RowsResult([(workflow_uuid, "workflows/tickets.py", "run")]),
        _RowsResult([(integration_uuid, "Halo")]),
        _ScalarRowsResult([schema]),
        _ScalarRowsResult([mapping]),
        _RowsResult([(app_uuid, "portal")]),
        _RowsResult([(table_uuid, "Tickets", org_uuid)]),
        _RowsResult([(file_policy_uuid, org_uuid, "workspace", "docs/", UUID(APP_ID))]),
        _RowsResult([(UUID(CONFIG_ID), "api_url", integration_uuid, org_uuid, "https://api", schema)]),
        _RowsResult([(policy_rule_uuid, "Support read", "table", org_uuid)]),
        _RowsResult([(claim_uuid, "allowed_campus_ids", org_uuid)]),
    )

    cache = await manifest_import.ManifestResolver(db)._prefetch_existing_entities()

    assert cache["org_ids"] == {org_uuid}
    assert cache["org_by_name"] == {"Midtown": org_uuid}
    assert cache["role_ids"] == {UUID(ROLE_ID)}
    assert cache["role_by_name"] == {"Operator": UUID(ROLE_ID)}
    assert cache["wf_ids"] == {workflow_uuid}
    assert cache["wf_by_natural"] == {("workflows/tickets.py", "run"): workflow_uuid}
    assert cache["integ_ids"] == {integration_uuid}
    assert cache["integ_by_name"] == {"Halo": integration_uuid}
    assert cache["integ_cs"] == {integration_uuid: {"api_url": schema}}
    assert cache["integ_mappings"] == {integration_uuid: {ORG_ID: mapping}}
    assert cache["app_by_slug"] == {"portal": app_uuid}
    assert cache["table_ids"] == {table_uuid}
    assert cache["table_by_natural"] == {("Tickets", org_uuid): table_uuid}
    assert cache["file_policy_ids"] == {file_policy_uuid}
    assert cache["file_policy_by_natural"] == {
        (org_uuid, "workspace", "docs/", UUID(APP_ID)): file_policy_uuid
    }
    assert cache["config_by_natural"] == {
        ("api_url", integration_uuid, org_uuid): (UUID(CONFIG_ID), "https://api", schema)
    }
    assert cache["policy_rule_ids"] == {policy_rule_uuid}
    assert cache["policy_rule_by_natural"] == {
        ("Support read", "table", org_uuid): policy_rule_uuid
    }
    assert cache["claim_ids"] == {claim_uuid}
    assert cache["claim_by_natural"] == {("allowed_campus_ids", org_uuid): claim_uuid}
    assert len(db.statements) == 12


class _ManifestClaim:
    def __init__(self, *, claim_id: str = CONFIG_ID, org_id: str = ORG_ID):
        self.claim_id = claim_id
        self.org_id = org_id

    def to_orm_values(self, _destination):
        return SimpleNamespace(
            direct={
                "id": self.claim_id,
                "organization_id": self.org_id,
                "description": "Allowed campuses",
                "type": "list",
                "query": {"table": "memberships", "select": "campus_id"},
            }
        )


@pytest.mark.asyncio
async def test_resolve_custom_claim_updates_existing_natural_key_without_rekeying():
    existing_id = UUID(EXISTING_ID)
    db = _SequenceDb()
    resolver = manifest_import.ManifestResolver(db)

    assert await resolver._resolve_custom_claim(
        "allowed_campus_ids",
        _ManifestClaim(),
        {
            "claim_by_natural": {("allowed_campus_ids", UUID(ORG_ID)): existing_id},
            "claim_ids": set(),
        },
    ) == []

    assert len(db.statements) == 1
    params = db.statements[0].compile().params
    assert params["id_1"] == existing_id
    assert params["name"] == "allowed_campus_ids"
    assert params["organization_id"] == UUID(ORG_ID)
    assert params["query"] == {"table": "memberships", "select": "campus_id"}


@pytest.mark.asyncio
async def test_resolve_custom_claim_updates_existing_manifest_id_from_cache():
    db = _SequenceDb()
    resolver = manifest_import.ManifestResolver(db)
    claim_id = UUID(CONFIG_ID)

    assert await resolver._resolve_custom_claim(
        "allowed_campus_ids",
        _ManifestClaim(),
        {
            "claim_by_natural": {},
            "claim_ids": {claim_id},
        },
    ) == []

    assert len(db.statements) == 1
    params = db.statements[0].compile().params
    assert params["id_1"] == claim_id
    assert params["name"] == "allowed_campus_ids"
    assert params["type"] == "list"


@pytest.mark.asyncio
async def test_resolve_custom_claim_inserts_new_claim_when_cache_misses():
    db = _SequenceDb()
    resolver = manifest_import.ManifestResolver(db)

    assert await resolver._resolve_custom_claim(
        "allowed_campus_ids",
        _ManifestClaim(),
        {
            "claim_by_natural": {},
            "claim_ids": set(),
        },
    ) == []

    assert len(db.statements) == 1
    params = db.statements[0].compile().params
    assert params["id"] == UUID(CONFIG_ID)
    assert params["name"] == "allowed_campus_ids"
    assert params["description"] == "Allowed campuses"
    assert params["organization_id"] == UUID(ORG_ID)


@pytest.mark.asyncio
async def test_resolve_solution_files_noops_without_manifest_entries_or_sidecar():
    resolver = manifest_import.ManifestResolver(_SequenceDb())
    install_id = UUID(APP_ID)

    await resolver._resolve_solution_files(
        Manifest(),
        install_id=install_id,
        sidecar_content=SimpleNamespace(solution_files=[]),
    )
    await resolver._resolve_solution_files(
        Manifest(
            solution_files=[
                ManifestSolutionFile(
                    location="shared",
                    path="docs/readme.md",
                    sha256="0" * 64,
                    size=5,
                )
            ]
        ),
        install_id=install_id,
        sidecar_content=None,
    )


@pytest.mark.asyncio
async def test_resolve_solution_files_fails_closed_before_writing(monkeypatch):
    writes = []

    async def fake_write_solution_file(*args, **kwargs):
        writes.append((args, kwargs))

    monkeypatch.setattr(
        "src.services.solution_files.write_solution_file",
        fake_write_solution_file,
    )
    resolver = manifest_import.ManifestResolver(_SequenceDb())
    manifest = Manifest(
        solution_files=[
            ManifestSolutionFile(
                location="shared",
                path="docs/readme.md",
                sha256="0" * 64,
                size=5,
            )
        ]
    )

    with pytest.raises(ValueError, match="matching sidecar bytes"):
        await resolver._resolve_solution_files(
            manifest,
            install_id=UUID(APP_ID),
            sidecar_content=SimpleNamespace(solution_files=[]),
        )
    with pytest.raises(ValueError, match="no content_b64"):
        await resolver._resolve_solution_files(
            manifest,
            install_id=UUID(APP_ID),
            sidecar_content=SimpleNamespace(
                solution_files=[
                    {
                        "location": "shared",
                        "path": "docs/readme.md",
                        "content_b64": "",
                    }
                ]
            ),
        )

    assert writes == []


@pytest.mark.asyncio
async def test_resolve_solution_files_decodes_sidecar_and_replaces_files(monkeypatch):
    import base64

    writes = []

    async def fake_write_solution_file(db, install_id, location, path, content, *, mode):
        writes.append((db, install_id, location, path, content, mode))

    monkeypatch.setattr(
        "src.services.solution_files.write_solution_file",
        fake_write_solution_file,
    )
    db = _SequenceDb()
    install_id = UUID(APP_ID)
    manifest = Manifest(
        solution_files=[
            ManifestSolutionFile(
                location="shared",
                path="docs/readme.md",
                sha256="0" * 64,
                size=5,
            )
        ]
    )

    await manifest_import.ManifestResolver(db)._resolve_solution_files(
        manifest,
        install_id=install_id,
        sidecar_content=SimpleNamespace(
            solution_files=[
                {
                    "location": "shared",
                    "path": "docs/readme.md",
                    "content_b64": base64.b64encode(b"hello").decode(),
                }
            ]
        ),
    )

    assert writes == [(db, install_id, "shared", "docs/readme.md", b"hello", "replace")]


def test_resolve_organization_uses_id_name_then_insert_paths():
    resolver = manifest_import.ManifestResolver(_SequenceDb())
    org_id = UUID(ORG_ID)
    by_id = ManifestOrganization(id=ORG_ID, name="Renamed")
    by_name = ManifestOrganization(id=OTHER_ORG_ID, name="Midtown")
    new_org = ManifestOrganization(
        id="edededed-eded-eded-eded-edededededed",
        name="New org",
    )

    id_op = resolver._resolve_organization(
        by_id,
        {"org_ids": {org_id}, "org_by_name": {}},
    )[0]
    name_op = resolver._resolve_organization(
        by_name,
        {"org_ids": set(), "org_by_name": {"Midtown": org_id}},
    )[0]
    insert_op = resolver._resolve_organization(
        new_org,
        {"org_ids": set(), "org_by_name": {}},
    )[0]

    assert id_op.match_on == "id"
    assert id_op.values == {"name": "Renamed", "is_active": True}
    assert name_op.match_on == "name"
    assert name_op.values["id"] == UUID(OTHER_ORG_ID)
    assert name_op.values["name"] == "Midtown"
    assert insert_op.match_on == "id"
    assert insert_op.values["created_by"] == "git-sync"


def test_resolve_role_uses_id_name_then_insert_paths():
    resolver = manifest_import.ManifestResolver(_SequenceDb())
    role_id = UUID(ROLE_ID)
    by_id = ManifestRole(id=ROLE_ID, name="Renamed")
    by_name = ManifestRole(id=EXISTING_ID, name="Operator")
    new_role = ManifestRole(
        id="edededed-eded-eded-eded-edededededed",
        name="New role",
    )

    id_op = resolver._resolve_role(
        by_id,
        {"role_ids": {role_id}, "role_by_name": {}},
    )[0]
    name_op = resolver._resolve_role(
        by_name,
        {"role_ids": set(), "role_by_name": {"Operator": role_id}},
    )[0]
    insert_op = resolver._resolve_role(
        new_role,
        {"role_ids": set(), "role_by_name": {}},
    )[0]

    assert id_op.match_on == "id"
    assert id_op.values == {"name": "Renamed"}
    assert name_op.match_on == "name"
    assert name_op.values == {"id": UUID(EXISTING_ID), "name": "Operator"}
    assert insert_op.match_on == "id"
    assert insert_op.values == {"name": "New role", "created_by": "git-sync"}


@pytest.mark.asyncio
async def test_resolve_deletions_requires_manifest_or_work_dir():
    resolver = manifest_import.ManifestResolver(_SequenceDb())

    with pytest.raises(ValueError, match="Either manifest or work_dir must be provided"):
        await resolver._resolve_deletions()


@pytest.mark.asyncio
async def test_resolve_deletions_honors_explicit_config_removal_and_preserves_tables():
    stale_table_id = UUID("abababab-abab-abab-abab-abababababab")
    db = _SequenceDb(
        _RowsResult([
            (
                UUID(CONFIG_ID),
                UUID(INTEGRATION_ID),
                UUID(ORG_ID),
                "api_url",
            )
        ]),
        _RowsResult([(stale_table_id, "Tickets")]),
    )
    resolver = manifest_import.ManifestResolver(db)
    manifest = Manifest(
        configs={
            "api_url": ManifestConfig(
                id=CONFIG_ID,
                integration_id=INTEGRATION_ID,
                organization_id=ORG_ID,
                key="api_url",
                value="https://api.example",
            ),
        },
        tables={
            "tickets": ManifestTable(
                id="cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd",
                name="Tickets",
            )
        },
    )

    changes = await resolver._resolve_deletions(
        manifest=manifest,
        dry_run=True,
        removed_entity_ids={"configs": {CONFIG_ID}},
    )

    assert [(change.action, change.entity_type, change.name) for change in changes] == [
        ("removed", "configs", CONFIG_ID),
        ("keep", "tables", "Tickets"),
    ]
    assert resolver.configs_touched == {(ORG_ID, "api_url")}
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_resolve_deletions_applies_explicit_hard_and_soft_removals():
    db = _SequenceDb(
        _RowsResult([(UUID(WORKFLOW_ID), "Import tickets")]),
        _RowsResult([]),
        _RowsResult([]),
        _RowsResult([(UUID(ORG_ID), "Midtown")]),
        _RowsResult([]),
        _RowsResult([(UUID(ROLE_ID), "Operator")]),
        _RowsResult([]),
    )
    resolver = manifest_import.ManifestResolver(db)

    changes = await resolver._resolve_deletions(
        manifest=Manifest(),
        removed_entity_ids={
            "workflows": {WORKFLOW_ID},
            "organizations": {ORG_ID},
            "roles": {ROLE_ID},
        },
    )

    assert [(change.action, change.entity_type, change.name) for change in changes] == [
        ("removed", "workflows", "Import tickets"),
        ("removed", "organizations", "Midtown"),
        ("removed", "roles", "Operator"),
    ]
    assert len(db.statements) == 7


@pytest.mark.asyncio
async def test_resolve_integration_rekeys_dependent_cache_and_syncs_children(monkeypatch):
    from src.services.sync_ops import Upsert

    old_integration_id = UUID(EXISTING_ID)
    new_integration_id = UUID(INTEGRATION_ID)
    existing_schema = SimpleNamespace(
        key="api_url",
        type="string",
        required=False,
        description="Old URL",
        options=None,
        position=9,
    )
    stale_schema = SimpleNamespace(
        key="old_key",
        type="string",
        required=False,
        description="Remove me",
        options=None,
        position=10,
    )
    existing_mapping = SimpleNamespace(
        id=UUID("abababab-abab-abab-abab-abababababab"),
        organization_id=UUID(ORG_ID),
        entity_id="old-tenant",
        entity_name="Old Tenant",
    )
    stale_mapping = SimpleNamespace(
        id=UUID("cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd"),
        organization_id=None,
        entity_id="global",
        entity_name="Global",
    )
    db = _SequenceDb(
        _ScalarRowsResult([existing_schema, stale_schema]),
        _ScalarRowsResult([existing_mapping, stale_mapping]),
        _RowsResult([]),
        _RowsResult([]),
        _ScalarRowsResult([existing_schema]),
        _RowsResult([]),
        _RowsResult([]),
        _RowsResult([]),
        _RowsResult([]),
    )
    resolver = manifest_import.ManifestResolver(db)
    executed_upserts = []

    async def capture_execute(self, _db):
        executed_upserts.append(self)

    monkeypatch.setattr(Upsert, "execute", capture_execute)

    cache = {
        "integ_by_name": {"Halo": old_integration_id},
        "integ_cs": {old_integration_id: {"api_url": existing_schema, "old_key": stale_schema}},
        "integ_mappings": {
            old_integration_id: {ORG_ID: existing_mapping, None: stale_mapping}
        },
        "config_by_natural": {
            ("api_url", old_integration_id, UUID(ORG_ID)): (
                UUID(CONFIG_ID),
                "https://old.example",
                None,
            )
        },
    }
    integration = ManifestIntegration(
        id=INTEGRATION_ID,
        name="Halo",
        entity_id="tenant_id",
        entity_id_name="Tenant",
        default_entity_id="default-tenant",
        list_entities_data_provider_id=WORKFLOW_ID,
        config_schema=[
            ManifestIntegrationConfigSchema(
                key="api_url",
                type="string",
                required=True,
                description="Tenant URL",
                options=["https://api.example"],
                position=0,
            ),
            ManifestIntegrationConfigSchema(
                key="token_url",
                type="string",
                required=False,
                description="Token URL",
                position=1,
            ),
        ],
        oauth_provider=ManifestOAuthProvider(
            provider_name="halo",
            display_name="Halo OAuth",
            oauth_flow_type="authorization_code",
            client_id="client-id",
            authorization_url="https://auth.example/authorize",
            token_url="https://auth.example/token",
            token_url_defaults={"audience": "halo"},
            scopes=["read", "write"],
            provider_metadata={"pkce": True},
            redirect_uri="https://app.example/callback",
        ),
        mappings=[
            ManifestIntegrationMapping(
                organization_id=ORG_ID,
                entity_id="tenant-1",
                entity_name="Tenant One",
            ),
            ManifestIntegrationMapping(
                organization_id=OTHER_ORG_ID,
                entity_id="tenant-2",
                entity_name="Tenant Two",
            ),
        ],
    )

    assert await resolver._resolve_integration("Halo", integration, cache) == []

    assert executed_upserts[0].id == old_integration_id
    assert executed_upserts[0].values["id"] == new_integration_id
    assert executed_upserts[0].values["list_entities_data_provider_id"] == UUID(WORKFLOW_ID)
    assert existing_schema.type == "string"
    assert existing_schema.required is True
    assert existing_schema.description == "Tenant URL"
    assert existing_schema.options == ["https://api.example"]
    assert existing_schema.position == 0
    assert existing_mapping.entity_id == "tenant-1"
    assert existing_mapping.entity_name == "Tenant One"
    assert old_integration_id not in cache["integ_cs"]
    assert old_integration_id not in cache["integ_mappings"]
    assert ("api_url", new_integration_id, UUID(ORG_ID)) in cache["config_by_natural"]
    assert len(db.statements) == 8


@pytest.mark.asyncio
async def test_sync_role_assignments_adds_before_removing_stale_roles():
    from src.models.orm.workflow_roles import WorkflowRole

    current_role_id = UUID(ROLE_ID)
    stale_role_id = UUID(EXISTING_ID)
    new_role_id = UUID(DELEGATE_ID)
    db = _SequenceDb(_RowsResult([(current_role_id,), (stale_role_id,)]))
    resolver = manifest_import.ManifestResolver(db)

    await resolver._sync_role_assignments(
        UUID(WORKFLOW_ID),
        [str(current_role_id), str(new_role_id)],
        WorkflowRole,
        "workflow_id",
    )

    assert len(db.statements) == 3
    insert_params = db.statements[1].compile().params
    assert insert_params["workflow_id"] == UUID(WORKFLOW_ID)
    assert insert_params["role_id"] == new_role_id
    assert insert_params["assigned_by"] == "git-sync"
    delete_sql = str(db.statements[2].compile(compile_kwargs={"literal_binds": True}))
    assert "DELETE FROM workflow_roles" in delete_sql


@pytest.mark.asyncio
async def test_resolve_table_realigns_existing_natural_key_and_seeds_access():
    db = _SequenceDb()
    resolver = manifest_import.ManifestResolver(db)
    existing_table_id = UUID(EXISTING_ID)
    table = ManifestTable(
        id=CONFIG_ID,
        name="Tickets",
        organization_id=ORG_ID,
        description="Ticket intake",
        schema={"columns": [{"name": "summary", "type": "text"}]},
        policies=None,
    )

    await resolver._resolve_table(
        "Tickets",
        table,
        {
            "table_by_natural": {("Tickets", UUID(ORG_ID)): existing_table_id},
            "table_ids": set(),
        },
    )

    assert len(db.statements) == 1
    params = db.statements[0].compile().params
    assert params["id"] == UUID(CONFIG_ID)
    assert params["description"] == "Ticket intake"
    assert params["schema"] == {"columns": [{"name": "summary", "type": "text"}]}
    assert params["access"]["policies"][0]["name"] == "admin_bypass"


@pytest.mark.asyncio
async def test_resolve_table_validates_refs_but_preserves_manifest_ref(monkeypatch):
    from shared.policy_rules import resolve_policy_refs

    calls = []

    async def fake_resolve_policy_refs(policy_model, *, repo, action_domain):
        calls.append((policy_model, repo, action_domain))

    monkeypatch.setattr(
        "shared.policy_rules.resolve_policy_refs",
        fake_resolve_policy_refs,
    )
    db = _SequenceDb()
    resolver = manifest_import.ManifestResolver(db)
    table = ManifestTable(
        id=CONFIG_ID,
        name="Tickets",
        policies=[ManifestPolicyRef(**{"$ref": "support-read"})],
    )

    await resolver._resolve_table(
        "Tickets",
        table,
        {
            "table_by_natural": {},
            "table_ids": {UUID(CONFIG_ID)},
        },
    )

    assert resolve_policy_refs is not fake_resolve_policy_refs
    assert calls[0][2] == "table"
    params = db.statements[0].compile().params
    assert params["access"] == {"policies": [{"$ref": "support-read"}]}


@pytest.mark.asyncio
async def test_resolve_file_policy_uses_solution_aware_natural_key(monkeypatch):
    calls = []

    async def fake_resolve_policy_refs(policy_model, *, repo, action_domain):
        calls.append((policy_model, repo, action_domain))

    monkeypatch.setattr(
        "shared.policy_rules.resolve_policy_refs",
        fake_resolve_policy_refs,
    )
    db = _SequenceDb()
    resolver = manifest_import.ManifestResolver(db)
    existing_policy_id = UUID(EXISTING_ID)
    policy = ManifestFilePolicy(
        id=CONFIG_ID,
        organization_id=ORG_ID,
        location="workspace",
        path="docs/",
        solution_id=APP_ID,
        policies=[{"$ref": "docs-read"}],
    )

    await resolver._resolve_file_policy(
        policy,
        {
            "file_policy_by_natural": {
                (UUID(ORG_ID), "workspace", "docs/", UUID(APP_ID)): existing_policy_id
            },
            "file_policy_ids": set(),
        },
    )

    assert calls[0][2] == "file"
    assert len(db.statements) == 1
    params = db.statements[0].compile().params
    assert params["id"] == UUID(CONFIG_ID)
    assert params["policies"] == {"policies": [{"$ref": "docs-read"}]}


@pytest.mark.asyncio
async def test_resolve_file_policy_falls_back_to_id_then_inserts(monkeypatch):
    async def fake_resolve_policy_refs(_policy_model, *, repo, action_domain):
        return None

    monkeypatch.setattr(
        "shared.policy_rules.resolve_policy_refs",
        fake_resolve_policy_refs,
    )
    existing_id_db = _SequenceDb()
    resolver = manifest_import.ManifestResolver(existing_id_db)
    policy = ManifestFilePolicy(
        id=CONFIG_ID,
        organization_id=None,
        location="shared",
        path="",
        policies=[],
    )

    await resolver._resolve_file_policy(
        policy,
        {
            "file_policy_by_natural": {},
            "file_policy_ids": {UUID(CONFIG_ID)},
        },
    )

    update_params = existing_id_db.statements[0].compile().params
    assert update_params["location"] == "shared"
    assert update_params["path"] == ""
    assert update_params["solution_id"] is None

    insert_db = _SequenceDb()
    await manifest_import.ManifestResolver(insert_db)._resolve_file_policy(
        policy,
        {
            "file_policy_by_natural": {},
            "file_policy_ids": set(),
        },
    )

    insert_params = insert_db.statements[0].compile().params
    assert insert_params["id"] == UUID(CONFIG_ID)
    assert insert_params["location"] == "shared"
    assert insert_params["created_by"] is None
