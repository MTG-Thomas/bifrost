"""CLI commands for managing workspace files.

Implements the ``bifrost files`` sub-group. Each verb is a thin wrapper
around an ``api/bifrost/files.py`` SDK method, which in turn calls the
matching ``/api/files/*`` HTTP endpoint.

Verbs:

* ``bifrost files read <path> [--location LOC] [--solution SLUG|ID]``
* ``bifrost files stat <path> [--location LOC] [--solution SLUG|ID]``
* ``bifrost files graph <path> [--from-file F] [--direction DIR]``
* ``bifrost files write <path> (--content S | --from-file F | -) [--check-impact] [--create-only | --expected-version VERSION]``
* ``bifrost files list [directory] [--location LOC] [--solution SLUG|ID]``
* ``bifrost files delete <path> [--location LOC]`` -> SDK ``files.delete``
* ``bifrost files exists <path> [--location LOC]`` -> SDK ``files.exists``;
  exits 0 if exists, 1 if not
* ``bifrost files search <query> [--regex] [--case-sensitive]
  [--include GLOB] [--max-results N]`` -> SDK ``files.search``

The ``--solution`` flag targets the install scope for a solution install (by
slug or UUID).  It passes ``?solution=<install_id>`` to the API so the server
resolves the correct install-scoped storage prefix.

There is no ``mode`` flag -- workers always run in cloud mode; local mode is
for the laptop CLI where the user controls cwd directly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

import click
import yaml

import uuid as _uuid_module

from bifrost.client import BifrostClient
from bifrost.files import files as files_sdk

from .base import entity_group, output_result, pass_resolver, run_async

_FILES_GROUP_HELP = """Read, write, list, search files and manage file policies.

Without --solution, commands target the instance _repo source file scope
(location "workspace" by default). With --solution <slug|id>, read/write/list
target that Solution install's runtime file scope and default the location to
"solutions". Solution source files are deployed from the local workspace with
`bifrost solution deploy`; `bifrost files --solution ...` is for runtime/user
file bytes after install, not for editing deploy-owned source.

`bifrost files` is the direct authoring surface for instance _repo text source
and managed runtime/user files. For an existing file, record its version with
`files stat`, read it, then write with `--expected-version`. For a new path,
use `--create-only`. A conflict never overwrites the remote file.

\b
Examples:
  bifrost files list workflows/              # instance _repo files
  bifrost files write notes.txt --content hi --create-only
  bifrost files stat workflows/contact.py --json
  bifrost files graph workflows/contact.py --direction both
  bifrost files read apps/desk/pages/App.tsx # instance _repo file
  bifrost files list --solution desk         # Solution runtime files
  bifrost files read notes/today.txt --solution desk
