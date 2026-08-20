"""Isolated import smoke for one fully materialized Workspace release tree."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path, PurePosixPath

WORKSPACE_ROOTS = {
    "agents",
    "apps",
    "features",
    "helpers",
    "integrations",
    "modules",
    "shared",
    "workflows",
}


def _module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def run_smoke(root: Path, entry_path: str, entry_function: str) -> dict[str, object]:
    """Import only from the supplied tree for every Workspace-owned root."""
    root = root.resolve(strict=True)
    for workspace_root in WORKSPACE_ROOTS:
        directory = root / workspace_root
        if not directory.exists():
            continue
        package_file = directory / "__init__.py"
        if not package_file.exists():
            package_file.write_bytes(b"")

    for name in list(sys.modules):
        if name.split(".", 1)[0] in WORKSPACE_ROOTS:
            del sys.modules[name]
    sys.path.insert(0, str(root))
    module_name = _module_name(entry_path)
    if not module_name:
        raise RuntimeError("entry path does not identify a module")
    module = importlib.import_module(module_name)
    function = getattr(module, entry_function, None)
    if not callable(function):
        raise RuntimeError(
            f"entry function {entry_function!r} is not callable in {entry_path}"
        )
    return {
        "module": module_name,
        "entry_path": entry_path,
        "entry_function": entry_function,
        "imported": True,
        "function_callable": True,
        "workspace_source_root": str(root),
    }


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: workspace_release_smoke ROOT PATH FUNCTION")
    print(json.dumps(run_smoke(Path(sys.argv[1]), sys.argv[2], sys.argv[3])))
