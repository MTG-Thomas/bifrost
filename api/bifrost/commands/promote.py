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
    sha256_bytes,
    snapshot_id,
    validate_submitted_bundle,
)
from bifrost.workspace_release import workspace_closure_id
from bifrost.workspace_impact import (
    WorkspaceImpactAnalysis,
    analyze_workspace_impact,
    reverse_edges,
    transitive_distances,
)

PROMOTION_PREVIEW_ENDPOINT = "/api/workspace-promotions/preview"
PROMOTION_DRAFTS_ENDPOINT = "/api/workspace-promotions/drafts"
PROMOTION_DRAFT_CANARY_ENDPOINT = (
    "/api/workspace-promotions/drafts/{artifact_id}/canary"
)
PROMOTION_LIVE_ENDPOINT = "/api/workspace-promotions/live"
PROMOTION_RELEASE_ENDPOINT = "/api/workspace-promotions/releases/{release_id}"
REVIEWED_PROMOTION_SCHEMA = "bifrost.workspace-promotion-bundle/v2"
DRAFT_SCHEMA = "bifrost.workspace-promotion-draft/v1"
LOCAL_RUN_SCHEMA = "bifrost.workspace-local-run-evidence/v1"
_SUBCOMMANDS = {"draft", "canary", "preview", "status"}


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
        help="Manually execute an exact local draft without registration or triggers",
    )
    canary.add_argument("draft", help="Draft ID or path emitted by `promote draft`")
    canary.add_argument(
        "--params",
        default="{}",
        help="Workflow parameters as a JSON object",
    )
    canary.add_argument(
        "--ttl-seconds",
        type=int,
        default=900,
        help="Maximum isolated draft lifetime (60-900 seconds; default: 900)",
    )
    canary.add_argument("--json", action="store_true", help="Emit machine JSON")

    preview = subparsers.add_parser(
        "preview",
        help="Preview exact reviewed Git bytes; does not activate",
    )
    _common_source_arguments(preview)
    preview.add_argument(
        "--source-ref",
        default="origin/main",
        help="Reviewed Git ref read directly from the object database (default: origin/main)",
    )
    preview.add_argument(
        "--supersedes-candidate-id",
        help="Candidate replaced by this immutable preview",
    )
    preview.add_argument(
        "--expected-base-release-id",
        help="Optional active-release CAS observed before preview",
    )

    status = subparsers.add_parser(
        "status",
        help="Show Live/runtime/history coherence for one immutable release",
    )
    status.add_argument("release_id", nargs="?", help="Release ID; omit for current Live")
    status.add_argument("--json", action="store_true", help="Emit machine JSON")
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


