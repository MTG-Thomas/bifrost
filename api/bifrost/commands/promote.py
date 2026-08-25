"""Immutable Workspace draft and reviewed promotion commands."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import time
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
    reviewed_changed_python_paths,
    sha256_bytes,
    snapshot_id,
)
from bifrost.workspace_release import workspace_closure_id, workspace_cohort_closure_id
from bifrost.workspace_release_authorization import (
    risk_acknowledgement,
    validate_activation_challenge,
)
from bifrost.workspace_impact import (
    WorkspaceImpactAnalysis,
    analyze_workspace_impact,
    reverse_edges,
    transitive_distances,
)

PROMOTION_PREVIEW_ENDPOINT = "/api/workspace-promotions/preview"
PROMOTION_CANARY_ENDPOINT = "/api/workspace-promotions/artifacts/{artifact_id}/canary"
PROMOTION_PREPARE_ENDPOINT = "/api/workspace-promotions/artifacts/{artifact_id}/prepare"
PROMOTION_ACTIVATE_ENDPOINT = "/api/workspace-promotions/releases/{release_id}/activate"
PROMOTION_RELEASE_STATUS_ENDPOINT = "/api/workspace-promotions/releases/{release_id}"
PROMOTION_LIVE_STATUS_ENDPOINT = "/api/workspace-promotions/live"
PLATFORM_JOB_STATUS_ENDPOINT = "/api/platform-jobs/{job_id}"
PREPARE_POLL_INTERVAL_SECONDS = 2.0
PREPARE_POLL_TIMEOUT_SECONDS = 30 * 60
REVIEWED_PROMOTION_SCHEMA = "bifrost.workspace-promotion-bundle/v2"
DRAFT_SCHEMA = "bifrost.workspace-draft-upload/v1"
LOCAL_RUN_SCHEMA = "bifrost.workspace-local-run-evidence/v1"
_SUBCOMMANDS = {
    "draft",
    "canary",
    "preview",
    "prepare",
    "activate",
    "status",
}


def _closure_id(
    bundle: PromotionBundle,
    *,
    selected_path: str,
    function_name: str,
    cohort_paths: list[str] | None = None,
) -> str:
    """Use the runtime's canonical forward-closure identity."""

    entry = {
        "path": normalize_workspace_path(selected_path),
        "function": function_name,
    }
    files = {str(item["path"]): str(item["sha256"]) for item in bundle.files}
    if cohort_paths:
        return workspace_cohort_closure_id(entry, files, cohort_paths)
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
    preview.add_argument(
        "--include-declared-changes",
        action="store_true",
        help=(
            "Promote the exact executable path cohort declared by the reviewed "
            "protected-main commit"
        ),
    )

    prepare = subparsers.add_parser(
        "prepare",
        help="Materialize one exact reviewed artifact as an immutable release",
    )
    prepare.add_argument("artifact_id", help="Artifact ID emitted by `promote preview`")
    prepare.add_argument(
        "--candidate-id",
        required=True,
        help="Immutable candidate ID emitted by the same preview",
    )
    prepare.add_argument("--json", action="store_true", help="Emit machine JSON")

    activate = subparsers.add_parser(
        "activate",
        help="Authorize and atomically move Live to one exact prepared release",
    )
    activate.add_argument("release_row_id", help="Release row ID emitted by prepare")
    activate.add_argument("--artifact-id", required=True)
    activate.add_argument("--candidate-id", required=True)
    activate.add_argument("--workspace-release-id", required=True)
    activate.add_argument("--expected-base-release-id", required=True)
    active_cas = activate.add_mutually_exclusive_group(required=True)
    active_cas.add_argument(
        "--expected-active-release-id",
        help="Exact release ID that must currently own Live",
    )
    active_cas.add_argument(
        "--expect-no-active-release",
        action="store_true",
        help="Explicitly require that no immutable release currently owns Live",
    )
    activate.add_argument("--prepared-evidence-id", required=True)
    authorization = activate.add_mutually_exclusive_group(required=True)
    authorization.add_argument(
        "--canary-execution-id",
        help="Successful exact reviewed canary for an R0 release",
    )
    authorization.add_argument(
        "--acknowledge-risk",
        choices=("R1", "R2"),
        help=(
            "Explicitly acknowledge activation without canary or effect "
            "execution for the exact risk class"
        ),
    )
    activate.add_argument("--json", action="store_true", help="Emit machine JSON")

    release_status = subparsers.add_parser(
        "status",
        help="Read one immutable release or the global Live pointer",
    )
    release_status.add_argument("release_row_id", nargs="?")
    release_status.add_argument(
        "--live",
        action="store_true",
        help="Read the global Live Workspace release",
    )
    release_status.add_argument("--json", action="store_true", help="Emit machine JSON")

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
    cohort_paths: list[str] | None = None,
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
    if (
        evidence.get("authority") != "local_only"
        or evidence.get("activatable") is not False
    ):
        raise PromotionBundleError("run evidence must be explicitly local-only")
    if evidence.get("succeeded") is not True:
        raise PromotionBundleError(
            "run evidence does not record a successful execution"
        )
    expected_closure_id = _closure_id(
        bundle,
        selected_path=selected_path,
        function_name=function_name,
        cohort_paths=cohort_paths,
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
        (
            blockers if path == selected_path or path in forward_paths else warnings
        ).append(item)

    for path in sorted(relevant_paths):
        if values := analysis.unresolved_imports.get(path):
            add(path, "unresolved_repo_import", ", ".join(values))
        if values := analysis.ambiguous_references.get(path):
            add(path, "ambiguous_workflow_reference", ", ".join(values))
        if path in analysis.dynamic_importers:
            add(
                path,
                "dynamic_import_unresolved",
                "computed import cannot be proven statically",
            )
        if path in analysis.dynamic_reference_importers:
            add(
                path,
                "dynamic_workflow_reference_unresolved",
                "computed workflow reference cannot be proven statically",
            )
    return warnings, blockers


def _local_graph(
    root: pathlib.Path, selected_path: str, bundle: PromotionBundle
) -> dict[str, Any]:
    hashes, contents = discover_python_snapshot(root)
    if snapshot_id(hashes) != bundle.snapshot_id:
        raise PromotionBundleError(
            "workspace changed while complete graph evidence was built"
        )
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
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{sha256_bytes(encoded)}"


def _default_draft_path(draft_id: str) -> pathlib.Path:
    directory = user_data_path("bifrost", appauthor=False) / "workspace-promotions"
    return directory / f"{draft_id.removeprefix('sha256:')}.json"


def _validated_draft_output_path(
    path: pathlib.Path, *, workspace_root: pathlib.Path
) -> pathlib.Path:
    """Resolve a draft output beneath an explicit private authority root."""
    workspace_root = workspace_root.resolve()
    private_root = (
        user_data_path("bifrost", appauthor=False) / "workspace-promotions"
    ).resolve()
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    candidate = candidate.resolve(strict=False)
    if not any(
        candidate.is_relative_to(allowed_root)
        for allowed_root in (workspace_root, private_root)
    ):
        raise PromotionBundleError(
            "draft output must remain inside the current workspace or the private "
            "Bifrost workspace-promotions directory"
        )
    return candidate


def _write_private_json(
    path: pathlib.Path,
    payload: Mapping[str, Any],
    *,
    workspace_root: pathlib.Path,
) -> pathlib.Path:
    destination = _validated_draft_output_path(path, workspace_root=workspace_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Re-resolve after directory creation so an existing symlinked parent cannot
    # move the write outside either explicit authority root.
    destination = _validated_draft_output_path(
        destination, workspace_root=workspace_root
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


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
    requested_output = options.out or _default_draft_path(payload["draft_id"])
    output_path = _write_private_json(requested_output, payload, workspace_root=root)
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
        raise PromotionBundleError(
            f"canary parameters are not valid JSON: {exc}"
        ) from exc
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
    if str(result.get("artifact_id")) != options.artifact_id:
        raise PromotionBundleError("canary response belongs to a different artifact")
    if options.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Canary execution: {result['execution_id']}")
        print(f"Reviewed artifact: {options.artifact_id}")
        print("Canary only: no registration, trigger, or Live pointer change")
    return 0


def _poll_prepare_job(client: BifrostClient, job_id: str) -> dict[str, Any]:
    started = time.monotonic()
    last_phase: tuple[str | None, int, int | None] | None = None
    while True:
        response = client.get_sync(PLATFORM_JOB_STATUS_ENDPOINT.format(job_id=job_id))
        raise_for_status_with_detail(response)
        body = response.json()
        if not isinstance(body, dict):
            raise PromotionBundleError("prepare job returned invalid status data")
        job_status = body.get("status")
        if job_status == "succeeded":
            return body
        if job_status in {"failed", "cancelled"}:
            error = body.get("error") or {}
            detail = error.get("message") if isinstance(error, dict) else None
            raise PromotionBundleError(
                f"release preparation {job_status} (job {job_id})"
                + (f": {detail}" if detail else "")
            )

        progress = body.get("progress") or {}
        phase = progress.get("phase") if isinstance(progress, dict) else None
        current = int(progress.get("current") or 0) if isinstance(progress, dict) else 0
        total = progress.get("total") if isinstance(progress, dict) else None
        observed = (phase, current, total)
        if observed != last_phase:
            count = f" ({current}/{total})" if total is not None else ""
            print(
                f"Prepare {phase or job_status or 'in progress'}{count}",
                file=sys.stderr,
            )
            last_phase = observed

        if time.monotonic() - started >= PREPARE_POLL_TIMEOUT_SECONDS:
            raise PromotionBundleError(
                f"release preparation is still running after "
                f"{PREPARE_POLL_TIMEOUT_SECONDS // 60} minutes (job {job_id}); "
                "inspect that durable job before retrying"
            )
        time.sleep(PREPARE_POLL_INTERVAL_SECONDS)


def _prepare_result(
    job: Mapping[str, Any], *, artifact_id: str, candidate_id: str
) -> Mapping[str, Any]:
    result = job.get("result")
    if not isinstance(result, dict):
        raise PromotionBundleError(
            "successful prepare job did not return release evidence"
        )
    required = {
        "release_row_id",
        "artifact_id",
        "candidate_id",
        "release_id",
        "prepared_evidence_id",
        "risk_class",
        "computed_effects",
        "computed_effects_id",
        "protected_source",
        "effect_execution",
        "activation_authorization",
    }
    missing = sorted(required - result.keys())
    if missing:
        raise PromotionBundleError(
            "successful prepare job omitted required evidence: " + ", ".join(missing)
        )
    if str(result["artifact_id"]) != artifact_id:
        raise PromotionBundleError("prepare response belongs to a different artifact")
    if result["candidate_id"] != candidate_id:
        raise PromotionBundleError("prepare response belongs to a different candidate")
    try:
        challenge = validate_activation_challenge(result["activation_authorization"])
    except (TypeError, ValueError) as exc:
        raise PromotionBundleError(
            "prepare response contains an invalid activation challenge"
        ) from exc
    if (
        challenge["artifact_id"] != artifact_id
        or challenge["candidate_id"] != candidate_id
        or challenge["workspace_release_id"] != result["release_id"]
        or challenge["prepared_evidence_id"] != result["prepared_evidence_id"]
        or challenge["risk_class"] != result["risk_class"]
        or challenge["computed_effects"] != result["computed_effects"]
        or challenge["computed_effects_id"] != result["computed_effects_id"]
        or challenge["protected_source"] != result["protected_source"]
    ):
        raise PromotionBundleError(
            "prepare response activation challenge does not match its evidence"
        )
    expected_effect_execution = (
        "reviewed_canary_required"
        if challenge["risk_class"] == "R0"
        else "not_performed"
    )
    if result["effect_execution"] != expected_effect_execution:
        raise PromotionBundleError(
            "prepare response makes an invalid effect-execution claim for its risk class"
        )
    return result


def _handle_prepare(options: argparse.Namespace) -> int:
    client = BifrostClient.get_instance(require_auth=True)
    response = client.post_sync(
        PROMOTION_PREPARE_ENDPOINT.format(artifact_id=options.artifact_id),
        json={"candidate_id": options.candidate_id},
    )
    raise_for_status_with_detail(response)
    accepted = response.json()
    if not isinstance(accepted, dict) or not accepted.get("job_id"):
        raise PromotionBundleError("prepare did not return a durable platform job ID")
    job_id = str(accepted["job_id"])
    completed = _poll_prepare_job(client, job_id)
    result = _prepare_result(
        completed,
        artifact_id=options.artifact_id,
        candidate_id=options.candidate_id,
    )
    if options.json:
        print(json.dumps(completed, indent=2, sort_keys=True))
    else:
        print(f"Preparation job: {job_id}")
        print(f"Prepared release row: {result['release_row_id']}")
        print(f"Reviewed artifact: {result['artifact_id']}")
        print(f"Candidate: {result['candidate_id']}")
        print(f"Workspace release: {result['release_id']}")
        print(f"Prepared evidence: {result['prepared_evidence_id']}")
        print(f"Risk: {result['risk_class']}")
        print(f"Protected main: {result['protected_source']['commit_sha']}")
        print("Computed effects:")
        for effect in result["computed_effects"]:
            print(f"  {effect}")
        print(f"Effect execution: {result['effect_execution'].replace('_', ' ')}")
        challenge = result["activation_authorization"]
        print(f"Authorization required: {challenge['required_authorization']}")
        print(f"Authorization challenge: {challenge['challenge_id']}")
        print("Prepared only: the global Live pointer has not changed")
    return 0


def _activation_payload(
    options: argparse.Namespace, challenge: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        verified_challenge = validate_activation_challenge(challenge)
    except (TypeError, ValueError) as exc:
        raise PromotionBundleError(
            "release status has an invalid activation challenge"
        ) from exc
    expected_challenge = {
        "artifact_id": options.artifact_id,
        "candidate_id": options.candidate_id,
        "workspace_release_id": options.workspace_release_id,
        "prepared_evidence_id": options.prepared_evidence_id,
    }
    if any(
        str(verified_challenge.get(key)) != value
        for key, value in expected_challenge.items()
    ):
        raise PromotionBundleError(
            "activation challenge belongs to different immutable release evidence"
        )
    expected_active_release_id = options.expected_active_release_id
    if options.expect_no_active_release:
        if not options.expected_base_release_id.startswith("repo-v1:"):
            raise PromotionBundleError(
                "--expect-no-active-release requires a repo-v1 expected base"
            )
        expected_active_release_id = None
    elif expected_active_release_id != options.expected_base_release_id:
        raise PromotionBundleError(
            "--expected-active-release-id must exactly equal --expected-base-release-id"
        )
    if options.canary_execution_id is not None:
        if verified_challenge["required_authorization"] != "reviewed_canary":
            raise PromotionBundleError(
                "R1/R2 releases cannot use or claim a reviewed canary"
            )
        authorization = {
            "kind": "reviewed_canary",
            "challenge_id": verified_challenge["challenge_id"],
            "canary_execution_id": options.canary_execution_id,
        }
    else:
        if (
            verified_challenge["required_authorization"] != "risk_acknowledgement"
            or options.acknowledge_risk != verified_challenge["risk_class"]
        ):
            raise PromotionBundleError(
                "--acknowledge-risk must exactly match the prepared R1/R2 release"
            )
        authorization = {
            "kind": "risk_acknowledgement",
            "acknowledgement": risk_acknowledgement(verified_challenge),
        }
    return {
        "artifact_id": options.artifact_id,
        "candidate_id": options.candidate_id,
        "workspace_release_id": options.workspace_release_id,
        "expected_base_release_id": options.expected_base_release_id,
        "expected_active_release_id": expected_active_release_id,
        "prepared_evidence_id": options.prepared_evidence_id,
        "authorization": authorization,
    }


def _render_release_status(result: Mapping[str, Any]) -> None:
    print(f"Release row: {result.get('release_row_id')}")
    print(f"Workspace release: {result.get('release_id')}")
    print(f"Candidate: {result.get('candidate_id')}")
    print(f"Activation: {result.get('activation_state')}")
    print(f"Live: {'yes' if result.get('is_live') else 'no'}")
    runtime = result.get("runtime") or {}
    history = result.get("history") or {}
    print(f"Runtime: {runtime.get('state')}")
    print(f"History: {history.get('state')} ({history.get('lock_state')})")
    if history.get("job_id"):
        print(f"History projection job: {history['job_id']}")


def _handle_activate(options: argparse.Namespace) -> int:
    client = BifrostClient.get_instance(require_auth=True)
    status_response = client.get_sync(
        PROMOTION_RELEASE_STATUS_ENDPOINT.format(release_id=options.release_row_id)
    )
    raise_for_status_with_detail(status_response)
    release_status = status_response.json()
    runtime = (
        release_status.get("runtime") if isinstance(release_status, dict) else None
    )
    challenge = (
        runtime.get("activation_authorization") if isinstance(runtime, dict) else None
    )
    if not isinstance(challenge, dict):
        raise PromotionBundleError(
            "prepared release status did not return an activation challenge"
        )
    payload = _activation_payload(options, challenge)
    if options.acknowledge_risk is not None and not options.json:
        print(f"Risk acknowledgement: {challenge['risk_class']}")
        print(f"Protected main: {challenge['protected_source']['commit_sha']}")
        print("Computed effects:")
        for effect in challenge["computed_effects"]:
            print(f"  {effect}")
        print("Effect execution: not performed")
        print(f"Authorization challenge: {challenge['challenge_id']}")
    response = client.post_sync(
        PROMOTION_ACTIVATE_ENDPOINT.format(release_id=options.release_row_id),
        json=payload,
    )
    raise_for_status_with_detail(response)
    result = response.json()
    if not isinstance(result, dict):
        raise PromotionBundleError("activation returned invalid release status")
    expected = {
        "release_row_id": options.release_row_id,
        "artifact_id": options.artifact_id,
        "candidate_id": options.candidate_id,
        "release_id": options.workspace_release_id,
    }
    for field, value in expected.items():
        if str(result.get(field)) != value:
            raise PromotionBundleError(
                f"activation response {field} does not match the requested release"
            )
    if result.get("is_live") is not True:
        raise PromotionBundleError(
            "activation response did not prove the release is Live"
        )
    if options.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _render_release_status(result)
        print(
            "Live activation is complete; signed-history projection may still be pending"
        )
    return 0


def _handle_status(options: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if bool(options.release_row_id) == bool(options.live):
        parser.error("status requires exactly one of RELEASE_ROW_ID or --live")
    client = BifrostClient.get_instance(require_auth=True)
    if options.live:
        endpoint = PROMOTION_LIVE_STATUS_ENDPOINT
    else:
        endpoint = PROMOTION_RELEASE_STATUS_ENDPOINT.format(
            release_id=options.release_row_id
        )
    response = client.get_sync(endpoint)
    raise_for_status_with_detail(response)
    result = response.json()
    if not isinstance(result, dict):
        raise PromotionBundleError("release status returned invalid data")
    if options.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif options.live:
        active = result.get("active_release")
        if active is None:
            print("Live Workspace release: none")
        elif isinstance(active, dict):
            print("Global Live Workspace release:")
            _render_release_status(active)
        else:
            raise PromotionBundleError(
                "Live status returned invalid active-release data"
            )
    else:
        if str(result.get("release_row_id")) != options.release_row_id:
            raise PromotionBundleError(
                "status response belongs to a different release row"
            )
        _render_release_status(result)
    return 0


def _preview_payload(
    options: argparse.Namespace,
) -> tuple[dict[str, Any], PromotionBundle, str, str]:
    root = pathlib.Path.cwd().resolve()
    selected_path = _normalize_selected(root, options.path)
    refreshed = refresh_protected_main(root)
    cohort_paths = (
        list(reviewed_changed_python_paths(root, commit_sha=refreshed.commit_sha))
        if getattr(options, "include_declared_changes", False)
        else []
    )
    bundle, provenance = build_reviewed_promotion_bundle(
        root,
        selected_path,
        cohort_paths=tuple(cohort_paths),
    )
    local_run = _load_run_evidence(
        options.run_evidence,
        bundle=bundle,
        selected_path=selected_path,
        function_name=options.workflow,
        cohort_paths=cohort_paths,
    )
    entry_closure_id = _closure_id(
        bundle,
        selected_path=selected_path,
        function_name=options.workflow,
        cohort_paths=cohort_paths,
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
    if cohort_paths:
        payload["cohort_paths"] = cohort_paths
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
    state = registration.get("state")
    if isinstance(state, Mapping):
        role_ids = state.get("role_ids") or []
        print(
            "Preserved activation surface: "
            f"access={state.get('access_level')} "
            f"roles={len(role_ids)} "
            f"endpoint={bool(state.get('endpoint_enabled'))} "
            f"public={bool(state.get('public_endpoint'))} "
            f"api_key={bool(state.get('api_key_enabled'))}"
        )
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
            print(
                f"  [{purpose}/{severity}] {item.get('code')}{location}: {item.get('message')}"
            )
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
        if options.command == "prepare":
            return _handle_prepare(options)
        if options.command == "activate":
            return _handle_activate(options)
        if options.command == "status":
            return _handle_status(options, parser)
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
    "PROMOTION_PREPARE_ENDPOINT",
    "PROMOTION_ACTIVATE_ENDPOINT",
    "PROMOTION_RELEASE_STATUS_ENDPOINT",
    "PROMOTION_LIVE_STATUS_ENDPOINT",
    "PROMOTION_PREVIEW_ENDPOINT",
    "REVIEWED_PROMOTION_SCHEMA",
    "handle_promote",
]
