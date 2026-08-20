"""Isolated import smoke for one fully materialized Workspace release tree."""

from __future__ import annotations

import importlib
import importlib.util
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


def _load_module_from_path(
    name: str,
    path: Path,
    *,
    package_locations: list[str] | None = None,
) -> None:
    spec = importlib.util.spec_from_file_location(
        name,
        path,
        submodule_search_locations=package_locations,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"trusted SDK module {name!r} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise


def _load_trusted_sdk(sdk_package: Path, effects_contract: Path) -> None:
    sdk_package = sdk_package.resolve(strict=True)
    effects_contract = effects_contract.resolve(strict=True)
    sdk_init = sdk_package / "__init__.py"
    if (
        sdk_package.name != "bifrost"
        or not sdk_init.is_file()
        or effects_contract.name != "_bifrost_workspace_effects.py"
        or effects_contract.parent != sdk_package.parent
    ):
        raise RuntimeError("trusted SDK paths do not identify the bundled SDK")

    for name in list(sys.modules):
        if (
            name == "bifrost"
            or name.startswith("bifrost.")
            or name == "_bifrost_workspace_effects"
        ):
            del sys.modules[name]

    _load_module_from_path("_bifrost_workspace_effects", effects_contract)
    _load_module_from_path(
        "bifrost",
        sdk_init,
        package_locations=[str(sdk_package)],
    )


def run_smoke(
    root: Path,
    entry_path: str,
    entry_function: str,
    sdk_package: Path,
    effects_contract: Path,
) -> dict[str, object]:
    """Import from the immutable tree with only the bundled SDK preloaded."""
    if not sys.flags.isolated:
        raise RuntimeError("candidate import smoke requires isolated Python mode")
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
    _load_trusted_sdk(sdk_package, effects_contract)
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
        "sdk_source": "trusted_bundled_sdk",
    }


if __name__ == "__main__":
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: workspace_release_smoke ROOT PATH FUNCTION SDK EFFECTS"
        )
    print(
        json.dumps(
            run_smoke(
                Path(sys.argv[1]),
                sys.argv[2],
                sys.argv[3],
                Path(sys.argv[4]),
                Path(sys.argv[5]),
            )
        )
    )
