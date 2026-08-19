"""Immutable Workspace draft and reviewed promotion commands."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any, Mapping

from platformdirs import user_data_path

from bifrost import __version__
from bifrost.client import BifrostClient, raise_for_status_with_detail
from bifrost.contract_version import CONTRACT_VERSION
from bifrost.promotion import (
    PromotionBundle,
    PromotionBundleError,
    build_promotion_bundle,
    build_reviewed_promotion_bundle,
    discover_python_snapshot,
    normalize_workspace_path,
    refresh_protected_main,
    sha256_bytes,
    snapshot_id,
)
from bifrost.workspace_release import workspace_closure_id
from bifrost.workspace_impact import (
    WorkspaceImpactAnalysis,
    analyze_workspace_impact,
    reverse_edges,
    transitive_distances,
)

PROMOTION_PREVIEW_ENDPOINT = "/api/workspace-promotions/preview"
PROMOTION_CANARY_ENDPOINT = (
    "/api/workspace-promotions/artifacts/{artifact_id}/canary"
)
REVIEWED_PROMOTION_SCHEMA = "bifrost.workspace-promotion-bundle/v2"
DRAFT_SCHEMA = "bifrost.workspace-draft-upload/v1"
LOCAL_RUN_SCHEMA = "bifrost.workspace-local-run-evidence/v1"
_SUBCOMMANDS = {"draft", "canary", "preview", "activate", "status"}


def _closure_id(
    bundle: PromotionBundle,
    *,
    selected_path: str,
    function_name: str,
) -> str:
    """Use the runtime's canonical forward-closure identity."""

    entry = {
        "path": normalize_workspace_path(selected_path),
        "function": function_name,
    }
    files = {str(item["path"]): str(item["sha256"]) for item in bundle.files}
    return workspace_closure_id(entry, files)


