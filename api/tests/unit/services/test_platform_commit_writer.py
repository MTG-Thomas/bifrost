import base64
import hashlib
import json
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


def head_response(*, history=None, oid="a" * 40) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "repository": {
                    "ref": {
                        "target": {
                            "oid": oid,
                            "tree": {"oid": "1" * 40},
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
    assert len(seen) == 8


@pytest.mark.asyncio
async def test_writer_reuses_reachable_changeset_commit_instead_of_replaying_mutation(
    private_key_pem,
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
                    history=[
                        {
                            "oid": existing_sha,
                            "message": (
                                "Publish production source\n\n"
                                f"Workspace-Changeset-ID: {changeset_id} \r\n"
                            ),
                        }
                    ],
                )
            if "mutation CreatePlatformCommit" in payload["query"]:
                mutation_calls += 1
            if "query CommitReadback" in payload["query"]:
                response = readback_response(existing_sha)
                payload = response.json()
                payload["data"]["repository"]["ref"]["target"]["oid"] = "e" * 40
                return httpx.Response(200, json=payload)
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
        result = await writer.write(commit_request(changeset_id=changeset_id))

    assert result.commit_sha == existing_sha
    assert mutation_calls == 0


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
