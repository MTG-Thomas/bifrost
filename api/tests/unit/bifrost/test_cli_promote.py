"""CLI contract tests for preview-only Workspace promotion."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from bifrost.commands import promote
from bifrost import cli


def _workspace(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
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
    subprocess.run(["git", "commit", "-qm", "reviewed"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/workspace.git"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=tmp_path,
        check=True,
    )
    return path


def test_legacy_promote_spelling_is_a_reviewed_preview_alias(
    tmp_path: Path, monkeypatch
) -> None:
    path = _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {"candidate_id": "sha256:" + "a" * 64, "closure": []}

    class Client:
        def post_sync(self, endpoint, **kwargs):
            captured["endpoint"] = endpoint
            captured["payload"] = kwargs["json"]
            return Response()

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kwargs: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert promote.handle_promote([str(path), "-w", "demo", "--preview"]) == 0
    assert captured["endpoint"] == promote.PROMOTION_PREVIEW_ENDPOINT
    assert captured["payload"]["schema_version"] == promote.REVIEWED_PROMOTION_SCHEMA
    assert len(captured["payload"]["protected_source"]["commit_sha"]) == 40


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

    assert promote.handle_promote(["preview", str(path), "-w", "demo"]) == 0

    assert captured["endpoint"] == "/api/workspace-promotions/preview"
    assert captured["payload"]["target"] == "production"
    assert captured["payload"]["entry"] == {
        "path": "features/demo.py",
        "function": "demo",
    }
    protected = captured["payload"]["protected_source"]
    assert len(protected["commit_sha"]) == 40
    assert len(protected["tree_sha"]) == 40
    assert "closure_id" not in captured["payload"]["snapshot"]
    assert "content_base64" not in captured["payload"]["snapshot"]["closure"][0]
    assert "No source bytes were activated" in capsys.readouterr().out


def test_draft_is_private_local_only_and_includes_complete_graph(
    tmp_path: Path, monkeypatch
) -> None:
    path = _workspace(tmp_path)
    dependent = tmp_path / "features/consumer.py"
    dependent.write_text("from features.demo import demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    output = tmp_path / "draft.json"
    monkeypatch.chdir(tmp_path)

    assert (
        promote.handle_promote(
            ["draft", str(path), "-w", "demo", "--out", str(output), "--json"]
        )
        == 0
    )

    draft = json.loads(output.read_text(encoding="utf-8"))
    assert draft["authority"] == "local_only"
    assert draft["activatable"] is False
    assert draft["graph"]["traversal_complete"] is True
    assert [item["path"] for item in draft["graph"]["reverse_dependencies"]] == [
        "features/consumer.py"
    ]
    assert output.stat().st_mode & 0o077 == 0


def test_reviewed_preview_uses_git_blob_not_dirty_worktree(
    tmp_path: Path, monkeypatch
) -> None:
    path = _workspace(tmp_path)
    reviewed = path.read_bytes()
    path.write_bytes(
        reviewed.replace(b"return []", b"return ['dirty']").replace(b"\n", b"\r\n")
    )
    monkeypatch.chdir(tmp_path)
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {"candidate_id": "sha256:" + "a" * 64, "closure": []}

    class Client:
        def post_sync(self, _endpoint, **kwargs):
            captured["payload"] = kwargs["json"]
            return Response()

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kwargs: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert promote.handle_promote(["preview", str(path), "-w", "demo"]) == 0
    encoded = captured["payload"]["snapshot"]["closure"][0]["content_base64"]
    assert base64.b64decode(encoded) == reviewed


def test_canary_uploads_exact_non_activatable_draft_then_executes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = _workspace(tmp_path)
    draft_path = tmp_path / "draft.json"
    monkeypatch.chdir(tmp_path)
    assert (
        promote.handle_promote(
            ["draft", str(path), "-w", "demo", "--out", str(draft_path)]
        )
        == 0
    )
    captured = []

    class Response:
        is_success = True

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Client:
        def post_sync(self, endpoint, **kwargs):
            captured.append((endpoint, kwargs))
            if endpoint == promote.PROMOTION_DRAFTS_ENDPOINT:
                return Response(
                    {
                        "artifact_id": "draft-artifact",
                        "content_id": "sha256:" + "5" * 64,
                        "authority": "local_only",
                        "activatable": False,
                    }
                )
            return Response(
                {
                    "execution_id": "execution-1",
                    "expires_at": "2026-08-19T12:15:00Z",
                }
            )

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kwargs: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert (
        promote.handle_promote(
            [
                "canary",
                str(draft_path),
                "--params",
                '{"tenant":"bounded"}',
                "--ttl-seconds",
                "300",
            ]
        )
        == 0
    )

    assert captured[0][0] == promote.PROMOTION_DRAFTS_ENDPOINT
    assert captured[0][1]["json"]["activatable"] is False
    assert captured[0][1]["timeout"] == 120.0
    assert captured[1] == (
        promote.PROMOTION_DRAFT_CANARY_ENDPOINT.format(artifact_id="draft-artifact"),
        {"json": {"parameters": {"tenant": "bounded"}, "ttl_seconds": 300}},
    )
    assert "no registration, trigger, or Live pointer change" in capsys.readouterr().out


def test_canary_refuses_tampered_draft_before_network(
    tmp_path: Path, monkeypatch
) -> None:
    path = _workspace(tmp_path)
    draft_path = tmp_path / "draft.json"
    monkeypatch.chdir(tmp_path)
    assert (
        promote.handle_promote(
            ["draft", str(path), "-w", "demo", "--out", str(draft_path)]
        )
        == 0
    )
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["snapshot"]["closure"][0]["content_base64"] = base64.b64encode(
        b"tampered\n"
    ).decode("ascii")
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    called = False

    class Client:
        @staticmethod
        def post_sync(*_args, **_kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kwargs: Client())

    assert promote.handle_promote(["canary", str(draft_path)]) == 1
    assert called is False


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
    assert evidence["closure_id"].startswith("sha256:")
    assert evidence["evidence_id"].startswith("local:")
    assert evidence["authority"] == "local_only"
    assert evidence["activatable"] is False
    assert evidence["entry"] == {"path": "features/demo.py", "function": "demo"}
    assert len(evidence["input_sha256"]) == 64
    assert len(evidence["result_sha256"]) == 64


def test_status_distinguishes_live_runtime_coherent_history_pending(
    monkeypatch, capsys
) -> None:
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {
                "release_id": "sha256:" + "1" * 64,
                "candidate_id": "sha256:" + "2" * 64,
                "activation_state": "live",
                "propagation_state": "complete",
                "history_state": "pending",
                "coherent": True,
                "files": [
                    {
                        "path": "modules/meraki.py",
                        "expected_sha256": "3" * 64,
                        "immutable_coherent": True,
                        "cache_coherent": True,
                        "projected_repo_coherent": True,
                        "history_coherent": False,
                    }
                ],
                "diagnostics": [],
            }

    class Client:
        def get_sync(self, endpoint):
            captured["endpoint"] = endpoint
            return Response()

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kwargs: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert promote.handle_promote(["status"]) == 0

    output = capsys.readouterr().out
    assert captured["endpoint"] == promote.PROMOTION_LIVE_ENDPOINT
    assert "Runtime coherent: yes" in output
    assert "History: pending" in output
    assert "Outcome: Live / runtime coherent / history pending" in output
    assert "history=MISMATCH" in output


def test_status_reads_one_exact_release_as_json(monkeypatch, capsys) -> None:
    release_id = "sha256:" + "4" * 64
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {"release_id": release_id, "coherent": False}

    class Client:
        def get_sync(self, endpoint):
            captured["endpoint"] = endpoint
            return Response()

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kwargs: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert promote.handle_promote(["status", release_id, "--json"]) == 0
    assert captured["endpoint"] == promote.PROMOTION_RELEASE_ENDPOINT.format(
        release_id=release_id
    )
    assert json.loads(capsys.readouterr().out)["release_id"] == release_id


def test_preview_surfaces_dma_registration_only_intent(capsys) -> None:
    commit = "1" * 40
    tree = "2" * 40
    digest = "3" * 64
    payload = {
        "entry": {"path": "features/dmarc/ingest.py", "function": "ingest_dmarc"},
        "protected_source": {"commit_sha": commit, "tree_sha": tree},
        "snapshot": {
            "snapshot_id": "sha256:" + "4" * 64,
            "closure": [{"path": "features/dmarc/ingest.py", "sha256": digest}],
        },
    }
    result = {
        "candidate_id": "sha256:" + "5" * 64,
        "closure_id": "sha256:" + "6" * 64,
        "risk_class": "R0",
        "lifecycle_status": "eligible",
        "ready_to_activate": True,
        "registration": {
            "intent": [
                {
                    "action": "create",
                    "path": "features/dmarc/ingest.py",
                    "function_name": "ingest_dmarc",
                }
            ],
            "intent_fingerprint": "sha256:" + "7" * 64,
            "state_fingerprint": "sha256:" + "8" * 64,
        },
        "closure": [
            {
                "path": "features/dmarc/ingest.py",
                "sha256": digest,
                "relation": "selected",
            }
        ],
        "diagnostics": [],
    }

    promote._render_preview(
        result,
        payload,
        repository="example/workspace",
        expected_closure_id="sha256:" + "6" * 64,
    )

    output = capsys.readouterr().out
    assert "Registration intent:" in output
    assert "create" in output
    assert "features/dmarc/ingest.py::ingest_dmarc" in output
    assert "Ready to activate: yes" in output
