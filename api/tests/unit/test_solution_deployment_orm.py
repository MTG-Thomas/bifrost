from sqlalchemy.orm import configure_mappers

from src.models.orm.solution_deployments import (
    SolutionDeployment,
    SolutionDeploymentDependency,
)
from src.models.orm.solutions import Solution
from src.repositories.solution_deployments import SolutionDeploymentRepository


def test_solution_deployment_schema_exposes_runtime_closure_and_exact_edges():
    configure_mappers()
    deployment_columns = SolutionDeployment.__table__.columns
    dependency_columns = SolutionDeploymentDependency.__table__.columns

    assert deployment_columns["compiled_manifest"].nullable is False
    assert deployment_columns["compiled_manifest_hash"].nullable is False
    assert deployment_columns["resolution_map"].nullable is False
    assert deployment_columns["resolution_map_hash"].nullable is False
    assert deployment_columns["organization_id"].nullable is True
    assert deployment_columns["source_artifact_key"].nullable is False
    assert dependency_columns["dependency_deployment_id"].nullable is False
    assert "active_deployment_id" in Solution.__table__.columns

    deployment_constraints = {c.name for c in SolutionDeployment.__table__.constraints}
    dependency_constraints = {
        c.name for c in SolutionDeploymentDependency.__table__.constraints
    }
    solution_constraints = {c.name for c in Solution.__table__.constraints}
    assert "fk_solution_deployment_parent_same_solution" in deployment_constraints
    assert "fk_solution_deployment_base_same_solution" in deployment_constraints
    assert "fk_solution_dependency_exact_deployment" in dependency_constraints
    assert "fk_solutions_active_deployment" in solution_constraints


def test_deployment_repository_does_not_expose_unscoped_crud():
    assert not hasattr(SolutionDeploymentRepository, "get_by_id")
    assert not hasattr(SolutionDeploymentRepository, "get_all")
    assert not hasattr(SolutionDeploymentRepository, "update")
    assert not hasattr(SolutionDeploymentRepository, "delete")
