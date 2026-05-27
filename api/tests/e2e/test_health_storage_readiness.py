import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Response


pytestmark = pytest.mark.e2e

_HEALTH_PATH = Path(__file__).parents[2] / "src" / "routers" / "health.py"
_SPEC = importlib.util.spec_from_file_location("health_router_for_e2e", _HEALTH_PATH)
health = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(health)


class DummyDB:
    async def execute(self, statement):
        return None


def _azure_blob_settings(configured: bool = True):
    return SimpleNamespace(
        environment="test",
        object_storage_provider="azure_blob",
        redis_url="redis://redis-secret@example:6379/0",
        rabbitmq_url="amqp://rabbit-secret@example:5672/",
        s3_configured=False,
        s3_bucket=None,
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_region="us-east-1",
        azure_blob_configured=configured,
    )


async def _healthy_component(name: str, type_: str):
    return name, {"status": "healthy", "type": type_}


def _mock_core_dependency_checks(monkeypatch):
    monkeypatch.setattr(
        health,
        "check_database",
        lambda db: _healthy_component("database", "postgresql"),
    )
    monkeypatch.setattr(
        health, "check_redis", lambda settings: _healthy_component("redis", "redis")
    )
    monkeypatch.setattr(
        health,
        "check_rabbitmq",
        lambda settings: _healthy_component("rabbitmq", "rabbitmq"),
    )


@pytest.mark.asyncio
async def test_ready_health_check_uses_azure_blob_backend_when_configured(monkeypatch):
    settings = _azure_blob_settings()
    monkeypatch.setattr(health, "get_settings", lambda: settings)
    _mock_core_dependency_checks(monkeypatch)

    class FakeStorage:
        def __init__(self, actual_settings):
            assert actual_settings is settings

        async def close(self):
            return None

        def get_client(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return None

        async def head_bucket(self, *, Bucket):
            assert Bucket == ""

    monkeypatch.setattr(health, "AzureBlobStorageClient", FakeStorage)

    response = Response()
    result = await health.ready_health_check(response, DummyDB())

    assert response.status_code == 200
    assert result.status == "healthy"
    assert result.components["s3"] == {"status": "healthy", "type": "azure_blob"}


@pytest.mark.asyncio
async def test_ready_health_check_allows_unconfigured_azure_blob_backend(monkeypatch):
    monkeypatch.setattr(
        health, "get_settings", lambda: _azure_blob_settings(configured=False)
    )
    _mock_core_dependency_checks(monkeypatch)

    response = Response()
    result = await health.ready_health_check(response, DummyDB())

    assert response.status_code == 200
    assert result.status == "healthy"
    assert result.components["s3"] == {"status": "not_configured", "type": "azure_blob"}
