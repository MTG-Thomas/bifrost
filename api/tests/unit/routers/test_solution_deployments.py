import sys
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI

# Import the focused router without eagerly importing every optional router
# dependency from src.routers.__init__.
routers_package = ModuleType("src.routers")
routers_package.__path__ = [str(Path(__file__).parents[3] / "src" / "routers")]
sys.modules["src.routers"] = routers_package

from src.routers.solution_deployments import router  # noqa: E402


def test_solution_deployment_openapi_exposes_minimal_cs_surface():
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    paths = schema["paths"]

    base = "/api/solutions/{solution_id}/deployments"
    item = f"{base}/{{deployment_id}}"
    assert set(paths[base]) >= {"post"}
    assert set(paths[item]) >= {"get"}
    assert set(paths[f"{item}/activate"]) >= {"post"}
    assert set(paths[f"{item}/rollback"]) >= {"post"}
    create_schema = paths[base]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert create_schema["$ref"].endswith("SolutionDeploymentCreate")
    assert "multipart/form-data" not in paths[base]["post"]["requestBody"]["content"]
