"""Regression coverage for coherent rapid-promotion source bundles."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from bifrost.promotion import (
    PromotionBundleError,
    build_promotion_bundle,
    dependency_edges,
    snapshot_id,
    validate_submitted_bundle,
)


def _git_workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for path, content in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return tmp_path


def test_bundle_contains_transitive_forward_closure_and_hash_inventory(
    tmp_path: Path,
) -> None:
    root = _git_workspace(
        tmp_path,
        {
            "features/demo/workflow.py": "from modules.client import fetch\n",
            "modules/client.py": "from helpers.parse import parse\ndef fetch(): return parse('x')\n",
            "helpers/parse.py": "def parse(value): return value\n",
            "features/unrelated.py": "VALUE = 1\n",
        },
    )

    bundle = build_promotion_bundle(root, "features/demo/workflow.py")

    assert {item["path"] for item in bundle.files} == {
        "features/demo/workflow.py",
        "modules/client.py",
        "helpers/parse.py",
    }
    assert "features/unrelated.py" in bundle.snapshot_files
    assert bundle.snapshot_id == snapshot_id(bundle.snapshot_files)


def test_server_rebuild_rejects_missing_dependency(tmp_path: Path) -> None:
    root = _git_workspace(
        tmp_path,
        {
            "features/demo/workflow.py": "from modules.client import fetch\n",
            "modules/client.py": "def fetch(): return 1\n",
        },
    )
    bundle = build_promotion_bundle(root, "features/demo/workflow.py")
    selected_only = [
        item for item in bundle.files if item["path"] == "features/demo/workflow.py"
    ]

    with pytest.raises(PromotionBundleError, match="missing modules/client.py"):
        validate_submitted_bundle(
            selected_path="features/demo/workflow.py",
            snapshot_id_value=bundle.snapshot_id,
            snapshot_files=bundle.snapshot_files,
            files=selected_only,
        )


def test_server_rejects_mixed_snapshot_bytes(tmp_path: Path) -> None:
    root = _git_workspace(
        tmp_path,
        {"features/demo/workflow.py": "VALUE = 1\n"},
    )
    bundle = build_promotion_bundle(root, "features/demo/workflow.py")
    item = dict(bundle.files[0])
    item["content_base64"] = base64.b64encode(b"VALUE = 2\n").decode("ascii")

    with pytest.raises(PromotionBundleError, match="content hash mismatch"):
        validate_submitted_bundle(
            selected_path="features/demo/workflow.py",
            snapshot_id_value=bundle.snapshot_id,
            snapshot_files=bundle.snapshot_files,
            files=[item],
        )


def test_server_rejects_casefold_collision() -> None:
    files = {
        "Modules/client.py": "0" * 64,
        "modules/client.py": "1" * 64,
    }
    with pytest.raises(PromotionBundleError, match="case/Unicode-colliding"):
        validate_submitted_bundle(
            selected_path="Modules/client.py",
            snapshot_id_value=snapshot_id(files),
            snapshot_files=files,
            files=[],
        )


def test_dependency_edges_exposes_reverse_consumers() -> None:
    edges = dependency_edges(
        {
            "features/a.py": b"from modules.shared import parse\n",
            "features/b.py": b"from modules.shared import parse\n",
            "modules/shared.py": b"def parse(value): return value\n",
        }
    )

    importers = {
        path
        for path, dependencies in edges.items()
        if "modules/shared.py" in dependencies
    }
    assert importers == {"features/a.py", "features/b.py"}


def test_package_initializer_relative_import_is_in_closure(tmp_path: Path) -> None:
    root = _git_workspace(
        tmp_path,
        {
            "features/demo/workflow.py": "from modules.vendor import fetch\n",
            "modules/vendor/__init__.py": "from .client import fetch\n",
            "modules/vendor/client.py": "def fetch(): return 1\n",
        },
    )

    bundle = build_promotion_bundle(root, "features/demo/workflow.py")

    assert {item["path"] for item in bundle.files} == {
        "features/demo/workflow.py",
        "modules/vendor/__init__.py",
        "modules/vendor/client.py",
    }


def test_cove_candidate_cannot_expand_into_unrelated_live_roots(tmp_path: Path) -> None:
    root = _git_workspace(
        tmp_path,
        {
            "features/cove/restore.py": (
                "from helpers.autotask_ticket_migration import suppress_notifications\n"
            ),
            "helpers/autotask_ticket_migration.py": (
                "def suppress_notifications(): return True\n"
            ),
            "features/ninja/recovery.py": (
                "from helpers.autotask_ticket_migration import suppress_notifications\n"
            ),
            "features/meraki/rollup.py": "def rollup(): return 96\n",
        },
    )

    bundle = build_promotion_bundle(root, "features/cove/restore.py")

    assert {item["path"] for item in bundle.files} == {
        "features/cove/restore.py",
        "helpers/autotask_ticket_migration.py",
    }
    assert "features/ninja/recovery.py" in bundle.snapshot_files
    assert "features/meraki/rollup.py" in bundle.snapshot_files


def test_attribute_dunder_import_is_in_forward_closure(tmp_path: Path) -> None:
    root = _git_workspace(
        tmp_path,
        {
            "features/demo/workflow.py": (
                "import builtins\nclient = builtins.__import__('modules.client')\n"
            ),
            "modules/client.py": "VALUE = 1\n",
        },
    )

    bundle = build_promotion_bundle(root, "features/demo/workflow.py")

    assert {item["path"] for item in bundle.files} == {
        "features/demo/workflow.py",
        "modules/client.py",
    }
