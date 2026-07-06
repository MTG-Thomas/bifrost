from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.services.solutions.update_check import (
    compute_update_available,
    fetch_remote_version,
)


def test_compute_update_available_handles_ordering_and_invalid_versions() -> None:
    assert compute_update_available(installed="1.0.0", remote="1.1.0") == "1.1.0"
    assert compute_update_available(installed="1.1.0", remote="1.1.0") is None
    assert compute_update_available(installed="1.2.0", remote="1.1.0") is None
    assert compute_update_available(installed=None, remote="1.0.0") == "1.0.0"
    assert compute_update_available(installed="local-dev", remote="1.0.0") == "1.0.0"
    assert compute_update_available(installed="1.0.0", remote="not pep440") is None
    assert compute_update_available(installed="1.0.0", remote=None) is None


def _install_descriptor_modules(monkeypatch, *, is_workspace: bool, version: str):
    descriptor = ModuleType("bifrost.solution_descriptor")
    descriptor.is_solution_workspace = lambda root: is_workspace
    descriptor.load_descriptor = lambda root: SimpleNamespace(version=version)
    package = ModuleType("bifrost")
    package.solution_descriptor = descriptor
    monkeypatch.setitem(sys.modules, "bifrost", package)
    monkeypatch.setitem(sys.modules, "bifrost.solution_descriptor", descriptor)


def _install_git_sync_module(monkeypatch, *, fail_clone: bool = False):
    calls = []
    git_sync = ModuleType("src.services.solutions.git_sync")

    async def clone_repo_to_dir(repo_url, work, ref=None):
        calls.append(("clone", repo_url, work, ref))
        if fail_clone:
            raise RuntimeError("clone failed")

    def resolve_repo_subpath(work, repo_subpath):
        calls.append(("resolve", work, repo_subpath))
        return work / (repo_subpath or "")

    git_sync.clone_repo_to_dir = clone_repo_to_dir
    git_sync.resolve_repo_subpath = resolve_repo_subpath
    monkeypatch.setitem(sys.modules, "src.services.solutions.git_sync", git_sync)
    return calls


@pytest.mark.asyncio
async def test_fetch_remote_version_returns_descriptor_version(monkeypatch):
    _install_descriptor_modules(monkeypatch, is_workspace=True, version="2.0.0")
    calls = _install_git_sync_module(monkeypatch)

    version = await fetch_remote_version(
        repo_url="https://example.test/repo.git",
        repo_subpath="solutions/demo",
        ref="main",
    )

    assert version == "2.0.0"
    assert calls[0][0] == "clone"
    assert calls[0][1] == "https://example.test/repo.git"
    assert calls[0][3] == "main"
    assert calls[1][0] == "resolve"
    assert calls[1][2] == "solutions/demo"


@pytest.mark.asyncio
async def test_fetch_remote_version_returns_none_for_clone_failure(monkeypatch):
    _install_descriptor_modules(monkeypatch, is_workspace=True, version="2.0.0")
    _install_git_sync_module(monkeypatch, fail_clone=True)

    assert (
        await fetch_remote_version(
            repo_url="https://example.test/repo.git",
            repo_subpath="solutions/demo",
            ref="main",
        )
        is None
    )


@pytest.mark.asyncio
async def test_fetch_remote_version_returns_none_for_non_solution_workspace(monkeypatch):
    _install_descriptor_modules(monkeypatch, is_workspace=False, version="2.0.0")
    _install_git_sync_module(monkeypatch)

    assert (
        await fetch_remote_version(
            repo_url="https://example.test/repo.git",
            repo_subpath=None,
            ref=None,
        )
        is None
    )
