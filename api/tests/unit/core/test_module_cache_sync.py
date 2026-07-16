import importlib
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

def _module_cache_sync() -> Any:
    return cast(Any, importlib.import_module("src.core.module_cache_sync"))


def test_s3_client_disabled_when_provider_is_azure_blob(monkeypatch):
    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "azure_blob")
    monkeypatch.setenv("BIFROST_S3_ACCESS_KEY", "access")
    monkeypatch.setenv("BIFROST_S3_SECRET_KEY", "secret")

    module_cache_sync = _module_cache_sync()
    module_cache_sync._s3_available = None
    module_cache_sync._s3_client = None

    assert module_cache_sync._get_s3_client() is None


def test_object_storage_provider_prefers_blob_when_blob_configured(monkeypatch):
    monkeypatch.delenv("BIFROST_OBJECT_STORAGE_PROVIDER", raising=False)
    monkeypatch.setenv("BIFROST_AZURE_BLOB_ACCOUNT_URL", "https://example.blob.core.windows.net")
    monkeypatch.setenv("BIFROST_AZURE_BLOB_CONTAINER", "bifrost-objects")

    module_cache_sync = _module_cache_sync()

    assert module_cache_sync._object_storage_provider() == "azure_blob"


def test_get_engine_credentials_reads_credentials(monkeypatch):
    module_cache_sync = _module_cache_sync()

    monkeypatch.setattr(
        "bifrost.credentials.get_credentials",
        lambda: {
            "api_url": "https://bifrost.example.com/",
            "access_token": "token-1",
        },
    )

    assert module_cache_sync._get_engine_credentials() == (
        "https://bifrost.example.com",
        "token-1",
    )


def test_get_engine_credentials_returns_none_when_unavailable(monkeypatch):
    module_cache_sync = _module_cache_sync()

    def raise_missing():
        raise RuntimeError("no credentials")

    monkeypatch.setattr("bifrost.credentials.get_credentials", raise_missing)

    assert module_cache_sync._get_engine_credentials() is None


def test_fetch_module_from_api_handles_success_and_http_statuses(monkeypatch):
    module_cache_sync = _module_cache_sync()
    monkeypatch.setattr(
        module_cache_sync,
        "_get_engine_credentials",
        lambda: ("https://api.example", "token"),
    )
    calls: list[tuple[str, dict, float]] = []

    class Httpx:
        def __init__(self, status_code: int, body: dict | None = None):
            self.status_code = status_code
            self.body = body or {}

        def get(self, url, headers, timeout):
            calls.append((url, headers, timeout))
            return SimpleNamespace(
                status_code=self.status_code,
                json=lambda: self.body,
            )

    monkeypatch.setitem(
        __import__("sys").modules,
        "httpx",
        Httpx(200, {"content": "print(1)", "path": "modules/a.py", "hash": "h"}),
    )

    assert module_cache_sync._fetch_module_from_api("modules/a.py") == {
        "content": "print(1)",
        "path": "modules/a.py",
        "hash": "h",
    }
    assert calls[-1] == (
        "https://api.example/api/sdk/modules/modules/a.py",
        {"Authorization": "Bearer token"},
        10.0,
    )

    monkeypatch.setitem(__import__("sys").modules, "httpx", Httpx(404))
    assert module_cache_sync._fetch_module_from_api("missing.py") is None

    monkeypatch.setitem(__import__("sys").modules, "httpx", Httpx(500))
    assert module_cache_sync._fetch_module_from_api("error.py") is None


def test_fetch_module_index_from_api_and_requirements(monkeypatch):
    module_cache_sync = _module_cache_sync()
    monkeypatch.setattr(
        module_cache_sync,
        "_get_engine_credentials",
        lambda: ("https://api.example", "token"),
    )

    class Httpx:
        def get(self, url, headers, timeout):
            if url.endswith("/api/sdk/modules-index"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"paths": ["modules/a.py", "workflows/b.py"]},
                )
            if url.endswith("/api/sdk/requirements"):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"content": "requests==2.32.0\n"},
                )
            raise AssertionError(url)

    monkeypatch.setitem(__import__("sys").modules, "httpx", Httpx())

    assert module_cache_sync._fetch_module_index_from_api() == {
        "modules/a.py",
        "workflows/b.py",
    }
    assert module_cache_sync._fetch_requirements_from_api() == "requests==2.32.0\n"


