"""Pure workspace promotion bundle compilation shared by CLI and API.

The client discovers one coherent local snapshot and sends only the selected
workflow's forward repo-local Python closure.  The server runs the same parser
again against the submitted bytes and the complete path/hash manifest; client
claims are never sufficient to make an incomplete bundle eligible.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import pathlib
import subprocess
import unicodedata
from collections import deque
from dataclasses import dataclass
from typing import Iterable

PROMOTION_BUNDLE_SCHEMA = "bifrost.workspace-promotion-bundle/v1"
MAX_SNAPSHOT_FILES = 4_000
MAX_CLOSURE_FILES = 200
MAX_CLOSURE_BYTES = 4 * 1024 * 1024


class PromotionBundleError(ValueError):
    """The local or submitted promotion bundle is incomplete or incoherent."""


@dataclass(frozen=True)
class PromotionBundle:
    snapshot_id: str
    snapshot_files: dict[str, str]
    files: tuple[dict[str, str], ...]


def normalize_workspace_path(value: str) -> str:
    path = pathlib.PurePosixPath(value.replace("\\", "/").strip("/"))
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PromotionBundleError(f"invalid workspace path: {value!r}")
    return path.as_posix()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def snapshot_id(snapshot_files: dict[str, str]) -> str:
    canonical = json.dumps(
        {
            normalize_workspace_path(path): digest
            for path, digest in snapshot_files.items()
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256_bytes(canonical)}"


def discover_python_snapshot(
    root: pathlib.Path,
) -> tuple[dict[str, str], dict[str, bytes]]:
    """Read tracked and untracked, non-ignored Python files from one stable tree."""
    root = root.resolve()
    command = [
        "git",
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        "*.py",
    ]

    def list_paths() -> list[str]:
        try:
            result = subprocess.run(
                command, cwd=root, check=True, capture_output=True, text=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise PromotionBundleError(
                "promotion preview requires a Git workspace"
            ) from exc
        return sorted(
            {
                normalize_workspace_path(line)
                for line in result.stdout.splitlines()
                if line
            }
        )

    paths = list_paths()
    if not paths or len(paths) > MAX_SNAPSHOT_FILES:
        raise PromotionBundleError(
            f"snapshot must contain 1-{MAX_SNAPSHOT_FILES} Python files"
        )

    _reject_path_collisions(paths)
    for path in paths:
        candidate = root / path
        if candidate.is_symlink():
            raise PromotionBundleError(
                f"symlinks are not eligible for promotion: {path}"
            )

    def read_all() -> dict[str, bytes]:
        return {path: (root / path).read_bytes() for path in paths}

    first = read_all()
    second = read_all()
    first_hashes = {path: sha256_bytes(content) for path, content in first.items()}
    second_hashes = {path: sha256_bytes(content) for path, content in second.items()}
    if first_hashes != second_hashes or paths != list_paths():
        raise PromotionBundleError(
            "workspace changed while promotion snapshot was read"
        )
    return first_hashes, second


def _module_name(path: str) -> str:
    parts = list(pathlib.PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_index(snapshot_paths: Iterable[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in snapshot_paths:
        normalized = normalize_workspace_path(path)
        if not normalized.endswith(".py"):
            continue
        module = _module_name(normalized)
        if not module:
            continue
        if previous := index.get(module):
            raise PromotionBundleError(
                f"ambiguous Python module {module!r}: {previous}, {normalized}"
            )
        index[module] = normalized
    return index


def _reject_path_collisions(paths: Iterable[str]) -> None:
    identities: dict[str, str] = {}
    for raw_path in paths:
        path = normalize_workspace_path(raw_path)
        identity = unicodedata.normalize("NFC", path).casefold()
        if previous := identities.get(identity):
            raise PromotionBundleError(
                f"case/Unicode-colliding workspace paths: {previous}, {path}"
            )
        identities[identity] = path


def _resolve_imports(path: str, raw: bytes, modules: dict[str, str]) -> set[str]:
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise PromotionBundleError(f"cannot parse {path}: {exc}") from exc
    current_module = _module_name(path)
    package_parts = current_module.split(".")
    if pathlib.PurePosixPath(path).name != "__init__.py":
        package_parts = package_parts[:-1]
    resolved: set[str] = set()

    def add_module(name: str) -> None:
        parts = name.split(".")
        for length in range(len(parts), 0, -1):
            candidate = ".".join(parts[:length])
            target = modules.get(candidate)
            if target:
                resolved.add(target)
                return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_module(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base_parts = package_parts[:]
            if node.level:
                trim = node.level - 1
                base_parts = (
                    base_parts[: len(base_parts) - trim] if trim else base_parts
                )
            elif node.module:
                base_parts = []
            module_parts = (node.module or "").split(".") if node.module else []
            base = ".".join([*base_parts, *module_parts])
            if base:
                add_module(base)
            for alias in node.names:
                if alias.name != "*":
                    add_module(".".join(value for value in (base, alias.name) if value))
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id in {"import_module", "__import__"}
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"import_module", "__import__"}
                )
            )
        ):
            add_module(node.args[0].value)
    resolved.discard(path)
    return resolved


def build_promotion_bundle(root: pathlib.Path, selected_path: str) -> PromotionBundle:
    selected_path = normalize_workspace_path(selected_path)
    snapshot_files, contents = discover_python_snapshot(root)
    if selected_path not in snapshot_files:
        raise PromotionBundleError(
            f"selected path is not in the Git workspace: {selected_path}"
        )
    modules = _module_index(snapshot_files)
    closure: set[str] = set()
    queue = deque([selected_path])
    while queue:
        path = queue.popleft()
        if path in closure:
            continue
        closure.add(path)
        for dependency in sorted(_resolve_imports(path, contents[path], modules)):
            if dependency not in closure:
                queue.append(dependency)
    if len(closure) > MAX_CLOSURE_FILES:
        raise PromotionBundleError(
            f"dependency closure exceeds {MAX_CLOSURE_FILES} files; reviewed promotion is required"
        )
    total = sum(len(contents[path]) for path in closure)
    if total > MAX_CLOSURE_BYTES:
        raise PromotionBundleError(
            f"dependency closure exceeds {MAX_CLOSURE_BYTES} bytes; reviewed promotion is required"
        )
    files = tuple(
        {
            "path": path,
            "sha256": snapshot_files[path],
            "content_base64": base64.b64encode(contents[path]).decode("ascii"),
        }
        for path in sorted(closure)
    )
    return PromotionBundle(
        snapshot_id=snapshot_id(snapshot_files),
        snapshot_files=snapshot_files,
        files=files,
    )


def dependency_edges(contents: dict[str, bytes]) -> dict[str, set[str]]:
    """Return importer -> repo-local imports for one complete Python inventory."""
    normalized = {normalize_workspace_path(path): raw for path, raw in contents.items()}
    _reject_path_collisions(normalized)
    modules = _module_index(normalized)
    return {
        path: _resolve_imports(path, raw, modules)
        for path, raw in sorted(normalized.items())
    }


def validate_submitted_bundle(
    *,
    selected_path: str,
    snapshot_id_value: str,
    snapshot_files: dict[str, str],
    files: Iterable[dict[str, str]],
) -> dict[str, bytes]:
    """Rebuild closure evidence from submitted bytes; raise on any mismatch."""
    selected_path = normalize_workspace_path(selected_path)
    if len(snapshot_files) > MAX_SNAPSHOT_FILES:
        raise PromotionBundleError("snapshot path manifest is too large")
    normalized_snapshot = {
        normalize_workspace_path(path): digest
        for path, digest in snapshot_files.items()
    }
    _reject_path_collisions(normalized_snapshot)
    for path, digest in normalized_snapshot.items():
        if not path.endswith(".py"):
            raise PromotionBundleError(f"snapshot contains a non-Python path: {path}")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise PromotionBundleError(
                f"snapshot contains an invalid SHA-256 for {path}"
            )
    if snapshot_id(normalized_snapshot) != snapshot_id_value:
        raise PromotionBundleError("snapshot_id does not match the path/hash manifest")
    decoded: dict[str, bytes] = {}
    for item in files:
        path = normalize_workspace_path(item["path"])
        if path in decoded:
            raise PromotionBundleError(f"duplicate closure path: {path}")
        try:
            raw = base64.b64decode(item["content_base64"], validate=True)
        except Exception as exc:
            raise PromotionBundleError(f"invalid base64 content for {path}") from exc
        digest = sha256_bytes(raw)
        if item.get("sha256") != digest or normalized_snapshot.get(path) != digest:
            raise PromotionBundleError(f"content hash mismatch for {path}")
        decoded[path] = raw
    if selected_path not in decoded:
        raise PromotionBundleError("selected workflow path is absent from closure")
    if (
        len(decoded) > MAX_CLOSURE_FILES
        or sum(map(len, decoded.values())) > MAX_CLOSURE_BYTES
    ):
        raise PromotionBundleError("submitted closure exceeds promotion limits")
    modules = _module_index(normalized_snapshot)
    required: set[str] = set()
    queue = deque([selected_path])
    while queue:
        path = queue.popleft()
        if path in required:
            continue
        required.add(path)
        raw = decoded.get(path)
        if raw is None:
            raise PromotionBundleError(f"dependency closure is missing {path}")
        for dependency in sorted(_resolve_imports(path, raw, modules)):
            if dependency not in required:
                queue.append(dependency)
    extras = set(decoded) - required
    if extras:
        raise PromotionBundleError(
            "closure contains unrelated paths: " + ", ".join(sorted(extras))
        )
    return decoded


def git_source_revision(root: pathlib.Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip().lower()
    return (
        value
        if len(value) == 40 and all(c in "0123456789abcdef" for c in value)
        else None
    )


__all__ = [
    "PROMOTION_BUNDLE_SCHEMA",
    "PromotionBundle",
    "PromotionBundleError",
    "build_promotion_bundle",
    "dependency_edges",
    "git_source_revision",
    "normalize_workspace_path",
    "sha256_bytes",
    "snapshot_id",
    "validate_submitted_bundle",
]
