import base64
import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from src.services.platform_commit_writer import (
    GitHubAppCommitWriter,
    PlatformCommitError,
    PlatformCommitFile,
    PlatformCommitRequest,
    parse_github_repository,
)


@pytest.fixture(scope="session")
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def commit_request(*, changeset_id=None) -> PlatformCommitRequest:
    return PlatformCommitRequest(
        commit_message="Publish production source\nPreserve this context.",
        operator="operator@example.com",
        changeset_id=changeset_id or uuid4(),
        plan_id="plan-42",
        protected_main_source_sha="d" * 40,
        files=(
            PlatformCommitFile(
                path="workflows/example.py",
                content_base64=base64.b64encode(b"print('verified')\n").decode(),
                expected_before_sha256=None,
                expected_sha256=hashlib.sha256(b"print('verified')\n").hexdigest(),
            ),
            PlatformCommitFile(
                path="workflows/deleted.py",
                content_base64=None,
                expected_before_sha256=hashlib.sha256(
                    b"print('deleted')\n"
                ).hexdigest(),
                expected_sha256=None,
            ),
        ),
    )


def graphql_payload(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


def head_response(
    *, history=None, oid="a" * 40, signature_state="VALID"
) -> httpx.Response:
    valid = signature_state == "VALID"
    return httpx.Response(
        200,
        json={
            "data": {
                "repository": {
                    "ref": {
                        "target": {
                            "oid": oid,
                            "tree": {"oid": "1" * 40},
                            "signature": {
                                "isValid": valid,
                                "state": signature_state,
                                "wasSignedByGitHub": valid,
                            },
                            "history": {"nodes": history or []},
                        }
                    }
                }
            }
        },
    )


def readback_response(commit_sha: str, *, signature_state="VALID") -> httpx.Response:
    valid = signature_state == "VALID"
    return httpx.Response(
        200,
        json={
            "data": {
                "repository": {
                    "ref": {
                        "target": {
                            "oid": commit_sha,
                            "history": {"nodes": [{"oid": commit_sha}]},
                        }
                    },
                    "object": {
                        "oid": commit_sha,
                        "tree": {"oid": "2" * 40},
                        "signature": {
                            "isValid": valid,
                            "state": signature_state,
                            "wasSignedByGitHub": valid,
                        },
                    },
                }
            }
        },
    )


def ref_response(commit_sha: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "ref": "refs/heads/production-live",
            "object": {"type": "commit", "sha": commit_sha},
        },
    )


@pytest.mark.asyncio
async def test_writer_scopes_token_creates_cas_commit_and_verifies_remote_tree(
    private_key_pem,
):
    created_sha = "b" * 40
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            claims = jwt.decode(
                request.headers["Authorization"].removeprefix("Bearer "),
                options={"verify_signature": False},
            )
            assert claims["iss"] == "123"
            assert claims["exp"] - claims["iat"] == 600
            assert json.loads(request.content.decode()) == {
                "repositories": ["workspace"],
                "permissions": {"contents": "write"},
            }
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            payload = graphql_payload(request)
            query = payload["query"]
            assert request.headers["Authorization"] == "Bearer installation-token"
            if "query BranchHead" in query:
                return head_response()
            if "mutation CreatePlatformCommit" in query:
                commit_input = payload["variables"]["input"]
                assert commit_input["expectedHeadOid"] == "a" * 40
                assert commit_input["branch"] == {
                    "repositoryNameWithOwner": "MTG-Thomas/workspace",
                    "branchName": "production-live",
                }
                assert commit_input["fileChanges"] == {
                    "additions": [
                        {
                            "path": "workflows/example.py",
                            "contents": base64.b64encode(
                                b"print('verified')\n"
                            ).decode(),
                        }
                    ],
                    "deletions": [{"path": "workflows/deleted.py"}],
                }
                message = commit_input["message"]
                assert message["headline"] == "Publish production source"
                assert "Preserve this context." in message["body"]
                assert "Operator: operator@example.com" in message["body"]
                assert "Workspace-Changeset-ID:" in message["body"]
                assert "Plan-ID: plan-42" in message["body"]
                assert f"Protected-Main-Source-SHA: {'d' * 40}" in message["body"]
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "createCommitOnBranch": {
                                "commit": {"oid": created_sha},
                                "ref": {"name": "production-live"},
                            }
                        }
                    },
                )
            if "query CommitReadback" in query:
                return readback_response(created_sha)
        if request.url.path.endswith("/git/ref/heads/production-live"):
            return ref_response(created_sha)
        if request.url.path.endswith("/contents/workflows/example.py"):
            assert request.headers["Accept"] == "application/vnd.github.raw+json"
            if request.url.params["ref"] == "a" * 40:
                return httpx.Response(404, json={"message": "Not Found"})
            assert request.url.params["ref"] == created_sha
            return httpx.Response(200, content=b"print('verified')\n")
        if request.url.path.endswith("/contents/workflows/deleted.py"):
            assert request.headers["Accept"] == "application/vnd.github.raw+json"
            if request.url.params["ref"] == "a" * 40:
                return httpx.Response(200, content=b"print('deleted')\n")
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace.git",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        result = await writer.write(commit_request())

    assert result.commit_sha == created_sha
    assert result.tree_sha == "2" * 40
    assert result.signature_state == "VALID"
    assert len(seen) == 9


