from __future__ import annotations

from unittest import mock

import httpx
import pytest
from click.testing import CliRunner

from bifrost.commands.apps import _parse_deps, apps_group
from bifrost.commands.events import (
    _build_schedule_config,
    _build_webhook_config,
    _pop_schedule_fields,
    _pop_webhook_fields,
    events_group,
)
from bifrost.commands.files import _policy_params, _policy_path, files_group
from bifrost.commands.policy_rules import policy_rule_group


class _Response:
    def __init__(self, body: object, *, status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.text = ""
        self.reason_phrase = "OK" if status_code < 400 else "Bad Request"
        self.request = httpx.Request("GET", "https://bifrost.test/api")

    def json(self) -> object:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code} {self.reason_phrase}",
                request=self.request,
                response=httpx.Response(
                    self.status_code,
                    json=self._body,
                    request=self.request,
                ),
            )


class _Client:
    def __init__(self, responses: dict[tuple[str, str], _Response]):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, path: str, **kwargs):
        self.calls.append(("get", path, kwargs))
        return self.responses[("get", path)]

    async def post(self, path: str, **kwargs):
        self.calls.append(("post", path, kwargs))
        return self.responses[("post", path)]

    async def put(self, path: str, **kwargs):
        self.calls.append(("put", path, kwargs))
        return self.responses[("put", path)]

    async def patch(self, path: str, **kwargs):
        self.calls.append(("patch", path, kwargs))
        return self.responses[("patch", path)]

    async def delete(self, path: str, **kwargs):
        self.calls.append(("delete", path, kwargs))
        return self.responses[("delete", path)]


class _Resolver:
    def __init__(self, _client):
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, kind: str, ref: str) -> str:
        self.calls.append((kind, ref))
        return f"{kind}-uuid-for-{ref}"


def _invoke_with_client(group, args: list[str], client: _Client):
    with (
        mock.patch("bifrost.commands.base.BifrostClient.get_instance", return_value=client),
        mock.patch("bifrost.commands.base.RefResolver", _Resolver),
    ):
        return CliRunner().invoke(group, args)


def test_policy_rule_commands_send_scope_filters_and_mutation_params():
    client = _Client(
        {
            ("get", "/api/policy-rules"): _Response(
                [{"name": "read_all", "domain": "file", "body": {"actions": ["read"]}}]
            ),
            ("put", "/api/policy-rules/file/read_all"): _Response({"updated": True}),
            ("delete", "/api/policy-rules/file/read_all"): _Response({}),
            ("get", "/api/policy-rules/file/read_all/usages"): _Response({"usages": []}),
        }
    )

    result = _invoke_with_client(
        policy_rule_group,
        ["list", "--domain", "file", "--scope", "org-1", "--json"],
        client,
    )
    assert result.exit_code == 0, result.output
    assert client.calls[-1] == (
        "get",
        "/api/policy-rules",
        {"params": {"domain": "file", "organization_id": "org-1"}},
    )

    result = _invoke_with_client(
        policy_rule_group,
        ["get", "file", "read_all", "--scope", "org-1", "--json"],
        client,
    )
    assert result.exit_code == 0, result.output
    assert '"name": "read_all"' in result.output

    result = _invoke_with_client(
        policy_rule_group,
        [
            "update",
            "file",
            "read_all",
            "--scope",
            "org-1",
            "--description",
            "Allow reads",
            "--json",
        ],
        client,
    )
    assert result.exit_code == 0, result.output
    assert client.calls[-1][0:2] == ("put", "/api/policy-rules/file/read_all")
    assert client.calls[-1][2]["params"] == {"organization_id": "org-1"}
    assert client.calls[-1][2]["json"] == {"description": "Allow reads"}

    result = _invoke_with_client(
        policy_rule_group,
        ["delete", "file", "read_all", "--scope", "org-1", "--json"],
        client,
    )
    assert result.exit_code == 0, result.output
    assert client.calls[-1] == (
        "delete",
        "/api/policy-rules/file/read_all",
        {"params": {"organization_id": "org-1"}},
    )

    result = _invoke_with_client(
        policy_rule_group,
        ["usages", "file", "read_all", "--scope", "org-1", "--json"],
        client,
    )
    assert result.exit_code == 0, result.output
    assert client.calls[-1][0:2] == (
        "get",
        "/api/policy-rules/file/read_all/usages",
    )


def test_policy_rule_get_reports_missing_named_rule():
    client = _Client({("get", "/api/policy-rules"): _Response([])})

    result = _invoke_with_client(
        policy_rule_group,
        ["get", "table", "missing"],
        client,
    )

    assert result.exit_code == 1
    assert "policy rule 'missing'" in result.output


@pytest.mark.asyncio
async def test_event_flat_config_helpers_resolve_nested_payloads(tmp_path):
    config_file = tmp_path / "webhook.json"
    config_file.write_text('{"secret": "value"}', encoding="utf-8")
    resolver = _Resolver(None)

    assert await _build_schedule_config(cron=None, timezone=None, enabled=None) is None
    assert await _build_schedule_config(
        cron="*/5 * * * *",
        timezone="America/Indiana/Indianapolis",
        enabled=False,
    ) == {
        "cron_expression": "*/5 * * * *",
        "timezone": "America/Indiana/Indianapolis",
        "enabled": False,
    }

    webhook = await _build_webhook_config(
        adapter="github",
        integration_ref="GitHub OAuth",
        config_raw=f"@{config_file}",
        resolver=resolver,
    )
    assert webhook == {
        "adapter_name": "github",
        "integration_id": "integration-uuid-for-GitHub OAuth",
        "config": {"secret": "value"},
    }

    fields = {
        "schedule_cron": "* * * * *",
        "schedule_timezone": "UTC",
        "schedule_enabled": True,
        "webhook_adapter": "slack",
        "webhook_integration": "Slack",
        "webhook_config": '{"channel": "#ops"}',
        "name": "keep",
    }
    assert _pop_schedule_fields(fields) == ("* * * * *", "UTC", True)
    assert _pop_webhook_fields(fields) == ("slack", "Slack", '{"channel": "#ops"}')
    assert fields == {"name": "keep"}