def _common_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", help="Workspace-relative Python workflow path")
    parser.add_argument("--workflow", "-w", required=True, help="Decorated function")
    parser.add_argument(
        "--run-evidence",
        type=pathlib.Path,
        help="Optional local-only evidence emitted for the exact forward closure",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine JSON")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bifrost promote",
        description=(
            "Build a local-only immutable draft or preview exact protected-main bytes. "
            "Local draft bytes can never activate production."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    draft = subparsers.add_parser(
        "draft",
        help="Store an immutable local-only draft and complete graph evidence",
    )
    _common_source_arguments(draft)
    draft.add_argument(
        "--out",
        type=pathlib.Path,
        help="Write the draft to this path instead of the per-user Bifrost data directory",
    )

    canary = subparsers.add_parser(
        "canary",
        help="Execute an exact reviewed artifact without registration or triggers",
    )
    canary.add_argument("artifact_id", help="Artifact ID emitted by `promote preview`")
    canary.add_argument(
        "--params",
        default="{}",
        help="Workflow parameters as a JSON object",
    )
    canary.add_argument("--json", action="store_true", help="Emit machine JSON")

    preview = subparsers.add_parser(
        "preview",
        help="Preview exact reviewed Git bytes; does not activate",
    )
    _common_source_arguments(preview)
    preview.add_argument(
        "--supersedes-candidate-id",
        help="Candidate replaced by this immutable preview",
    )
    preview.add_argument(
        "--expected-base-release-id",
        help="Optional active-release CAS observed before preview",
    )

    return parser


def _normalize_selected(root: pathlib.Path, raw_path: str) -> str:
    selected = pathlib.Path(raw_path)
    if selected.is_absolute():
        try:
            return selected.resolve().relative_to(root).as_posix()
        except ValueError as exc:
            raise PromotionBundleError(
                "selected workflow must be inside the current Git workspace"
            ) from exc
    return normalize_workspace_path(selected.as_posix())


def _load_run_evidence(
    path: pathlib.Path | None,
    *,
    bundle: PromotionBundle,
    selected_path: str,
    function_name: str,
) -> dict[str, Any] | None:
    if path is None:
        return None
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(evidence, dict):
        raise PromotionBundleError("run evidence must be a JSON object")
    if evidence.get("schema_version") != LOCAL_RUN_SCHEMA:
        raise PromotionBundleError(
            "run evidence uses an unsupported schema; rerun with the current CLI"
        )
    if evidence.get("authority") != "local_only" or evidence.get("activatable") is not False:
        raise PromotionBundleError("run evidence must be explicitly local-only")
    if evidence.get("succeeded") is not True:
        raise PromotionBundleError("run evidence does not record a successful execution")
    expected_closure_id = _closure_id(
        bundle,
        selected_path=selected_path,
        function_name=function_name,
    )
    if evidence.get("closure_id") != expected_closure_id:
        raise PromotionBundleError(
            "run evidence does not match the selected workflow's current dependency closure"
        )
    if evidence.get("entry") != {"path": selected_path, "function": function_name}:
        raise PromotionBundleError("run evidence belongs to a different workflow entry")
    return evidence


def _graph_diagnostics(
    analysis: WorkspaceImpactAnalysis,
    *,
    selected_path: str,
    forward_paths: set[str],
    relevant_paths: set[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    blockers: list[dict[str, str]] = []

    def add(path: str, code: str, message: str) -> None:
        item = {"path": path, "code": code, "message": message}
        (blockers if path == selected_path or path in forward_paths else warnings).append(item)

    for path in sorted(relevant_paths):
        if values := analysis.unresolved_imports.get(path):
            add(path, "unresolved_repo_import", ", ".join(values))
        if values := analysis.ambiguous_references.get(path):
            add(path, "ambiguous_workflow_reference", ", ".join(values))
        if path in analysis.dynamic_importers:
            add(path, "dynamic_import_unresolved", "computed import cannot be proven statically")
        if path in analysis.dynamic_reference_importers:
            add(
                path,
                "dynamic_workflow_reference_unresolved",
                "computed workflow reference cannot be proven statically",
            )
    return warnings, blockers


def _local_graph(root: pathlib.Path, selected_path: str, bundle: PromotionBundle) -> dict[str, Any]:
    hashes, contents = discover_python_snapshot(root)
    if snapshot_id(hashes) != bundle.snapshot_id:
        raise PromotionBundleError("workspace changed while complete graph evidence was built")
    analysis = analyze_workspace_impact(contents)
    forward = transitive_distances(selected_path, analysis.edges)
    reverse = transitive_distances(selected_path, reverse_edges(analysis.edges))
    forward_paths = set(forward) - {selected_path}
    reverse_paths = set(reverse) - {selected_path}
    relevant = {selected_path, *forward_paths, *reverse_paths}
    warnings, blockers = _graph_diagnostics(
        analysis,
        selected_path=selected_path,
        forward_paths=forward_paths,
        relevant_paths=relevant,
    )
    edges = [
        {
            "importer": importer,
            "dependency": dependency,
            "kind": "registry"
            if (importer, dependency) in analysis.registry_edges
            else "import",
        }
        for importer, dependencies in sorted(analysis.edges.items())
        for dependency in sorted(dependencies)
        if importer in relevant and dependency in relevant
    ]
    return {
        "traversal_complete": True,
        "analyzed_path_count": len(relevant),
        "edge_count": len(edges),
        "forward_dependencies": [
            {"path": path, "sha256": hashes[path], "depth": forward[path]}
            for path in sorted(forward_paths)
        ],
        "reverse_dependencies": [
            {"path": path, "sha256": hashes[path], "depth": reverse[path]}
            for path in sorted(reverse_paths)
        ],
        "edges": edges,
        "analysis_warnings": warnings,
        "mutation_blockers": blockers,
    }


def _client_contract() -> dict[str, str]:
    return {
        "cli_version": __version__,
        "sdk_version": __version__,
        "contract_version": str(CONTRACT_VERSION),
    }


def _draft_id(payload: Mapping[str, Any]) -> str:
    identity = {
        "schema_version": payload["schema_version"],
        "entry": payload["entry"],
        "snapshot_id": payload["snapshot"]["snapshot_id"],
        "closure_id": payload["snapshot"]["closure_id"],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{sha256_bytes(encoded)}"


def _default_draft_path(draft_id: str) -> pathlib.Path:
    directory = user_data_path("bifrost", appauthor=False) / "workspace-promotions"
    return directory / f"{draft_id.removeprefix('sha256:')}.json"


def _write_private_json(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(path)


def _render_closure(bundle: PromotionBundle) -> None:
    print(f"Complete forward closure ({len(bundle.files)} files):")
    for item in bundle.files:
        print(f"  {item['sha256']}  {item['path']}")


def _handle_draft(options: argparse.Namespace) -> int:
    root = pathlib.Path.cwd().resolve()
    selected_path = _normalize_selected(root, options.path)
    bundle = build_promotion_bundle(root, selected_path)
    local_run = _load_run_evidence(
        options.run_evidence,
        bundle=bundle,
        selected_path=selected_path,
        function_name=options.workflow,
    )
    entry_closure_id = _closure_id(
        bundle,
        selected_path=selected_path,
        function_name=options.workflow,
    )
    payload: dict[str, Any] = {
        "schema_version": DRAFT_SCHEMA,
        "target": "draft",
        "authority": "local_only",
        "activatable": False,
        "entry": {"path": selected_path, "function": options.workflow},
        "snapshot": {
            "snapshot_id": bundle.snapshot_id,
            "closure_id": entry_closure_id,
            "files": bundle.snapshot_files,
            "closure": list(bundle.files),
        },
        "graph": _local_graph(root, selected_path, bundle),
        "local_run": local_run,
        "client": _client_contract(),
    }
    payload["draft_id"] = _draft_id(payload)
    output_path = options.out or _default_draft_path(payload["draft_id"])
    _write_private_json(output_path, payload)
    if options.json:
        result = {
            "schema_version": DRAFT_SCHEMA,
            "draft_id": payload["draft_id"],
            "authority": "local_only",
            "activatable": False,
            "entry": payload["entry"],
            "snapshot_id": bundle.snapshot_id,
            "closure_id": entry_closure_id,
            "closure": [
                {"path": item["path"], "sha256": item["sha256"]}
                for item in bundle.files
            ],
            "graph": payload["graph"],
            "local_run": local_run,
            "stored_at": str(output_path),
        }
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Draft: {payload['draft_id']}")
        print("Authority: local-only (cannot activate or register)")
        print(f"Stored: {output_path}")
        print(f"Snapshot: {bundle.snapshot_id}")
        print(f"Closure: {entry_closure_id}")
        graph = payload["graph"]
        print(
            "Graph: complete "
            f"({graph['analyzed_path_count']} connected paths, {graph['edge_count']} edges)"
        )
        print(f"Analysis warnings: {len(graph['analysis_warnings'])}")
        print(f"Mutation blockers: {len(graph['mutation_blockers'])}")
        _render_closure(bundle)
        print(
            "Next: use `bifrost run` locally, commit the result, then use "
            "`bifrost promote preview` against reviewed origin/main bytes."
        )
    return 0


def _handle_canary(options: argparse.Namespace) -> int:
    try:
        parameters = json.loads(options.params)
    except json.JSONDecodeError as exc:
        raise PromotionBundleError(f"canary parameters are not valid JSON: {exc}") from exc
    if not isinstance(parameters, dict):
        raise PromotionBundleError("canary parameters must be a JSON object")
    client = BifrostClient.get_instance(require_auth=True)
    canary_response = client.post_sync(
        PROMOTION_CANARY_ENDPOINT.format(artifact_id=options.artifact_id),
        json={"parameters": parameters},
    )
    raise_for_status_with_detail(canary_response)
    result = canary_response.json()
    if not isinstance(result, dict) or not result.get("execution_id"):
        raise PromotionBundleError("canary did not return an execution ID")
    if options.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Canary execution: {result['execution_id']}")
        print(f"Reviewed artifact: {options.artifact_id}")
        print("Canary only: no registration, trigger, or Live pointer change")
    return 0


def _preview_payload(
    options: argparse.Namespace,
) -> tuple[dict[str, Any], PromotionBundle, str, str]:
    root = pathlib.Path.cwd().resolve()
    selected_path = _normalize_selected(root, options.path)
    refresh_protected_main(root)
    bundle, provenance = build_reviewed_promotion_bundle(
        root, selected_path
    )
    local_run = _load_run_evidence(
        options.run_evidence,
        bundle=bundle,
        selected_path=selected_path,
        function_name=options.workflow,
    )
    entry_closure_id = _closure_id(
        bundle,
        selected_path=selected_path,
        function_name=options.workflow,
    )
    payload: dict[str, Any] = {
        "schema_version": REVIEWED_PROMOTION_SCHEMA,
        "target": "production",
        "entry": {"path": selected_path, "function": options.workflow},
        "snapshot": {
            "snapshot_id": bundle.snapshot_id,
            "files": bundle.snapshot_files,
            "closure": [
                {"path": item["path"], "sha256": item["sha256"]}
                for item in bundle.files
            ],
        },
        "protected_source": {
            "commit_sha": provenance.commit_sha,
            "tree_sha": provenance.tree_sha,
        },
        "local_run": local_run,
        "client": _client_contract(),
    }
    if options.supersedes_candidate_id:
        payload["supersedes_candidate_id"] = options.supersedes_candidate_id
    if options.expected_base_release_id:
        payload["expected_base_release_id"] = options.expected_base_release_id
    return payload, bundle, provenance.repository, entry_closure_id


def _render_preview(
    result: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    repository: str,
    expected_closure_id: str,
) -> None:
    protected = payload["protected_source"]
    print(f"Candidate: {result.get('candidate_id')}")
    print(
        "Protected main: "
        f"{repository}@{protected['commit_sha']} (tree {protected['tree_sha']})"
    )
    print(f"Snapshot: {payload['snapshot']['snapshot_id']}")
    print(f"Closure: {result.get('closure_id') or expected_closure_id}")
    print(f"Risk: {result.get('risk_class')}")
    artifact_state = (
        result.get("lifecycle_status")
        or result.get("artifact_state")
        or result.get("disposition")
    )
    print(f"Artifact state: {artifact_state}")
    registration = result.get("registration") or {}
    intents = registration.get("intent") or []
    if intents:
        print("Registration intent:")
        for item in intents:
            print(
                f"  {item.get('action', 'unknown'):<10} "
                f"{item.get('path') or payload['entry']['path']}::"
                f"{item.get('function_name') or payload['entry']['function']}"
            )
    else:
        print("Registration intent: none")
    if fingerprint := registration.get("intent_fingerprint"):
        print(f"Registration intent fingerprint: {fingerprint}")
    if fingerprint := registration.get("state_fingerprint"):
        print(f"Registration state fingerprint: {fingerprint}")
    print("Reviewed closure:")
    closure = result.get("closure") or payload["snapshot"]["closure"]
    for item in closure:
        relation = item.get("relation", "dependency")
        print(f"  {relation:<10} {item['sha256']}  {item['path']}")
    diagnostics = result.get("diagnostics") or []
    if diagnostics:
        print("Evidence and diagnostics:")
        for item in diagnostics:
            severity = item.get("severity", "info")
            purpose = "MUTATION BLOCKER" if severity == "blocker" else "ANALYSIS"
            location = f" ({item['path']})" if item.get("path") else ""
            print(f"  [{purpose}/{severity}] {item.get('code')}{location}: {item.get('message')}")
    ready = bool(result.get("ready_to_activate"))
    print(f"Ready to activate: {'yes' if ready else 'no'}")
    print("No source bytes were activated by this preview.")


def _handle_preview(options: argparse.Namespace) -> int:
    payload, _bundle, repository, expected_closure_id = _preview_payload(options)
    client = BifrostClient.get_instance(require_auth=True)
    response = client.post_sync(PROMOTION_PREVIEW_ENDPOINT, json=payload)
    raise_for_status_with_detail(response)
    result = response.json()
    if not isinstance(result, dict):
        raise PromotionBundleError("promotion preview returned invalid data")
    if options.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _render_preview(
            result,
            payload,
            repository=repository,
            expected_closure_id=expected_closure_id,
        )
    return 0


def _legacy_args(args: list[str]) -> list[str]:
    """Keep the preview-only v0 spelling as an exact reviewed-preview alias."""

    if not args or args[0] in _SUBCOMMANDS or args[0] in {"-h", "--help"}:
        return args
    normalized = [item for item in args if item != "--preview"]
    return ["preview", *normalized]


def handle_promote(args: list[str]) -> int:
    parser = _parser()
    try:
        options = parser.parse_args(_legacy_args(args))
        if options.command == "draft":
            return _handle_draft(options)
        if options.command == "canary":
            return _handle_canary(options)
        if options.command == "preview":
            return _handle_preview(options)
        raise PromotionBundleError(f"unsupported promotion command: {options.command}")
    except SystemExit as exc:
        return int(exc.code or 0)
    except (PromotionBundleError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "DRAFT_SCHEMA",
    "LOCAL_RUN_SCHEMA",
    "PROMOTION_CANARY_ENDPOINT",
    "PROMOTION_PREVIEW_ENDPOINT",
    "REVIEWED_PROMOTION_SCHEMA",
    "handle_promote",
]
