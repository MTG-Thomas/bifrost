"""Canonical authoritative/checkout/Git-tree snapshot semantics."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from git.objects.tree import Tree

from shared.sync_content_hash import normalize_line_endings
from src.services.repo_storage import RepoStorage


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(normalize_line_endings(content)).hexdigest()


def _revision(file_hashes: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, content_hash in sorted(file_hashes.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(content_hash.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _is_authored_path(path: str) -> bool:
    from src.services.editor.file_filter import is_excluded_path

    return not path.endswith("/") and not is_excluded_path(path)


@dataclass(frozen=True)
class WorkspaceSnapshot:
    revision: str
    file_hashes: dict[str, str]
    root_revisions: dict[str, str]


def build_snapshot(file_hashes: dict[str, str]) -> WorkspaceSnapshot:
    normalized = {
        path: content_hash
        for path, content_hash in file_hashes.items()
        if _is_authored_path(path)
    }
    roots: dict[str, dict[str, str]] = {}
    for path, content_hash in normalized.items():
        root = path.split("/", 1)[0]
        roots.setdefault(root, {})[path] = content_hash
    return WorkspaceSnapshot(
        revision=_revision(normalized),
        file_hashes=dict(sorted(normalized.items())),
        root_revisions={root: _revision(files) for root, files in sorted(roots.items())},
    )


async def snapshot_repo_storage(repo: RepoStorage | None = None) -> WorkspaceSnapshot:
    storage = repo or RepoStorage()
    paths = sorted(path for path in await storage.list() if _is_authored_path(path))
    file_hashes = {path: _content_hash(await storage.read(path)) for path in paths}
    return build_snapshot(file_hashes)


def snapshot_worktree(root: Path) -> WorkspaceSnapshot:
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if _is_authored_path(relative):
            files[relative] = _content_hash(path.read_bytes())
    return build_snapshot(files)


def snapshot_git_tree(tree: Tree) -> WorkspaceSnapshot:
    files: dict[str, str] = {}
    for item in tree.traverse():
        if item.type != "blob":
            continue
        path = str(item.path)
        if _is_authored_path(path):
            files[path] = _content_hash(item.data_stream.read())
    return build_snapshot(files)


def mismatch_paths(
    authoritative: WorkspaceSnapshot, remote: WorkspaceSnapshot
) -> list[str]:
    paths = set(authoritative.file_hashes) | set(remote.file_hashes)
    return sorted(
        path
        for path in paths
        if authoritative.file_hashes.get(path) != remote.file_hashes.get(path)
    )
