"""Focused coverage for Solution contract model behavior."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.solutions import (
    DependencyRef,
    OutsideReference,
    PullAckEntity,
    PullAckRequest,
    Solution,
    SolutionCaptureCandidates,
    SolutionCaptureRequest,
    SolutionConfigSchemaChange,
    SolutionConfigSchemaState,
    SolutionDependencyPreview,
    SolutionDependencyPreviewRequest,
    SolutionEntityCounts,
    SolutionEntityDiff,
    SolutionEntitySummary,
    SolutionExistingInstall,
    SolutionExportJobsList,
    SolutionFileSummary,
    SolutionInstallPreview,
    SolutionReadme,
    SolutionReadmeUpdate,
    SolutionRepoPreviewRequest,
    SolutionSetupItem,
    SolutionSetupStatus,
    SolutionUpdate,
    SolutionUpgradeDiff,
    SolutionsList,
    UnmetNeed,
)


def test_solution_scope_is_derived_from_organization_id():
    org_id = uuid4()
    global_solution = Solution(id=uuid4(), slug="global", name="Global")
    org_solution = Solution(id=uuid4(), slug="org", name="Org", organization_id=org_id)

    assert global_solution.scope == "global"
    assert org_solution.scope == "org"
    assert global_solution.model_dump()["entity_counts"] == SolutionEntityCounts().model_dump()


def test_solution_update_distinguishes_omitted_from_explicit_null_org():
    omitted = SolutionUpdate(name="Renamed")
    explicit_global = SolutionUpdate(name="Renamed", organization_id=None)

    assert "organization_id" not in omitted.model_fields_set
    assert "organization_id" in explicit_global.model_fields_set
    assert omitted.model_dump(exclude_unset=True) == {"name": "Renamed"}
    assert explicit_global.model_dump(exclude_unset=True) == {
        "name": "Renamed",
        "organization_id": None,
    }


def test_solution_install_preview_and_diff_models_serialize_aliases():
    existing = SolutionExistingInstall(id=uuid4(), name="Installed", version="1.0.0")
    change = SolutionConfigSchemaChange(
        key="API_KEY",
        from_=SolutionConfigSchemaState(type="string", required=False),
        to=SolutionConfigSchemaState(type="secret", required=True),
    )
    diff = SolutionUpgradeDiff(
        workflows=SolutionEntityDiff(added=["new"], removed=["old"]),
        config_schemas={"changed": [change]},
    )
    preview = SolutionInstallPreview(
        slug="helpdesk",
        name="Helpdesk",
        scope="org",
        version="2.0.0",
        workflows=[{"id": str(uuid4()), "name": "main"}],
        existing_install=existing,
        diff=diff,
        requires_password=True,
        readme="# Helpdesk\n",
    )

    payload = preview.model_dump(by_alias=True)

    assert payload["existing_install"]["version"] == "1.0.0"
    assert payload["diff"]["workflows"]["added"] == ["new"]
    assert payload["diff"]["config_schemas"]["changed"][0]["from"] == {
        "type": "string",
        "required": False,
    }
    assert payload["requires_password"] is True


def test_dependency_preview_models_preserve_selection_and_warning_shape():
    workflow_id = str(uuid4())
    table_id = str(uuid4())
    preview = SolutionDependencyPreview(
        pulled_in=[
            DependencyRef(
                kind="workflow",
                ref=workflow_id,
                name="Ticket Triage",
                in_selection=True,
            ),
            DependencyRef(kind="module", ref="modules/shared.py", name="shared"),
        ],
        outside_references=[
            OutsideReference(
                referencer_kind="agent",
                referencer_ref=str(uuid4()),
                referencer_name="Dispatcher",
                target_kind="table",
                target_ref=table_id,
                target_name="Tickets",
            )
        ],
    )
    unmet = UnmetNeed(kind="module", ref="modules/missing.py", detail="imported")

    assert preview.scan_is_static is True
    assert preview.pulled_in[0].in_selection is True
    assert preview.outside_references[0].target_ref == table_id
    assert unmet.model_dump() == {
        "kind": "module",
        "ref": "modules/missing.py",
        "detail": "imported",
    }


def test_collection_defaults_are_independent_across_request_models():
    first_capture = SolutionCaptureRequest()
    second_capture = SolutionCaptureRequest()
    first_ack = PullAckRequest()
    second_ack = PullAckRequest()
    jobs = SolutionExportJobsList()

    first_capture.configs.append("API_KEY")
    first_ack.entities.append(PullAckEntity(entity_type="config", entity_id="API_KEY"))

    assert second_capture.configs == []
    assert second_ack.entities == []
    assert jobs.jobs == []


def test_summary_and_status_contracts_cover_optional_fields():
    created_at = datetime.now(timezone.utc)
    summary = SolutionEntitySummary(
        id=uuid4(),
        name="Desk",
        slug="desk",
        path="apps/desk",
        app_model="standalone_v2",
        logo="data:image/png;base64,abc",
        created_at=created_at,
    )
    candidates = SolutionCaptureCandidates(apps=[summary])
    setup = SolutionSetupStatus(
        setup_complete=False,
        items=[
            SolutionSetupItem(
                key="Halo",
                type="integration",
                required=True,
                is_set=False,
                kind="connection",
                has_oauth=True,
                connected=False,
            )
        ],
    )

    assert candidates.apps[0].logo == "data:image/png;base64,abc"
    assert setup.items[0].kind == "connection"
    assert setup.items[0].has_oauth is True


def test_repo_preview_requires_repo_url_and_readme_can_clear():
    with pytest.raises(ValidationError):
        SolutionRepoPreviewRequest(repo_url="")

    request = SolutionDependencyPreviewRequest(workflows=[uuid4()], include_imports=True)
    readme_update = SolutionReadmeUpdate(readme=None)
    readme = SolutionReadme(readme="# Docs\n")
    file_summary = SolutionFileSummary(location="workspace", path="docs/a.txt", size=123)
    solutions = SolutionsList(
        solutions=[Solution(id=uuid4(), slug="helpdesk", name="Helpdesk")]
    )

    assert request.include_imports is True
    assert readme_update.model_dump(exclude_unset=True) == {"readme": None}
    assert readme.readme == "# Docs\n"
    assert file_summary.size == 123
    assert solutions.solutions[0].scope == "global"
