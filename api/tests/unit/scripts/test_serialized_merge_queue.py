import json

import pytest

from scripts.serialized_merge_queue import (
    GitHubClient,
    QueueInvariantError,
    _head_contains_base,
    _same_repository_head,
    _verify_exact_merge,
    load_required_checks,
    required_check_state,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses

    def get(self, path):
        return self.responses[path]


def test_required_checks_preserve_app_identity_and_legacy_statuses():
    required = [
        {"context": "Unit Tests", "app_id": 15368},
        {"context": "CodeRabbit", "app_id": None},
        {"context": "SonarCloud", "app_id": 57789},
    ]
    runs = [
        {"name": "Unit Tests", "app": {"id": 15368}, "status": "completed", "conclusion": "success"},
        {"name": "Unit Tests", "app": {"id": 999}, "status": "completed", "conclusion": "success"},
        {"name": "SonarCloud", "app": {"id": 57789}, "status": "in_progress", "conclusion": None},
    ]
    statuses = [{"context": "CodeRabbit", "state": "success"}]

    state = required_check_state(required, runs, statuses)

    assert state.pending == ("SonarCloud",)
    assert state.failed == ()


def test_failed_required_check_is_not_accepted():
    state = required_check_state(
        [{"context": "E2E Tests", "app_id": 15368}],
        [{"name": "E2E Tests", "app": {"id": 15368}, "status": "completed", "conclusion": "failure"}],
        [],
    )
    assert state.failed == ("E2E Tests",)


def test_required_check_policy_is_explicit_and_validated(tmp_path):
    policy = tmp_path / "queue.json"
    policy.write_text(
        json.dumps({"required_checks": [{"context": "CodeRabbit", "app_id": None}]}),
        encoding="utf-8",
    )
    assert load_required_checks(policy) == [{"context": "CodeRabbit", "app_id": None}]
    policy.write_text(json.dumps({"required_checks": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="must define required_checks"):
        load_required_checks(policy)


def test_queue_branch_updates_are_limited_to_same_repository_heads():
    client = GitHubClient("MTG-Thomas/bifrost", "unused")
    assert _same_repository_head(client, {"head": {"repo": {"full_name": "MTG-Thomas/bifrost"}}})
    assert not _same_repository_head(client, {"head": {"repo": {"full_name": "contributor/bifrost"}}})


@pytest.mark.parametrize(("status", "expected"), [("ahead", True), ("identical", True), ("behind", False), ("diverged", False)])
def test_exact_base_ancestry_is_required(status, expected):
    client = FakeClient({"/compare/base...head": {"status": status}})
    assert _head_contains_base(client, "base", "head") is expected


def test_exact_merge_verification_is_tree_and_parent_bound():
    merge_sha, head_sha, base_sha, tree_sha = (letter * 40 for letter in "abcd")
    client = FakeClient(
        {
            "/git/ref/heads/main": {"object": {"sha": merge_sha}},
            f"/git/commits/{merge_sha}": {"parents": [{"sha": base_sha}], "tree": {"sha": tree_sha}},
            f"/git/commits/{head_sha}": {"tree": {"sha": tree_sha}},
        }
    )
    assert "Verified exact protected-main merge" in _verify_exact_merge(
        client, merge_sha=merge_sha, validated_head_sha=head_sha, validated_base_sha=base_sha
    )
    client.responses["/git/ref/heads/main"] = {"object": {"sha": "e" * 40}}
    with pytest.raises(QueueInvariantError, match="not current protected main"):
        _verify_exact_merge(
            client, merge_sha=merge_sha, validated_head_sha=head_sha, validated_base_sha=base_sha
        )