"""

files_group = entity_group("files", _FILES_GROUP_HELP)
policies_group = click.Group("policies", help="Manage file access policies.")


_LOCATION_HELP = (
    'Storage location. Special: "workspace" (default), "temp", "uploads". '
    'Custom names (e.g. "reports") are accepted; "_repo", "_tmp", and "_apps" are blocked.'
)

_SOLUTION_HELP = (
    "Solution install slug or UUID. When given, targets that install's file scope "
    '(location defaults to "solutions"). Slug resolved via GET /api/solutions.'
)


async def _resolve_solution_install_id(client: BifrostClient, solution_ref: str) -> str:
    """Resolve a solution slug or UUID to the install id.

    If ``solution_ref`` is a valid UUID it is returned unchanged.  Otherwise the
    solutions list is fetched and matched by slug (first match wins — slugs are
    unique per scope/org).

    Raises :class:`click.ClickException` if resolution fails.
    """
    try:
        _uuid_module.UUID(solution_ref)
        return solution_ref
    except (ValueError, AttributeError):
        pass
    # Not a UUID — resolve by slug.
    resp = await client.get("/api/solutions")
    if resp.status_code != 200:
        raise click.ClickException(
            f"Failed to list solutions while resolving --solution "
            f"({resp.status_code}): {resp.text[:200]}"
        )
    installs = resp.json().get("solutions", [])
    matches = [s for s in installs if s.get("slug") == solution_ref]
    if len(matches) == 0:
        raise click.ClickException(
            f"No solution install found with slug {solution_ref!r}. "
            "Pass the install UUID directly or check `bifrost solutions list`."
        )
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        raise click.ClickException(
            f"Slug {solution_ref!r} is installed in multiple orgs ({ids}). "
            "Pass the install UUID directly to disambiguate."
        )
    return matches[0]["id"]


def _policy_path(path: str) -> str:
    """Encode a policy path for the REST route while preserving no slashes."""
    return quote(path.strip("/"), safe="")


def _policy_params(location: str, scope: str | None) -> dict[str, str]:
    params = {"location": location}
    if scope is not None:
        params["scope"] = scope
    return params


def _load_policy_document(path: str) -> list[dict] | dict:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if isinstance(loaded, dict) and "policies" in loaded:
        policies = loaded["policies"]
    else:
        policies = loaded
    if not isinstance(policies, (list, dict)):
        raise click.BadParameter(
            "policy file must contain a policies list or an object with a policies key"
        )
    return policies


def _json_requested(ctx: click.Context) -> bool:
    return bool(isinstance(ctx.obj, dict) and ctx.obj.get("json_output"))


def _render_impact(result: dict, *, ctx: click.Context) -> None:
    if _json_requested(ctx):
        output_result(result, ctx=ctx)
        return
    click.echo(f"Workspace impact: {result['path']}")
    click.echo(f"  candidate: {result['candidate_id']}")
    click.echo(f"  snapshot: {result['snapshot_id']}")
    click.echo(f"  proposed SHA-256: {result['proposed_sha256']}")
    current = result.get("current_sha256") or "<missing>"
    click.echo(f"  current SHA-256: {current}")
    click.echo(f"  changed: {'yes' if result.get('changed') else 'no'}")

    forward = result.get("forward_dependencies") or []
    click.echo(f"Forward dependencies ({len(forward)}):")
    for item in forward:
        click.echo(f"  -> [{item['depth']}] {item['path']}  {item['sha256']}")
    reverse = result.get("reverse_dependencies") or []
    click.echo(f"Reverse dependencies ({len(reverse)}):")
    for item in reverse:
        click.echo(f"  <- [{item['depth']}] {item['path']}  {item['sha256']}")

    diagnostics = result.get("diagnostics") or []
    if diagnostics:
        click.echo("Diagnostics:")
        for item in diagnostics:
            location = f" {item['path']}" if item.get("path") else ""
            click.echo(
                f"  [{item['severity']}] {item['code']}:{location} {item['message']}"
            )
    click.echo(
        "Ready for checked write: "
        + ("yes" if result.get("ready_to_write") else "no")
    )


async def _preview_impact(
    client: BifrostClient,
    *,
    path: str,
    content: str | None,
    direction: str = "both",
) -> dict:
    response = await client.post(
        "/api/files/impact",
        json={"path": path, "content": content, "direction": direction},
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise click.ClickException("Workspace impact endpoint returned invalid data")
    return result


@files_group.command("read")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option("--solution", "solution_ref", default=None, help=_SOLUTION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def read_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    solution_ref: str | None,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Read a workspace file and write its contents to stdout.

    Text files only. The SDK has `read_bytes` for binary; this CLI verb does not.
    Pass ``--solution`` to target a solution install's file scope.
    """
    if solution_ref is not None and location == "workspace":
        location = "solutions"
    if solution_ref is not None:
        install_id = await _resolve_solution_install_id(client, solution_ref)
        resp = await client.post(
            f"/api/files/read?solution={install_id}",
            json={"path": path, "location": location, "mode": "cloud", "binary": False},
        )
        if resp.status_code != 200:
            raise click.ClickException(
                f"read failed ({resp.status_code}): {resp.text[:200]}"
            )
        content = resp.json()["content"]
    else:
        content = await files_sdk.read(path, location=location)
    # Avoid output_result()'s key:value dict formatting; raw stdout is what
    # shell pipelines and agents expect from a `read` verb.
    click.echo(content, nl=False)


