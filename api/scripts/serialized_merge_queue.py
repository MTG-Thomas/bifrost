#!/usr/bin/env python3
"""Advance one pull request through Bifrost's serialized merge queue."""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

QUEUE_LABEL = "merge-queued"
BLOCKED_LABEL = "merge-queue-blocked"
BASE_BRANCH = "main"
ACCEPTED_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}
DEFAULT_POLICY_PATH = Path(".github/serialized-merge-queue.json")


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub API returned {status}: {message}")
        self.status = status


class BranchUpdateError(RuntimeError):
    pass


class MainAdvancedError(BranchUpdateError):
    pass


class QueueInvariantError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, repository: str, token: str) -> None:
        self.repository = repository
        self.base_url = f"https://api.github.com/repos/{repository}"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "bifrost-serialized-merge-queue",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=self.headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                message = str(json.loads(raw).get("message", raw))
            except json.JSONDecodeError:
                message = raw
            raise GitHubError(exc.code, message) from exc
        return json.loads(raw) if raw else None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("PUT", path, payload)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)


@dataclasses.dataclass(frozen=True)
class CheckState:
    pending: tuple[str, ...]
    failed: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.pending and not self.failed


def required_check_state(
    required_checks: Iterable[dict[str, Any]],
    check_runs: Iterable[dict[str, Any]],
    statuses: Iterable[dict[str, Any]],
) -> CheckState:
    latest: dict[tuple[str, int | None], dict[str, Any]] = {}
    for run in check_runs:
        key = (str(run.get("name", "")), run.get("app", {}).get("id"))
        latest.setdefault(key, run)

    latest_statuses: dict[str, dict[str, Any]] = {}
    for status in statuses:
        latest_statuses.setdefault(str(status.get("context", "")), status)

    pending: list[str] = []
    failed: list[str] = []
    for required in required_checks:
        context = str(required["context"])
        app_id = required["app_id"]
        run = latest.get((context, app_id))
        if run is not None and run.get("status") != "completed":
            pending.append(context)
        elif run is not None and run.get("conclusion") not in ACCEPTED_CHECK_CONCLUSIONS:
            failed.append(context)
        elif run is not None:
            continue
        elif app_id is not None:
            pending.append(context)
        else:
            status = latest_statuses.get(context)
            if status is None or status.get("state") in {None, "pending"}:
                pending.append(context)
            elif status.get("state") != "success":
                failed.append(context)
    return CheckState(tuple(sorted(pending)), tuple(sorted(failed)))


