"""Registration and policy contract for Workspace release preparation."""

from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.workspace_release_prepare import (
    WORKSPACE_RELEASE_PREPARE_DEFINITION,
)


def test_workspace_release_prepare_is_a_bounded_durable_job() -> None:
    definition = get_platform_job_definition("workspace.release.prepare")

    assert definition is WORKSPACE_RELEASE_PREPARE_DEFINITION
    assert definition.payload_version == 1
    assert definition.policy.max_attempts == 2
    assert definition.policy.max_concurrency == 2
    assert definition.policy.timeout_seconds == 15 * 60
