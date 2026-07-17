from fastapi import FastAPI

from src.models.contracts.solution_deployments import SolutionDeploymentCapabilities
from src.routers.solution_deployments import router


def test_solution_deployment_openapi_exposes_minimal_cs_surface():
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    paths = schema["paths"]

    base = "/api/solutions/{solution_id}/deployments"
    item = f"{base}/{{deployment_id}}"
    assert set(paths[base]) >= {"post"}
    assert set(paths[f"{base}/capabilities"]) >= {"get"}
    assert set(paths[item]) >= {"get"}
    assert set(paths[f"{item}/activate"]) >= {"post"}
    assert set(paths[f"{item}/rollback"]) >= {"post"}
    create_schema = paths[base]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert create_schema["$ref"].endswith("SolutionDeploymentCreate")
    assert "multipart/form-data" not in paths[base]["post"]["requestBody"]["content"]


def test_capabilities_fail_closed_for_unconfigured_end_to_end_deployment():
    capabilities = SolutionDeploymentCapabilities()

    assert capabilities.registration is True
    assert capabilities.inspection is True
    assert capabilities.artifact_upload is False
    assert capabilities.server_side_compilation is False
    assert capabilities.activation_configured is False
    assert capabilities.safe_for_end_to_end_cs_deploy is False
