"""Wire-level proof for progressive agent discovery on the default MCP URL."""

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from tests.fixtures.auth import create_test_jwt

TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")
MCP_ACCEPT = "application/json, text/event-stream"
MODERN_PROTOCOL_VERSION = "2026-07-28"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"
GATEWAY_TOOLS = {
    "bifrost_get_required_instructions",
    "bifrost_search_capabilities",
    "bifrost_execute_tool",
    "bifrost_get_execution",
    "bifrost_search_memory",
    "bifrost_save_memory",
    "bifrost_remove_memory",
}


def _mcp_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": MCP_ACCEPT,
    }


def _mcp_request(
    token: str,
    method: str,
    params: dict,
    *,
    path: str = "/mcp",
    request_id: int = 1,
    modern: bool = False,
    tasks: bool = False,
    expect_error: bool = False,
) -> dict:
    headers = _mcp_headers(token)
    request_params = dict(params)
    if modern:
        request_params["_meta"] = {
            PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
            CLIENT_INFO_META_KEY: {"name": "gateway-e2e", "version": "1.0"},
            CLIENT_CAPABILITIES_META_KEY: (
                {"extensions": {TASKS_EXTENSION_ID: {}}} if tasks else {}
            ),
        }
        headers.update(
            {
                "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
                "Mcp-Method": method,
            }
        )
        if method == "tools/call":
            headers["Mcp-Name"] = str(request_params["name"])
        elif method.startswith("tasks/"):
            headers["Mcp-Name"] = str(request_params["taskId"])

    response = requests.post(
        f"{TEST_API_URL}{path}",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": request_params,
        },
    )
    payload = response.json()
    if expect_error:
        assert response.status_code in (200, 400), response.text
        assert "error" in payload, payload
        return payload

    assert response.status_code == 200, response.text
    assert "error" not in payload, payload
    return payload


def _call_gateway(
    token: str,
    name: str,
    arguments: dict,
    *,
    modern: bool = False,
) -> dict:
    payload = _mcp_request(
        token,
        "tools/call",
        {"name": name, "arguments": arguments},
        modern=modern,
    )
    result = payload["result"]
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    assert len(result["content"]) == 1, result
    return json.loads(result["content"][0]["text"])


