from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.mcp_server.tools import files, policy_rules


def _context() -> SimpleNamespace:
    return SimpleNamespace(user_email="user@example.test")


@pytest.mark.asyncio
async def test_file_policy_helpers_encode_paths_and_build_optional_params():
    assert (
        files._policy_path("/Solutions/Alpha/read me.md")
        == "Solutions%2FAlpha%2Fread%20me.md"
    )
    assert files._policy_params("workspace", None) == {"location": "workspace"}
    assert files._policy_params("repo", "org", "billing") == {
        "location": "repo",
        "scope": "org",
        "solution": "billing",
    }


@pytest.mark.asyncio
async def test_list_file_policies_passes_filters_and_formats_body_shapes():
    calls = []

    async def call_rest(context, method, path, **kwargs):
        calls.append((context, method, path, kwargs))
        return 200, {"policies": [{"path": "a.py"}, {"path": "b.py"}]}

    with patch.object(files, "call_rest", call_rest):
        result = await files.list_file_policies(
            _context(),
            location="repo",
            scope="org",
            solution="billing",
        )

    assert result.structured_content == {
        "file_policies": [{"path": "a.py"}, {"path": "b.py"}],
        "count": 2,
    }
    assert calls[0][1:] == (
        "GET",
        "/api/files/policies",
        {"params": {"location": "repo", "scope": "org", "solution": "billing"}},
    )

    async def list_body_call_rest(*_args, **_kwargs):
        return 200, [{"path": "root.py"}]

    with patch.object(files, "call_rest", list_body_call_rest):
        list_result = await files.list_file_policies(_context())

    assert list_result.structured_content["file_policies"] == [{"path": "root.py"}]
    assert list_result.structured_content["count"] == 1

    async def failed_call_rest(*_args, **_kwargs):
        return 503, {"detail": "unavailable"}

    with patch.object(files, "call_rest", failed_call_rest):
        failed = await files.list_file_policies(_context())

    assert failed.structured_content["error"] == "list_file_policies failed: HTTP 503"
    assert failed.structured_content["body"] == {"detail": "unavailable"}