def test_fetch_api_helpers_return_empty_on_missing_credentials(monkeypatch):
    module_cache_sync = _module_cache_sync()
    monkeypatch.setattr(module_cache_sync, "_get_engine_credentials", lambda: None)

    assert module_cache_sync._fetch_module_from_api("modules/a.py") is None
    assert module_cache_sync._fetch_module_index_from_api() == set()
    assert module_cache_sync._fetch_requirements_from_api() is None


def test_storage_path_to_object_key_handles_repo_and_solution_paths():
    module_cache_sync = _module_cache_sync()

    assert (
        module_cache_sync._storage_path_to_s3_key("modules/a.py")
        == "_repo/modules/a.py"
    )
    assert (
        module_cache_sync._storage_path_to_s3_key("_solutions/sol-1/modules/a.py")
        == "_solutions/sol-1/modules/a.py"
    )


def test_list_blob_modules_returns_python_paths(monkeypatch):
    module_cache_sync = _module_cache_sync()
    client = MagicMock()
    client.list_blobs.return_value = [
        SimpleNamespace(name="_repo/modules/a.py"),
        SimpleNamespace(name="_repo/modules/readme.md"),
        SimpleNamespace(name="_repo/workflows/b.py"),
    ]
    monkeypatch.setattr(module_cache_sync, "_get_blob_container_client", lambda: client)

    assert module_cache_sync._list_blob_modules() == {
        "modules/a.py",
        "workflows/b.py",
    }


def test_solution_has_submodules_uses_s3_when_solution_context_active(monkeypatch):
    module_cache_sync = _module_cache_sync()
    solution_id = uuid4()
    client = MagicMock()
    client.list_objects_v2.return_value = {"KeyCount": 1}

    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("BIFROST_S3_BUCKET", "bucket")
    monkeypatch.setattr(module_cache_sync, "_get_s3_client", lambda: client)

    module_cache_sync.set_solution_context(solution_id, global_repo_access=False)
    try:
        assert module_cache_sync.solution_has_submodules("modules") is True
    finally:
        module_cache_sync.clear_solution_context()

    client.list_objects_v2.assert_called_once_with(
        Bucket="bucket",
        Prefix=f"_solutions/{solution_id}/modules/",
        MaxKeys=1,
    )


def test_solution_has_submodules_uses_blob_when_configured(monkeypatch):
    module_cache_sync = _module_cache_sync()
    solution_id = uuid4()
    client = MagicMock()
    client.list_blobs.return_value = iter([SimpleNamespace(name="x")])

    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "azure_blob")
    monkeypatch.setattr(module_cache_sync, "_get_blob_container_client", lambda: client)

    module_cache_sync.set_solution_context(solution_id, global_repo_access=False)
    try:
        assert module_cache_sync.solution_has_submodules("modules") is True
    finally:
        module_cache_sync.clear_solution_context()

    client.list_blobs.assert_called_once_with(
        name_starts_with=f"_solutions/{solution_id}/modules/"
    )


def test_candidate_paths_respect_solution_context():
    module_cache_sync = _module_cache_sync()
    solution_id = uuid4()

    assert module_cache_sync.candidate_module_paths("modules/a.py") == ["modules/a.py"]
    assert module_cache_sync.candidate_index_prefixes("modules") == ["modules/"]

    module_cache_sync.set_solution_context(solution_id, global_repo_access=False)
    try:
        assert module_cache_sync.candidate_module_paths("modules/a.py") == [
            f"_solutions/{solution_id}/modules/a.py"
        ]
        assert module_cache_sync.candidate_index_prefixes("modules") == [
            f"_solutions/{solution_id}/modules/"
        ]
    finally:
        module_cache_sync.clear_solution_context()

    deployment_id = uuid4()
    module_cache_sync.set_solution_context(
        solution_id,
        global_repo_access=False,
        runtime_storage_prefix=f"_solutions/{solution_id}/{deployment_id}/",
    )
    try:
        assert module_cache_sync.candidate_module_paths("modules/a.py") == [
            f"_solutions/{solution_id}/{deployment_id}/modules/a.py"
        ]
    finally:
        module_cache_sync.clear_solution_context()

    module_cache_sync.set_solution_context(solution_id, global_repo_access=True)
    try:
        assert module_cache_sync.candidate_module_paths("/modules/a.py") == [
            f"_solutions/{solution_id}/modules/a.py",
            "/modules/a.py",
        ]
        assert module_cache_sync.candidate_index_prefixes("modules/") == [
            f"_solutions/{solution_id}/modules/",
            "modules/",
        ]
    finally:
        module_cache_sync.clear_solution_context()