@pytest.mark.e2e
class TestMCPAgentGateway:
    @pytest.fixture(autouse=True, scope="class")
    def gateway_fixture(self, request, platform_admin):
        suffix = uuid.uuid4().hex[:8]
        function_name = f"gateway_echo_{suffix}"
        path = f"workflows/{function_name}.py"
        token = create_test_jwt(
            user_id=str(platform_admin.user_id),
            email=platform_admin.email,
            is_superuser=True,
            mcp_resource=f"{TEST_API_URL}/mcp",
        )
        headers = platform_admin.headers

        content = (
            "import asyncio\n\n"
            "from bifrost import tool\n\n"
            "@tool(description='Echo a message for the MCP gateway proof.')\n"
            f"async def {function_name}(message: str) -> dict:\n"
            "    if message == 'dropped-response':\n"
            "        delay = 4\n"
            "    elif message == 'cancel-me':\n"
            "        delay = 2\n"
            "    else:\n"
            "        delay = 0.4\n"
            "    await asyncio.sleep(delay)\n"
            "    return {'echo': message}\n"
        )
        write_response = requests.put(
            f"{TEST_API_URL}/api/files/editor/content",
            headers=headers,
            json={"path": path, "content": content, "encoding": "utf-8"},
        )
        assert write_response.status_code in (200, 201), write_response.text

        register_response = requests.post(
            f"{TEST_API_URL}/api/workflows/register",
            headers=headers,
            json={"path": path, "function_name": function_name},
        )
        assert register_response.status_code == 201, register_response.text
        workflow_id = register_response.json()["id"]

        agent_name = f"Gateway Proof {suffix}"
        prompt = f"Live gateway instructions {suffix}"
        agent_response = requests.post(
            f"{TEST_API_URL}/api/agents",
            headers=headers,
            json={
                "name": agent_name,
                "description": "Agent used to prove progressive MCP discovery.",
                "system_prompt": prompt,
                "channels": ["chat"],
                "tool_ids": [workflow_id],
                "system_tools": ["get_docs"],
            },
        )
        assert agent_response.status_code == 201, agent_response.text
        agent_id = agent_response.json()["id"]

        request.cls.token = token
        request.cls.headers = headers
        request.cls.agent_id = agent_id
        request.cls.agent_name = agent_name
        request.cls.prompt = prompt
        request.cls.function_name = function_name
        request.cls.workflow_id = workflow_id
        request.cls.workflow_tool_name = (
            f"{function_name}__{uuid.UUID(workflow_id).hex}"
        )

        yield

        requests.delete(
            f"{TEST_API_URL}/api/agents/{agent_id}",
            headers=headers,
        )
        requests.delete(
            f"{TEST_API_URL}/api/workflows/{workflow_id}",
            headers=headers,
        )
        requests.delete(
            f"{TEST_API_URL}/api/files/editor",
            headers=headers,
            params={"path": path},
        )

    def test_default_and_agent_scoped_surfaces_are_distinct(self):
        initialize = _mcp_request(
            self.token,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gateway-e2e", "version": "1.0"},
            },
        )
        assert "bifrost_search_capabilities" in initialize["result"]["instructions"]
        assert "bifrost_get_required_instructions" in initialize["result"]["instructions"]

        default_tools = _mcp_request(
            self.token,
            "tools/list",
            {},
        )["result"]["tools"]
        assert {tool["name"] for tool in default_tools} == GATEWAY_TOOLS

        scoped_tools = _mcp_request(
            self.token,
            "tools/list",
            {},
            path=f"/mcp/{self.agent_id}",
        )["result"]["tools"]
        scoped_names = {tool["name"] for tool in scoped_tools}
        assert self.workflow_tool_name in scoped_names
        assert not (scoped_names & GATEWAY_TOOLS)

        scoped_initialize = _mcp_request(
            self.token,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gateway-e2e", "version": "1.0"},
            },
            path=f"/mcp/{self.agent_id}",
        )
        assert scoped_initialize["result"]["protocolVersion"] == "2024-11-05"

        scoped_call = _mcp_request(
            self.token,
            "tools/call",
            {
                "name": self.workflow_tool_name,
                "arguments": {"message": "legacy"},
            },
            path=f"/mcp/{self.agent_id}",
        )
        assert scoped_call["result"]["structuredContent"] == {"echo": "legacy"}

    def test_modern_discover_list_and_call_on_both_surfaces(self):
        for path in ("/mcp", f"/mcp/{self.agent_id}"):
            discover = _mcp_request(
                self.token,
                "server/discover",
                {},
                path=path,
                modern=True,
            )
            assert MODERN_PROTOCOL_VERSION in discover["result"]["supportedVersions"]

        default_tools = _mcp_request(
            self.token,
            "tools/list",
            {},
            modern=True,
        )["result"]["tools"]
        assert {tool["name"] for tool in default_tools} == GATEWAY_TOOLS

        found = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"query": self.agent_name},
            modern=True,
        )
        assert any(agent["id"] == self.agent_id for agent in found["agents"])

        scoped_tools = _mcp_request(
            self.token,
            "tools/list",
            {},
            path=f"/mcp/{self.agent_id}",
            modern=True,
        )["result"]["tools"]
        scoped_names = {tool["name"] for tool in scoped_tools}
        assert self.workflow_tool_name in scoped_names
        scoped_call = _mcp_request(
            self.token,
            "tools/call",
            {
                "name": self.workflow_tool_name,
                "arguments": {"message": "modern"},
            },
            path=f"/mcp/{self.agent_id}",
            modern=True,
        )
        assert scoped_call["result"]["structuredContent"] == {"echo": "modern"}

    def test_dropped_response_and_concurrent_retries_reuse_one_execution(self):
        loaded = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "query": self.function_name},
        )
        tool_ref = next(
            tool["tool_ref"]
            for tool in loaded["agents"][0]["matching_tools"]
            if tool["source"] == "workflow"
        )

        before = requests.get(
            f"{TEST_API_URL}/api/executions",
            headers=self.headers,
            params={"workflowId": self.workflow_id, "limit": 1000},
        )
        assert before.status_code == 200, before.text
        before_ids = {
            execution["execution_id"]
            for execution in before.json()["executions"]
        }

        dropped_operation = f"dropped-{uuid.uuid4()}"
        dropped_arguments = {
            "agent_id": self.agent_id,
            "tool_ref": tool_ref,
            "arguments": {"message": "dropped-response"},
            "operation_id": dropped_operation,
        }
        with pytest.raises(requests.exceptions.ReadTimeout):
            requests.post(
                f"{TEST_API_URL}/mcp",
                headers=_mcp_headers(self.token),
                json={
                    "jsonrpc": "2.0",
                    "id": 77,
                    "method": "tools/call",
                    "params": {
                        "name": "bifrost_execute_tool",
                        "arguments": dropped_arguments,
                    },
                },
                timeout=2,
            )

        after_timeout = requests.get(
            f"{TEST_API_URL}/api/executions",
            headers=self.headers,
            params={"workflowId": self.workflow_id, "limit": 1000},
        )
        assert after_timeout.status_code == 200, after_timeout.text
        started_ids = {
            execution["execution_id"]
            for execution in after_timeout.json()["executions"]
        } - before_ids
        assert len(started_ids) == 1

        retry = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            dropped_arguments,
        )
        assert retry == {"echo": "dropped-response"}

        after_retry = requests.get(
            f"{TEST_API_URL}/api/executions",
            headers=self.headers,
            params={"workflowId": self.workflow_id, "limit": 1000},
        )
        assert after_retry.status_code == 200, after_retry.text
        final_ids = {
            execution["execution_id"]
            for execution in after_retry.json()["executions"]
        } - before_ids
        assert final_ids == started_ids

        concurrent_operation = f"concurrent-{uuid.uuid4()}"

        def execute_retry() -> dict:
            return _call_gateway(
                self.token,
                "bifrost_execute_tool",
                {
                    "agent_id": self.agent_id,
                    "tool_ref": tool_ref,
                    "arguments": {"message": "concurrent"},
                    "operation_id": concurrent_operation,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: execute_retry(), range(2)))

        assert responses == [{"echo": "concurrent"}, {"echo": "concurrent"}]

    def test_modern_tasks_use_execution_status_and_requester_authorization(
        self,
        non_admin_user,
    ):
        loaded = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "query": self.function_name},
        )
        tool_ref = next(
            tool["tool_ref"]
            for tool in loaded["agents"][0]["matching_tools"]
            if tool["source"] == "workflow"
        )
        created = _mcp_request(
            self.token,
            "tools/call",
            {
                "name": "bifrost_execute_tool",
                "arguments": {
                    "agent_id": self.agent_id,
                    "tool_ref": tool_ref,
                    "arguments": {"message": "task-result"},
                    "operation_id": f"task-{uuid.uuid4()}",
                },
            },
            modern=True,
            tasks=True,
        )["result"]
        assert created["resultType"] == "task"
        assert created["taskId"].startswith("execution:")
        assert created["createdAt"] != "1970-01-01T00:00:00+00:00"

        task_id = created["taskId"]
        deadline = time.monotonic() + 10
        current = created
        while current["status"] == "working" and time.monotonic() < deadline:
            time.sleep(0.1)
            current = _mcp_request(
                self.token,
                "tasks/get",
                {"taskId": task_id},
                modern=True,
                tasks=True,
            )["result"]
        assert current["status"] == "completed", current
        # MCP Tasks draft: a completed tools/call task returns the original
        # CallToolResult shape, not a Bifrost-specific nested result object.
        call_result = current["result"]
        assert set(call_result) == {
            "content",
            "structuredContent",
            "isError",
            "resultType",
        }
        assert call_result["content"][0]["type"] == "text"
        assert call_result["structuredContent"]["result"]["result"] == {
            "echo": "task-result"
        }
        assert call_result["isError"] is False

        other_token = create_test_jwt(
            user_id=str(non_admin_user.user_id),
            email=non_admin_user.email,
            organization_id=str(non_admin_user.organization_id),
            mcp_resource=f"{TEST_API_URL}/mcp",
        )
        denied = _mcp_request(
            other_token,
            "tasks/get",
            {"taskId": task_id},
            request_id=99,
            modern=True,
            tasks=True,
            expect_error=True,
        )
        assert denied["error"]["message"] == "Task not found"

        admin_resolution = requests.post(
            f"{TEST_API_URL}/api/mcp/operation-receipts/{uuid.uuid4()}/resolve",
            headers=non_admin_user.headers,
            json={
                "resolution": "failed_unknown",
                "reason": "Attempted non-admin resolution",
            },
        )
        assert admin_resolution.status_code == 403

        cancellable = _mcp_request(
            self.token,
            "tools/call",
            {
                "name": "bifrost_execute_tool",
                "arguments": {
                    "agent_id": self.agent_id,
                    "tool_ref": tool_ref,
                    "arguments": {"message": "cancel-me"},
                    "operation_id": f"cancel-{uuid.uuid4()}",
                },
            },
            modern=True,
            tasks=True,
        )["result"]
        denied_cancel = _mcp_request(
            other_token,
            "tasks/cancel",
            {"taskId": cancellable["taskId"]},
            request_id=100,
            modern=True,
            tasks=True,
            expect_error=True,
        )
        assert denied_cancel["error"]["message"] == "Task not found or not cancellable"

        cancelled = _mcp_request(
            self.token,
            "tasks/cancel",
            {"taskId": cancellable["taskId"]},
            modern=True,
            tasks=True,
        )["result"]
        assert cancelled["resultType"] == "complete"

        deadline = time.monotonic() + 10
        current = cancellable
        while current["status"] != "cancelled" and time.monotonic() < deadline:
            time.sleep(0.1)
            current = _mcp_request(
                self.token,
                "tasks/get",
                {"taskId": cancellable["taskId"]},
                modern=True,
                tasks=True,
            )["result"]
        assert current["status"] == "cancelled", current

    def test_live_discovery_schema_execution_and_revocation(self):
        found = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"query": self.agent_name},
        )
        assert any(
            agent["id"] == self.agent_id for agent in found["agents"]
        )

        loaded = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id},
        )
        assert loaded["agents"][0]["instructions"] == self.prompt
        workflow_search = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "query": self.function_name},
        )
        workflow_tool = next(
            tool
            for tool in workflow_search["agents"][0]["matching_tools"]
            if tool["source"] == "workflow"
        )
        tool_ref = workflow_tool["tool_ref"]
        system_search = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "query": "get docs"},
        )
        system_tool = next(
            tool
            for tool in system_search["agents"][0]["matching_tools"]
            if tool["source"] == "system" and tool["name"] == "get_docs"
        )

        schema = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "tool_ref": tool_ref},
        )
        exact_tool = schema["agents"][0]["matching_tools"][0]
        assert exact_tool["input_schema"]["required"] == ["message"]
        assert exact_tool["schema_included"] is True

        invalid = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": tool_ref,
                "arguments": {"message": 42},
                "operation_id": f"invalid-{self.agent_id}",
            },
        )
        assert invalid["code"] == "INVALID_ARGUMENTS"
        assert invalid["retryable"] is True
        assert invalid["agent_id"] == self.agent_id
        assert invalid["issues"][0]["path"] == "/message"

        executed = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": system_tool["tool_ref"],
                "arguments": {},
                "operation_id": f"docs-{self.agent_id}",
            },
        )
        assert "schema" in executed

        updated_prompt = f"{self.prompt} updated"
        update_response = requests.put(
            f"{TEST_API_URL}/api/agents/{self.agent_id}",
            headers=self.headers,
            json={"system_prompt": updated_prompt},
        )
        assert update_response.status_code == 200, update_response.text
        refreshed = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id},
        )
        assert refreshed["agents"][0]["instructions"] == updated_prompt

        revoke_response = requests.put(
            f"{TEST_API_URL}/api/agents/{self.agent_id}",
            headers=self.headers,
            json={"tool_ids": []},
        )
        assert revoke_response.status_code == 200, revoke_response.text
        revoked = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": tool_ref,
                "arguments": {"message": "must not run"},
                "operation_id": f"revoked-{self.agent_id}",
            },
        )
        assert revoked["code"] == "TOOL_NOT_FOUND_OR_FORBIDDEN"
