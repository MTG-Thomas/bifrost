from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.models.orm.solution_deployments import SolutionDeployment
from src.repositories.solution_deployments import (
    InvalidDeploymentTransition,
    SolutionDeploymentRepository,
)
from src.services.solutions.deployment_activation import (
    SolutionDeploymentActivationService,
)


def deployment(
    solution_id: UUID, state: str, base: UUID | None = None
) -> SolutionDeployment:
    return SolutionDeployment(
        id=uuid4(),
        organization_id=None,
        solution_id=solution_id,
        state=state,
        base_deployment_id=base,
        bundle_hash="sha256:bundle",
        compiled_manifest={},
        compiled_manifest_hash="sha256:manifest",
        resolution_map={},
        resolution_map_hash="sha256:resolution",
        source_artifact_key="source.zip",
        runtime_storage_prefix="runtime/",
        created_by=uuid4(),
    )


class FakeRepository:
    def __init__(self, deployments: list[SolutionDeployment], active: UUID | None):
        self.deployments = {item.id: item for item in deployments}
        self.active = active

    async def get_runtime_closure(
        self, deployment_id, organization_id, solution_id=None
    ):
        item = self.deployments.get(deployment_id)
        return (
            item
            if item is not None
            and item.organization_id == organization_id
            and (solution_id is None or item.solution_id == solution_id)
            else None
        )

    @asynccontextmanager
    async def projection_savepoint(self):
        yield

    async def transition(
        self, deployment_id, organization_id, *, expected_state, new_state, **values
    ):
        item = await self.get_runtime_closure(deployment_id, organization_id)
        if item is None or item.state != expected_state:
            raise ValueError("state changed")
        item.state = new_state
        for key, value in values.items():
            if value is not None:
                setattr(item, key, value)
        return item

    async def get_solution_active_deployment(self, solution_id, organization_id):
        return self.active

    async def compare_and_set_active_deployment(
        self,
        solution_id,
        organization_id,
        *,
        expected_active_deployment_id,
        new_active_deployment_id,
    ):
        if self.active != expected_active_deployment_id:
            return False
        self.active = new_active_deployment_id
        return True


class FakeHooks:
    def __init__(self, *, fail_verify: bool = False, fail_projection: bool = False):
        self.fail_verify = fail_verify
        self.fail_projection = fail_projection
        self.verified: list[UUID] = []
        self.projected: list[UUID] = []

    async def verify_finalized(self, item):
        self.verified.append(item.id)
        if self.fail_verify:
            raise OSError("artifact unavailable")

    async def rebuild_projections(self, item):
        self.projected.append(item.id)
        if self.fail_projection:
            raise RuntimeError("projection failed")


def service(repository: FakeRepository, hooks: FakeHooks):
    return SolutionDeploymentActivationService(
        cast(SolutionDeploymentRepository, cast(Any, repository)), hooks
    )


@pytest.mark.asyncio
async def test_two_drafts_from_one_base_have_deterministic_cas_conflict():
    solution_id = uuid4()
    current = deployment(solution_id, "active")
    first = deployment(solution_id, "ready", current.id)
    second = deployment(solution_id, "ready", current.id)
    repository = FakeRepository([current, first, second], current.id)
    hooks = FakeHooks()
    activation = service(repository, hooks)

    winner = await activation.activate(
        first.id, None, solution_id, expected_active_deployment_id=current.id
    )
    loser = await activation.activate(
        second.id, None, solution_id, expected_active_deployment_id=current.id
    )

    assert winner.state == "active"
    assert repository.active == first.id
    assert current.state == "superseded"
    assert loser.state == "conflicted"
    assert loser.conflict == {
        "operation": "activate",
        "solution_id": str(solution_id),
        "target_deployment_id": str(second.id),
        "expected_active_deployment_id": str(current.id),
        "actual_active_deployment_id": str(first.id),
    }
    assert hooks.projected == [first.id]


