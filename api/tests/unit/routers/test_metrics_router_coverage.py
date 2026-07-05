from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models import RecentFailure
from src.routers import metrics


class Result:
    def __init__(self, *, scalar=None, one=None, all_rows=None, scalar_rows=None):
        self._scalar = scalar
        self._one = one
        self._all_rows = list(all_rows or [])
        self._scalar_rows = list(scalar_rows or [])

    def scalar_one_or_none(self):
        return self._scalar

    def scalar(self):
        return self._scalar

    def one(self):
        return self._one

    def all(self):
        return self._all_rows

    def scalars(self):
        rows = self._scalar_rows

        class Scalars:
            def all(self):
                return rows

        return Scalars()


class FakeDb:
    def __init__(self, *results: Result):
        self.results = list(results)
        self.execute = AsyncMock(side_effect=self.results)


def _ctx(db: FakeDb):
    return SimpleNamespace(db=db)


def _snapshot():
    return SimpleNamespace(
        workflow_count=7,
        form_count=3,
        data_provider_count=2,
        organization_count=4,
        user_count=10,
        total_executions=100,
        total_success=80,
        total_failed=12,
        executions_24h=9,
        success_24h=6,
        failed_24h=3,
        running_count=5,
        pending_count=3,
        avg_duration_ms_24h=1500,
        total_memory_bytes_24h=4096,
        total_cpu_seconds_24h=2.5,
        success_rate_all_time=80.0,
        success_rate_24h=66.67,
        time_saved_24h=12,
        value_24h=42.5,
        refreshed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_get_metrics_uses_snapshot_and_recent_failures() -> None:
    snapshot = _snapshot()
    roi_settings = SimpleNamespace(time_saved_unit="minutes", value_unit="USD")
    db = FakeDb(Result(scalar=snapshot))
    recent_failure = RecentFailure(
        execution_id=str(uuid4()),
        workflow_name="wf",
        error_message="boom",
        started_at="2026-01-01T00:00:00+00:00",
    )

    with (
        patch.object(metrics, "_get_recent_failures", AsyncMock(return_value=[recent_failure])),
        patch.object(
            metrics,
            "ROISettingsService",
            return_value=SimpleNamespace(get_settings=AsyncMock(return_value=roi_settings)),
        ),
    ):
        response = await metrics.get_metrics(_ctx(db), SimpleNamespace())

    assert response.workflow_count == 7
    assert response.execution_stats.total_executions == 100
    assert response.execution_stats.avg_duration_seconds == 1.5
    assert response.roi_24h.total_value == 42.5
    assert response.recent_failures == [recent_failure]


@pytest.mark.asyncio
async def test_get_metrics_falls_back_when_snapshot_missing() -> None:
    fallback = SimpleNamespace(workflow_count=1)

    with patch.object(metrics, "_compute_metrics_directly", AsyncMock(return_value=fallback)):
        response = await metrics.get_metrics(_ctx(FakeDb(Result(scalar=None))), SimpleNamespace())

    assert response is fallback


@pytest.mark.asyncio
async def test_get_metrics_snapshot_returns_full_snapshot_and_404_when_missing() -> None:
    response = await metrics.get_metrics_snapshot(
        _ctx(FakeDb(Result(scalar=_snapshot()))),
        SimpleNamespace(),
    )

    assert response.organization_count == 4
    assert response.refreshed_at == "2026-01-02T00:00:00+00:00"

    with pytest.raises(HTTPException) as exc:
        await metrics.get_metrics_snapshot(_ctx(FakeDb(Result(scalar=None))), SimpleNamespace())

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_daily_metrics_formats_org_rows() -> None:
    org_id = uuid4()
    row = SimpleNamespace(
        date=date(2026, 1, 3),
        organization_id=org_id,
        execution_count=5,
        success_count=4,
        failed_count=1,
        timeout_count=0,
        cancelled_count=0,
        avg_duration_ms=22,
        max_duration_ms=40,
        peak_memory_bytes=1024,
        total_memory_bytes=2048,
        peak_cpu_seconds=1.5,
        total_cpu_seconds=3.0,
    )

    response = await metrics.get_daily_metrics(
        _ctx(FakeDb(Result(scalar_rows=[row]))),
        SimpleNamespace(),
        days=7,
        organization_id=f"ORG:{org_id}",
    )

    assert response.total_days == 1
    assert response.days[0].organization_id == f"ORG:{org_id}"
    assert response.days[0].execution_count == 5


@pytest.mark.asyncio
async def test_get_organization_metrics_computes_success_rate_and_defaults() -> None:
    org_id = uuid4()
    rows = [
        SimpleNamespace(
            organization_id=org_id,
            org_name=None,
            total_executions=4,
            success_count=3,
            failed_count=None,
            total_memory=None,
            total_cpu=None,
            total_duration=100,
        ),
        SimpleNamespace(
            organization_id=uuid4(),
            org_name="Empty",
            total_executions=0,
            success_count=0,
            failed_count=0,
            total_memory=0,
            total_cpu=0,
            total_duration=0,
        ),
    ]

    response = await metrics.get_organization_metrics(
        _ctx(FakeDb(Result(all_rows=rows))),
        SimpleNamespace(),
        days=30,
        limit=20,
    )

    assert response.total_organizations == 2
    assert response.organizations[0].organization_name == "Unknown"
    assert response.organizations[0].success_rate == 75.0
    assert response.organizations[0].avg_duration_ms == 25
    assert response.organizations[1].success_rate == 0.0


@pytest.mark.asyncio
async def test_get_resource_metrics_avoids_division_by_zero() -> None:
    row = SimpleNamespace(
        date=date(2026, 1, 3),
        peak_memory_bytes=4096,
        total_memory_bytes=8192,
        peak_cpu_seconds=2.0,
        total_cpu_seconds=4.0,
        execution_count=0,
    )

    response = await metrics.get_resource_metrics(
        _ctx(FakeDb(Result(scalar_rows=[row]))),
        SimpleNamespace(),
        days=30,
    )

    assert response.days[0].avg_memory_bytes == 8192
    assert response.days[0].avg_cpu_seconds == 4.0


@pytest.mark.asyncio
async def test_get_workflow_metrics_formats_rows_and_default_sort() -> None:
    rows = [
        SimpleNamespace(
            workflow_name="sync",
            total_executions=10,
            success_count=8,
            failed_count=None,
            avg_memory=100.8,
            avg_duration=20.9,
            avg_cpu=1.2345,
            peak_memory=200,
            max_duration=50,
        )
    ]

    response = await metrics.get_workflow_metrics(
        _ctx(FakeDb(Result(all_rows=rows))),
        SimpleNamespace(),
        days=30,
        sort_by="unknown",
        limit=20,
    )

    assert response.sort_by == "unknown"
    assert response.workflows[0].success_rate == 80.0
    assert response.workflows[0].avg_memory_bytes == 100
    assert response.workflows[0].avg_cpu_seconds == 1.234


@pytest.mark.asyncio
async def test_get_recent_failures_formats_rows() -> None:
    execution_id = uuid4()
    started_at = datetime(2026, 1, 4, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(
            id=execution_id,
            workflow_name=None,
            error_message="failed",
            started_at=started_at,
        )
    ]

    failures = await metrics._get_recent_failures(_ctx(FakeDb(Result(all_rows=rows))))

    assert failures[0].execution_id == str(execution_id)
    assert failures[0].workflow_name == "Unknown"
    assert failures[0].started_at == started_at.isoformat()