@pytest.mark.asyncio
async def test_writer_settles_stale_branch_ref_after_commit_creation(
    private_key_pem,
    monkeypatch,
):
    parent_sha = "a" * 40
    created_sha = "b" * 40
    ref_reads = 0

    monkeypatch.setattr(
        "src.services.platform_commit_writer._REF_SETTLE_DELAYS_SECONDS",
        (0.0, 0.0),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ref_reads
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            payload = graphql_payload(request)
            if "query BranchHead" in payload["query"]:
                return head_response(oid=parent_sha)
            if "mutation CreatePlatformCommit" in payload["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "createCommitOnBranch": {
                                "commit": {"oid": created_sha},
                                "ref": {"name": "production-live"},
                            }
                        }
                    },
                )
            if "query CommitReadback" in payload["query"]:
                return readback_response(created_sha)
        if request.url.path.endswith("/git/ref/heads/production-live"):
            ref_reads += 1
            return ref_response(parent_sha if ref_reads == 1 else created_sha)
        if request.url.path.endswith(f"/compare/{created_sha}...{parent_sha}"):
            return httpx.Response(200, json={"status": "behind"})
        if request.url.path.endswith("/contents/workflows/example.py"):
            if request.url.params["ref"] == parent_sha:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, content=b"print('verified')\n")
        if request.url.path.endswith("/contents/workflows/deleted.py"):
            if request.url.params["ref"] == parent_sha:
                return httpx.Response(200, content=b"print('deleted')\n")
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        result = await writer.write(commit_request())

    assert result.commit_sha == created_sha
    assert ref_reads == 2


@pytest.mark.asyncio
async def test_writer_accepts_created_commit_already_behind_current_head(
    private_key_pem,
):
    parent_sha = "a" * 40
    created_sha = "b" * 40
    advanced_sha = "c" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            payload = graphql_payload(request)
            if "query BranchHead" in payload["query"]:
                return head_response(oid=parent_sha)
            if "mutation CreatePlatformCommit" in payload["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "createCommitOnBranch": {
                                "commit": {"oid": created_sha},
                                "ref": {"name": "production-live"},
                            }
                        }
                    },
                )
            if "query CommitReadback" in payload["query"]:
                return readback_response(created_sha)
        if request.url.path.endswith("/git/ref/heads/production-live"):
            return ref_response(advanced_sha)
        if request.url.path.endswith(f"/compare/{created_sha}...{advanced_sha}"):
            return httpx.Response(200, json={"status": "ahead"})
        if request.url.path.endswith("/contents/workflows/example.py"):
            if request.url.params["ref"] == parent_sha:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, content=b"print('verified')\n")
        if request.url.path.endswith("/contents/workflows/deleted.py"):
            if request.url.params["ref"] == parent_sha:
                return httpx.Response(200, content=b"print('deleted')\n")
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        result = await writer.write(commit_request())

    assert result.commit_sha == created_sha


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_candidate", [False, True])
async def test_writer_reuses_reachable_changeset_commit_instead_of_replaying_mutation(
    private_key_pem,
    explicit_candidate,
):
    changeset_id = uuid4()
    existing_sha = "c" * 40
    mutation_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mutation_calls
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            payload = graphql_payload(request)
            if "query BranchHead" in payload["query"]:
                return head_response(
                    oid="e" * 40,
                    history=(
                        []
                        if explicit_candidate
                        else [
                            {
                                "oid": existing_sha,
                                "message": (
                                    "Publish production source\n\n"
                                    f"Workspace-Changeset-ID: {changeset_id} \r\n"
                                ),
                            }
                        ]
                    ),
                )
            if "mutation CreatePlatformCommit" in payload["query"]:
                mutation_calls += 1
            if "query CommitReadback" in payload["query"]:
                return readback_response(existing_sha)
        if request.url.path.endswith("/git/ref/heads/production-live"):
            return ref_response("e" * 40)
        if request.url.path.endswith(f"/compare/{existing_sha}...{'e' * 40}"):
            return httpx.Response(200, json={"status": "ahead"})
        if request.url.path.endswith("/contents/workflows/example.py"):
            return httpx.Response(200, content=b"print('verified')\n")
        if request.url.path.endswith("/contents/workflows/deleted.py"):
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="git@github.com:MTG-Thomas/workspace.git",
            branch="refs/heads/production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        request = commit_request(changeset_id=changeset_id)
        if explicit_candidate:
            request = replace(request, candidate_commit_sha=existing_sha)
        result = await writer.write(request)

    assert result.commit_sha == existing_sha
    assert mutation_calls == 0


