"""Verified GitHub App commits for platform-authored workspace history."""

from __future__ import annotations

import asyncio
import hashlib
import time
import urllib.parse
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
import jwt

from bifrost.promotion import MAX_CLOSURE_BYTES, MAX_CLOSURE_FILES

_GITHUB_API_VERSION = "2022-11-28"
_GRAPHQL_URL = "https://api.github.com/graphql"
_REST_URL = "https://api.github.com"
_CHANGESET_TRAILER = "Workspace-Changeset-ID"
_CONVERGENCE_TRAILER = "Workspace-History-Convergence-Candidate"
_RECONCILED_CHANGESET_TRAILER = "Workspace-Reconciled-Changeset-ID"
_WORKSPACE_RELEASE_TRAILER = "Workspace-Release-ID"
_WORKSPACE_RELEASE_ROW_TRAILER = "Workspace-Release-Row-ID"
_WORKSPACE_RELEASE_LEDGER_TRAILER = "Workspace-Release-Ledger-SHA256"
_REF_SETTLE_DELAYS_SECONDS = (0.0, 0.25, 0.75, 1.5, 3.0)


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
    expected_head_sha: str | None = None
    expected_head_tree_sha: str | None = None
    convergence_candidate_id: str | None = None
    reconciled_changeset_ids: tuple[UUID, ...] = ()
    workspace_release_id: str | None = None
    workspace_release_row_id: UUID | None = None
    workspace_release_ledger_sha256: str | None = None

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
        if self.convergence_candidate_id:
            provenance.append(
                f"{_CONVERGENCE_TRAILER}: {self.convergence_candidate_id}"
            )
        provenance.extend(
            f"{_RECONCILED_CHANGESET_TRAILER}: {changeset_id}"
            for changeset_id in self.reconciled_changeset_ids
        )
        if self.workspace_release_id:
            provenance.append(
                f"{_WORKSPACE_RELEASE_TRAILER}: {self.workspace_release_id}"
            )
        if self.workspace_release_row_id:
            provenance.append(
                f"{_WORKSPACE_RELEASE_ROW_TRAILER}: {self.workspace_release_row_id}"
            )
        if self.workspace_release_ledger_sha256:
            provenance.append(
                f"{_WORKSPACE_RELEASE_LEDGER_TRAILER}: "
                f"{self.workspace_release_ledger_sha256}"
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


@dataclass(frozen=True)
class PlatformCommitSnapshot:
    commit_sha: str
    tree_sha: str
    file_sha256: dict[str, str | None]
    signature_state: str | None = None


@dataclass(frozen=True)
class PlatformSourceSnapshot:
    commit_sha: str
    tree_sha: str
    files: dict[str, bytes]


class PlatformCommitWriter(Protocol):
    async def inspect(
        self,
        paths: tuple[str, ...],
        *,
        ref: str | None = None,
        reachable_from: str | None = None,
    ) -> PlatformCommitSnapshot:
        raise NotImplementedError

    async def write(self, request: PlatformCommitRequest) -> PlatformCommitResult:
        raise NotImplementedError

    async def read_files(
        self,
        paths: tuple[str, ...],
        *,
        ref: str,
        reachable_from: str | None = None,
    ) -> PlatformSourceSnapshot:
        raise NotImplementedError


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

    async def inspect(
        self,
        paths: tuple[str, ...],
        *,
        ref: str | None = None,
        reachable_from: str | None = None,
    ) -> PlatformCommitSnapshot:
        """Read one immutable Git tree without changing the configured branch."""
        normalized_paths = tuple(sorted(set(paths)))
        if not normalized_paths:
            raise PlatformCommitError("platform commit inspection requires paths")
        if self.client is not None:
            return await self._inspect_with_client(
                self.client,
                normalized_paths,
                ref=ref,
                reachable_from=reachable_from,
            )
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._inspect_with_client(
                client,
                normalized_paths,
                ref=ref,
                reachable_from=reachable_from,
            )

    async def read_files(
        self,
        paths: tuple[str, ...],
        *,
        ref: str,
        reachable_from: str | None = None,
    ) -> PlatformSourceSnapshot:
        """Read exact bytes from one immutable, protected Git tree."""
        normalized_paths = tuple(sorted(set(paths)))
        if not normalized_paths:
            raise PlatformCommitError("platform source read requires paths")
        if len(normalized_paths) > MAX_CLOSURE_FILES:
            raise PlatformCommitError("platform source read exceeds the file limit")
        if self.client is not None:
            return await self._read_files_with_client(
                self.client,
                normalized_paths,
                ref=ref,
                reachable_from=reachable_from,
            )
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._read_files_with_client(
                client,
                normalized_paths,
                ref=ref,
                reachable_from=reachable_from,
            )

    async def _read_files_with_client(
        self,
        client: httpx.AsyncClient,
        paths: tuple[str, ...],
        *,
        ref: str,
        reachable_from: str | None,
    ) -> PlatformSourceSnapshot:
        token = await self._installation_token(client)
        commit = await self._commit_snapshot(client, token, ref)
        if reachable_from is not None:
            await self._verify_reachable_from(
                client, token, commit["oid"], reachable_from
            )
        files = await self._file_contents(client, token, paths, commit["oid"])
        return PlatformSourceSnapshot(
            commit_sha=commit["oid"],
            tree_sha=commit["tree_oid"],
            files=files,
        )

    async def _inspect_with_client(
        self,
        client: httpx.AsyncClient,
        paths: tuple[str, ...],
        *,
        ref: str | None,
        reachable_from: str | None,
    ) -> PlatformCommitSnapshot:
        token = await self._installation_token(client)
        if ref is None:
            if reachable_from is not None:
                raise PlatformCommitError(
                    "reachability inspection requires an immutable source ref"
                )
            commit = await self._branch_head(client, token)
            signature_state = self._verified_signature_state(commit, commit["oid"])
        else:
            commit = await self._commit_snapshot(client, token, ref)
            signature_state = None
            if reachable_from is not None:
                await self._verify_reachable_from(
                    client, token, commit["oid"], reachable_from
                )
        hashes = await self._file_hashes(client, token, paths, commit["oid"])
        return PlatformCommitSnapshot(
            commit_sha=commit["oid"],
            tree_sha=commit["tree_oid"],
            file_sha256=hashes,
            signature_state=signature_state,
        )

    async def _verify_reachable_from(
        self,
        client: httpx.AsyncClient,
        token: str,
        commit_sha: str,
        branch: str,
    ) -> None:
        normalized_branch = branch.removeprefix("refs/heads/")
        compare = await client.get(
            f"{_REST_URL}/repos/{self.owner}/{self.repository}/compare/"
            f"{urllib.parse.quote(commit_sha, safe='')}..."
            f"{urllib.parse.quote(normalized_branch, safe='')}",
            headers=self._headers(token),
        )
        payload = self._response_json(
            compare, f"verify source commit reachability from {normalized_branch}"
        )
        if payload.get("status") not in {"identical", "ahead"}:
            raise PlatformCommitError(
                f"GitHub source commit is not reachable from {normalized_branch}"
            )

    async def _write_with_client(
        self,
        client: httpx.AsyncClient,
        request: PlatformCommitRequest,
        headline: str,
        body: str,
    ) -> PlatformCommitResult:
        token = await self._installation_token(client)
        head = await self._branch_head(client, token)
        marker = (
            f"{_CONVERGENCE_TRAILER}: {request.convergence_candidate_id}"
            if request.convergence_candidate_id
            else f"{_CHANGESET_TRAILER}: {request.changeset_id}"
        )
        candidate = request.candidate_commit_sha or next(
            (
                item["oid"]
                for item in head["history"]
                if marker
                in {
                    line.strip() for line in str(item.get("message") or "").splitlines()
                }
            ),
            None,
        )
        if candidate is not None:
            return await self._verify_commit(
                client, token, request, candidate, require_head=False
            )

        if request.expected_head_sha and head["oid"] != request.expected_head_sha:
            raise PlatformCommitError(
                "GitHub branch head changed after the reviewed preview"
            )
        if (
            request.expected_head_tree_sha
            and head["tree_oid"] != request.expected_head_tree_sha
        ):
            raise PlatformCommitError(
                "GitHub branch tree changed after the reviewed preview"
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
        data = await self._graphql(
            client, token, self._CREATE_COMMIT_MUTATION, variables
        )
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
            self._BRANCH_HEAD_QUERY,
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
        return {
            "oid": str(oid),
            "tree_oid": str(tree["oid"]),
            "history": history,
            "signature": target.get("signature"),
        }

    async def _commit_snapshot(
        self, client: httpx.AsyncClient, token: str, ref: str
    ) -> dict:
        data = await self._graphql(
            client,
            token,
            self._COMMIT_SNAPSHOT_QUERY,
            {
                "owner": self.owner,
                "name": self.repository,
                "expression": ref,
            },
        )
        commit = (data.get("repository") or {}).get("object") or {}
        oid = commit.get("oid")
        tree = commit.get("tree") or {}
        if not oid or not tree.get("oid"):
            raise PlatformCommitError(
                "GitHub source ref did not resolve to an immutable commit"
            )
        return {"oid": str(oid), "tree_oid": str(tree["oid"]), "history": []}

    async def _file_hashes(
        self,
        client: httpx.AsyncClient,
        token: str,
        paths: tuple[str, ...],
        ref: str,
    ) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        for path in paths:
            encoded_path = urllib.parse.quote(path, safe="/")
            response = await client.get(
                f"{_REST_URL}/repos/{self.owner}/{self.repository}/contents/{encoded_path}",
                headers=self._headers(token, accept="application/vnd.github.raw+json"),
                params={"ref": ref},
            )
            if response.status_code == 404:
                result[path] = None
                continue
            if response.is_error:
                raise PlatformCommitError(
                    f"GitHub could not inspect file {path}: HTTP {response.status_code}"
                )
            result[path] = hashlib.sha256(response.content).hexdigest()
        return result

    async def _file_contents(
        self,
        client: httpx.AsyncClient,
        token: str,
        paths: tuple[str, ...],
        ref: str,
    ) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        total_bytes = 0
        for path in paths:
            encoded_path = urllib.parse.quote(path, safe="/")
            response = await client.get(
                f"{_REST_URL}/repos/{self.owner}/{self.repository}/contents/{encoded_path}",
                headers=self._headers(token, accept="application/vnd.github.raw+json"),
                params={"ref": ref},
            )
            if response.status_code == 404:
                raise PlatformCommitError(
                    f"protected source does not contain required path {path}"
                )
            if response.is_error:
                raise PlatformCommitError(
                    f"GitHub could not read file {path}: HTTP {response.status_code}"
                )
            total_bytes += len(response.content)
            if total_bytes > MAX_CLOSURE_BYTES:
                raise PlatformCommitError("platform source read exceeds the byte limit")
            result[path] = response.content
        return result

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
            self._COMMIT_READBACK_QUERY,
            {
                "owner": self.owner,
                "name": self.repository,
                "oid": commit_sha,
            },
        )
        repository = data.get("repository") or {}
        commit = repository.get("object") or {}
        if commit.get("oid") != commit_sha:
            raise PlatformCommitError(
                "GitHub commit readback did not match the created commit",
                commit_sha=commit_sha,
            )
        tree_sha = (commit.get("tree") or {}).get("oid")
        if not tree_sha:
            raise PlatformCommitError(
                "GitHub commit tree readback was incomplete", commit_sha=commit_sha
            )
        signature_state = self._verified_signature_state(commit, commit_sha)
        await self._verify_files(
            client,
            token,
            tuple((item.path, item.expected_sha256) for item in request.files),
            commit_sha,
            phase="committed tree",
        )
        await self._settle_branch_contains_commit(
            client,
            token,
            commit_sha,
            require_head=require_head,
        )
        return PlatformCommitResult(
            commit_sha=commit_sha,
            tree_sha=str(tree_sha),
            signature_state=signature_state,
        )

    async def _settle_branch_contains_commit(
        self,
        client: httpx.AsyncClient,
        token: str,
        commit_sha: str,
        *,
        require_head: bool,
    ) -> None:
        """Prove the immutable commit is at or behind the authoritative branch ref."""
        encoded_ref = urllib.parse.quote(f"heads/{self.branch}", safe="/")
        for delay in _REF_SETTLE_DELAYS_SECONDS:
            if delay:
                await asyncio.sleep(delay)
            response = await client.get(
                f"{_REST_URL}/repos/{self.owner}/{self.repository}/git/ref/{encoded_ref}",
                headers=self._headers(token),
            )
            payload = self._response_json(
                response,
                f"read back branch ref {self.branch}",
                commit_sha=commit_sha,
            )
            target = payload.get("object") or {}
            head_sha = target.get("sha")
            if target.get("type") != "commit" or not head_sha:
                raise PlatformCommitError(
                    "GitHub branch ref readback did not resolve to a commit",
                    commit_sha=commit_sha,
                )
            if head_sha == commit_sha:
                return

            compare = await client.get(
                f"{_REST_URL}/repos/{self.owner}/{self.repository}/compare/"
                f"{urllib.parse.quote(commit_sha, safe='')}..."
                f"{urllib.parse.quote(str(head_sha), safe='')}",
                headers=self._headers(token),
            )
            comparison = self._response_json(
                compare,
                f"verify commit reachability from branch {self.branch}",
                commit_sha=commit_sha,
            )
            if comparison.get("status") in {"identical", "ahead"}:
                return

        message = (
            "GitHub branch ref did not settle on history containing the created commit"
            if require_head
            else "previous changeset commit is no longer reachable from the configured branch"
        )
        raise PlatformCommitError(message, commit_sha=commit_sha)

    @staticmethod
    def _verified_signature_state(commit: dict, commit_sha: str) -> str:
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
        return str(signature["state"])

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
                headers=self._headers(token, accept="application/vnd.github.raw+json"),
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
            if response.is_error:
                raise PlatformCommitError(
                    f"GitHub could not read back {phase} file {path}: "
                    f"HTTP {response.status_code}",
                    commit_sha=commit_sha,
                )
            actual = hashlib.sha256(response.content).hexdigest()
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
    def _headers(
        token: str, *, accept: str = "application/vnd.github+json"
    ) -> dict[str, str]:
        return {
            "Accept": accept,
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

    _BRANCH_HEAD_QUERY = """
    query BranchHead($owner: String!, $name: String!, $ref: String!) {
      repository(owner: $owner, name: $name) {
        ref(qualifiedName: $ref) {
          target {
            ... on Commit {
              oid
              tree { oid }
              signature { isValid state wasSignedByGitHub }
              history(first: 100) { nodes { oid message } }
            }
          }
        }
      }
    }
    """

    _CREATE_COMMIT_MUTATION = """
    mutation CreatePlatformCommit($input: CreateCommitOnBranchInput!) {
      createCommitOnBranch(input: $input) {
        commit { oid }
        ref { name }
      }
    }
    """

    _COMMIT_SNAPSHOT_QUERY = """
    query CommitSnapshot(
      $owner: String!, $name: String!, $expression: String!
    ) {
      repository(owner: $owner, name: $name) {
        object(expression: $expression) {
          ... on Commit {
            oid
            tree { oid }
          }
        }
      }
    }
    """

    _COMMIT_READBACK_QUERY = """
    query CommitReadback(
      $owner: String!, $name: String!, $oid: String!
    ) {
      repository(owner: $owner, name: $name) {
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
