"""Cross-replica coverage for the dynamic MCP workflow-tool catalog."""

import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
import requests
from sqlalchemy import update

from src.models.orm.mcp_catalog_revision import (
    MCPCatalogRevision,
    WORKFLOW_CATALOG_NAME,
)
from src.models.orm.workflows import Workflow
from tests.fixtures.auth import create_test_jwt

TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")
TEST_API_REPLICA_URL = os.getenv(
    "TEST_API_REPLICA_URL",
    "http://api-replica:8000",
)
LATEST_MCP_PROTOCOL = "2026-07-28"
MCP_ACCEPT_HEADER = "application/json, text/event-stream"
MCP_RESOURCE = f"{TEST_API_URL}/mcp"
MCP_CANONICAL_HOST = "api:8000"


def _admin_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _mcp_headers(token: str) -> dict[str, str]:
    return {
        **_admin_headers(token),
        "Accept": MCP_ACCEPT_HEADER,
        "Host": MCP_CANONICAL_HOST,
        "MCP-Protocol-Version": LATEST_MCP_PROTOCOL,
    }


def _mcp_post(
    base_url: str,
    agent_id: str,
    headers: dict[str, str],
    method: str,
    params: dict[str, Any],
    request_id: int,
) -> dict[str, Any]:
    request_headers = {**headers, "MCP-Method": method}
    if method == "tools/call":
        request_headers["MCP-Name"] = str(params["name"])
    stamped_params = {
        **params,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": LATEST_MCP_PROTOCOL,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {
                "name": "replica-test",
                "version": "1.0",
            },
        },
    }
    response = requests.post(
        f"{base_url}/mcp/{agent_id}",
        headers=request_headers,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": stamped_params,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    assert "result" in payload, payload
    return payload


def _discover(
    base_url: str,
    agent_id: str,
    headers: dict[str, str],
) -> None:
    payload = _mcp_post(
        base_url,
        agent_id,
        headers,
        "server/discover",
        {},
        1,
    )
    assert LATEST_MCP_PROTOCOL in payload["result"]["supportedVersions"]
    _assert_private_zero_ttl(payload)


def _list_tools(
    base_url: str,
    agent_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    return _mcp_post(base_url, agent_id, headers, "tools/list", {}, 2)


def _wait_for_catalog(
    operation: Callable[[], dict[str, Any]],
    predicate: Callable[[set[str]], bool],
) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_payload = operation()
        names = {
            tool["name"]
            for tool in last_payload["result"]["tools"]
        }
        if predicate(names):
            return last_payload
        time.sleep(0.1)
    raise AssertionError(
        f"MCP catalog did not converge across replicas: {last_payload}"
    )


def _assert_private_zero_ttl(payload: dict[str, Any]) -> None:
    result = payload["result"]
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workflow_catalog_is_replica_safe_and_fail_closed_for_caching(
    platform_admin,
    db_session,
):
    """Workflow mutations converge and list-to-call routing crosses replicas."""
    suffix = uuid.uuid4().hex[:8]
    path = f"workflows/test_mcp_replica_{suffix}.py"
    function_name = f"test_mcp_replica_{suffix}"
    renamed_tool_name = f"renamed_replica_tool_{suffix}"

    token = create_test_jwt(
        user_id=str(platform_admin.user_id),
        email=platform_admin.email,
        name=platform_admin.name,
        is_superuser=True,
        organization_id=(
            str(platform_admin.organization_id)
            if platform_admin.organization_id is not None
            else None
        ),
        mcp_resource=MCP_RESOURCE,
    )
    admin_headers = platform_admin.headers
    mcp_headers = _mcp_headers(token)

    workflow_id: str | None = None
    agent_id: str | None = None
    try:
        source = f'''
from bifrost import tool

@tool
async def {function_name}(message: str) -> str:
    """Echo a marker for cross-replica MCP routing coverage."""
    return f"replica-ok:{{message}}"
'''
        write_response = requests.put(
            f"{TEST_API_URL}/api/files/editor/content",
            headers=admin_headers,
            json={"path": path, "content": source, "encoding": "utf-8"},
        )
        assert write_response.status_code in (200, 201), write_response.text

        register_response = requests.post(
            f"{TEST_API_URL}/api/workflows/register",
            headers=admin_headers,
            json={"path": path, "function_name": function_name},
        )
        assert register_response.status_code == 201, register_response.text
        workflow_id = register_response.json()["id"]
        exposed_tool_name = f"{function_name}__{uuid.UUID(workflow_id).hex}"
        renamed_exposed_tool_name = (
            f"{renamed_tool_name}__{uuid.UUID(workflow_id).hex}"
        )

        agent_response = requests.post(
            f"{TEST_API_URL}/api/agents",
            headers=admin_headers,
            json={
                "name": f"MCP Replica Test Agent {suffix}",
                "system_prompt": "Test cross-replica MCP catalog behavior.",
                "channels": ["chat"],
                "tool_ids": [workflow_id],
            },
        )
        assert agent_response.status_code == 201, agent_response.text
        agent_id = agent_response.json()["id"]

        _discover(TEST_API_URL, agent_id, mcp_headers)
        _discover(TEST_API_REPLICA_URL, agent_id, mcp_headers)

        replica_list = _wait_for_catalog(
            lambda: _list_tools(
                TEST_API_REPLICA_URL,
                agent_id,
                mcp_headers,
            ),
            lambda names: exposed_tool_name in names,
        )
        _assert_private_zero_ttl(replica_list)

        # Discovery and execution deliberately hit different processes. The
        # tool name returned by A must be callable on B without session affinity.
        primary_list = _list_tools(TEST_API_URL, agent_id, mcp_headers)
        assert exposed_tool_name in {
            tool["name"] for tool in primary_list["result"]["tools"]
        }
        assert primary_list["result"]["tools"] == replica_list["result"]["tools"]
        _assert_private_zero_ttl(primary_list)

        call_payload = _mcp_post(
            TEST_API_REPLICA_URL,
            agent_id,
            mcp_headers,
            "tools/call",
            {"name": exposed_tool_name, "arguments": {"message": "routed"}},
            3,
        )
        call_result = call_payload["result"]
        assert not call_result.get("isError", False), call_payload
        content_text = " ".join(
            block.get("text", "")
            for block in call_result.get("content", [])
            if isinstance(block, dict)
        )
        assert "replica-ok:routed" in content_text

        # Simulate the mutating API process exiting after its DB commit but
        # before Redis wake-up publication. The workflow change and durable
        # revision commit together; no second mutation or pub/sub event occurs.
        await db_session.execute(
            update(Workflow)
            .where(Workflow.id == uuid.UUID(workflow_id))
            .values(name=renamed_tool_name)
        )
        await db_session.execute(
            update(MCPCatalogRevision)
            .where(MCPCatalogRevision.catalog == WORKFLOW_CATALOG_NAME)
            .values(revision=MCPCatalogRevision.revision + 1)
        )
        await db_session.commit()

        renamed_list = _wait_for_catalog(
            lambda: _list_tools(
                TEST_API_REPLICA_URL,
                agent_id,
                mcp_headers,
            ),
            lambda names: (
                renamed_exposed_tool_name in names
                and exposed_tool_name not in names
            ),
        )
        _assert_private_zero_ttl(renamed_list)

        delete_response = requests.delete(
            f"{TEST_API_URL}/api/workflows/{workflow_id}",
            headers=admin_headers,
            json={"force_deactivation": True},
        )
        assert delete_response.status_code == 200, delete_response.text
        workflow_id = None

        removed_list = _wait_for_catalog(
            lambda: _list_tools(
                TEST_API_REPLICA_URL,
                agent_id,
                mcp_headers,
            ),
            lambda names: renamed_exposed_tool_name not in names,
        )
        _assert_private_zero_ttl(removed_list)
    finally:
        if agent_id is not None:
            requests.delete(
                f"{TEST_API_URL}/api/agents/{agent_id}",
                headers=admin_headers,
            )
        if workflow_id is not None:
            requests.delete(
                f"{TEST_API_URL}/api/workflows/{workflow_id}",
                headers=admin_headers,
            )
        requests.delete(
            f"{TEST_API_URL}/api/files/editor",
            headers=admin_headers,
            params={"path": path},
        )
