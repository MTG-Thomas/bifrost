"""Preview-only rapid Workspace promotion command."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from bifrost import __version__
from bifrost.client import BifrostClient, raise_for_status_with_detail
from bifrost.contract_version import CONTRACT_VERSION
from bifrost.promotion import (
    PROMOTION_BUNDLE_SCHEMA,
    PromotionBundleError,
    build_promotion_bundle,
    git_source_revision,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bifrost promote",
        description=(
            "Compile and submit an immutable Workspace promotion preview. "
            "This command cannot activate production."
        ),
    )
    parser.add_argument("path", help="Workspace-relative Python workflow path")
    parser.add_argument("--workflow", "-w", required=True, help="Decorated function")
    parser.add_argument(
        "--preview",
        action="store_true",
        required=True,
        help="Required safety acknowledgement; no activation is performed",
    )
    parser.add_argument(
        "--run-evidence",
        type=pathlib.Path,
        help="Optional JSON evidence emitted for this exact local snapshot",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine JSON")
    return parser


def handle_promote(args: list[str]) -> int:
    parser = _parser()
    try:
        options = parser.parse_args(args)
        root = pathlib.Path.cwd().resolve()
        selected = pathlib.Path(options.path)
        if selected.is_absolute():
            selected_path = selected.resolve().relative_to(root).as_posix()
        else:
            selected_path = selected.as_posix()
        bundle = build_promotion_bundle(root, selected_path)
        local_run = None
        if options.run_evidence:
            local_run = json.loads(options.run_evidence.read_text(encoding="utf-8"))
            if local_run.get("snapshot_id") != bundle.snapshot_id:
                raise PromotionBundleError(
                    "run evidence does not match the current Workspace snapshot"
                )
        payload = {
            "schema_version": PROMOTION_BUNDLE_SCHEMA,
            "target": "production",
            "entry": {"path": selected_path, "function": options.workflow},
            "snapshot": {
                "snapshot_id": bundle.snapshot_id,
                "files": bundle.snapshot_files,
                "closure": list(bundle.files),
            },
            "source_revision": git_source_revision(root),
            "local_run": local_run,
            "client": {
                "cli_version": __version__,
                "sdk_version": __version__,
                "contract_version": str(CONTRACT_VERSION),
            },
        }
        client = BifrostClient.get_instance(require_auth=True)
        response = client.post_sync("/api/workspace-promotions/preview", json=payload)
        raise_for_status_with_detail(response)
        result = response.json()
        if options.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"Candidate: {result.get('candidate_id')}")
            print(f"Risk: {result.get('risk_class')}")
            print(f"Disposition: {result.get('disposition')}")
            print("Activation: disabled (preview-only dark launch)")
            for item in result.get("closure", []):
                print(f"  {item['relation']:<10} {item['sha256']}  {item['path']}")
            for item in result.get("diagnostics", []):
                print(f"  [{item['severity']}] {item['code']}: {item['message']}")
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except (PromotionBundleError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


__all__ = ["handle_promote"]
