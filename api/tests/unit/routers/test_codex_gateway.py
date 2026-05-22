from asyncio import sleep

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routers.codex_gateway import get_codex_gateway_runtime, router
from src.services.codex_gateway.runtime import CodexGatewayResponse


class FakeRuntime:
    def __init__(self):
        self.calls = []

    async def create_response(self, **kwargs):
        await sleep(0)
        self.calls.append(kwargs)
        return CodexGatewayResponse(
            status_code=200,
            body={"id": "resp_route_test", "output": []},
        )


def test_v1_responses_uses_openai_compatible_bearer_key():
    app = FastAPI()
    app.include_router(router)
    runtime = FakeRuntime()
    app.dependency_overrides[get_codex_gateway_runtime] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer bfck_route_test"},
        json={"model": "gpt-5.1-codex", "input": "do not log me"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_route_test"
    [runtime_call] = runtime.calls
    assert runtime_call["gateway_key"] == "bfck_route_test"
    assert runtime_call["payload"] == {
        "model": "gpt-5.1-codex",
        "input": "do not log me",
    }