@pytest.mark.asyncio
async def test_writer_rejects_commit_outside_authoritative_branch_history(
    private_key_pem,
    monkeypatch,
):
    candidate_sha = "c" * 40
    divergent_sha = "e" * 40
    ref_reads = 0

    monkeypatch.setattr(
        "src.services.platform_commit_writer._REF_SETTLE_DELAYS_SECONDS",
        (0.0, 0.0),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ref_reads
        if request.url.path.endswith("/git/ref/heads/production-live"):
            ref_reads += 1
            return ref_response(divergent_sha)
        if request.url.path.endswith(f"/compare/{candidate_sha}...{divergent_sha}"):
            return httpx.Response(200, json={"status": "diverged"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        with pytest.raises(PlatformCommitError, match="no longer reachable") as exc:
            await writer._settle_branch_contains_commit(
                client,
                "installation-token",
                candidate_sha,
                require_head=False,
            )

    assert exc.value.commit_sha == candidate_sha
    assert ref_reads == 2


@pytest.mark.asyncio
async def test_writer_returns_candidate_sha_when_signature_verification_fails(
    private_key_pem,
):
    created_sha = "b" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path.endswith("/contents/workflows/example.py"):
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path.endswith("/contents/workflows/deleted.py"):
            return httpx.Response(200, content=b"print('deleted')\n")
        payload = graphql_payload(request)
        if "query BranchHead" in payload["query"]:
            return head_response()
        if "mutation CreatePlatformCommit" in payload["query"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "createCommitOnBranch": {
                            "commit": {"oid": created_sha},
                            "ref": {"name": "production-live"},
                        }
                    }
                },
            )
        return readback_response(created_sha, signature_state="UNSIGNED")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        with pytest.raises(PlatformCommitError, match="not verified") as exc:
            await writer.write(commit_request())

    assert exc.value.commit_sha == created_sha


@pytest.mark.asyncio
async def test_writer_rejects_remote_path_drift_before_creating_commit(
    private_key_pem,
):
    mutation_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mutation_calls
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            payload = graphql_payload(request)
            if "query BranchHead" in payload["query"]:
                return head_response()
            if "mutation CreatePlatformCommit" in payload["query"]:
                mutation_calls += 1
        if request.url.path.endswith("/contents/workflows/example.py"):
            return httpx.Response(200, content=b"remote drift\n")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        with pytest.raises(PlatformCommitError, match="expected .* to be absent"):
            await writer.write(commit_request())

    assert mutation_calls == 0


@pytest.mark.asyncio
async def test_writer_inspects_exact_commit_bytes_without_mutation(private_key_pem):
    source_sha = "d" * 40
    content = b"reviewed source\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            payload = graphql_payload(request)
            assert "query CommitSnapshot" in payload["query"]
            assert payload["variables"]["expression"] == source_sha
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "object": {
                                "oid": source_sha,
                                "tree": {"oid": "1" * 40},
                            }
                        }
                    }
                },
            )
        if request.url.path.endswith("/contents/workflows/example.py"):
            assert request.url.params["ref"] == source_sha
            return httpx.Response(200, content=content)
        if request.url.path.endswith("/contents/workflows/missing.py"):
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        snapshot = await writer.inspect(
            ("workflows/missing.py", "workflows/example.py"), ref=source_sha
        )

    assert snapshot.commit_sha == source_sha
    assert snapshot.tree_sha == "1" * 40
    assert snapshot.file_sha256 == {
        "workflows/example.py": hashlib.sha256(content).hexdigest(),
        "workflows/missing.py": None,
    }
    assert snapshot.signature_state is None


