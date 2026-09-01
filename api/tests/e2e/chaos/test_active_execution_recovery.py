"""Container-kill recovery proof using a Meraki delta-sweep shaped workflow."""

import base64
import json
import uuid
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import poll_until, write_and_register


READY_PATH = Path("/bifrost-results/active-execution-chaos-ready.json")


def _read_workspace_json(client, headers, path):
    response = client.post(
        "/api/files/read",
        headers=headers,
        json={"path": path, "mode": "cloud", "location": "workspace", "binary": True},
    )
    if response.status_code != 200:
        return None
    return json.loads(base64.b64decode(response.json()["content"]).decode())


@pytest.mark.e2e
def test_worker_container_loss_replays_meraki_delta_without_duplicate_artifacts(
    e2e_client, platform_admin
):
    run_id = uuid.uuid4().hex
    root = f".artifacts/chaos/meraki-delta/{run_id}"
    workflow_content = '''
import asyncio
import base64
import json
from bifrost import api, workflow

async def _read(path):
    response = await api.post("/api/files/read", json={
        "path": path, "mode": "cloud", "location": "workspace", "binary": True,
    })
    if response.status_code != 200:
        return None
    return json.loads(base64.b64decode(response.json()["content"]).decode())

async def _write(path, value):
    encoded = base64.b64encode(json.dumps(value, sort_keys=True).encode()).decode()
    response = await api.post("/api/files/write", json={
        "path": path, "content": encoded, "mode": "cloud",
        "location": "workspace", "binary": True,
    })
    response.raise_for_status()

@workflow(name="chaos_meraki_config_delta", execution_mode="async", timeout_seconds=600)
async def chaos_meraki_config_delta(run_id: str, pause_seconds: int = 300):
    root = f".artifacts/chaos/meraki-delta/{run_id}"
    state_path = f"{root}/sweep-state.json"
    state = await _read(state_path) or {"completed_orgs": []}
    completed = list(state["completed_orgs"])

    if "meraki-org-a" not in completed:
        await _write(f"{root}/meraki-org-a/incremental.json", {"org": "meraki-org-a", "delta": 1})
        completed.append("meraki-org-a")
        await _write(state_path, {"completed_orgs": completed})
        await asyncio.sleep(pause_seconds)

    if "meraki-org-b" not in completed:
        await _write(f"{root}/meraki-org-b/incremental.json", {"org": "meraki-org-b", "delta": 1})
        completed.append("meraki-org-b")
        await _write(state_path, {"completed_orgs": completed})

    return {"completed_orgs": completed}
'''
    registered = write_and_register(
        e2e_client,
        platform_admin.headers,
        f"chaos_meraki_delta_{run_id}.py",
        workflow_content,
        "chaos_meraki_config_delta",
    )
    response = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={
            "workflow_id": registered["id"],
            "input_data": {"run_id": run_id, "pause_seconds": 300},
        },
    )
    assert response.status_code in {200, 202}, response.text
    execution_id = response.json()["execution_id"]

    def first_checkpoint_ready():
        state = _read_workspace_json(
            e2e_client, platform_admin.headers, f"{root}/sweep-state.json"
        )
        if state == {"completed_orgs": ["meraki-org-a"]}:
            detail = e2e_client.get(
                f"/api/executions/{execution_id}", headers=platform_admin.headers
            )
            if detail.status_code == 200 and detail.json()["status"] == "Running":
                return state
        return None

    assert poll_until(first_checkpoint_ready, max_wait=30) is not None
    READY_PATH.write_text(
        json.dumps({"execution_id": execution_id, "run_id": run_id}), encoding="utf-8"
    )

    def terminal_success():
        try:
            detail = e2e_client.get(
                f"/api/executions/{execution_id}", headers=platform_admin.headers
            )
        except httpx.HTTPError:
            return None
        if detail.status_code == 200 and detail.json()["status"] == "Success":
            return detail.json()
        return None

    detail = poll_until(terminal_success, max_wait=120)
    assert detail is not None, f"execution {execution_id} did not recover"
    attempts = detail["attempt_history"]["attempts"]
    assert [attempt["attempt_number"] for attempt in attempts] == [1, 2]
    assert [attempt["status"] for attempt in attempts] == [
        "worker_lost",
        "succeeded",
    ]
    assert detail["result"] == {"completed_orgs": ["meraki-org-a", "meraki-org-b"]}
    assert _read_workspace_json(
        e2e_client, platform_admin.headers, f"{root}/meraki-org-a/incremental.json"
    ) == {"org": "meraki-org-a", "delta": 1}
    assert _read_workspace_json(
        e2e_client, platform_admin.headers, f"{root}/meraki-org-b/incremental.json"
    ) == {"org": "meraki-org-b", "delta": 1}

    READY_PATH.unlink(missing_ok=True)
    e2e_client.delete(
        f"/api/files/editor?path=chaos_meraki_delta_{run_id}.py",
        headers=platform_admin.headers,
    )
