"""Focused branch coverage for solution deploy helpers."""

import base64
from enum import Enum
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.services.solutions import deploy


INSTALL_ID = UUID("11111111-1111-1111-1111-111111111111")
WORKFLOW_ID = UUID("22222222-2222-2222-2222-222222222222")
MAPPED_WORKFLOW_ID = UUID("33333333-3333-3333-3333-333333333333")
TABLE_ID = UUID("44444444-4444-4444-4444-444444444444")
APP_ID = UUID("55555555-5555-5555-5555-555555555555")
FORM_ID = UUID("66666666-6666-6666-6666-666666666666")
AGENT_ID = UUID("77777777-7777-7777-7777-777777777777")
DELEGATED_AGENT_ID = UUID("88888888-8888-8888-8888-888888888888")
CLAIM_ID = UUID("99999999-9999-9999-9999-999999999999")
CONFIG_SCHEMA_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
EVENT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SUBSCRIPTION_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
EXTERNAL_WORKFLOW_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
PRESERVED_WORKFLOW_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _OwnerLookupDb:
    def __init__(self, owned_by_install=None):
        self.owned_by_install = set(owned_by_install or [])
        self.queries = 0

    async def execute(self, statement):
        self.queries += 1
        params = statement.compile().params.values()
        for entity_id in self.owned_by_install:
            if entity_id in params:
                return _ScalarResult(INSTALL_ID)
        return _ScalarResult(None)


class AccessLevel(Enum):
    PRIVATE = "private"
    PUBLIC = "public"


def test_remap_form_field_providers_maps_only_dict_fields_with_provider_ids():
    form = {
        "workflow_id": str(WORKFLOW_ID),
        "form_schema": {
            "fields": [
                {"data_provider_id": str(WORKFLOW_ID)},
                {"data_provider_id": "workflow.py::choices"},
                {"label": "plain"},
                "not-a-dict",
            ]
        },
    }

    deploy.SolutionDeployer._remap_form_field_providers(
        form,
        {WORKFLOW_ID: MAPPED_WORKFLOW_ID},
    )

    assert form["workflow_id"] == str(WORKFLOW_ID)
    assert form["form_schema"]["fields"][0]["data_provider_id"] == str(
        MAPPED_WORKFLOW_ID
    )
    assert form["form_schema"]["fields"][1]["data_provider_id"] == (
        "workflow.py::choices"
    )
    assert form["form_schema"]["fields"][2] == {"label": "plain"}
    assert form["form_schema"]["fields"][3] == "not-a-dict"


@pytest.mark.parametrize("form", [{"form_schema": None}, {"form_schema": []}, {}])
def test_remap_form_field_providers_ignores_non_dict_schema(form):
    deploy.SolutionDeployer._remap_form_field_providers(
        form,
        {WORKFLOW_ID: MAPPED_WORKFLOW_ID},
    )

    assert form == form


def test_validate_access_level_returns_valid_value_and_rejects_unknown_value():
    assert (
        deploy.SolutionDeployer._validate_access_level(
            "public",
            AccessLevel,
            "agent",
        )
        == "public"
    )

    with pytest.raises(deploy.SolutionDeployConflict) as exc_info:
        deploy.SolutionDeployer._validate_access_level(
            "internal",
            AccessLevel,
            "agent",
        )

    assert "agent has invalid access_level 'internal'" in str(exc_info.value)
    assert "private" in str(exc_info.value)
    assert "public" in str(exc_info.value)


def test_parse_uuids_accepts_lists_only():
    assert deploy.SolutionDeployer._parse_uuids([str(WORKFLOW_ID), WORKFLOW_ID]) == [
        WORKFLOW_ID,
        WORKFLOW_ID,
    ]
    assert deploy.SolutionDeployer._parse_uuids(None) == []
    assert deploy.SolutionDeployer._parse_uuids(str(WORKFLOW_ID)) == []


def test_apply_readme_full_replaces_existing_value():
    solution = SimpleNamespace(readme="old")

    deploy.SolutionDeployer._apply_readme(solution, SimpleNamespace(readme="new"))
    assert solution.readme == "new"

    deploy.SolutionDeployer._apply_readme(solution, SimpleNamespace())
    assert solution.readme is None


@pytest.mark.asyncio
async def test_write_bundle_files_writes_dataclass_and_secret_blob_entries(monkeypatch):
    calls = []

    async def fake_write_solution_file(db, install_id, location, path, content, mode):
        calls.append((db, install_id, location, path, content, mode))

    monkeypatch.setattr(
        "src.services.solution_files.write_solution_file",
        fake_write_solution_file,
    )
    deployer = deploy.SolutionDeployer(db=object())
    dataclass_entry = SimpleNamespace(
        content_bytes=b"plain",
        location="workspace",
        path="docs/readme.md",
    )
    dict_entry = {
        "content_b64": base64.b64encode(b"secret").decode("ascii"),
        "location": "secrets",
        "path": "token.txt",
    }

    await deployer._write_bundle_files(
        INSTALL_ID,
        [
            dataclass_entry,
            SimpleNamespace(content_bytes=None, location="workspace", path="skip.txt"),
            dict_entry,
            {"location": "secrets", "path": "empty.txt"},
        ],
        "merge",
    )

    assert calls == [
        (deployer.db, INSTALL_ID, "workspace", "docs/readme.md", b"plain", "merge"),
        (deployer.db, INSTALL_ID, "secrets", "token.txt", b"secret", "merge"),
    ]


