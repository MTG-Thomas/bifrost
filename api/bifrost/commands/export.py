"""CLI command ``bifrost export`` — workspace bundle export.

Implements Task 14 of the CLI mutation surface plan.

Shape:

* ``bifrost export <out-dir>`` — no-scrub dump. Writes the regenerated
  manifest (via ``GET /api/files/manifest``) and copies workflow + app
  source files from the current workspace.
* ``bifrost export --portable <out-dir>`` — same, but pipes the manifest
  through :func:`bifrost.portable.scrub` first, producing a bundle that
  can be shared with the community or imported into a different
  environment. Writes ``bundle.meta.yaml`` documenting the scrub.

Out-of-scope for this command:

* **Code scrubbing.** Workflow ``.py`` and app source files are copied
  verbatim — the portability contract only applies to ``.bifrost/*.yaml``.
* **Import.** ``bifrost import`` (Task 15) is the reverse direction.
"""

from __future__ import annotations

import pathlib
import shutil
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urlparse

import click
import yaml

from bifrost.client import BifrostClient
from bifrost.portable import scrub

from .base import pass_resolver, run_async

# Top-level source directories copied into the bundle alongside .bifrost/.
# Kept narrow intentionally: a portable bundle is manifest + the code
# files the manifest references, not the entire workspace.
_CODE_DIRS: tuple[str, ...] = ("workflows", "apps")


@click.group(name="export", help="Export a workspace bundle.")
def export_group() -> None:
    """Top-level ``bifrost export`` group.

    Registered from :mod:`bifrost.cli` alongside ``sync`` / ``push`` /
    ``pull`` rather than through ``ENTITY_GROUPS`` — ``export`` is a
    workspace-level operation, not an entity mutation.
    """


def _parse_manifest_files(manifest_files: dict[str, str]) -> dict[str, Any]:
    """Merge a ``{filename: yaml_content}`` map into one manifest dict.

    Mirrors :func:`bifrost.manifest.parse_manifest_dir` but returns a plain
    ``dict`` rather than a Pydantic ``Manifest`` so :func:`scrub` can
    mutate freely without running every Pydantic validator against a
    half-scrubbed payload.
    """
    from bifrost.manifest import MANIFEST_FILES

    merged: dict[str, Any] = {}
    for key, filename in MANIFEST_FILES.items():
        content = manifest_files.get(filename)
        if not content or not content.strip():
            continue
        data = yaml.safe_load(content)
        if isinstance(data, dict) and key in data:
            merged[key] = data[key]
    return merged


def _serialize_manifest_dict(manifest: dict[str, Any]) -> dict[str, str]:
    """Serialize a manifest dict back to ``{filename: yaml_content}``."""
    from bifrost.manifest import MANIFEST_FILES

    files: dict[str, str] = {}
    for key, filename in MANIFEST_FILES.items():
        section = manifest.get(key)
        if not section:
            continue
        if isinstance(section, dict):
            section = dict(sorted(section.items()))
        files[filename] = (
            yaml.dump(
                {key: section},
                default_flow_style=False,
                sort_keys=True,
                allow_unicode=True,
            ).rstrip("\n")
            + "\n"
        )
    return files


def _copy_code_tree(source_root: pathlib.Path, dest_root: pathlib.Path) -> None:
    """Copy ``workflows/`` and ``apps/`` from the current workspace to ``dest_root``.

    Skips anything outside :data:`_CODE_DIRS`. The bundle is intentionally
    narrow: manifest plus the code files the manifest references.
    """
    for top in _CODE_DIRS:
        src = source_root / top
        if not src.is_dir():
            continue
        dst = dest_root / top
        # ``dirs_exist_ok`` is 3.8+. We recreate deterministically by
        # wiping the destination first so re-exports don't pick up stale
        # files from a prior run.
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "node_modules",
                ".venv",
                ".git",
            ),
        )


def _bifrost_version() -> str:
    """Return the installed ``bifrost`` package version, or ``"unknown"``."""
    try:
        return version("bifrost")
    except PackageNotFoundError:
        return "unknown"


def _source_env_from_client(client: BifrostClient) -> str:
    """Extract a ``hostname:port`` label from the client's ``api_url``.

    Falls back to the full URL if parsing fails; bundle.meta.yaml is a
    documentation artefact, so "unknown" is not useful here.
    """
    parsed = urlparse(client.api_url)
    host = parsed.hostname or client.api_url
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


async def _fetch_manifest(client: BifrostClient) -> dict[str, str]:
    """Fetch the regenerated manifest from ``GET /api/files/manifest``."""
    response = await client.get("/api/files/manifest")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Unexpected /api/files/manifest payload: {type(data).__name__}"
        )
    return data