def test_get_module_sync_returns_redis_hit_without_fallbacks(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = json.dumps(
        {"content": "print(1)", "path": "modules/a.py", "hash": "h"}
    )
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(
        module_cache_sync,
        "_fetch_module_from_api",
        MagicMock(side_effect=AssertionError("should not fetch API")),
    )

    assert module_cache_sync.get_module_sync("modules/a.py") == {
        "content": "print(1)",
        "path": "modules/a.py",
        "hash": "h",
    }
    redis_client.get.assert_called_once_with(
        f"{module_cache_sync.MODULE_KEY_PREFIX}modules/a.py"
    )


def test_get_module_sync_recaches_api_fallback(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = None
    module = {"content": "print(2)", "path": "modules/a.py", "hash": "api"}
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(module_cache_sync, "_fetch_module_from_api", lambda path: module)
    monkeypatch.setattr(
        module_cache_sync,
        "_get_object_storage_module",
        MagicMock(side_effect=AssertionError("API hit should stop fallback")),
    )

    assert module_cache_sync.get_module_sync("modules/a.py") == module
    redis_client.setex.assert_called_once_with(
        f"{module_cache_sync.MODULE_KEY_PREFIX}modules/a.py",
        module_cache_sync.MODULE_CACHE_TTL,
        json.dumps(module),
    )
    redis_client.sadd.assert_called_once_with(
        module_cache_sync.MODULE_INDEX_KEY,
        "modules/a.py",
    )


def test_get_module_sync_builds_module_from_object_storage(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = None
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(module_cache_sync, "_fetch_module_from_api", lambda path: None)
    monkeypatch.setattr(
        module_cache_sync,
        "_get_object_storage_module",
        lambda path: b"print('storage')",
    )

    result = module_cache_sync.get_module_sync("modules/a.py")

    assert result["content"] == "print('storage')"
    assert result["path"] == "modules/a.py"
    assert len(result["hash"]) == 64
    redis_client.setex.assert_called_once()
    redis_client.sadd.assert_called_once_with(
        module_cache_sync.MODULE_INDEX_KEY,
        "modules/a.py",
    )


def test_deployment_import_rejects_manifest_hash_mismatch(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.get.return_value = json.dumps(
        {"content": "tampered", "path": "modules/a.py", "hash": "bad"}
    )
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    module_cache_sync.set_solution_context(
        "solution",
        False,
        runtime_storage_prefix="_solutions/solution/deployment/",
        source_hashes={"modules/a.py": "sha256:expected"},
    )
    try:
        with pytest.raises(RuntimeError, match="import integrity mismatch"):
            module_cache_sync.get_module_sync("modules/a.py")
    finally:
        module_cache_sync.clear_solution_context()


def test_get_module_index_sync_repopulates_from_api_then_storage(monkeypatch):
    module_cache_sync = _module_cache_sync()
    redis_client = MagicMock()
    redis_client.smembers.return_value = set()
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(
        module_cache_sync,
        "_fetch_module_index_from_api",
        lambda: {"modules/a.py", "workflows/b.py"},
    )

    assert module_cache_sync.get_module_index_sync() == {
        "modules/a.py",
        "workflows/b.py",
    }
    redis_client.sadd.assert_called_once()
    redis_client.expire.assert_called_once_with(
        module_cache_sync.MODULE_INDEX_KEY,
        module_cache_sync.MODULE_CACHE_TTL,
    )

    redis_client = MagicMock()
    redis_client.smembers.return_value = set()
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: redis_client)
    monkeypatch.setattr(module_cache_sync, "_fetch_module_index_from_api", set)
    monkeypatch.setattr(
        module_cache_sync,
        "_list_object_storage_modules",
        lambda: {"modules/storage.py"},
    )

    assert module_cache_sync.get_module_index_sync() == {"modules/storage.py"}
    redis_client.sadd.assert_called_once_with(
        module_cache_sync.MODULE_INDEX_KEY,
        "modules/storage.py",
    )
