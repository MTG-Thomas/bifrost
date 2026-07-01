from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.routers.solutions import (
    DEPLOY_ORPHAN_STALE_SECONDS,
    _is_stale_deploy_job,
    reconcile_orphaned_deploy_jobs,
)


@pytest.mark.asyncio
async def test_reconcile_orphaned_deploy_jobs_fails_non_terminal_jobs(db_session):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    sol = Solution(slug="demo", name="Demo")
    db_session.add(sol)
    await db_session.flush()

    stale_queued = SolutionDeployJob(
        install_id=sol.id,
        status="queued",
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=30),
    )
    stale_running = SolutionDeployJob(
        install_id=sol.id,
        status="running",
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=30),
    )
    fresh_queued = SolutionDeployJob(
        install_id=sol.id,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    succeeded = SolutionDeployJob(
        install_id=sol.id,
        status="succeeded",
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=30),
    )
    db_session.add_all([stale_queued, stale_running, fresh_queued, succeeded])
    await db_session.flush()

    changed = await reconcile_orphaned_deploy_jobs(db_session, now=now)

    assert changed == 2
    assert stale_queued.status == "failed"
    assert stale_running.status == "failed"
    assert fresh_queued.status == "queued"
    assert "API restarted" in (stale_queued.error or "")
    assert "API restarted" in (stale_running.error or "")
    assert succeeded.status == "succeeded"


@pytest.mark.asyncio
async def test_is_stale_deploy_job_uses_heartbeat_stale_threshold():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fresh_running = SolutionDeployJob(
        install_id=UUID("00000000-0000-0000-0000-000000000001"),
        status="running",
        updated_at=now - timedelta(seconds=DEPLOY_ORPHAN_STALE_SECONDS - 1),
    )
    stale_running = SolutionDeployJob(
        install_id=UUID("00000000-0000-0000-0000-000000000001"),
        status="running",
        updated_at=now - timedelta(seconds=DEPLOY_ORPHAN_STALE_SECONDS + 1),
    )

    assert not _is_stale_deploy_job(fresh_running, now)
    assert _is_stale_deploy_job(stale_running, now)