async def _fetch_role_names(client: BifrostClient) -> dict[str, str]:
    """Build a ``{role_id: role_name}`` map from ``GET /api/roles``."""
    response = await client.get("/api/roles")
    response.raise_for_status()
    roles = response.json()
    mapping: dict[str, str] = {}
    if isinstance(roles, list):
        for role in roles:
            if not isinstance(role, dict):
                continue
            role_id = role.get("id")
            name = role.get("name")
            if role_id and name:
                mapping[str(role_id)] = str(name)
    return mapping


def _write_manifest_files(
    manifest_files: dict[str, str], bifrost_dir: pathlib.Path
) -> None:
    """Write ``.bifrost/*.yaml`` files into ``bifrost_dir``."""
    bifrost_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in manifest_files.items():
        (bifrost_dir / filename).write_text(content)


def _write_meta_file(
    out_dir: pathlib.Path,
    *,
    source_env: str,
    scrubbed_rules: list[str] | None,
) -> None:
    """Write ``bundle.meta.yaml`` documenting the export.

    ``scrubbed_rules`` is ``None`` for a non-portable export (the meta
    file still records source + version for traceability) and a list of
    rule descriptions for a portable export.
    """
    meta: dict[str, Any] = {
        "source_env": source_env,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "bifrost_version": _bifrost_version(),
        "portable": scrubbed_rules is not None,
        "scrubbed": scrubbed_rules if scrubbed_rules is not None else [],
    }
    (out_dir / "bundle.meta.yaml").write_text(
        yaml.dump(meta, default_flow_style=False, sort_keys=True)
    )


async def _export_impl(
    *,
    client: BifrostClient,
    out_dir: pathlib.Path,
    portable: bool,
    workspace_root: pathlib.Path,
) -> None:
    """Shared implementation between ``export`` and its tests.

    Split from the Click callback so tests can drive the export path
    without routing through :class:`click.testing.CliRunner` every time.
    """
    manifest_files = await _fetch_manifest(client)

    rules_applied: list[str] | None = None
    if portable:
        manifest_dict = _parse_manifest_files(manifest_files)
        role_names = await _fetch_role_names(client)
        scrubbed, rules_applied = scrub(
            manifest_dict, role_names_by_id=role_names
        )
        manifest_files = _serialize_manifest_dict(scrubbed)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest_files(manifest_files, out_dir / ".bifrost")
    _copy_code_tree(workspace_root, out_dir)
    _write_meta_file(
        out_dir,
        source_env=_source_env_from_client(client),
        scrubbed_rules=rules_applied,
    )


@export_group.command("dump")
@click.argument(
    "out_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=pathlib.Path),
)
@click.option(
    "--portable",
    is_flag=True,
    default=False,
    help="Strip env-specific fields (org IDs, timestamps, secrets) for sharing.",
)
@click.option(
    "--workspace",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=pathlib.Path),
    default=None,
    help="Workspace root to copy workflows/ and apps/ from (default: cwd).",
)
@click.pass_context
@pass_resolver
@run_async
async def export_dump(
    ctx: click.Context,  # noqa: ARG001 - required for @pass_resolver plumbing
    out_dir: pathlib.Path,
    *,
    portable: bool,
    workspace: pathlib.Path | None,
    client: BifrostClient,
    resolver: Any,  # noqa: ARG001 - unused but required by pass_resolver
) -> None:
    """Write a workspace bundle to ``OUT_DIR``.

    Without ``--portable``, produces a full-fidelity dump suitable for
    local round-trip. With ``--portable``, scrubs env-specific fields and
    records the scrub rules in ``bundle.meta.yaml``.
    """
    workspace_root = (workspace or pathlib.Path.cwd()).resolve()
    await _export_impl(
        client=client,
        out_dir=out_dir.resolve(),
        portable=portable,
        workspace_root=workspace_root,
    )
    click.echo(str(out_dir.resolve()))


def handle_export(args: list[str]) -> int:
    """Dispatch ``bifrost export`` from :func:`bifrost.cli.main`.

    Shimmed into a positional CLI where ``export`` takes an optional
    ``--portable`` flag and a single ``<out-dir>`` positional, invoking
    the Click callback directly.
    """
    # Accept both ``bifrost export <dir>`` and ``bifrost export dump <dir>``.
    # The top-level form drops the ``dump`` subcommand for ergonomics.
    if not args or args[0] != "dump":
        args = ["dump", *args]
    try:
        export_group.main(
            args=args, standalone_mode=False, prog_name="bifrost export"
        )
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.UsageError as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


__all__ = ["export_group", "handle_export"]