@pytest.mark.asyncio
async def test_rollback_reactivates_prior_immutable_deployment_by_cas():
    solution_id = uuid4()
    prior = deployment(solution_id, "superseded")
    current = deployment(solution_id, "active", prior.id)
    repository = FakeRepository([prior, current], current.id)
    activation = service(repository, FakeHooks())

    result = await activation.rollback(
        prior.id, None, solution_id, expected_active_deployment_id=current.id
    )

    assert result.state == "active"
    assert repository.active == prior.id
    assert prior.state == "active"
    assert current.state == "superseded"
    assert prior.bundle_hash == "sha256:bundle"


@pytest.mark.asyncio
async def test_artifact_failure_records_recovery_without_moving_pointer():
    solution_id = uuid4()
    current = deployment(solution_id, "active")
    candidate = deployment(solution_id, "ready", current.id)
    repository = FakeRepository([current, candidate], current.id)
    activation = service(repository, FakeHooks(fail_verify=True))

    result = await activation.activate(
        candidate.id, None, solution_id, expected_active_deployment_id=current.id
    )

    assert result.state == "recovery_required"
    assert repository.active == current.id
    assert result.recovery["stage"] == "artifact_finalization"
    assert result.recovery["target_deployment_id"] == str(candidate.id)


@pytest.mark.asyncio
async def test_projection_failure_records_pointer_moved_recovery_evidence():
    solution_id = uuid4()
    current = deployment(solution_id, "active")
    candidate = deployment(solution_id, "ready", current.id)
    repository = FakeRepository([current, candidate], current.id)
    activation = service(repository, FakeHooks(fail_projection=True))

    result = await activation.activate(
        candidate.id, None, solution_id, expected_active_deployment_id=current.id
    )

    assert result.state == "recovery_required"
    assert repository.active == candidate.id
    assert result.recovery["pointer_moved"] is True
    assert result.recovery["stage"] == "projection_or_pointer_finalization"


@pytest.mark.asyncio
async def test_activation_rejects_expected_pointer_that_is_not_draft_base():
    solution_id = uuid4()
    candidate = deployment(solution_id, "ready", uuid4())
    repository = FakeRepository([candidate], candidate.base_deployment_id)
    activation = service(repository, FakeHooks())
    unexpected_active_id = uuid4()

    with pytest.raises(ValueError, match="draft base"):
        await activation.activate(
            candidate.id,
            None,
            solution_id,
            expected_active_deployment_id=unexpected_active_id,
        )


@pytest.mark.asyncio
async def test_activation_rejects_deployment_from_different_solution_in_same_scope():
    route_solution_id = uuid4()
    actual_solution_id = uuid4()
    candidate = deployment(actual_solution_id, "ready", None)
    repository = FakeRepository([candidate], None)

    with pytest.raises(ValueError, match="missing or out of scope"):
        await service(repository, FakeHooks()).activate(
            candidate.id,
            None,
            route_solution_id,
            expected_active_deployment_id=None,
        )


@pytest.mark.asyncio
async def test_repository_transition_rules_allow_rollback_but_not_ready_to_active():
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = MagicMock(spec=SolutionDeployment)
    session.execute = AsyncMock(return_value=result)
    repository = SolutionDeploymentRepository(cast(Any, session))

    await repository.transition(
        uuid4(), None, expected_state="superseded", new_state="activating"
    )
    deployment_id = uuid4()
    with pytest.raises(InvalidDeploymentTransition, match="ready -> active"):
        await repository.transition(
            deployment_id, None, expected_state="ready", new_state="active"
        )


@pytest.mark.asyncio
async def test_pointer_cas_marks_solution_as_deployment_runtime():
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = uuid4()
    session.execute = AsyncMock(return_value=result)
    repository = SolutionDeploymentRepository(cast(Any, session))

    moved = await repository.compare_and_set_active_deployment(
        uuid4(),
        None,
        expected_active_deployment_id=None,
        new_active_deployment_id=uuid4(),
    )

    assert moved is True
    statement = session.execute.await_args.args[0]
    values = {column.key: value.value for column, value in statement._values.items()}
    assert values["execution_runtime_mode"] == "deployment-v1"
    assert "active_deployment_id" in values
