"""Focused tests for manifest import helper behavior."""

from types import SimpleNamespace

import pytest

from src.services.manifest_import import (
    _collect_removed_entity_ids,
    _filter_non_file_entities_to_scope,
    _filter_manifest_to_scope,
    _safe_app_repo_path,
)


class _App(SimpleNamespace):
    def model_copy(self, *, update):
        return _App(**{**self.__dict__, **update})


def test_collect_removed_entity_ids_groups_only_explicit_deletes():
    changes = [
        {"action": "delete", "entity_type": "workflows", "id": "wf-1"},
        {"action": "delete", "entity_type": "workflows", "id": "wf-2"},
        {"action": "delete", "entity_type": "forms", "id": "form-1"},
        {"action": "update", "entity_type": "workflows", "id": "wf-3"},
        {"action": "delete", "entity_type": "agents"},
        {"action": "delete", "id": "missing-type"},
    ]

    assert _collect_removed_entity_ids(changes) == {
        "workflows": {"wf-1", "wf-2"},
        "forms": {"form-1"},
    }


@pytest.mark.parametrize(
    ("app", "expected"),
    [
        (_App(id="app-1", slug="portal", path=None), "apps/portal"),
        (_App(id="app-1", slug="portal", path="apps/portal/"), "apps/portal"),
        (_App(id="app-1", slug=None, path="apps/portal"), "apps/portal"),
        (_App(id="app-1", slug="portal", path="apps\\portal"), "apps/portal"),
    ],
)
def test_safe_app_repo_path_normalizes_allowed_app_paths(app, expected):
    assert _safe_app_repo_path(app) == expected


@pytest.mark.parametrize(
    "app",
    [
        _App(id="app-1", slug=None, path=None),
        _App(id="app-1", slug="portal", path="/apps/portal"),
        _App(id="app-1", slug="portal", path="apps/../portal"),
        _App(id="app-1", slug="portal", path="apps/portal/src"),
        _App(id="app-1", slug="portal", path="workflows/portal"),
        _App(id="app-1", slug="portal", path="apps/other"),
    ],
)
def test_safe_app_repo_path_rejects_unsafe_or_mismatched_paths(app):
    with pytest.raises(ValueError):
        _safe_app_repo_path(app)


def test_filter_manifest_to_scope_keeps_only_apps_with_existing_safe_dirs():
    kept_app = _App(id="keep", slug="portal", path="apps/portal")
    defaulted_app = _App(id="defaulted", slug="defaulted", path=None)
    missing_app = _App(id="missing", slug="missing", path="apps/missing")
    unsafe_app = _App(id="unsafe", slug="portal", path="../portal")
    manifest = SimpleNamespace(
        workflows={},
        forms={},
        agents={},
        apps={
            "keep": kept_app,
            "defaulted": defaulted_app,
            "missing": missing_app,
            "unsafe": unsafe_app,
        },
    )

    _filter_manifest_to_scope(
        manifest,
        path_exists=lambda _path: False,
        dir_exists=lambda path: path in {"apps/portal", "apps/defaulted"},
    )

    assert set(manifest.apps) == {"keep", "defaulted"}
    assert manifest.apps["keep"].path == "apps/portal"
    assert manifest.apps["defaulted"].path == "apps/defaulted"


def test_filter_manifest_to_scope_includes_delegated_agents_transitively():
    manifest = SimpleNamespace(
        workflows={
            "workflow": SimpleNamespace(id="wf-1", path="workflows/a.py"),
            "outside": SimpleNamespace(id="wf-2", path="workflows/missing.py"),
        },
        forms={},
        agents={
            "parent": SimpleNamespace(
                id="agent-parent",
                tool_ids=["wf-1"],
                delegated_agent_ids=["agent-child"],
            ),
            "child": SimpleNamespace(
                id="agent-child",
                tool_ids=["wf-2"],
                delegated_agent_ids=["agent-grandchild"],
            ),
            "grandchild": SimpleNamespace(
                id="agent-grandchild",
                tool_ids=["wf-2"],
                delegated_agent_ids=[],
            ),
            "outside": SimpleNamespace(
                id="agent-outside",
                tool_ids=["wf-2"],
                delegated_agent_ids=[],
            ),
        },
        apps={},
    )

    _filter_manifest_to_scope(
        manifest,
        path_exists=lambda path: path == "workflows/a.py",
        dir_exists=lambda _path: False,
    )

    assert set(manifest.agents) == {"parent", "child", "grandchild"}


