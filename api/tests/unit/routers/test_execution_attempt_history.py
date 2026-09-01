"""Authorized execution-attempt detail projection tests."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.routers.executions import _load_attempt_history


def _user(*, is_superuser: bool) -> UserPrincipal:
    user_id = uuid4()
    return UserPrincipal(
        user_id=user_id,
        email=f"{user_id}@example.test",
        organization_id=uuid4(),
        is_superuser=is_superuser,
    )


def _session_with_attempt(row: object) -> AsyncMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = AsyncMock()
    session.execute.return_value = result
    return session


def _attempt() -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        attempt_number=1,
        status="running",
        phase="execution",
        failure_phase=None,
        failure_code=None,
        worker_id="worker-slot",
        worker_incarnation_id=uuid4(),
        process_id="process-1",
        runtime_mode="deployment-v1",
        runtime_evidence_hash="sha256:runtime",
        dispatch_evidence_hash="sha256:dispatch",
        policy_digest="sha256:policy",
        policy_version="workflow-attempt/v1",
        created_at=now,
        published_at=now,
        claimed_at=now,
        started_at=now,
        heartbeat_at=now,
        completed_at=None,
        duration_ms=None,
        peak_memory_bytes=1024,
        cpu_total_seconds=0.25,
    )


@pytest.mark.asyncio
async def test_owner_attempt_projection_redacts_platform_identity() -> None:
    history = await _load_attempt_history(
        _session_with_attempt(_attempt()),
        uuid4(),
        _user(is_superuser=False),
        tracking_enabled=True,
    )

    attempt = history.attempts[0]
    assert attempt.worker_id is None
    assert attempt.worker_incarnation_id is None
    assert attempt.process_id is None
    assert attempt.runtime_evidence_hash is None
    assert attempt.dispatch_evidence_hash is None
    assert attempt.policy_digest is None
    assert attempt.peak_memory_bytes is None


@pytest.mark.asyncio
async def test_admin_attempt_projection_includes_platform_identity() -> None:
    row = _attempt()
    history = await _load_attempt_history(
        _session_with_attempt(row),
        uuid4(),
        _user(is_superuser=True),
        tracking_enabled=True,
    )

    attempt = history.attempts[0]
    assert attempt.worker_id == row.worker_id
    assert attempt.worker_incarnation_id == row.worker_incarnation_id
    assert attempt.dispatch_evidence_hash == row.dispatch_evidence_hash
    assert attempt.policy_digest == row.policy_digest


@pytest.mark.asyncio
async def test_new_preclaim_execution_has_recorded_empty_history() -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result

    history = await _load_attempt_history(
        session,
        uuid4(),
        _user(is_superuser=False),
        tracking_enabled=True,
    )

    assert history.coverage == "recorded"
    assert history.attempts == []


@pytest.mark.asyncio
async def test_unreconciled_inline_execution_has_legacy_coverage() -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result

    history = await _load_attempt_history(
        session,
        uuid4(),
        _user(is_superuser=False),
        tracking_enabled=False,
    )

    assert history.coverage == "legacy_unavailable"