def load_required_checks(path: Path = DEFAULT_POLICY_PATH) -> list[dict[str, Any]]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    checks = policy.get("required_checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("serialized merge queue policy must define required_checks")
    normalized = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or set(check) != {"context", "app_id"}:
            raise ValueError(f"required_checks[{index}] must contain only context and app_id")
        if not isinstance(check["context"], str) or not check["context"]:
            raise ValueError(f"required_checks[{index}] has an invalid context")
        if check["app_id"] is not None and not isinstance(check["app_id"], int):
            raise ValueError(f"required_checks[{index}] has an invalid app_id")
        normalized.append({"context": check["context"], "app_id": check["app_id"]})
    return normalized


def _summary(message: str) -> None:
    print(message)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(f"- {message}\n")


def _queued_pulls(client: GitHubClient) -> list[dict[str, Any]]:
    label = urllib.parse.quote(QUEUE_LABEL)
    issues = client.get(f"/issues?state=open&labels={label}&per_page=100")
    pulls = []
    for issue in issues:
        if "pull_request" not in issue:
            continue
        pull = client.get(f"/pulls/{issue['number']}")
        if pull["base"]["ref"] != BASE_BRANCH:
            continue
        events = client.get(f"/issues/{issue['number']}/events?per_page=100")
        queue_events = [
            event["created_at"]
            for event in events
            if event.get("event") == "labeled"
            and event.get("label", {}).get("name") == QUEUE_LABEL
        ]
        pull["queue_entered_at"] = queue_events[-1] if queue_events else pull["created_at"]
        pulls.append(pull)
    return sorted(pulls, key=lambda pull: (pull["queue_entered_at"], pull["number"]))


def _remove_from_queue(client: GitHubClient, pull_number: int, reason: str) -> None:
    try:
        client.delete(f"/issues/{pull_number}/labels/{urllib.parse.quote(QUEUE_LABEL)}")
    except GitHubError as exc:
        if exc.status != 404:
            raise
    client.post(f"/issues/{pull_number}/labels", {"labels": [BLOCKED_LABEL]})
    client.post(
        f"/issues/{pull_number}/comments",
        {"body": f"Removed from `{QUEUE_LABEL}`: {reason}"},
    )


def _run_git(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode:
        detail = result.stderr.strip()
        raise BranchUpdateError(f"git {args[0]} failed: {detail or 'no stderr'}")
    return result.stdout.strip()


def _same_repository_head(client: GitHubClient, pull: dict[str, Any]) -> bool:
    return pull.get("head", {}).get("repo", {}).get("full_name") == client.repository


def _head_contains_base(client: GitHubClient, base_sha: str, head_sha: str) -> bool:
    comparison = client.get(f"/compare/{base_sha}...{head_sha}")
    return comparison.get("status") in {"ahead", "identical"}


def _verify_exact_merge(
    client: GitHubClient,
    *,
    merge_sha: str,
    validated_head_sha: str,
    validated_base_sha: str,
) -> str:
    current_main_sha = str(client.get(f"/git/ref/heads/{BASE_BRANCH}")["object"]["sha"])
    if current_main_sha != merge_sha:
        raise QueueInvariantError("merged SHA is not current protected main")
    merge_commit = client.get(f"/git/commits/{merge_sha}")
    head_commit = client.get(f"/git/commits/{validated_head_sha}")
    parents = [str(parent.get("sha", "")) for parent in merge_commit.get("parents", [])]
    if parents != [validated_base_sha]:
        raise QueueInvariantError("squash merge parent is not the validated protected-main base")
    merge_tree = str(merge_commit.get("tree", {}).get("sha", ""))
    head_tree = str(head_commit.get("tree", {}).get("sha", ""))
    if not merge_tree or merge_tree != head_tree:
        raise QueueInvariantError("squash merge tree does not match the validated pull request head")
    return (
        f"Verified exact protected-main merge {merge_sha}: parent {validated_base_sha}, "
        f"validated tree {merge_tree}."
    )


def _update_branch_with_deploy_key(client: GitHubClient, pull: dict[str, Any]) -> str:
    if not _same_repository_head(client, pull):
        raise BranchUpdateError("pull request head is not in this repository")
    head = pull["head"]
    head_ref, head_sha = str(head["ref"]), str(head["sha"])
    full_ref = f"refs/heads/{head_ref}"
    if not head_ref or head_ref.startswith("-"):
        raise BranchUpdateError("pull request head branch is invalid")
    key_path = Path(os.environ.get("QUEUE_DEPLOY_KEY_PATH", ""))
    known_hosts_path = Path(os.environ.get("QUEUE_KNOWN_HOSTS_PATH", ""))
    if not key_path.is_file() or not known_hosts_path.is_file():
        raise BranchUpdateError("queue SSH credential is unavailable")
    if key_path.stat().st_mode & 0o077:
        raise BranchUpdateError("queue SSH private key permissions are too broad")

    current_base_sha = str(client.get(f"/git/ref/heads/{BASE_BRANCH}")["object"]["sha"])
    remote_url = f"git@github.com:{client.repository}.git"
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = " ".join(
        [
            "ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o",
            f"UserKnownHostsFile={shlex.quote(str(known_hosts_path))}",
            "-i", shlex.quote(str(key_path)),
        ]
    )
    with tempfile.TemporaryDirectory(prefix="bifrost-merge-queue-") as temp_dir:
        worktree = Path(temp_dir)
        _run_git(["init", "--quiet"], worktree, env)
        _run_git(["check-ref-format", full_ref], worktree, env)
        _run_git(
            ["fetch", "--quiet", "--no-tags", remote_url,
             f"refs/heads/{BASE_BRANCH}:refs/remotes/origin/{BASE_BRANCH}",
             f"{full_ref}:refs/remotes/origin/queue-head"],
            worktree, env,
        )
        fetched_base = _run_git(["rev-parse", f"refs/remotes/origin/{BASE_BRANCH}"], worktree, env)
        fetched_head = _run_git(["rev-parse", "refs/remotes/origin/queue-head"], worktree, env)
        if fetched_base != current_base_sha:
            raise MainAdvancedError("main advanced while the queue prepared the update")
        if fetched_head != head_sha:
            raise BranchUpdateError("pull request head advanced while the queue prepared the update")
        _run_git(["checkout", "--quiet", "--detach", head_sha], worktree, env)
        _run_git(["config", "user.name", "bifrost merge queue"], worktree, env)
        _run_git(
            ["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            worktree, env,
        )
        _run_git(["merge", "--no-ff", "--no-edit", current_base_sha], worktree, env)
        updated_sha = _run_git(["rev-parse", "HEAD"], worktree, env)
        latest_base = str(client.get(f"/git/ref/heads/{BASE_BRANCH}")["object"]["sha"])
        if latest_base != current_base_sha:
            raise MainAdvancedError("main advanced before the queue could push the update")
        _run_git(
            ["push", "--quiet", f"--force-with-lease={full_ref}:{head_sha}",
             remote_url, f"HEAD:{full_ref}"],
            worktree, env,
        )
        pushed_head = str(client.get(f"/git/ref/heads/{head_ref}")["object"]["sha"])
        if pushed_head != updated_sha:
            raise BranchUpdateError("updated pull request head could not be verified")
    return updated_sha


def advance_queue(client: GitHubClient, required_checks: Iterable[dict[str, Any]]) -> str:
    pulls = _queued_pulls(client)
    if not pulls:
        return "Queue is empty."
    pull = pulls[0]
    number = int(pull["number"])
    head_sha = str(pull["head"]["sha"])
    current_base_sha = str(client.get(f"/git/ref/heads/{BASE_BRANCH}")["object"]["sha"])
    label = f"PR #{number}"
    if pull["draft"]:
        _remove_from_queue(client, number, "the pull request is still a draft")
        return f"{label} was removed because it is a draft."
    if pull.get("mergeable") is False or pull.get("mergeable_state") == "dirty":
        _remove_from_queue(client, number, "the pull request conflicts with main")
        return f"{label} was removed because it conflicts with main."
    if pull.get("mergeable") is None or pull.get("mergeable_state") == "unknown":
        return f"{label} mergeability is still being calculated."
    if not _head_contains_base(client, current_base_sha, head_sha):
        if not _same_repository_head(client, pull):
            _remove_from_queue(client, number, "a fork head cannot be refreshed onto current main")
            return f"{label} was removed because its fork head is not based on current main."
        try:
            updated_sha = _update_branch_with_deploy_key(client, pull)
        except MainAdvancedError:
            return f"{label} refresh raced with a main update; it will retry next pass."
        except BranchUpdateError as exc:
            _remove_from_queue(client, number, f"branch refresh failed: {exc}")
            return f"{label} was removed because its branch could not be refreshed: {exc}."
        return f"{label} was updated onto current main as {updated_sha}; checks will rerun."

    runs = client.get(f"/commits/{head_sha}/check-runs?filter=latest&per_page=100").get(
        "check_runs", []
    )
    statuses = client.get(f"/commits/{head_sha}/status").get("statuses", [])
    state = required_check_state(required_checks, runs, statuses)
    if state.failed:
        names = ", ".join(state.failed)
        _remove_from_queue(client, number, f"required checks failed: {names}")
        return f"{label} was removed after required check failure: {names}."
    if state.pending:
        return f"{label} is waiting for required checks: {', '.join(state.pending)}."

    latest_base_sha = str(client.get(f"/git/ref/heads/{BASE_BRANCH}")["object"]["sha"])
    if latest_base_sha != current_base_sha:
        return f"{label} validation raced with a main update; it will retry next pass."
    title = str(pull["title"])
    try:
        result = client.put(
            f"/pulls/{number}/merge",
            {"sha": head_sha, "merge_method": "squash", "commit_title": f"{title} (#{number})"},
        )
    except GitHubError as exc:
        if exc.status in {405, 409}:
            return f"{label} remains queued because GitHub protection rejected the merge: {exc}."
        raise
    if not result.get("merged"):
        return f"{label} remains queued: {result.get('message', 'GitHub rejected the merge')}."
    merge_sha = str(result.get("sha", ""))
    verification = _verify_exact_merge(
        client,
        merge_sha=merge_sha,
        validated_head_sha=head_sha,
        validated_base_sha=current_base_sha,
    )
    client.post(
        "/actions/workflows/ci.yml/dispatches",
        {
            "ref": BASE_BRANCH,
            "inputs": {"queue_post_merge": "true", "queue_merge_sha": merge_sha},
        },
    )
    return f"{label} merged as {merge_sha}. {verification}"


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repository or not token:
        print("GITHUB_REPOSITORY and GITHUB_TOKEN are required", file=sys.stderr)
        return 2
    _summary(advance_queue(GitHubClient(repository, token), load_required_checks()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
