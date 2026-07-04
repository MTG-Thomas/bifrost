"""Tests for bifrost git commands (formerly sync)."""
from __future__ import annotations

import pytest

from bifrost import git_commands
from bifrost.git_commands import (
    EXIT_CLEAN,
    EXIT_CONFLICTS,
    EXIT_ERROR,
    RESOLUTION_MAP,
    _format_sync_result,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(
        self,
        *,
        post_response: FakeResponse | None = None,
        get_responses: list[FakeResponse] | None = None,
    ):
        self.post_response = post_response or FakeResponse(200, {"job_id": "job-1"})
        self.get_responses = list(get_responses or [])
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    def post_sync(self, endpoint: str, json: dict | None = None) -> FakeResponse:
        self.posts.append((endpoint, json or {}))
        return self.post_response

    def get_sync(self, endpoint: str) -> FakeResponse:
        self.gets.append(endpoint)
        if self.get_responses:
            return self.get_responses.pop(0)
        return FakeResponse(200, {"status": "success", "data": {}})


class TestFormatSyncResult:
    """Test sync result output formatting."""

    def test_success_no_changes(self):
        """Should report no changes on success with zero counts."""
        result = {"status": "success", "pulled": 0, "pushed": 0, "commit_sha": None}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "no changes" in text.lower()

    def test_success_with_pulled_and_pushed(self):
        """Should summarize pull/push counts on success."""
        result = {
            "status": "success",
            "pulled": 3,
            "pushed": 1,
            "commit_sha": "abc1234def5678",
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "pulled 3" in text
        assert "pushed 1" in text
        assert "abc1234" in text

    def test_success_completed_status(self):
        """Should also accept 'completed' as a success status."""
        result = {"status": "completed", "pulled": 1, "pushed": 0, "commit_sha": None}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Push complete" in text

    def test_conflicts_shown(self):
        """Should list each conflict with path and resolve command."""
        result = {
            "status": "conflict",
            "conflicts": [
                {
                    "path": "workflows/billing.py",
                    "display_name": "billing",
                    "entity_type": "workflow",
                },
            ],
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "workflows/billing.py" in text
        assert "bifrost git resolve" in text
        assert "keep_remote" in text
        assert "keep_local" in text

    def test_conflict_resolution_commands_quote_paths(self):
        """Suggested resolve commands must be safe to copy into a shell."""
        result = {
            "status": "conflict",
            "conflicts": [
                {
                    "path": "workflows/billing.py; echo owned",
                    "display_name": "billing",
                    "entity_type": "workflow",
                },
            ],
        }

        lines = _format_sync_result(result)
        text = "\n".join(lines)

        assert "bifrost git resolve 'workflows/billing.py; echo owned=keep_remote'" in text
        assert "bifrost git resolve workflows/billing.py; echo owned=keep_remote" not in text

    def test_multiple_conflicts(self):
        """Should list all conflicts."""
        result = {
            "status": "conflict",
            "conflicts": [
                {"path": "workflows/a.py", "display_name": "a", "entity_type": "workflow"},
                {"path": "workflows/b.py", "display_name": "b", "entity_type": "workflow"},
            ],
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "2 conflicts" in text
        assert "workflows/a.py" in text
        assert "workflows/b.py" in text

    def test_failed_with_error(self):
        """Should show error message on failure."""
        result = {"status": "failed", "error": "Authentication failed"}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Authentication failed" in text
        assert "failed" in text.lower()

    def test_failed_unknown_error(self):
        """Should show fallback message when no error provided."""
        result = {"status": "failed"}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Unknown error" in text


class TestResolutionMap:
    """Test CLI-to-API resolution mapping."""

    def test_keep_local_maps_to_ours(self):
        assert RESOLUTION_MAP["keep_local"] == "ours"

    def test_keep_remote_maps_to_theirs(self):
        assert RESOLUTION_MAP["keep_remote"] == "theirs"


class TestPollJob:
    def test_poll_job_prints_phase_progress_and_returns_terminal_result(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        client = FakeClient(
            get_responses=[
                FakeResponse(200, {"status": "pending", "message": "phase one"}),
                FakeResponse(200, {"status": "pending", "message": "phase one"}),
                FakeResponse(200, {"status": "success", "data": {"ok": True}}),
            ]
        )
        monkeypatch.setattr(git_commands.time, "sleep", lambda _seconds: None)

        result = git_commands.poll_job(client, "job-1", label="Testing", timeout=5)

        assert result == {"status": "success", "data": {"ok": True}}
        assert client.gets == ["/api/jobs/job-1"] * 3
        output = capsys.readouterr().out
        assert "Testing" in output
        assert "phase one" in output

    def test_poll_job_raises_on_non_200_status(self):
        client = FakeClient(get_responses=[FakeResponse(500, text="bad")])

        with pytest.raises(RuntimeError, match="500"):
            git_commands.poll_job(client, "job-1")

    def test_poll_job_times_out(self, monkeypatch: pytest.MonkeyPatch):
        times = iter([0, 2])
        client = FakeClient()
        monkeypatch.setattr(git_commands.time, "time", lambda: next(times))

        with pytest.raises(TimeoutError, match="timed out"):
            git_commands.poll_job(client, "job-1", label="Slow", timeout=1)


class TestPostAndPoll:
    def test_post_and_poll_posts_body_and_polls_job(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        client = FakeClient(post_response=FakeResponse(200, {"job_id": "queued"}))

        def fake_poll(poll_client, job_id, *, label, timeout):
            assert poll_client is client
            assert job_id == "queued"
            assert label == "Doing"
            assert timeout == 9
            return {"status": "success"}

        monkeypatch.setattr(git_commands, "poll_job", fake_poll)

        result = git_commands._post_and_poll(
            client, "/endpoint", "Doing", json_body={"x": 1}, timeout=9
        )

        assert result == {"status": "success"}
        assert client.posts == [("/endpoint", {"x": 1})]

    def test_post_and_poll_exits_on_api_error(self, capsys: pytest.CaptureFixture[str]):
        client = FakeClient(post_response=FakeResponse(400, text="bad request"))

        with pytest.raises(SystemExit) as exc:
            git_commands._post_and_poll(client, "/endpoint", "Doing")

        assert exc.value.code == EXIT_ERROR
        assert "400 - bad request" in capsys.readouterr().err

    def test_post_and_poll_exits_on_poll_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        client = FakeClient()
        monkeypatch.setattr(
            git_commands,
            "poll_job",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("slow")),
        )

        with pytest.raises(SystemExit) as exc:
            git_commands._post_and_poll(client, "/endpoint", "Doing")

        assert exc.value.code == EXIT_ERROR
        assert "slow" in capsys.readouterr().err


class TestFormatHelpers:
    def test_format_changed_files_prints_symbols(self, capsys: pytest.CaptureFixture[str]):
        git_commands._format_changed_files(
            {
                "changed_files": [
                    {"status": "added", "path": "new.py"},
                    {"status": "modified", "path": "changed.py"},
                    {"status": "deleted", "path": "old.py"},
                    {"status": "renamed", "path": "move.py"},
                    {"status": "other", "path": "mystery.py"},
                    {},
                ]
            }
        )

        output = capsys.readouterr().out
        assert "6 changed file(s)" in output
        assert "+ new.py" in output
        assert "~ changed.py" in output
        assert "- old.py" in output
        assert "R move.py" in output
        assert "? mystery.py" in output
        assert "? unknown" in output

    def test_format_changed_files_prints_empty_message(
        self, capsys: pytest.CaptureFixture[str]
    ):
        git_commands._format_changed_files({"changed_files": []})

        assert "No changed files" in capsys.readouterr().out

    def test_format_ahead_behind_prints_counts(self, capsys: pytest.CaptureFixture[str]):
        git_commands._format_ahead_behind({"commits_ahead": 2, "commits_behind": 3})

        assert "2 ahead, 3 behind" in capsys.readouterr().out

    def test_format_sync_result_entity_changes_from_data(self):
        lines = _format_sync_result(
            {
                "status": "success",
                "data": {
                    "entity_changes": [
                        {"action": "added", "entity_type": "workflow", "name": "A"},
                        {
                            "action": "updated",
                            "entity_type": "form",
                            "name": "B",
                            "reason": "changed",
                        },
                        {"action": "removed", "entity_type": "table", "name": "C"},
                        {"action": "unknown", "entity_type": "app", "name": "D"},
                    ]
                },
            }
        )

        text = "\n".join(lines)
        assert "4 entity change(s): 1 added, 1 updated, 1 removed" in text
        assert "+ workflow" in text
        assert "~ form" in text
        assert "(changed)" in text
        assert "- table" in text
        assert "? app" in text

    def test_format_sync_result_conflict_defaults_unknown_values(self):
        lines = _format_sync_result({"status": "conflict", "conflicts": [{}]})

        text = "\n".join(lines)
        assert "unknown (file: unknown)" in text
        assert "unknown=keep_remote" in text

    def test_format_sync_result_failed_uses_message_fallback(self):
        lines = _format_sync_result({"status": "failed", "message": "no token"})

        assert lines == ["Push failed: no token"]


class TestGitSubcommands:
    def test_run_git_fetch_prints_preflight_issues(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {
                "status": "success",
                "data": {
                    "commits_ahead": 1,
                    "changed_files": [{"status": "added", "path": "x.py"}],
                    "preflight": {
                        "valid": False,
                        "issues": [
                            {"severity": "error", "message": "broken"},
                            {"severity": "warning", "message": "careful"},
                        ],
                    },
                },
            },
        )

        assert git_commands.run_git_fetch(FakeClient()) == EXIT_CLEAN
        output = capsys.readouterr().out
        assert "1 ahead" in output
        assert "x.py" in output
        assert "preflight error" in output
        assert "broken" in output
        assert "preflight warning" in output
        assert "careful" in output

    def test_run_git_fetch_returns_error_on_failed_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": "failed", "error": "nope"},
        )

        assert git_commands.run_git_fetch(FakeClient()) == EXIT_ERROR
        assert "nope" in capsys.readouterr().err

    def test_run_git_status_prints_conflicts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {
                "status": "success",
                "data": {
                    "changed_files": [],
                    "conflicts": [{"path": "conflict.py"}],
                },
            },
        )

        assert git_commands.run_git_status(FakeClient()) == EXIT_CLEAN
        assert "conflict.py" in capsys.readouterr().out

    def test_run_git_status_returns_error_on_failed_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": "failed", "error": "bad status"},
        )

        assert git_commands.run_git_status(FakeClient()) == EXIT_ERROR
        assert "bad status" in capsys.readouterr().err

    def test_run_git_commit_prints_commit_and_warnings(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        calls = []

        def fake_post(_client, endpoint, label, json_body=None, timeout=120):
            calls.append((endpoint, label, json_body, timeout))
            return {
                "status": "success",
                "data": {
                    "commit_sha": "abcdef123",
                    "files_committed": 2,
                    "preflight": {
                        "valid": False,
                        "issues": [{"severity": "warning", "message": "warn"}],
                    },
                },
            }

        monkeypatch.setattr(git_commands, "_post_and_poll", fake_post)

        assert git_commands.run_git_commit(FakeClient(), "msg") == EXIT_CLEAN
        assert calls == [("/api/github/commit", "Committing", {"message": "msg"}, 120)]
        output = capsys.readouterr().out
        assert "Committed abcdef1" in output
        assert "2 file(s) committed" in output
        assert "warn" in output

    def test_run_git_commit_blocks_on_preflight_errors(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {
                "status": "success",
                "data": {
                    "preflight": {
                        "valid": False,
                        "issues": [{"severity": "error", "message": "stop"}],
                    }
                },
            },
        )

        assert git_commands.run_git_commit(FakeClient(), "msg") == EXIT_ERROR
        assert "commit blocked" in capsys.readouterr().out

    def test_run_git_commit_prints_nothing_to_commit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": "success", "data": {}},
        )

        assert git_commands.run_git_commit(FakeClient(), "msg") == EXIT_CLEAN
        assert "Nothing to commit" in capsys.readouterr().out

    def test_run_git_commit_returns_error_on_failed_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": "failed", "error": "commit bad"},
        )

        assert git_commands.run_git_commit(FakeClient(), "msg") == EXIT_ERROR
        assert "commit bad" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("status", "expected_exit", "expected_output"),
        [
            ("success", EXIT_CLEAN, "Push complete"),
            ("completed", EXIT_CLEAN, "Push complete"),
            ("conflict", EXIT_CONFLICTS, "0 conflicts detected"),
            ("failed", EXIT_ERROR, "Push failed: push bad"),
        ],
    )
    def test_run_git_push_maps_status_to_exit_code(
        self,
        status: str,
        expected_exit: int,
        expected_output: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": status, "error": "push bad"},
        )

        assert git_commands.run_git_push(FakeClient()) == expected_exit
        assert expected_output in capsys.readouterr().out

    def test_run_git_resolve_maps_resolution_names_and_exit_code(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = []

        def fake_post(_client, endpoint, label, json_body=None, timeout=120):
            calls.append((endpoint, label, json_body, timeout))
            return {"status": "conflict", "conflicts": []}

        monkeypatch.setattr(git_commands, "_post_and_poll", fake_post)

        assert (
            git_commands.run_git_resolve(
                FakeClient(), {"a.py": "keep_local", "b.py": "keep_remote"}
            )
            == EXIT_CONFLICTS
        )
        assert calls == [
            (
                "/api/github/resolve",
                "Resolving",
                {"resolutions": {"a.py": "ours", "b.py": "theirs"}},
                120,
            )
        ]

    def test_run_git_diff_prints_unified_diff(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {
                "status": "success",
                "data": {"diff": "@@ diff"},
            },
        )

        assert git_commands.run_git_diff(FakeClient(), "x.py") == EXIT_CLEAN
        assert "@@ diff" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"head_content": None, "working_content": "new"}, "New file: x.py"),
            ({"head_content": "old", "working_content": None}, "Deleted file: x.py"),
            ({"head_content": "same", "working_content": "same"}, "No changes"),
            ({"head_content": "old", "working_content": "new"}, "--- x.py (HEAD)"),
        ],
    )
    def test_run_git_diff_prints_fallback_content(
        self,
        data: dict,
        expected: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": "success", "data": data},
        )

        assert git_commands.run_git_diff(FakeClient(), "x.py") == EXIT_CLEAN
        assert expected in capsys.readouterr().out

    def test_run_git_diff_returns_error_on_failed_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": "failed", "error": "diff bad"},
        )

        assert git_commands.run_git_diff(FakeClient(), "x.py") == EXIT_ERROR
        assert "diff bad" in capsys.readouterr().err

    def test_run_git_discard_prints_discarded_paths(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        calls = []

        def fake_post(_client, endpoint, label, json_body=None, timeout=120):
            calls.append((endpoint, label, json_body, timeout))
            return {
                "status": "success",
                "data": {"discarded_paths": ["a.py", "b.py"]},
            }

        monkeypatch.setattr(git_commands, "_post_and_poll", fake_post)

        assert git_commands.run_git_discard(FakeClient(), ["a.py", "b.py"]) == EXIT_CLEAN
        assert calls == [
            ("/api/github/discard", "Discarding", {"paths": ["a.py", "b.py"]}, 120)
        ]
        output = capsys.readouterr().out
        assert "Discarded changes to 2 file(s)" in output
        assert "a.py" in output

    def test_run_git_discard_prints_no_changes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": "success", "data": {}},
        )

        assert git_commands.run_git_discard(FakeClient(), []) == EXIT_CLEAN
        assert "No changes discarded" in capsys.readouterr().out

    def test_run_git_discard_returns_error_on_failed_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        monkeypatch.setattr(
            git_commands,
            "_post_and_poll",
            lambda *_args, **_kwargs: {"status": "failed", "error": "discard bad"},
        )

        assert git_commands.run_git_discard(FakeClient(), ["a.py"]) == EXIT_ERROR
        assert "discard bad" in capsys.readouterr().err