@pytest.mark.asyncio
async def test_writer_reads_exact_protected_source_bytes(private_key_pem):
    source_sha = "d" * 40
    content = b"reviewed source\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "object": {
                                "oid": source_sha,
                                "tree": {"oid": "1" * 40},
                            }
                        }
                    }
                },
            )
        if "/compare/" in request.url.path:
            return httpx.Response(200, json={"status": "identical"})
        if request.url.path.endswith("/contents/workflows/example.py"):
            assert request.url.params["ref"] == source_sha
            return httpx.Response(200, content=content)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        snapshot = await writer.read_files(
            ("workflows/example.py",), ref=source_sha, reachable_from="main"
        )

    assert snapshot.commit_sha == source_sha
    assert snapshot.tree_sha == "1" * 40
    assert snapshot.files == {"workflows/example.py": content}


@pytest.mark.asyncio
async def test_writer_inspects_verified_branch_head(private_key_pem):
    content = b"live source\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            payload = graphql_payload(request)
            assert "query BranchHead" in payload["query"]
            return head_response()
        if request.url.path.endswith("/contents/workflows/example.py"):
            return httpx.Response(200, content=content)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        snapshot = await writer.inspect(("workflows/example.py",))

    assert snapshot.signature_state == "VALID"
    assert snapshot.file_sha256 == {
        "workflows/example.py": hashlib.sha256(content).hexdigest()
    }


@pytest.mark.asyncio
async def test_writer_rejects_unverified_branch_head_inspection(private_key_pem):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            return head_response(signature_state="MISSING")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        with pytest.raises(PlatformCommitError, match="not verified"):
            await writer.inspect(("workflows/example.py",))


@pytest.mark.asyncio
async def test_writer_requires_reviewed_source_to_be_reachable_from_main(
    private_key_pem,
):
    source_sha = "d" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path == "/graphql":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "object": {
                                "oid": source_sha,
                                "tree": {"oid": "1" * 40},
                            }
                        }
                    }
                },
            )
        if "/compare/" in request.url.path:
            assert request.url.path.endswith(f"/compare/{source_sha}...main")
            return httpx.Response(200, json={"status": "diverged"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        with pytest.raises(PlatformCommitError, match="not reachable from main"):
            await writer.inspect(
                ("workflows/example.py",),
                ref=source_sha,
                reachable_from="main",
            )


@pytest.mark.asyncio
async def test_writer_rejects_head_drift_bound_by_convergence_candidate(
    private_key_pem,
):
    mutation_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mutation_calls
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"})
        payload = graphql_payload(request)
        if "query BranchHead" in payload["query"]:
            return head_response(oid="e" * 40)
        if "mutation CreatePlatformCommit" in payload["query"]:
            mutation_calls += 1
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubAppCommitWriter(
            repo_url="https://github.com/MTG-Thomas/workspace",
            branch="production-live",
            app_id=123,
            installation_id=456,
            private_key=private_key_pem,
            client=client,
        )
        request = replace(
            commit_request(),
            expected_head_sha="f" * 40,
            convergence_candidate_id="sha256:" + "a" * 64,
        )
        with pytest.raises(PlatformCommitError, match="head changed"):
            await writer.write(request)

    assert mutation_calls == 0


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("ssh://git@github.com/owner/repo.git", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
    ],
)
def test_parse_github_repository(url, expected):
    assert parse_github_repository(url) == expected


def test_parse_github_repository_rejects_non_github_hosts():
    with pytest.raises(ValueError, match="github.com"):
        parse_github_repository("https://example.test/owner/repo")
