from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.models.orm import AgentDelegation, AgentTool
from src.services.file_storage.indexers.agent import AgentIndexer


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _db(*execute_results):
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=list(execute_results)),
        add=MagicMock(),
    )
    return db


@pytest.mark.asyncio
async def test_index_agent_rejects_invalid_yaml() -> None:
    db = _db()
    indexer = AgentIndexer(db)

    assert await indexer.index_agent("agents/bad.agent.yaml", b"name: [") is False
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_agent_requires_name_and_system_prompt() -> None:
    indexer = AgentIndexer(_db())

    with pytest.raises(ValueError, match="agent name is required"):
        await indexer.index_agent("agents/missing-name.agent.yaml", b"system_prompt: hi")

    with pytest.raises(ValueError, match="system_prompt is required"):
        await indexer.index_agent("agents/missing-prompt.agent.yaml", b"name: Ada")


@pytest.mark.asyncio
async def test_index_agent_injects_id_normalizes_lists_and_syncs_existing_refs(
    monkeypatch,
) -> None:
    agent_id = UUID("00000000-0000-0000-0000-000000000001")
    workflow_id = UUID("00000000-0000-0000-0000-000000000002")
    child_id = UUID("00000000-0000-0000-0000-000000000003")
    monkeypatch.setattr(
        "src.services.file_storage.indexers.agent.uuid4",
        MagicMock(return_value=agent_id),
    )
    db = _db(
        None,  # agent upsert
        None,  # delete old tools
        _ScalarResult(workflow_id),
        None,  # delete old delegations
        _ScalarResult(child_id),
    )
    indexer = AgentIndexer(db)
    content = f"""
name: Ada
description: Helps with tickets
system_prompt: Be useful
channels: [chat, invalid, email]
knowledge_sources: not-a-list
tools:
  - {workflow_id}
  - not-a-uuid
  - {workflow_id}
delegated_agent_ids:
  - {child_id}
  - bad-child
system_tools: [search_knowledge, execute_workflow]
max_iterations: 8
max_token_budget: 1234
""".encode()

    modified = await indexer.index_agent("agents/ada.agent.yaml", content)

    assert modified is True
    added_tools = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], AgentTool)]
    added_delegations = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], AgentDelegation)
    ]
    assert len(added_tools) == 1
    assert added_tools[0].agent_id == agent_id
    assert added_tools[0].workflow_id == workflow_id
    assert len(added_delegations) == 1
    assert added_delegations[0].parent_agent_id == agent_id
    assert added_delegations[0].child_agent_id == child_id
    assert db.execute.await_count == 5
    upsert = db.execute.await_args_list[0].args[0]
    compiled = upsert.compile()
    assert compiled.params["system_tools"] == ["search_knowledge"]
    assert "system_tools" in str(upsert).split("DO UPDATE SET", maxsplit=1)[1]


@pytest.mark.asyncio
async def test_index_agent_replaces_invalid_id_and_defaults_bad_channels(
    monkeypatch,
) -> None:
    generated_id = UUID("00000000-0000-0000-0000-000000000010")
    monkeypatch.setattr(
        "src.services.file_storage.indexers.agent.uuid4",
        MagicMock(return_value=generated_id),
    )
    db = _db(None, None, None)
    indexer = AgentIndexer(db)

    modified = await indexer.index_agent(
        "agents/invalid-id.agent.yaml",
        b"""
id: not-a-uuid
name: Ada
system_prompt: Be useful
channels: not-a-list
knowledge_sources: [kb-1]
tool_ids: not-a-list
delegated_agent_ids: not-a-list
""",
    )

    assert modified is True
    assert db.execute.await_count == 1
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_delete_agent_for_file_ignores_non_agent_or_invalid_uuid_paths() -> None:
    db = _db()
    indexer = AgentIndexer(db)

    assert await indexer.delete_agent_for_file("agents/readme.md") == 0
    assert await indexer.delete_agent_for_file("agents/not-a-uuid.agent.yaml") == 0
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_agent_for_file_deletes_uuid_from_path() -> None:
    agent_id = uuid4()
    result = SimpleNamespace(rowcount=1)
    db = _db(result)
    indexer = AgentIndexer(db)

    assert await indexer.delete_agent_for_file(f"nested/{agent_id}.agent.yaml") == 1
    db.execute.assert_awaited_once()
