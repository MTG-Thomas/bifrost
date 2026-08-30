from dataclasses import fields

from bifrost import workspace_effects as sdk_contract
from shared import workspace_effects as runtime_contract


def test_downloadable_sdk_effect_contract_matches_platform_runtime() -> None:
    assert sdk_contract.WORKFLOW_EFFECT_KINDS == runtime_contract.WORKFLOW_EFFECT_KINDS
    assert [item.name for item in fields(sdk_contract.WorkflowEffect)] == [
        item.name for item in fields(runtime_contract.WorkflowEffect)
    ]
    assert [item.name for item in fields(sdk_contract.WorkflowBounds)] == [
        item.name for item in fields(runtime_contract.WorkflowBounds)
    ]


def test_downloadable_sdk_normalizes_the_same_declaration() -> None:
    declaration = [
        {"kind": "integration.read", "target": "Microsoft Graph"},
        {"kind": "bifrost.read", "target": "organizations"},
    ]
    sdk_effects = sdk_contract.normalize_workflow_effects(declaration)
    runtime_effects = runtime_contract.normalize_workflow_effects(declaration)

    assert [(effect.kind, effect.target) for effect in sdk_effects or ()] == [
        (effect.kind, effect.target) for effect in runtime_effects or ()
    ]