@pytest.mark.asyncio
async def test_get_set_and_delete_file_policy_validate_and_bridge_rest_calls():
    missing_get = await files.get_file_policy(_context(), "")
    missing_set = await files.set_file_policy(_context(), "", {"allow": ["read"]})
    bad_set = await files.set_file_policy(  # type: ignore[arg-type]
        _context(),
        "a.py",
        "read",
    )
    missing_delete = await files.delete_file_policy(_context(), "")

    assert missing_get.structured_content["error"] == "path is required"
    assert missing_set.structured_content["error"] == "path is required"
    assert bad_set.structured_content["error"] == "policies must be a list or object"
    assert missing_delete.structured_content["error"] == "path is required"

    calls = []

    async def call_rest(_context, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return 200, {"path": "folder/a.py", "policies": [{"allow": ["read"]}]}
        if method == "PUT":
            return 201, {
                "path": "folder/a.py",
                "policies": kwargs["json_body"]["policies"],
            }
        return 204, None

    with patch.object(files, "call_rest", call_rest):
        got = await files.get_file_policy(_context(), "folder/a.py", scope="org")
        set_result = await files.set_file_policy(
            _context(),
            "folder/a.py",
            [{"allow": ["read"]}],
            scope="org",
        )
        deleted = await files.delete_file_policy(_context(), "folder/a.py", scope="org")

    assert got.structured_content["path"] == "folder/a.py"
    assert set_result.structured_content["policies"] == [{"allow": ["read"]}]
    assert deleted.structured_content == {
        "deleted": "folder/a.py",
        "location": "workspace",
        "scope": "org",
    }
    assert calls == [
        (
            "GET",
            "/api/files/policies/folder%2Fa.py",
            {"params": {"location": "workspace", "scope": "org"}},
        ),
        (
            "PUT",
            "/api/files/policies/folder%2Fa.py",
            {
                "params": {"location": "workspace", "scope": "org"},
                "json_body": {"policies": [{"allow": ["read"]}]},
            },
        ),
        (
            "DELETE",
            "/api/files/policies/folder%2Fa.py",
            {"params": {"location": "workspace", "scope": "org"}},
        ),
    ]


@pytest.mark.asyncio
async def test_file_policy_tools_report_rest_errors_and_wrap_non_dict_body():
    async def get_failure(_context, method, path, **kwargs):
        return 404, {"detail": "missing"}

    with patch.object(files, "call_rest", get_failure):
        failed_get = await files.get_file_policy(_context(), "a.py")

    assert failed_get.structured_content["error"] == "get_file_policy failed: HTTP 404"
    assert failed_get.structured_content["body"] == {"detail": "missing"}

    async def set_text_body(_context, method, path, **kwargs):
        return 200, "created"

    with patch.object(files, "call_rest", set_text_body):
        set_result = await files.set_file_policy(_context(), "a.py", {"allow": ["read"]})

    assert set_result.structured_content == {"body": "created"}

    async def set_failure(_context, method, path, **kwargs):
        return 422, {"detail": "invalid policy"}

    with patch.object(files, "call_rest", set_failure):
        failed_set = await files.set_file_policy(_context(), "a.py", {"allow": []})

    assert failed_set.structured_content["error"] == "set_file_policy failed: HTTP 422"
    assert failed_set.structured_content["body"] == {"detail": "invalid policy"}

    async def delete_failure(_context, method, path, **kwargs):
        return 409, {"detail": "in use"}

    with patch.object(files, "call_rest", delete_failure):
        failed_delete = await files.delete_file_policy(_context(), "a.py")

    assert failed_delete.structured_content["error"] == "delete_file_policy failed: HTTP 409"
    assert failed_delete.structured_content["body"] == {"detail": "in use"}


@pytest.mark.asyncio
async def test_policy_rules_list_create_delete_validate_and_bridge_rest_calls():
    calls = []

    async def call_rest(_context, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return 200, [{"name": "readonly"}]
        if method == "POST":
            return 201, {
                "name": kwargs["json_body"]["name"],
                "domain": kwargs["json_body"]["domain"],
            }
        return 200, {"deleted": True}

    with patch.object(policy_rules, "call_rest", call_rest):
        listed = await policy_rules.list_policy_rules(_context(), domain="file")
        created = await policy_rules.create_policy_rule(
            _context(),
            name="readonly",
            domain="file",
            body={"actions": ["read"], "when": {"path": "*.md"}},
            description="Docs can be read",
            organization_id="org-1",
        )
        deleted = await policy_rules.delete_policy_rule(
            _context(),
            domain="file",
            name="readonly",
            organization_id="org-1",
        )

    assert listed.structured_content == {
        "policy_rules": [{"name": "readonly"}],
        "count": 1,
    }
    assert created.structured_content == {"name": "readonly", "domain": "file"}
    assert deleted.structured_content == {"deleted": "file/readonly"}
    assert calls == [
        ("GET", "/api/policy-rules?domain=file", {}),
        (
            "POST",
            "/api/policy-rules",
            {
                "json_body": {
                    "name": "readonly",
                    "domain": "file",
                    "body": {"actions": ["read"], "when": {"path": "*.md"}},
                    "description": "Docs can be read",
                    "organization_id": "org-1",
                }
            },
        ),
        ("DELETE", "/api/policy-rules/file/readonly?organization_id=org-1", {}),
    ]

    missing_name = await policy_rules.create_policy_rule(_context(), "", "file", {})
    missing_domain = await policy_rules.create_policy_rule(_context(), "r", "", {})
    missing_body = await policy_rules.create_policy_rule(_context(), "r", "file", {})
    delete_missing_domain = await policy_rules.delete_policy_rule(_context(), "", "r")
    delete_missing_name = await policy_rules.delete_policy_rule(_context(), "file", "")

    assert missing_name.structured_content["error"] == "name is required"
    assert missing_domain.structured_content["error"] == "domain is required"
    assert missing_body.structured_content["error"] == "body is required"
    assert delete_missing_domain.structured_content["error"] == "domain is required"
    assert delete_missing_name.structured_content["error"] == "name is required"


@pytest.mark.asyncio
async def test_policy_rules_report_rest_errors_and_non_list_success_body():
    async def failed_call_rest(*_args, **_kwargs):
        return 500, {"detail": "boom"}

    with patch.object(policy_rules, "call_rest", failed_call_rest):
        list_failed = await policy_rules.list_policy_rules(_context())
        create_failed = await policy_rules.create_policy_rule(
            _context(),
            "r",
            "file",
            {"when": {}},
        )
        delete_failed = await policy_rules.delete_policy_rule(_context(), "file", "r")

    assert list_failed.structured_content["error"] == "list_policy_rules failed: HTTP 500"
    assert create_failed.structured_content["error"] == "create_policy_rule failed: HTTP 500"
    assert delete_failed.structured_content["error"] == "delete_policy_rule failed: HTTP 500"

    async def dict_body_call_rest(*_args, **_kwargs):
        return 200, {"policy_rules": [{"name": "ignored"}]}

    with patch.object(policy_rules, "call_rest", dict_body_call_rest):
        listed = await policy_rules.list_policy_rules(_context())

    assert listed.structured_content == {"policy_rules": [], "count": 0}


def test_rest_parity_tool_registration_exposes_metadata():
    registered: list[tuple[object, str, str, object]] = []

    def fake_register_tool_with_context(mcp, func, tool_id, description, get_context_fn):
        registered.append((func, tool_id, description, get_context_fn))

    get_context = object()
    with patch(
        "src.services.mcp_server.generators.fastmcp_generator.register_tool_with_context",
        side_effect=fake_register_tool_with_context,
    ):
        files.register_tools(object(), get_context)
        policy_rules.register_tools(object(), get_context)

    assert [(tool_id, description) for _, tool_id, description, _ in registered] == [
        ("list_file_policies", "List file access policies."),
        ("get_file_policy", "Get a file access policy."),
        ("set_file_policy", "Create or replace a file access policy."),
        ("delete_file_policy", "Delete a file access policy."),
        ("list_policy_rules", "List named policy rules visible to the caller."),
        ("create_policy_rule", "Create a named, reusable policy rule."),
        ("delete_policy_rule", "Delete a named policy rule by domain and name."),
    ]
    assert all(context is get_context for *_rest, context in registered)
