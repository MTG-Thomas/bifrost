"""Mechanical parity between the shipped CLI and promotion HTTP contracts."""

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path
from uuid import uuid4

from bifrost.commands import promote
from src.config import Settings
from src.models.contracts.workspace_promotions import (
    WorkspacePromotionCanaryAccepted,
    WorkspacePromotionCanaryRequest,
    WorkspacePromotionDraftRequest,
    WorkspacePromotionPreviewRequest,
)
from src.routers.workspace_promotions import router


def _workspace(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
    )
    source = tmp_path / "workflows/demo.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from bifrost import workflow\n"
        "@workflow(effects=[{'kind': 'bifrost.read'}], "
        "enforced_bounds={'max_duration_seconds': 30, 'max_external_calls': 1, "
        "'max_records_read': 10, 'max_output_bytes': 1000})\n"
        "async def demo(): return []\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "reviewed"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://github.com/example/workspace.git",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=tmp_path,
        check=True,
    )
    return source


def _route_paths() -> set[str]:
    return {route.path for route in router.routes}


def test_reviewed_preview_payload_validates_against_server_dto(
    tmp_path: Path, monkeypatch
) -> None:
    source = _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(promote, "refresh_protected_main", lambda _root: None)
    payload, _bundle, _repository, _closure = promote._preview_payload(
        Namespace(
            path=str(source),
            workflow="demo",
            run_evidence=None,
            supersedes_candidate_id=None,
            expected_base_release_id=None,
        )
    )

    parsed = WorkspacePromotionPreviewRequest.model_validate(payload)

    assert parsed.schema_version == promote.REVIEWED_PROMOTION_SCHEMA
    assert promote.PROMOTION_PREVIEW_ENDPOINT in _route_paths()
    assert all(member.content_base64 is None for member in parsed.snapshot.closure)


def test_local_draft_file_validates_as_inert_upload_contract(
    tmp_path: Path, monkeypatch
) -> None:
    source = _workspace(tmp_path)
    output = tmp_path / "draft.json"
    monkeypatch.chdir(tmp_path)

    assert promote.handle_promote(
        ["draft", str(source), "--workflow", "demo", "--out", str(output)]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    parsed = WorkspacePromotionDraftRequest.model_validate(payload)

    assert parsed.target == "draft"
    assert parsed.schema_version == promote.DRAFT_SCHEMA
    assert payload["authority"] == "local_only"
    assert payload["activatable"] is False
    assert "/api/workspace-promotions/drafts" in _route_paths()


def test_canary_cli_uses_only_reviewed_artifact_route(monkeypatch) -> None:
    captured = {}
    artifact_id = uuid4()
    accepted = WorkspacePromotionCanaryAccepted(
        execution_id=uuid4(), artifact_id=artifact_id
    )

    class Response:
        @staticmethod
        def json():
            return accepted.model_dump(mode="json")

    class Client:
        def post_sync(self, endpoint, **kwargs):
            captured["endpoint"] = endpoint
            captured["payload"] = kwargs["json"]
            return Response()

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kw: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert promote.handle_promote(
        ["canary", str(artifact_id), "--params", '{"limit": 1}', "--json"]
    ) == 0
    assert captured["endpoint"] == (
        f"/api/workspace-promotions/artifacts/{artifact_id}/canary"
    )
    assert promote.PROMOTION_CANARY_ENDPOINT.replace(
        "{artifact_id}", str(artifact_id)
    ) == captured["endpoint"]
    assert "/api/workspace-promotions/artifacts/{artifact_id}/canary" in _route_paths()
    assert WorkspacePromotionCanaryRequest.model_validate(captured["payload"])


def test_promotion_capabilities_are_independently_default_off() -> None:
    fields = Settings.model_fields
    assert fields["workspace_rapid_promotion_preview_enabled"].default is False
    assert fields["workspace_rapid_promotion_draft_upload_enabled"].default is False
    assert fields["workspace_release_prepare_canary_enabled"].default is False
    assert fields["workspace_release_activation_enabled"].default is False