@files_group.command("stat")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option("--solution", "solution_ref", default=None, help=_SOLUTION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def stat_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    solution_ref: str | None,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Show existence, version, size, and last-edit metadata without content."""
    if solution_ref is not None and location == "workspace":
        location = "solutions"
    query = ""
    if solution_ref is not None:
        install_id = await _resolve_solution_install_id(client, solution_ref)
        query = f"?solution={install_id}"
    response = await client.post(
        f"/api/files/stat{query}",
        json={"path": path, "location": location, "mode": "cloud"},
    )
    response.raise_for_status()
    result = response.json()
    output_result(result, ctx=ctx)
    if not result["exists"]:
        sys.exit(1)


@files_group.command("graph")
@click.argument("path")
@click.option("--content", "content_flag", default=None, help="Proposed inline source.")
@click.option(
    "--from-file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Analyze proposed source from a local UTF-8 file.",
)
@click.option(
    "--direction",
    type=click.Choice(["forward", "reverse", "both"]),
    default="both",
    show_default=True,
)
@click.pass_context
@pass_resolver
@run_async
async def graph_cmd(
    ctx: click.Context,
    path: str,
    content_flag: str | None,
    from_file: str | None,
    direction: str,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Trace a Workspace Python file's forward and reverse dependency graph.

    Without a content option this inspects durable live bytes.  ``--content``
    or ``--from-file`` overlays proposed bytes without writing them.
    """
    if content_flag is not None and from_file is not None:
        raise click.UsageError("--content and --from-file cannot be combined.")
    content = (
        content_flag
        if content_flag is not None
        else Path(from_file).read_text(encoding="utf-8")
        if from_file is not None
        else None
    )
    result = await _preview_impact(
        client,
        path=path,
        content=content,
        direction=direction,
    )
    _render_impact(result, ctx=ctx)


@files_group.command("write")
@click.argument("path")
@click.argument("source", required=False)
@click.option("--content", "content_flag", default=None, help="Inline content to write.")
@click.option(
    "--from-file",
    "from_file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Read content from a local file.",
)
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option("--solution", "solution_ref", default=None, help=_SOLUTION_HELP)
@click.option(
    "--expected-version",
    default=None,
    help="Write only if the remote file still has this version from `files stat`.",
)
@click.option(
    "--create-only",
    is_flag=True,
    default=False,
    help="Create a new file; fail if the path already exists.",
)
@click.option(
    "--check-impact",
    is_flag=True,
    default=False,
    help=(
        "Preview the proposed Python dependency/reverse-dependency graph, then "
        "write only if the server recomputes the same blocker-free candidate."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def write_cmd(
    ctx: click.Context,
    path: str,
    source: str | None,
    content_flag: str | None,
    from_file: str | None,
    location: str,
    solution_ref: str | None,
    expected_version: str | None,
    create_only: bool,
    check_impact: bool,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Write to a workspace file. Source: --content, --from-file, or `-` for stdin.

    Text files only. Pass --content "" to truncate an existing file.
    Use --create-only for a new path or --expected-version for a guarded
    replacement. Pass ``--solution`` to target a solution install's file scope.
    """
    if create_only and expected_version is not None:
        raise click.UsageError(
            "--create-only and --expected-version cannot be combined."
        )
    sources = [s for s in (content_flag, from_file, source) if s is not None]
    if len(sources) != 1:
        raise click.UsageError(
            "Provide exactly one content source: --content, --from-file, or `-` for stdin."
        )

    if content_flag is not None:
        content = content_flag
    elif from_file is not None:
        content = Path(from_file).read_text(encoding="utf-8")
    elif source == "-":
        content = sys.stdin.read()
    else:
        # Positional source other than `-` is not allowed (avoids ambiguity
        # with shell expansion accidentally passing a filename).
        raise click.UsageError(
            "Positional content must be `-` for stdin. Use --content or --from-file otherwise."
        )

    if solution_ref is not None and location == "workspace":
        location = "solutions"
    query = ""
    if solution_ref is not None:
        install_id = await _resolve_solution_install_id(client, solution_ref)
        query = f"?solution={install_id}"
    impact_candidate_id = None
    if check_impact:
        if solution_ref is not None or location != "workspace":
            raise click.UsageError(
                "--check-impact currently supports instance Workspace files only."
            )
        if not path.endswith(".py"):
            raise click.UsageError("--check-impact currently supports Python files only.")
        impact = await _preview_impact(
            client,
            path=path,
            content=content,
        )
        _render_impact(impact, ctx=ctx)
        if not impact.get("ready_to_write"):
            raise click.ClickException(
                "Checked write blocked by Workspace impact diagnostics; no bytes were written."
            )
        impact_candidate_id = str(impact.get("candidate_id") or "")
        if not impact_candidate_id.startswith("sha256:"):
            raise click.ClickException(
                "Workspace impact preview did not return an immutable candidate."
            )

    response = await client.post(
        f"/api/files/write{query}",
        json={
            "path": path,
            "content": content,
            "location": location,
            "mode": "cloud",
            "binary": False,
            "expected_version": expected_version,
            "create_only": create_only,
            "impact_candidate_id": impact_candidate_id,
        },
    )
    response.raise_for_status()


@files_group.command("list")
@click.argument("directory", required=False, default="")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option("--solution", "solution_ref", default=None, help=_SOLUTION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def list_cmd(
    ctx: click.Context,
    directory: str,
    location: str,
    solution_ref: str | None,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """List files in a directory (default: location root).

    Pass ``--solution`` to target a solution install's file scope.
    """
    if solution_ref is not None and location == "workspace":
        location = "solutions"
    if solution_ref is not None:
        install_id = await _resolve_solution_install_id(client, solution_ref)
        resp = await client.post(
            f"/api/files/list?solution={install_id}",
            json={"directory": directory, "location": location, "mode": "cloud"},
        )
        if resp.status_code != 200:
            raise click.ClickException(
                f"list failed ({resp.status_code}): {resp.text[:200]}"
            )
        items = resp.json()["files"]
    else:
        items = await files_sdk.list(directory=directory, location=location)
    output_result(items, ctx=ctx)


@files_group.command("delete")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option(
    "--expected-version",
    default=None,
    help="Delete only if the remote file still has this version from `files stat`.",
)
@click.pass_context
@pass_resolver
@run_async
async def delete_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    expected_version: str | None,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Delete a workspace file, optionally guarded by its current version."""
    response = await client.post(
        "/api/files/delete",
        json={
            "path": path,
            "location": location,
            "mode": "cloud",
            "expected_version": expected_version,
        },
    )
    response.raise_for_status()


@files_group.command("exists")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def exists_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Check if a file exists. Exits 0 if yes, 1 if no (script-friendly)."""
    found = await files_sdk.exists(path, location=location)
    output_result({"exists": found}, ctx=ctx)
    if not found:
        sys.exit(1)


@files_group.command("search")
@click.argument("query")
@click.option("--regex", "is_regex", is_flag=True, default=False, help="Treat query as a regex.")
@click.option("--case-sensitive", "case_sensitive", is_flag=True, default=False)
@click.option(
    "--include",
    "include_pattern",
    default="**/*",
    help='Glob restricting which files to search (default: "**/*").',
)
@click.option(
    "--max-results",
    "max_results",
    type=click.IntRange(1, 10000),
    default=1000,
    help="Maximum results to return (default: 1000, max: 10000).",
)
@click.pass_context
@pass_resolver
@run_async
async def search_cmd(
    ctx: click.Context,
    query: str,
    is_regex: bool,
    case_sensitive: bool,
    include_pattern: str,
    max_results: int,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Search workspace file contents."""
    result = await files_sdk.search(
        query,
        case_sensitive=case_sensitive,
        is_regex=is_regex,
        include_pattern=include_pattern,
        max_results=max_results,
    )
    output_result(result, ctx=ctx)


@policies_group.command("list")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option("--scope", default=None, help="Organization UUID for org-scoped policies.")
@click.pass_context
@pass_resolver
@run_async
async def list_policies_cmd(
    ctx: click.Context,
    location: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """List file policies for a location and optional org scope."""
    response = await client.get(
        "/api/files/policies",
        params=_policy_params(location, scope),
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@policies_group.command("get")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option("--scope", default=None, help="Organization UUID for org-scoped policies.")
@click.pass_context
@pass_resolver
@run_async
async def get_policy_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Get the file policy for a path prefix."""
    response = await client.get(
        f"/api/files/policies/{_policy_path(path)}",
        params=_policy_params(location, scope),
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@policies_group.command("set")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option("--scope", default=None, help="Organization UUID for org-scoped policies.")
@click.option(
    "--file",
    "policy_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON/YAML policy document to store.",
)
@click.pass_context
@pass_resolver
@run_async
async def set_policy_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    scope: str | None,
    policy_file: str,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Create or replace the file policy for a path prefix."""
    response = await client.put(
        f"/api/files/policies/{_policy_path(path)}",
        params=_policy_params(location, scope),
        json={"policies": _load_policy_document(policy_file)},
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@policies_group.command("delete")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.option("--scope", default=None, help="Organization UUID for org-scoped policies.")
@click.pass_context
@pass_resolver
@run_async
async def delete_policy_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Delete the file policy for a path prefix."""
    response = await client.delete(
        f"/api/files/policies/{_policy_path(path)}",
        params=_policy_params(location, scope),
    )
    response.raise_for_status()
    output_result({"deleted": path, "location": location, "scope": scope}, ctx=ctx)


files_group.add_command(policies_group)


__all__ = ["files_group"]
