"""Focused coverage for manifest import helper behavior."""

from types import SimpleNamespace

import pytest

from bifrost.manifest import (
    Manifest,
    ManifestAgent,
    ManifestApp,
    ManifestConfig,
    ManifestForm,
    ManifestIntegration,
    ManifestIntegrationMapping,
    ManifestMCPConnection,
    ManifestMCPServer,
    ManifestOrganization,
    ManifestRole,
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


@pytest.mark.parametrize(
    ("app", "expected"),
    [
        (SimpleNamespace(id=APP_ID, path="", slug="portal"), "apps/portal"),
        (SimpleNamespace(id=APP_ID, path="apps\\portal\\", slug="portal"), "apps/portal"),
    ],
)
def test_safe_app_repo_path_normalizes_slug_paths(app, expected):
    assert manifest_import._safe_app_repo_path(app) == expected


@pytest.mark.parametrize(
    "app",
    [
        SimpleNamespace(id=APP_ID, path="", slug=None),
        SimpleNamespace(id=APP_ID, path="/apps/portal", slug="portal"),
        SimpleNamespace(id=APP_ID, path="apps/portal/build", slug="portal"),
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
