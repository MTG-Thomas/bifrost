"""PostgreSQL coverage for immutable Workspace registration activation."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from bifrost.workspace_release import canonical_digest
from src.services import workspace_release_activation as activation_module
from src.services.workflow_registration import find_workspace_workflow
from src.services.workspace_release_activation import (
    WorkspaceReleaseActivationService,
    registration_state_fingerprint,
)


@pytest.mark.e2e
async def test_net_new_registration_activates_through_real_transaction(
    db_session,
    org1,
) -> None:
    """A create intent must load relationships without async lazy IO."""

    organization_id = org1["id"]
    workflow_id = uuid4()
    path = f"features/test/net_new_{workflow_id.hex}.py"
    function_name = "net_new_registration"
    intent = [
        {
            "action": "create",
            "path": path,
            "function_name": function_name,
            "requested_id": str(workflow_id),
            "type": "workflow",
            "name": "Net-new registration",
            "organization_id": str(organization_id),
        }
    ]
    intent_fingerprint = canonical_digest(
        {
            "schema": activation_module.REGISTRATION_INTENT_SCHEMA,
            "actions": intent,
        }
    )
    state_fingerprint = registration_state_fingerprint(None)
    expected = {
        "path": path,
        "function": function_name,
        "workflow_id": str(workflow_id),
        "type": "workflow",
        "name": "Net-new registration",
        "organization_id": str(organization_id),
        "is_active": True,
        "source_sha256": "a" * 64,
        "runtime_bounds": {
            "max_duration_seconds": 30,
            "max_external_calls": 10,
            "max_records_read": 100,
            "max_output_bytes": 4096,
        },
        "access_level": "role_based",
        "role_ids": [],
        "endpoint_enabled": False,
        "public_endpoint": False,
        "api_key_enabled": False,
    }
    artifact = SimpleNamespace(
        registration_intent_fingerprint=intent_fingerprint,
        registration_state_fingerprint=state_fingerprint,
        manifest={
            "entry": {"path": path, "function": function_name},
            "registration": {
                "intent": intent,
                "intent_fingerprint": intent_fingerprint,
                "state": None,
                "state_fingerprint": state_fingerprint,
            },
            "effective_registrations": {f"{path}::{function_name}": expected},
        },
    )

    applied = await WorkspaceReleaseActivationService(
        db_session, organization_id
    )._apply_registration(artifact)

    assert applied == [{**intent[0], "workflow_id": str(workflow_id)}]
    workflow = await find_workspace_workflow(
        db_session,
        organization_id=organization_id,
        path=path,
        function_name=function_name,
    )
    assert workflow is not None
    assert workflow.path == path
    assert workflow.function_name == function_name
    assert workflow.roles == []