@pytest.mark.asyncio
async def test_remapped_bundle_rewrites_entity_ids_and_cross_refs_without_mutating_input():
    db = _OwnerLookupDb()
    deployer = deploy.SolutionDeployer(db=db)
    bundle = deploy.SolutionBundle(
        solution=SimpleNamespace(id=INSTALL_ID),
        python_files={"workflows/main.py": "source"},
        workflows=[{"id": str(WORKFLOW_ID), "name": "main"}],
        tables=[{"id": str(TABLE_ID), "name": "items"}],
        apps=[{"id": str(APP_ID), "name": "portal"}],
        forms=[
            {
                "id": str(FORM_ID),
                "workflow_id": str(WORKFLOW_ID),
                "launch_workflow_id": "workflows/main.py::run",
                "form_schema": {
                    "fields": [
                        {"data_provider_id": str(WORKFLOW_ID)},
                        {"data_provider_id": str(EXTERNAL_WORKFLOW_ID)},
                    ]
                },
            }
        ],
        agents=[
            {
                "id": str(AGENT_ID),
                "tool_ids": [str(WORKFLOW_ID), "workflows/main.py::run"],
                "delegated_agent_ids": [str(DELEGATED_AGENT_ID)],
            },
            {"id": str(DELEGATED_AGENT_ID), "tool_ids": [], "delegated_agent_ids": []},
        ],
        claims=[{"id": str(CLAIM_ID), "name": "region"}],
        config_schemas=[{"id": str(CONFIG_SCHEMA_ID), "key": "token"}],
        events=[
            {
                "id": str(EVENT_ID),
                "subscriptions": [
                    {
                        "id": str(SUBSCRIPTION_ID),
                        "workflow_id": str(WORKFLOW_ID),
                        "agent_id": str(AGENT_ID),
                    },
                    "ignored",
                ],
            }
        ],
        file_locations=["workspace"],
        version="1.2.3",
        readme="README",
    )

    remapped = await deployer._remapped_bundle(bundle)

    mapped_workflow_id = deploy.solution_entity_id(INSTALL_ID, WORKFLOW_ID)
    mapped_agent_id = deploy.solution_entity_id(INSTALL_ID, AGENT_ID)
    mapped_delegated_agent_id = deploy.solution_entity_id(INSTALL_ID, DELEGATED_AGENT_ID)
    mapped_subscription_id = deploy.solution_entity_id(INSTALL_ID, SUBSCRIPTION_ID)

    assert remapped is not bundle
    assert remapped.workflows[0]["id"] == str(mapped_workflow_id)
    assert remapped.tables[0]["id"] == str(
        deploy.solution_entity_id(INSTALL_ID, TABLE_ID)
    )
    assert remapped.apps[0]["id"] == str(deploy.solution_entity_id(INSTALL_ID, APP_ID))
    assert remapped.claims[0]["id"] == str(
        deploy.solution_entity_id(INSTALL_ID, CLAIM_ID)
    )
    assert remapped.config_schemas[0]["id"] == str(
        deploy.solution_entity_id(INSTALL_ID, CONFIG_SCHEMA_ID)
    )
    assert remapped.events[0]["id"] == str(
        deploy.solution_entity_id(INSTALL_ID, EVENT_ID)
    )

    remapped_form = remapped.forms[0]
    assert remapped_form["id"] == str(deploy.solution_entity_id(INSTALL_ID, FORM_ID))
    assert remapped_form["workflow_id"] == str(mapped_workflow_id)
    assert remapped_form["launch_workflow_id"] == "workflows/main.py::run"
    assert remapped_form["form_schema"]["fields"][0]["data_provider_id"] == str(
        mapped_workflow_id
    )
    assert remapped_form["form_schema"]["fields"][1]["data_provider_id"] == str(
        EXTERNAL_WORKFLOW_ID
    )

    remapped_agent = remapped.agents[0]
    assert remapped_agent["id"] == str(mapped_agent_id)
    assert remapped_agent["tool_ids"] == [
        str(mapped_workflow_id),
        "workflows/main.py::run",
    ]
    assert remapped_agent["delegated_agent_ids"] == [str(mapped_delegated_agent_id)]

    remapped_subscription = remapped.events[0]["subscriptions"][0]
    assert remapped_subscription["id"] == str(mapped_subscription_id)
    assert remapped_subscription["workflow_id"] == str(mapped_workflow_id)
    assert remapped_subscription["agent_id"] == str(mapped_agent_id)

    assert bundle.workflows[0]["id"] == str(WORKFLOW_ID)
    assert bundle.forms[0]["form_schema"]["fields"][0]["data_provider_id"] == str(
        WORKFLOW_ID
    )
    assert bundle.agents[0]["delegated_agent_ids"] == [str(DELEGATED_AGENT_ID)]
    assert remapped.python_files is bundle.python_files
    assert remapped.file_locations == ["workspace"]
    assert remapped.version == "1.2.3"
    assert remapped.readme == "README"


@pytest.mark.asyncio
async def test_remapped_bundle_preserves_ids_already_owned_by_install():
    db = _OwnerLookupDb(owned_by_install={PRESERVED_WORKFLOW_ID})
    deployer = deploy.SolutionDeployer(db=db)
    new_workflow_id = uuid4()
    bundle = deploy.SolutionBundle(
        solution=SimpleNamespace(id=INSTALL_ID),
        workflows=[
            {"id": str(PRESERVED_WORKFLOW_ID), "name": "existing"},
            {"id": str(new_workflow_id), "name": "new"},
        ],
    )

    remapped = await deployer._remapped_bundle(bundle)

    assert remapped.workflows[0]["id"] == str(PRESERVED_WORKFLOW_ID)
    assert remapped.workflows[1]["id"] == str(
        deploy.solution_entity_id(INSTALL_ID, new_workflow_id)
    )
    assert db.queries == 2
