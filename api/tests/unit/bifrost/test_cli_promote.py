"""CLI contract tests for preview-only Workspace promotion."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bifrost.commands import promote
from bifrost import cli
from bifrost.workspace_release_authorization import activation_challenge


def _authorization_challenge(
    artifact_id,
    candidate_id,
    release_id,
    prepared_id,
    *,
    risk_class="R0",
    effects=None,
):
    return activation_challenge(
        artifact_id=artifact_id,
        candidate_id=candidate_id,
        workspace_release_id=release_id,
        prepared_evidence_id=prepared_id,
        effective_manifest_id="sha256:" + "e" * 64,
        governed_manifest_id="sha256:" + "f" * 64,
        effective_registration_manifest_id="sha256:" + "0" * 64,
        risk_class=risk_class,
        computed_effects=effects or ["bifrost.read"],
        policy_version="test",
        protected_source={"commit_sha": "1" * 40, "tree_sha": "2" * 40},
    )


def _workspace(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
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
    monkeypatch.setattr(promote, "refresh_protected_main", lambda root: None)
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

    monkeypatch.setattr(
        promote.BifrostClient, "get_instance", lambda **_kwargs: Client()
    )
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
    monkeypatch.setattr(promote, "refresh_protected_main", lambda root: None)
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


def test_preview_can_include_all_reviewed_executable_changes(
    tmp_path: Path, monkeypatch
) -> None:
    selected = _workspace(tmp_path)
    second = tmp_path / "features/second.py"
    second.write_text(
        "from bifrost import workflow\n"
        "@workflow(effects=[{'kind': 'bifrost.read'}], "
        "enforced_bounds={'max_duration_seconds': 30, 'max_external_calls': 1, "
        "'max_records_read': 10, 'max_output_bytes': 1000})\n"
        "async def second(): return []\n",
        encoding="utf-8",
    )
    selected.write_text(
        selected.read_text() + "\n# reviewed change\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "cohort"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.chdir(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        promote,
        "refresh_protected_main",
        lambda _root: SimpleNamespace(commit_sha=head),
    )
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {"candidate_id": "sha256:" + "a" * 64, "closure": []}

    class Client:
        def post_sync(self, endpoint, **kwargs):
            captured["payload"] = kwargs["json"]
            return Response()

    monkeypatch.setattr(
        promote.BifrostClient, "get_instance", lambda **_kwargs: Client()
    )
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert (
        promote.handle_promote(
            [
                "preview",
                str(selected),
                "-w",
                "demo",
                "--include-declared-changes",
                "--json",
            ]
        )
        == 0
    )
    assert captured["payload"]["cohort_paths"] == [
        "features/demo.py",
        "features/second.py",
    ]
    assert {item["path"] for item in captured["payload"]["snapshot"]["closure"]} == {
        "features/demo.py",
        "features/second.py",
    }


def test_declared_helper_change_uses_unchanged_workflow_anchor(
    tmp_path: Path, monkeypatch
) -> None:
    selected = _workspace(tmp_path)
    helper = tmp_path / "helpers/shared.py"
    helper.parent.mkdir()
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    selected.write_text(
        "from bifrost import workflow\n"
        "from helpers.shared import VALUE\n"
        "@workflow(effects=[{'kind': 'bifrost.read'}], "
        "enforced_bounds={'max_duration_seconds': 30, 'max_external_calls': 1, "
        "'max_records_read': 10, 'max_output_bytes': 1000})\n"
        "async def demo(): return VALUE\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "baseline helper"], cwd=tmp_path, check=True
    )
    helper.write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "change helper"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=tmp_path,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        promote,
        "refresh_protected_main",
        lambda _root: SimpleNamespace(commit_sha=head),
    )
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

    monkeypatch.setattr(
        promote.BifrostClient, "get_instance", lambda **_kwargs: Client()
    )
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert (
        promote.handle_promote(
            [
                "preview",
                str(selected),
                "-w",
                "demo",
                "--include-declared-changes",
                "--json",
            ]
        )
        == 0
    )
    assert captured["payload"]["cohort_paths"] == ["helpers/shared.py"]
    assert {item["path"] for item in captured["payload"]["snapshot"]["closure"]} == {
        "features/demo.py",
        "helpers/shared.py",
    }


def test_draft_is_private_local_only_and_includes_complete_graph(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = _workspace(tmp_path)
    dependent = tmp_path / "features/consumer.py"
    dependent.write_text("from features.demo import demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    output = tmp_path / "draft.json"
    monkeypatch.chdir(tmp_path)

    assert (
        promote.handle_promote(["draft", str(path), "-w", "demo", "--out", str(output)])
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
    assert "use `bifrost run` locally" in capsys.readouterr().out


@pytest.mark.parametrize("escaped", ["../outside.json", "nested/../../outside.json"])
def test_draft_output_rejects_workspace_traversal(
    tmp_path: Path, monkeypatch, capsys, escaped: str
) -> None:
    path = _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert (
        promote.handle_promote(["draft", str(path), "-w", "demo", "--out", escaped])
        == 1
    )
    assert "draft output must remain" in capsys.readouterr().err


def test_draft_output_rejects_symlink_escape(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = _workspace(workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escaped").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(workspace)

    assert (
        promote.handle_promote(
            ["draft", str(path), "-w", "demo", "--out", "escaped/draft.json"]
        )
        == 1
    )
    assert "draft output must remain" in capsys.readouterr().err


def test_reviewed_preview_uses_git_blob_not_dirty_worktree(
    tmp_path: Path, monkeypatch
) -> None:
    path = _workspace(tmp_path)
    reviewed = path.read_bytes()
    path.write_bytes(
        reviewed.replace(b"return []", b"return ['dirty']").replace(b"\n", b"\r\n")
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(promote, "refresh_protected_main", lambda root: None)
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

    monkeypatch.setattr(
        promote.BifrostClient, "get_instance", lambda **_kwargs: Client()
    )
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert promote.handle_promote(["preview", str(path), "-w", "demo"]) == 0
    closure = captured["payload"]["snapshot"]["closure"]
    selected = next(item for item in closure if item["path"] == "features/demo.py")
    assert selected["sha256"] == hashlib.sha256(reviewed).hexdigest()
    assert "content_base64" not in selected


def test_canary_executes_only_an_existing_reviewed_artifact(
    monkeypatch, capsys
) -> None:
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
            return Response(
                {
                    "execution_id": "execution-1",
                    "artifact_id": "reviewed-artifact-id",
                }
            )

    monkeypatch.setattr(
        promote.BifrostClient, "get_instance", lambda **_kwargs: Client()
    )
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert (
        promote.handle_promote(
            [
                "canary",
                "reviewed-artifact-id",
                "--params",
                '{"tenant":"bounded"}',
            ]
        )
        == 0
    )

    assert captured == [
        (
            promote.PROMOTION_CANARY_ENDPOINT.format(
                artifact_id="reviewed-artifact-id"
            ),
            {"json": {"parameters": {"tenant": "bounded"}}},
        )
    ]
    output = capsys.readouterr().out
    assert "Reviewed artifact: reviewed-artifact-id" in output
    assert "no registration, trigger, or Live pointer change" in output


def test_canary_has_no_local_draft_or_client_ttl_options(capsys) -> None:
    assert promote.handle_promote(["canary", "draft.json", "--ttl-seconds", "300"]) == 2
    assert "unrecognized arguments: --ttl-seconds 300" in capsys.readouterr().err


def test_status_requires_exactly_one_status_target(capsys) -> None:
    assert promote.handle_promote(["status"]) == 2
    assert "exactly one of RELEASE_ROW_ID or --live" in capsys.readouterr().err


def test_prepare_polls_durable_job_and_preserves_exact_receipt(
    monkeypatch, capsys
) -> None:
    artifact_id = "11111111-1111-1111-1111-111111111111"
    candidate_id = "sha256:" + "a" * 64
    release_row_id = "22222222-2222-2222-2222-222222222222"
    job_id = "33333333-3333-3333-3333-333333333333"
    release_id = "sha256:" + "b" * 64
    prepared_id = "sha256:" + "c" * 64
    challenge = _authorization_challenge(
        artifact_id, candidate_id, release_id, prepared_id
    )
    captured = []

    class Response:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Client:
        def post_sync(self, endpoint, **kwargs):
            captured.append(("POST", endpoint, kwargs["json"]))
            return Response({"job_id": job_id, "status": "queued", "reused": False})

        def get_sync(self, endpoint):
            captured.append(("GET", endpoint, None))
            return Response(
                {
                    "id": job_id,
                    "status": "succeeded",
                    "result": {
                        "release_row_id": release_row_id,
                        "artifact_id": artifact_id,
                        "candidate_id": candidate_id,
                        "release_id": release_id,
                        "prepared_evidence_id": prepared_id,
                        "risk_class": "R0",
                        "computed_effects": ["bifrost.read"],
                        "computed_effects_id": challenge["computed_effects_id"],
                        "protected_source": challenge["protected_source"],
                        "effect_execution": "reviewed_canary_required",
                        "activation_authorization": challenge,
                    },
                }
            )

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kw: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert (
        promote.handle_promote(["prepare", artifact_id, "--candidate-id", candidate_id])
        == 0
    )
    assert captured == [
        (
            "POST",
            f"/api/workspace-promotions/artifacts/{artifact_id}/prepare",
            {"candidate_id": candidate_id},
        ),
        ("GET", f"/api/platform-jobs/{job_id}", None),
    ]
    output = capsys.readouterr().out
    assert f"Prepared release row: {release_row_id}" in output
    assert "Authorization required: reviewed_canary" in output
    assert "global Live pointer has not changed" in output


def test_prepare_rejects_effect_execution_claim_for_r2() -> None:
    artifact_id = "11111111-1111-1111-1111-111111111111"
    candidate_id = "sha256:" + "a" * 64
    release_id = "sha256:" + "b" * 64
    prepared_id = "sha256:" + "c" * 64
    challenge = _authorization_challenge(
        artifact_id,
        candidate_id,
        release_id,
        prepared_id,
        risk_class="R2",
        effects=["integration.write:halopsa"],
    )
    job = {
        "result": {
            "release_row_id": "22222222-2222-2222-2222-222222222222",
            "artifact_id": artifact_id,
            "candidate_id": candidate_id,
            "release_id": release_id,
            "prepared_evidence_id": prepared_id,
            "risk_class": "R2",
            "computed_effects": challenge["computed_effects"],
            "computed_effects_id": challenge["computed_effects_id"],
            "protected_source": challenge["protected_source"],
            "effect_execution": "reviewed_canary_required",
            "activation_authorization": challenge,
        }
    }

    with pytest.raises(promote.PromotionBundleError, match="effect-execution claim"):
        promote._prepare_result(
            job,
            artifact_id=artifact_id,
            candidate_id=candidate_id,
        )


def test_activate_sends_every_immutable_cas_and_canary_identity(
    monkeypatch, capsys
) -> None:
    release_row_id = "11111111-1111-1111-1111-111111111111"
    artifact_id = "22222222-2222-2222-2222-222222222222"
    canary_id = "33333333-3333-3333-3333-333333333333"
    candidate_id = "sha256:" + "a" * 64
    release_id = "sha256:" + "b" * 64
    evidence_id = "sha256:" + "c" * 64
    challenge = _authorization_challenge(
        artifact_id, candidate_id, release_id, evidence_id
    )
    captured = {}

    class Response:
        @staticmethod
        def json():
            return {
                "release_row_id": release_row_id,
                "artifact_id": artifact_id,
                "candidate_id": candidate_id,
                "release_id": release_id,
                "activation_state": "live",
                "is_live": True,
                "runtime": {"state": "coherent"},
                "history": {"state": "pending", "lock_state": "queued"},
            }

    class Client:
        def get_sync(self, _endpoint):
            return StatusResponse()

        def post_sync(self, endpoint, **kwargs):
            captured["endpoint"] = endpoint
            captured["payload"] = kwargs["json"]
            return Response()

    class StatusResponse:
        @staticmethod
        def json():
            return {"runtime": {"activation_authorization": challenge}}

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kw: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert (
        promote.handle_promote(
            [
                "activate",
                release_row_id,
                "--artifact-id",
                artifact_id,
                "--candidate-id",
                candidate_id,
                "--workspace-release-id",
                release_id,
                "--expected-base-release-id",
                "repo-v1:" + "d" * 64,
                "--expect-no-active-release",
                "--prepared-evidence-id",
                evidence_id,
                "--canary-execution-id",
                canary_id,
            ]
        )
        == 0
    )
    assert captured["endpoint"] == (
        f"/api/workspace-promotions/releases/{release_row_id}/activate"
    )
    assert captured["payload"] == {
        "artifact_id": artifact_id,
        "candidate_id": candidate_id,
        "workspace_release_id": release_id,
        "expected_base_release_id": "repo-v1:" + "d" * 64,
        "expected_active_release_id": None,
        "prepared_evidence_id": evidence_id,
        "authorization": {
            "kind": "reviewed_canary",
            "challenge_id": challenge["challenge_id"],
            "canary_execution_id": canary_id,
        },
    }
    assert "history projection may still be pending" in capsys.readouterr().out


def test_activate_requires_an_explicit_matching_live_cas(capsys) -> None:
    digest = "sha256:" + "a" * 64
    assert (
        promote.handle_promote(
            [
                "activate",
                "11111111-1111-1111-1111-111111111111",
                "--artifact-id",
                "22222222-2222-2222-2222-222222222222",
                "--candidate-id",
                digest,
                "--workspace-release-id",
                digest,
                "--expected-base-release-id",
                digest,
                "--prepared-evidence-id",
                digest,
                "--canary-execution-id",
                "33333333-3333-3333-3333-333333333333",
            ]
        )
        == 2
    )
    assert (
        "one of the arguments --expected-active-release-id" in capsys.readouterr().err
    )


def test_r2_activation_builds_exact_acknowledgement_without_canary_claim(
    monkeypatch, capsys
) -> None:
    release_row_id = "11111111-1111-1111-1111-111111111111"
    artifact_id = "22222222-2222-2222-2222-222222222222"
    candidate_id = "sha256:" + "a" * 64
    release_id = "sha256:" + "b" * 64
    prepared_id = "sha256:" + "c" * 64
    base_id = "sha256:" + "d" * 64
    challenge = _authorization_challenge(
        artifact_id,
        candidate_id,
        release_id,
        prepared_id,
        risk_class="R2",
        effects=["integration.write:halopsa"],
    )
    captured = {}

    class Response:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Client:
        def get_sync(self, _endpoint):
            return Response({"runtime": {"activation_authorization": challenge}})

        def post_sync(self, _endpoint, **kwargs):
            captured["payload"] = kwargs["json"]
            return Response(
                {
                    "release_row_id": release_row_id,
                    "artifact_id": artifact_id,
                    "candidate_id": candidate_id,
                    "release_id": release_id,
                    "activation_state": "live",
                    "is_live": True,
                    "runtime": {"state": "coherent"},
                    "history": {"state": "pending", "lock_state": "queued"},
                }
            )

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kw: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert (
        promote.handle_promote(
            [
                "activate",
                release_row_id,
                "--artifact-id",
                artifact_id,
                "--candidate-id",
                candidate_id,
                "--workspace-release-id",
                release_id,
                "--expected-base-release-id",
                base_id,
                "--expected-active-release-id",
                base_id,
                "--prepared-evidence-id",
                prepared_id,
                "--acknowledge-risk",
                "R2",
            ]
        )
        == 0
    )

    authorization = captured["payload"]["authorization"]
    assert authorization["kind"] == "risk_acknowledgement"
    assert authorization["acknowledgement"]["risk_class"] == "R2"
    assert authorization["acknowledgement"]["decision"] == (
        "activate_without_canary_or_effect_execution"
    )
    assert "canary" not in authorization
    output = capsys.readouterr().out
    assert "Effect execution: not performed" in output
    assert "integration.write:halopsa" in output


def test_r2_activation_rejects_canary_authorization(monkeypatch, capsys) -> None:
    digest = "sha256:" + "a" * 64
    challenge = _authorization_challenge(
        "22222222-2222-2222-2222-222222222222",
        digest,
        digest,
        digest,
        risk_class="R2",
        effects=["integration.write:halopsa"],
    )

    class Response:
        @staticmethod
        def json():
            return {"runtime": {"activation_authorization": challenge}}

    class Client:
        def get_sync(self, _endpoint):
            return Response()

        def post_sync(self, *_args, **_kwargs):
            raise AssertionError("invalid authorization must not be posted")

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kw: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert (
        promote.handle_promote(
            [
                "activate",
                "11111111-1111-1111-1111-111111111111",
                "--artifact-id",
                "22222222-2222-2222-2222-222222222222",
                "--candidate-id",
                digest,
                "--workspace-release-id",
                digest,
                "--expected-base-release-id",
                "repo-v1:" + "b" * 64,
                "--expect-no-active-release",
                "--prepared-evidence-id",
                digest,
                "--canary-execution-id",
                "33333333-3333-3333-3333-333333333333",
            ]
        )
        == 1
    )
    assert "cannot use or claim a reviewed canary" in capsys.readouterr().err


def test_status_uses_release_and_live_routes(monkeypatch, capsys) -> None:
    release_row_id = "11111111-1111-1111-1111-111111111111"
    requested = []

    class Response:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Client:
        def get_sync(self, endpoint):
            requested.append(endpoint)
            if endpoint.endswith("/live"):
                return Response({"active_release": None})
            return Response(
                {
                    "release_row_id": release_row_id,
                    "release_id": "sha256:" + "a" * 64,
                    "candidate_id": "sha256:" + "b" * 64,
                    "activation_state": "prepared",
                    "is_live": False,
                    "runtime": {"state": "prepared"},
                    "history": {"state": "not_queued", "lock_state": "not_queued"},
                }
            )

    monkeypatch.setattr(promote.BifrostClient, "get_instance", lambda **_kw: Client())
    monkeypatch.setattr(promote, "raise_for_status_with_detail", lambda _response: None)

    assert promote.handle_promote(["status", release_row_id]) == 0
    assert promote.handle_promote(["status", "--live"]) == 0
    assert requested == [
        f"/api/workspace-promotions/releases/{release_row_id}",
        "/api/workspace-promotions/live",
    ]
    assert "Live Workspace release: none" in capsys.readouterr().out


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
            "state": {
                "access_level": "authenticated",
                "role_ids": ["11111111-1111-1111-1111-111111111111"],
                "endpoint_enabled": True,
                "public_endpoint": True,
                "api_key_enabled": False,
            },
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
    assert (
        "Preserved activation surface: access=authenticated roles=1 "
        "endpoint=True public=True api_key=False"
    ) in output
    assert "Ready to activate: yes" in output
