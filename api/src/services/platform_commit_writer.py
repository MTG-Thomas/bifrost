"""Verified GitHub App commits for platform-authored workspace history."""

from __future__ import annotations

import base64
import hashlib
import time
import urllib.parse
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
import jwt

_GITHUB_API_VERSION = "2022-11-28"
_GRAPHQL_URL = "https://api.github.com/graphql"
_REST_URL = "https://api.github.com"
_CHANGESET_TRAILER = "Workspace-Changeset-ID"


class PlatformCommitError(RuntimeError):
    """A commit was not proven safe and verified.

    ``commit_sha`` is populated when GitHub may already have created the commit,
    allowing the durable changeset retry record to avoid replaying it.
    """

    def __init__(self, message: str, *, commit_sha: str | None = None):
        super().__init__(message)
        self.commit_sha = commit_sha


@dataclass(frozen=True)
class PlatformCommitFile:
    path: str
    content_base64: str | None
    expected_before_sha256: str | None
    expected_sha256: str | None


@dataclass(frozen=True)
class PlatformCommitRequest:
    commit_message: str
    operator: str
    changeset_id: UUID
    files: tuple[PlatformCommitFile, ...]
    plan_id: str | None = None
    protected_main_source_sha: str | None = None
    candidate_commit_sha: str | None = None

    def github_message(self) -> tuple[str, str]:
        headline, separator, body = self.commit_message.partition("\n")
        provenance = [
            f"Operator: {self.operator}",
            f"{_CHANGESET_TRAILER}: {self.changeset_id}",
        ]
        if self.plan_id:
            provenance.append(f"Plan-ID: {self.plan_id}")
        if self.protected_main_source_sha:
            provenance.append(
                f"Protected-Main-Source-SHA: {self.protected_main_source_sha}"
            )
        sections = []
        if separator and body.strip():
            sections.append(body.strip())
        sections.append("\n".join(provenance))
        return headline.strip(), "\n\n".join(sections)


@dataclass(frozen=True)
class PlatformCommitResult:
    commit_sha: str
    tree_sha: str
    signature_state: str


class PlatformCommitWriter(Protocol):
    async def write(self, request: PlatformCommitRequest) -> PlatformCommitResult: ...