def _read_draft(value: str) -> dict[str, Any]:
    path = pathlib.Path(value)
    if not path.exists() and value.startswith("sha256:"):
        path = _default_draft_path(value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PromotionBundleError(f"local draft not found: {value}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != DRAFT_SCHEMA:
        raise PromotionBundleError("draft uses an unsupported schema; rebuild it")
    if payload.get("authority") != "local_only" or payload.get("activatable") is not False:
        raise PromotionBundleError("draft is not explicitly local-only/non-activatable")
    if payload.get("draft_id") != _draft_id(payload):
        raise PromotionBundleError("draft identity does not match its immutable content")
    try:
        entry = payload["entry"]
        snapshot = payload["snapshot"]
        validate_submitted_bundle(
            selected_path=entry["path"],
            snapshot_id_value=snapshot["snapshot_id"],
            snapshot_files=snapshot["files"],
            files=snapshot["closure"],
        )
        expected_closure_id = workspace_closure_id(
            {
                "path": normalize_workspace_path(entry["path"]),
                "function": entry["function"],
            },
            {
                normalize_workspace_path(item["path"]): item["sha256"]
                for item in snapshot["closure"]
            },
        )
    except (KeyError, TypeError) as exc:
        raise PromotionBundleError("draft is missing immutable source evidence") from exc
    if snapshot.get("closure_id") != expected_closure_id:
        raise PromotionBundleError("draft closure identity does not match its source hashes")
    return payload


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
    return 0


def _handle_canary(options: argparse.Namespace) -> int:
    if not 60 <= options.ttl_seconds <= 900:
        raise PromotionBundleError("canary TTL must be between 60 and 900 seconds")
    try:
        parameters = json.loads(options.params)
    except json.JSONDecodeError as exc:
        raise PromotionBundleError(f"canary parameters are not valid JSON: {exc}") from exc
    if not isinstance(parameters, dict):
        raise PromotionBundleError("canary parameters must be a JSON object")
    draft = _read_draft(options.draft)
    client = BifrostClient.get_instance(require_auth=True)
    uploaded_response = client.post_sync(
        PROMOTION_DRAFTS_ENDPOINT,
        json=draft,
        timeout=120.0,
    )
    raise_for_status_with_detail(uploaded_response)
    uploaded = uploaded_response.json()
    if not isinstance(uploaded, dict) or not uploaded.get("artifact_id"):
        raise PromotionBundleError("draft upload did not return an artifact ID")
    if uploaded.get("authority") != "local_only" or uploaded.get("activatable") is not False:
        raise PromotionBundleError("server did not preserve non-activatable draft authority")
    canary_response = client.post_sync(
        PROMOTION_DRAFT_CANARY_ENDPOINT.format(artifact_id=uploaded["artifact_id"]),
        json={"parameters": parameters, "ttl_seconds": options.ttl_seconds},
    )
    raise_for_status_with_detail(canary_response)
    result = canary_response.json()
    if not isinstance(result, dict) or not result.get("execution_id"):
        raise PromotionBundleError("canary did not return an execution ID")
    if options.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Canary execution: {result['execution_id']}")
        print(f"Draft artifact: {uploaded['artifact_id']}")
        print(f"Content: {uploaded.get('content_id') or draft['snapshot']['closure_id']}")
        print("Authority: local-only; no registration, trigger, or Live pointer change")
        if expires_at := result.get("expires_at"):
            print(f"Expires: {expires_at}")
    return 0


def _preview_payload(
    options: argparse.Namespace,
) -> tuple[dict[str, Any], PromotionBundle, str, str]:
    root = pathlib.Path.cwd().resolve()
    selected_path = _normalize_selected(root, options.path)
    bundle, provenance = build_reviewed_promotion_bundle(
        root, selected_path, source_ref=options.source_ref
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


def _release_status_endpoint(release_id: str | None) -> str:
    if release_id is None:
        return PROMOTION_LIVE_ENDPOINT
    return PROMOTION_RELEASE_ENDPOINT.format(release_id=release_id)


def _render_status(result: Mapping[str, Any]) -> None:
    print(f"Release: {result.get('release_id') or '<none>'}")
    print(f"Candidate: {result.get('candidate_id') or '<none>'}")
    print(f"Activation: {result.get('activation_state') or 'unknown'}")
    print(f"Propagation: {result.get('propagation_state') or 'unknown'}")
    print(f"Runtime coherent: {'yes' if result.get('coherent') else 'no'}")
    print(f"History: {result.get('history_state') or 'unknown'}")
    if base_release := result.get("base_release_id"):
        print(f"Previous release: {base_release}")
    if manifest := result.get("effective_manifest_id"):
        print(f"Effective manifest: {manifest}")
    if execution_id := result.get("canary_execution_id"):
        print(f"Canary execution: {execution_id}")
    files = result.get("files") or []
    if files:
        print("Per-surface readback:")
        for item in files:
            flags = {
                "immutable": item.get("immutable_coherent"),
                "cache": item.get("cache_coherent"),
                "history": item.get("history_coherent"),
            }
            if "projected_repo_coherent" in item:
                flags["projection"] = item.get("projected_repo_coherent")
            state = ", ".join(
                f"{name}={'ok' if value else 'MISMATCH'}" for name, value in flags.items()
            )
            print(f"  {item.get('expected_sha256')}  {item.get('path')}  [{state}]")
    diagnostics = result.get("diagnostics") or []
    if diagnostics:
        print("Diagnostics:")
        for item in diagnostics:
            print(
                f"  [{item.get('severity', 'info')}] {item.get('code')}: "
                f"{item.get('message')}"
            )
    if result.get("activation_state") == "live" and result.get("coherent"):
        if result.get("history_state") in {"pending", "projecting"}:
            print("Outcome: Live / runtime coherent / history pending")
        elif result.get("history_state") in {"complete", "projected"}:
            print("Outcome: Live / runtime coherent / history projected")


def _handle_status(options: argparse.Namespace) -> int:
    client = BifrostClient.get_instance(require_auth=True)
    response = client.get_sync(_release_status_endpoint(options.release_id))
    raise_for_status_with_detail(response)
    result = response.json()
    if not isinstance(result, dict):
        raise PromotionBundleError("promotion status returned invalid data")
    if options.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _render_status(result)
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
        if options.command == "status":
            return _handle_status(options)
        raise PromotionBundleError(f"unsupported promotion command: {options.command}")
    except SystemExit as exc:
        return int(exc.code or 0)
    except (PromotionBundleError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "DRAFT_SCHEMA",
    "LOCAL_RUN_SCHEMA",
    "PROMOTION_DRAFTS_ENDPOINT",
    "PROMOTION_DRAFT_CANARY_ENDPOINT",
    "PROMOTION_PREVIEW_ENDPOINT",
    "PROMOTION_LIVE_ENDPOINT",
    "PROMOTION_RELEASE_ENDPOINT",
    "REVIEWED_PROMOTION_SCHEMA",
    "handle_promote",
]