def test_event_subscribe_validates_target_flags_before_api_calls():
    client = _Client({})

    result = _invoke_with_client(events_group, ["subscribe", "source-1"], client)
    assert result.exit_code != 0
    assert "exactly one" in result.output
    assert client.calls == []

    result = _invoke_with_client(
        events_group,
        ["subscribe", "source-1", "--workflow", "wf", "--agent", "agent"],
        client,
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
    assert client.calls == []


def test_event_subscription_lookup_filters_wrapped_items():
    client = _Client(
        {
            ("get", "/api/events/sources/event_source-uuid-for-hook/subscriptions"): _Response(
                {"items": [{"id": "sub-1", "target_type": "workflow"}]}
            )
        }
    )

    result = _invoke_with_client(
        events_group,
        ["get-subscription", "hook", "sub-1", "--json"],
        client,
    )

    assert result.exit_code == 0, result.output
    assert '"id": "sub-1"' in result.output
    assert client.calls == [
        (
            "get",
            "/api/events/sources/event_source-uuid-for-hook/subscriptions",
            {},
        )
    ]


def test_apps_deps_parser_handles_package_json_and_literal_values(tmp_path):
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name": "demo", "dependencies": {"react": "^18", "left-pad": 1}}',
        encoding="utf-8",
    )

    assert _parse_deps(f"@{package_json}") == {"react": "^18", "left-pad": "1"}
    assert _parse_deps('{"vite": 5}') == {"vite": "5"}
    assert _parse_deps('{"dependencies": "not-a-dict", "vite": 5}') == {
        "dependencies": "not-a-dict",
        "vite": "5",
    }


def test_apps_create_with_deps_posts_created_app_then_puts_dependencies():
    client = _Client(
        {
            ("post", "/api/applications"): _Response({"id": "app-1", "slug": "desk"}),
            ("put", "/api/applications/app-1/dependencies"): _Response({"ok": True}),
        }
    )

    result = _invoke_with_client(
        apps_group,
        [
            "create",
            "--name",
            "Desk",
            "--slug",
            "desk",
            "--deps",
            '{"react": "^18"}',
            "--json",
        ],
        client,
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [
        (
            "post",
            "/api/applications",
            {"json": {"name": "Desk", "slug": "desk"}},
        ),
        (
            "put",
            "/api/applications/app-1/dependencies",
            {"json": {"react": "^18"}},
        ),
    ]
    assert '"dependencies"' in result.output


def test_apps_update_surfaces_dependency_failure_with_created_context():
    client = _Client(
        {
            ("patch", "/api/applications/app-uuid-for-desk"): _Response(
                {"id": "app-1", "slug": "desk"}
            ),
            ("put", "/api/applications/app-uuid-for-desk/dependencies"): _Response(
                {"detail": "bad dependency"},
                status_code=400,
            ),
        }
    )

    result = _invoke_with_client(
        apps_group,
        [
            "update",
            "desk",
            "--description",
            "Updated",
            "--deps",
            '{"bad": "nope"}',
            "--json",
        ],
        client,
    )

    assert result.exit_code == 1
    assert '"dependencies_error"' in result.output
    assert '"bad dependency"' in result.output
    assert '"error": "http_error"' in result.output
    assert '"status": 400' in result.output


def test_files_policy_helpers_and_policy_subcommands(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('[{"rule": "read_all"}]', encoding="utf-8")
    assert _policy_path("/apps/desk/") == "apps%2Fdesk"
    assert _policy_params("workspace", None) == {"location": "workspace"}
    assert _policy_params("uploads", "org-1") == {
        "location": "uploads",
        "scope": "org-1",
    }

    client = _Client(
        {
            ("get", "/api/files/policies"): _Response([{"path": "apps/desk"}]),
            ("get", "/api/files/policies/apps%2Fdesk"): _Response({"policies": []}),
            ("put", "/api/files/policies/apps%2Fdesk"): _Response({"saved": True}),
            ("delete", "/api/files/policies/apps%2Fdesk"): _Response({}),
        }
    )

    for args, expected_call in [
        (
            ["policies", "list", "--location", "uploads", "--scope", "org-1"],
            ("get", "/api/files/policies"),
        ),
        (
            ["policies", "get", "/apps/desk/", "--location", "uploads", "--scope", "org-1"],
            ("get", "/api/files/policies/apps%2Fdesk"),
        ),
        (
            [
                "policies",
                "set",
                "/apps/desk/",
                "--location",
                "uploads",
                "--scope",
                "org-1",
                "--file",
                str(policy_file),
            ],
            ("put", "/api/files/policies/apps%2Fdesk"),
        ),
        (
            ["policies", "delete", "/apps/desk/", "--location", "uploads", "--scope", "org-1"],
            ("delete", "/api/files/policies/apps%2Fdesk"),
        ),
    ]:
        result = _invoke_with_client(files_group, args, client)
        assert result.exit_code == 0, result.output
        assert client.calls[-1][0:2] == expected_call
        assert client.calls[-1][2]["params"] == {
            "location": "uploads",
            "scope": "org-1",
        }

    assert client.calls[2][2]["json"] == {"policies": [{"rule": "read_all"}]}
