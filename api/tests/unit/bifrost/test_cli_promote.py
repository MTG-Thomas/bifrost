"""CLI contract tests for preview-only Workspace promotion."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bifrost.commands import promote
from bifrost import cli


def _workspace(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    path = tmp_path / "features/demo.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from bifrost import workflow\n"
        "@workflow(effects=[{'kind': 'bifrost.read'}], "
        "enforced_bounds={'max_duration_seconds': 30, 'max_external_calls': 1, "
        "'max_records_read': 10, 'max_output_bytes': 1000})\n"
        "async def demo(): return []\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return path


def test_promote_requires_explicit_preview(tmp_path: Path, monkeypatch) -> None:
    path = _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert promote.handle_promote([str(path), "-w", "demo"]) != 0


def test_promote_submits_exact_bundle_and_has_no_activation_option(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured = {}

    class Response:
        is_success = True

        def json(self):
            return {
                "candidate_id": "sha256:" + "a" * 64,
                "risk_class": "R0",
                "disposition": "review_required",
                "closure": [],
                "diagnostics": [],
            }

    class Client:
        def post_sync(self, endpoint, **kwargs):
            captured["endpoint"] = endpoint
            captured["payload"] = kwargs["json"]
            return Response()

    monkeypatch.setattr(
        promote.BifrostClient,
        "get_instance",
        lambda **_kwargs: Client(),
    )
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert promote.handle_promote([str(path), "-w", "demo", "--preview"]) == 0

    assert captured["endpoint"] == "/api/workspace-promotions/preview"
    assert captured["payload"]["target"] == "production"
    assert captured["payload"]["entry"] == {
        "path": "features/demo.py",
        "function": "demo",
    }
    assert "Activation: disabled" in capsys.readouterr().out


def test_successful_local_run_writes_snapshot_bound_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    path = _workspace(tmp_path)
    evidence_path = tmp_path / ".promotion-run.json"
    monkeypatch.chdir(tmp_path)

    class OfflineClient:
        @staticmethod
        def get_instance(**_kwargs):
            raise RuntimeError("offline")

    async def demo():
        return []

    monkeypatch.setattr(cli, "BifrostClient", OfflineClient)

    assert (
        cli._run_direct(
            "demo",
            {"demo": demo},
            {},
            promotion_evidence=evidence_path,
            workflow_file=str(path),
        )
        == 0
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["succeeded"] is True
    assert evidence["snapshot_id"].startswith("sha256:")
    assert evidence["evidence_id"].startswith("local:")
