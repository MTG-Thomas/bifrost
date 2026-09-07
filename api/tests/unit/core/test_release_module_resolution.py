"""Targeted imports must use immutable source even before mutable projection."""

import hashlib
import json
from unittest.mock import MagicMock

import pytest

from src.core import module_cache_sync as cache
from src.services.execution.virtual_import import VirtualModuleFinder


@pytest.fixture
def release(monkeypatch):
    prefix = "_workspace_releases/test/"
    source = {
        "features/archive/new_helper.py": "VALUE = 'immutable'\n",
        "features/archive/pkg/__init__.py": "VALUE = 'package'\n",
    }
    hashes = {path: hashlib.sha256(body.encode()).hexdigest() for path, body in source.items()}
    cache.set_workspace_release_context(
        "sha256:" + "a" * 64, runtime_storage_prefix=prefix, source_hashes=hashes
    )
    monkeypatch.setattr(cache, "get_solution_context", lambda: None)
    redis = MagicMock()
    redis.get.side_effect = lambda key: next(
        (
            json.dumps({"content": body, "hash": hashes[path], "path": path})
            for path, body in source.items()
            if key == f"{cache.MODULE_KEY_PREFIX}{prefix}{path}"
        ),
        None,
    )
    monkeypatch.setattr(cache, "_get_sync_redis", lambda: redis)
    for name in (
        "_get_cached_module_resolution", "_get_exact_scoped_module",
        "_fetch_module_resolution_from_api",
    ):
        monkeypatch.setattr(cache, name, MagicMock(side_effect=AssertionError("mutable lookup")))
    try:
        yield prefix, source, redis
    finally:
        cache.clear_workspace_release_context()


def test_new_immutable_helper_loads_without_mutable_projection(release):
    spec = VirtualModuleFinder().find_spec("features.archive.new_helper")
    assert spec is not None
    assert spec.loader is not None
    # Execute the real virtual loader, not a mocked source-resolution result.
    import importlib.util

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.VALUE == "immutable"


def test_manifest_packages_namespaces_and_missing_modules(release):
    assert cache.resolve_module_sync("features").kind == "namespace"
    package = cache.resolve_module_sync("features.archive.pkg")
    assert package.kind == "package"
    assert package.storage_path == release[0] + "features/archive/pkg/__init__.py"
    assert cache.resolve_module_sync("features.archive.absent").kind == "not_found"
    assert cache.resolve_module_sync("features.arch").kind == "not_found"


def test_missing_immutable_bytes_never_fall_back_to_mutable(release, monkeypatch):
    monkeypatch.setattr(cache, "get_module_sync", lambda path: None)
    with pytest.raises(cache.ModuleResolutionError, match="Immutable release module unavailable"):
        cache.resolve_module_sync("features.archive.new_helper")


def test_tampered_immutable_bytes_are_rejected(release, monkeypatch):
    monkeypatch.setattr(cache, "get_module_sync", lambda path: {
        "content": "VALUE = 'mutable'", "hash": "a" * 64, "path": path,
    })
    with pytest.raises(RuntimeError, match="integrity mismatch"):
        cache.resolve_module_sync("features.archive.new_helper")
