from sqlalchemy.orm import configure_mappers

from src.models.orm.solution_deployments import (
    SolutionDeployment,
    SolutionDeploymentDependency,
)
from src.models.orm.solutions import Solution


def test_solution_deployment_schema_exposes_runtime_closure_and_exact_edges():
    configure_mappers()
    deployment_columns = SolutionDeployment.__table__.columns
    dependency_columns = SolutionDeploymentDependency.__table__.columns

    assert deployment_columns["compiled_manifest"].nullable is False
    assert deployment_columns["compiled_manifest_hash"].nullable is False
    assert deployment_columns["resolution_map"].nullable is False
    assert deployment_columns["source_artifact_key"].nullable is False
    assert dependency_columns["dependency_deployment_id"].nullable is False
    assert "active_deployment_id" in Solution.__table__.columns