def parse_github_repository(repo_url: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` for a GitHub.com HTTPS or SSH remote."""
    value = repo_url.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urllib.parse.urlparse(value)
        if parsed.hostname != "github.com" or parsed.scheme not in {
            "http",
            "https",
            "ssh",
        }:
            raise ValueError(
                "platform commit writer requires a github.com repository URL"
            )
        path = parsed.path
    parts = path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(
            "GitHub repository URL must identify exactly one owner/repository"
        )
    return parts[0], parts[1]


class GitHubAppCommitWriter:
    """Create CAS-protected, GitHub-signed commits as one App installation."""

    def __init__(
        self,
        *,
        repo_url: str,
        branch: str,
        app_id: int,
        installation_id: int,
        private_key: str,
        client: httpx.AsyncClient | None = None,
    ):
        self.owner, self.repository = parse_github_repository(repo_url)
        self.branch = branch.removeprefix("refs/heads/")
        if not self.branch:
            raise ValueError("GitHub branch cannot be empty")
        self.app_id = app_id
        self.installation_id = installation_id
        self.private_key = private_key.replace("\\n", "\n")
        self.client = client

    async def write(self, request: PlatformCommitRequest) -> PlatformCommitResult:
        if not request.files:
            raise PlatformCommitError(
                "platform commit requires at least one file change"
            )
        headline, body = request.github_message()
        if not headline:
            raise PlatformCommitError("platform commit headline cannot be empty")

        if self.client is not None:
            return await self._write_with_client(self.client, request, headline, body)
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._write_with_client(client, request, headline, body)

    async def _write_with_client(
        self,
        client: httpx.AsyncClient,
        request: PlatformCommitRequest,
        headline: str,
        body: str,
    ) -> PlatformCommitResult:
        token = await self._installation_token(client)
        head = await self._branch_head(client, token)
        marker = f"{_CHANGESET_TRAILER}: {request.changeset_id}"
        candidate = request.candidate_commit_sha or next(
            (
                item["oid"]
                for item in head["history"]
                if marker in str(item.get("message") or "").splitlines()
            ),
            None,
        )
        if candidate is not None:
            return await self._verify_commit(
                client, token, request, candidate, require_head=False
            )

        await self._verify_files(
            client,
            token,
            tuple((item.path, item.expected_before_sha256) for item in request.files),
            head["oid"],
            phase="parent tree",
        )

        additions = [
            {"path": item.path, "contents": item.content_base64}
            for item in request.files
            if item.content_base64 is not None
        ]
        deletions = [
            {"path": item.path} for item in request.files if item.content_base64 is None
        ]
        variables = {
            "input": {
                "branch": {
                    "repositoryNameWithOwner": f"{self.owner}/{self.repository}",
                    "branchName": self.branch,
                },
                "expectedHeadOid": head["oid"],
                "message": {"headline": headline, "body": body},
                "fileChanges": {
                    "additions": additions,
                    "deletions": deletions,
                },
            }
        }
        data = await self._graphql(client, token, self._CREATE_COMMIT, variables)
        created = data.get("createCommitOnBranch") or {}
        commit = created.get("commit") or {}
        commit_sha = commit.get("oid")
        if not commit_sha:
            raise PlatformCommitError("GitHub did not return the created commit SHA")
        return await self._verify_commit(
            client, token, request, str(commit_sha), require_head=True
        )

    def _app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)},
            self.private_key,
            algorithm="RS256",
        )

    async def _installation_token(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"{_REST_URL}/app/installations/{self.installation_id}/access_tokens",
            headers=self._headers(self._app_jwt()),
            json={
                "repositories": [self.repository],
                "permissions": {"contents": "write"},
            },
        )
        payload = self._response_json(response, "create GitHub App installation token")
        token = payload.get("token")
        if not token:
            raise PlatformCommitError(
                "GitHub App installation token response was incomplete"
            )
        return str(token)

    async def _branch_head(self, client: httpx.AsyncClient, token: str) -> dict:
        data = await self._graphql(
            client,
            token,
            self._BRANCH_HEAD,
            {
                "owner": self.owner,
                "name": self.repository,
                "ref": f"refs/heads/{self.branch}",
            },
        )
        repository = data.get("repository") or {}
        ref = repository.get("ref") or {}
        target = ref.get("target") or {}
        oid = target.get("oid")
        tree = target.get("tree") or {}
        if not oid or not tree.get("oid"):
            raise PlatformCommitError("GitHub branch did not resolve to a commit")
        history = (target.get("history") or {}).get("nodes") or []
        return {"oid": str(oid), "tree_oid": str(tree["oid"]), "history": history}

    async def _verify_commit(
        self,
        client: httpx.AsyncClient,
        token: str,
        request: PlatformCommitRequest,
        commit_sha: str,
        *,
        require_head: bool,
    ) -> PlatformCommitResult:
        data = await self._graphql(
            client,
            token,
            self._COMMIT_READBACK,
            {
                "owner": self.owner,
                "name": self.repository,
                "ref": f"refs/heads/{self.branch}",
                "oid": commit_sha,
            },
        )
        repository = data.get("repository") or {}
        ref_target = (repository.get("ref") or {}).get("target") or {}
        commit = repository.get("object") or {}
        if commit.get("oid") != commit_sha:
            raise PlatformCommitError(
                "GitHub commit readback did not match the created commit",
                commit_sha=commit_sha,
            )
        reachable = {
            item.get("oid")
            for item in ((ref_target.get("history") or {}).get("nodes") or [])
        }
        if require_head and ref_target.get("oid") != commit_sha:
            raise PlatformCommitError(
                "GitHub branch ref did not advance to the created commit",
                commit_sha=commit_sha,
            )
        if not require_head and commit_sha not in reachable:
            raise PlatformCommitError(
                "previous changeset commit is no longer reachable from the configured branch",
                commit_sha=commit_sha,
            )
        tree_sha = (commit.get("tree") or {}).get("oid")
        if not tree_sha:
            raise PlatformCommitError(
                "GitHub commit tree readback was incomplete", commit_sha=commit_sha
            )
        signature = commit.get("signature") or {}
        if not (
            signature.get("isValid") is True
            and signature.get("wasSignedByGitHub") is True
            and signature.get("state") == "VALID"
        ):
            state = signature.get("state") or "MISSING"
            raise PlatformCommitError(
                f"GitHub commit signature is not verified: {state}",
                commit_sha=commit_sha,
            )
        await self._verify_files(
            client,
            token,
            tuple((item.path, item.expected_sha256) for item in request.files),
            commit_sha,
            phase="committed tree",
        )
        return PlatformCommitResult(
            commit_sha=commit_sha,
            tree_sha=str(tree_sha),
            signature_state=str(signature["state"]),
        )

    async def _verify_files(
        self,
        client: httpx.AsyncClient,
        token: str,
        files: tuple[tuple[str, str | None], ...],
        ref: str,
        *,
        phase: str,
    ) -> None:
        for path, expected_sha256 in files:
            encoded_path = urllib.parse.quote(path, safe="/")
            response = await client.get(
                f"{_REST_URL}/repos/{self.owner}/{self.repository}/contents/{encoded_path}",
                headers=self._headers(token),
                params={"ref": ref},
            )
            commit_sha = ref if phase == "committed tree" else None
            if expected_sha256 is None:
                if response.status_code == 404:
                    continue
                raise PlatformCommitError(
                    f"GitHub {phase} expected {path} to be absent",
                    commit_sha=commit_sha,
                )
            payload = self._response_json(
                response,
                f"read back {phase} file {path}",
                commit_sha=commit_sha,
            )
            content = payload.get("content")
            if payload.get("type") != "file" or not isinstance(content, str):
                raise PlatformCommitError(
                    f"GitHub {phase} readback for {path} was not a file",
                    commit_sha=commit_sha,
                )
            try:
                raw = base64.b64decode(content, validate=False)
            except ValueError as exc:
                raise PlatformCommitError(
                    f"GitHub returned invalid base64 for {path}",
                    commit_sha=commit_sha,
                ) from exc
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected_sha256:
                raise PlatformCommitError(
                    f"GitHub {phase} hash mismatch for {path}",
                    commit_sha=commit_sha,
                )

    async def _graphql(
        self,
        client: httpx.AsyncClient,
        token: str,
        query: str,
        variables: dict,
    ) -> dict:
        response = await client.post(
            _GRAPHQL_URL,
            headers=self._headers(token),
            json={"query": query, "variables": variables},
        )
        payload = self._response_json(response, "call GitHub GraphQL")
        errors = payload.get("errors")
        if errors:
            messages = "; ".join(
                str(item.get("message") or "GraphQL error") for item in errors
            )
            raise PlatformCommitError(
                f"GitHub GraphQL rejected platform commit: {messages}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlatformCommitError("GitHub GraphQL response did not contain data")
        return data

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        }

    @staticmethod
    def _response_json(
        response: httpx.Response,
        action: str,
        *,
        commit_sha: str | None = None,
    ) -> dict:
        if response.is_error:
            raise PlatformCommitError(
                f"GitHub could not {action}: HTTP {response.status_code}",
                commit_sha=commit_sha,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlatformCommitError(
                f"GitHub returned invalid JSON while trying to {action}",
                commit_sha=commit_sha,
            ) from exc
        if not isinstance(payload, dict):
            raise PlatformCommitError(
                f"GitHub returned an invalid response while trying to {action}",
                commit_sha=commit_sha,
            )
        return payload

    _BRANCH_HEAD = """
    query BranchHead($owner: String!, $name: String!, $ref: String!) {
      repository(owner: $owner, name: $name) {
        ref(qualifiedName: $ref) {
          target {
            ... on Commit {
              oid
              tree { oid }
              history(first: 100) { nodes { oid message } }
            }
          }
        }
      }
    }
    """

    _CREATE_COMMIT = """
    mutation CreatePlatformCommit($input: CreateCommitOnBranchInput!) {
      createCommitOnBranch(input: $input) {
        commit { oid }
        ref { name }
      }
    }
    """

    _COMMIT_READBACK = """
    query CommitReadback(
      $owner: String!, $name: String!, $ref: String!, $oid: String!
    ) {
      repository(owner: $owner, name: $name) {
        ref(qualifiedName: $ref) {
          target {
            ... on Commit {
              oid
              history(first: 100) { nodes { oid } }
            }
          }
        }
        object(expression: $oid) {
          ... on Commit {
            oid
            tree { oid }
            signature { isValid state wasSignedByGitHub }
          }
        }
      }
    }
    """
