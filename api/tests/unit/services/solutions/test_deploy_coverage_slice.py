"""Focused branch coverage for solution deploy helpers."""

import base64
from enum import Enum
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.services.solutions import deploy


INSTALL_ID = UUID("11111111-1111-1111-1111-111111111111")
WORKFLOW_ID = UUID("22222222-2222-2222-2222-222222222222")
MAPPED_WORKFLOW_ID = UUID("33333333-3333-3333-3333-333333333333")


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