def test_filter_non_file_entities_to_scope_keeps_only_declared_org_surface():
    org_keep = SimpleNamespace(id="org-keep")
    org_drop = SimpleNamespace(id="org-drop")
    role_keep = SimpleNamespace(id="role-keep")
    role_drop = SimpleNamespace(id="role-drop")
    integ_keep = SimpleNamespace(
        id="integ-keep",
        mappings=[SimpleNamespace(organization_id="org-keep")],
    )
    integ_by_mapping = SimpleNamespace(
        id="integ-by-mapping",
        mappings=[SimpleNamespace(organization_id="org-keep")],
    )
    integ_drop = SimpleNamespace(
        id="integ-drop",
        mappings=[SimpleNamespace(organization_id="org-drop")],
    )
    mcp_keep = SimpleNamespace(
        id="mcp-keep",
        organization_id=None,
        connections={"org": SimpleNamespace(organization_id="org-keep")},
    )
    mcp_drop = SimpleNamespace(
        id="mcp-drop",
        organization_id=None,
        connections={"org": SimpleNamespace(organization_id="org-drop")},
    )
    manifest = SimpleNamespace(
        organizations=[org_keep, org_drop],
        roles=[role_keep, role_drop],
        integrations={
            "keep": integ_keep,
            "by_mapping": integ_by_mapping,
            "drop": integ_drop,
        },
        configs={
            "keep": SimpleNamespace(id="cfg-keep", organization_id=None, integration_id=None),
            "org": SimpleNamespace(id="cfg-org", organization_id="org-keep", integration_id=None),
            "integ": SimpleNamespace(
                id="cfg-integ",
                organization_id=None,
                integration_id="integ-by-mapping",
            ),
            "drop": SimpleNamespace(id="cfg-drop", organization_id="org-drop", integration_id=None),
        },
        tables={
            "keep": SimpleNamespace(id="tbl-keep", organization_id=None),
            "org": SimpleNamespace(id="tbl-org", organization_id="org-keep"),
            "drop": SimpleNamespace(id="tbl-drop", organization_id="org-drop"),
        },
        events={
            "keep": SimpleNamespace(id="evt-keep", organization_id=None),
            "org": SimpleNamespace(id="evt-org", organization_id="org-keep"),
            "drop": SimpleNamespace(id="evt-drop", organization_id="org-drop"),
        },
        mcp_servers={"keep": mcp_keep, "drop": mcp_drop},
    )
    scope_manifest = SimpleNamespace(
        organizations=[org_keep],
        roles=[role_keep],
        integrations={"keep": integ_keep},
        configs={"keep": manifest.configs["keep"]},
        tables={"keep": manifest.tables["keep"]},
        events={"keep": manifest.events["keep"]},
        mcp_servers={"keep": mcp_keep},
        workflows={},
        forms={},
        agents={},
        apps={},
    )

    _filter_non_file_entities_to_scope(manifest, scope_manifest)

    assert manifest.organizations == [org_keep]
    assert manifest.roles == [role_keep]
    assert set(manifest.integrations) == {"keep", "by_mapping"}
    assert set(manifest.configs) == {"keep", "org", "integ"}
    assert set(manifest.tables) == {"keep", "org"}
    assert set(manifest.events) == {"keep", "org"}
    assert set(manifest.mcp_servers) == {"keep"}
