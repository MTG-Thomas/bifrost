from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.exc import IntegrityError  # type: ignore[reportMissingImports]

from src.core import metrics
from src.models.enums import ExecutionStatus


class _SessionContext:
    def __init__(self, session: AsyncMock):
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


async def test_update_daily_metrics_uses_provided_session_for_org_and_global(
    monkeypatch,
) -> None:
    calls: list[tuple[object, str | None, str, int, int, float, int, float]] = []

    async def fake_upsert(
        db,
        today,
        org_id,
        status,
        duration_ms,
        peak_memory_bytes,
        cpu_total_seconds,
        time_saved,
        value,
    ) -> None:
        calls.append(
            (
                db,
                str(org_id) if org_id is not None else None,
                status,
                duration_ms,
                peak_memory_bytes,
                cpu_total_seconds,
                time_saved,
                value,
            )
        )

    monkeypatch.setattr(metrics, "_upsert_daily_metrics", fake_upsert)
    db = AsyncMock()
    org_id = uuid4()

    await metrics.update_daily_metrics(
        org_id=f"ORG:{org_id}",
        status=ExecutionStatus.SUCCESS.value,
        duration_ms=120,
        peak_memory_bytes=4096,
        cpu_total_seconds=1.5,
        time_saved=7,
        value=12.5,
        db=db,
    )

    assert calls == [
        (
            db,
            str(org_id),
            ExecutionStatus.SUCCESS.value,
            120,
            4096,
            1.5,
            7,
            12.5,
        ),
        (
            db,
            None,
            ExecutionStatus.SUCCESS.value,
            120,
            4096,
            1.5,
            7,
            12.5,
        ),
    ]
    db.commit.assert_not_called()


async def test_update_daily_metrics_opens_session_and_commits(monkeypatch) -> None:
    session = AsyncMock()
    calls = 0

    async def fake_upsert(*args, **kwargs) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(metrics, "_upsert_daily_metrics", fake_upsert)
    monkeypatch.setattr(metrics, "get_session_factory", lambda: lambda: _SessionContext(session))

    await metrics.update_daily_metrics(
        org_id=None,
        status=ExecutionStatus.FAILED.value,
        duration_ms=None,
    )

    assert calls == 2
    session.commit.assert_awaited_once()


async def test_update_daily_metrics_swallows_metrics_failures(monkeypatch) -> None:
    async def failing_upsert(*args, **kwargs) -> None:
        raise RuntimeError("metrics db unavailable")

    monkeypatch.setattr(metrics, "_upsert_daily_metrics", failing_upsert)

    await metrics.update_daily_metrics(
        org_id=None,
        status=ExecutionStatus.FAILED.value,
        db=AsyncMock(),
    )


async def test_upsert_daily_metrics_recalculates_average_when_row_exists() -> None:
    db = AsyncMock()
    await metrics._upsert_daily_metrics(
        db,
        metrics.date.today(),
        None,
        ExecutionStatus.SUCCESS.value,
        100,
        2048,
        0.25,
        3,
        9.0,
    )

    db.execute.assert_awaited_once()
    upsert_stmt = db.execute.await_args.args[0]
    assert "avg_duration_ms" in str(upsert_stmt)


async def test_upsert_daily_metrics_skips_average_when_no_row() -> None:
    db = AsyncMock()
    await metrics._upsert_daily_metrics(
        db,
        metrics.date.today(),
        uuid4(),
        ExecutionStatus.CANCELLED.value,
        None,
        None,
        None,
        3,
        9.0,
    )

    db.execute.assert_awaited_once()


async def test_update_workflow_roi_daily_skips_when_workflow_missing() -> None:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar=lambda: False)

    await metrics.update_workflow_roi_daily(
        workflow_id=str(uuid4()),
        org_id=None,
        status=ExecutionStatus.SUCCESS.value,
        db=db,
    )

    db.rollback.assert_not_called()


async def test_update_workflow_roi_daily_uses_provided_session_when_workflow_exists(
    monkeypatch,
) -> None:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar=lambda: True)
    calls = []

    async def fake_upsert(
        session,
        today,
        workflow_id,
        org_id,
        is_success,
        time_saved,
        value,
    ) -> None:
        calls.append(
            (
                session,
                str(workflow_id),
                str(org_id) if org_id is not None else None,
                is_success,
                time_saved,
                value,
            )
        )

    monkeypatch.setattr(metrics, "_upsert_workflow_roi", fake_upsert)
    workflow_id = uuid4()
    org_id = uuid4()

    await metrics.update_workflow_roi_daily(
        workflow_id=str(workflow_id),
        org_id=f"ORG:{org_id}",
        status=ExecutionStatus.SUCCESS.value,
        time_saved=11,
        value=42.0,
        db=db,
    )

    assert calls == [
        (
            db,
            str(workflow_id),
            str(org_id),
            True,
            11,
            42.0,
        )
    ]


async def test_update_workflow_roi_daily_opens_session_and_commits(monkeypatch) -> None:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalar=lambda: True)
    upsert = AsyncMock()
    monkeypatch.setattr(metrics, "_upsert_workflow_roi", upsert)
    monkeypatch.setattr(metrics, "get_session_factory", lambda: lambda: _SessionContext(session))

    await metrics.update_workflow_roi_daily(
        workflow_id=str(uuid4()),
        org_id=None,
        status=ExecutionStatus.FAILED.value,
        time_saved=11,
        value=42.0,
    )

    upsert.assert_awaited_once()
    await_args = upsert.await_args
    assert await_args is not None
    assert await_args.args[4] is False
    assert await_args.args[5] == 0
    assert await_args.args[6] == 0.0
    session.commit.assert_awaited_once()


async def test_update_workflow_roi_daily_rolls_back_missing_fk_integrity_error(
    monkeypatch,
) -> None:
    class ForeignKeyViolationError(Exception):
        constraint_name = "workflow_roi_daily_workflow_id_fkey"

    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar=lambda: True)

    async def failing_upsert(*args, **kwargs) -> None:
        raise IntegrityError("insert", {}, ForeignKeyViolationError("missing workflow"))

    monkeypatch.setattr(metrics, "_upsert_workflow_roi", failing_upsert)

    await metrics.update_workflow_roi_daily(
        workflow_id=str(uuid4()),
        org_id=None,
        status=ExecutionStatus.SUCCESS.value,
        db=db,
    )

    db.rollback.assert_awaited_once()


async def test_update_workflow_roi_daily_rolls_back_other_errors(monkeypatch) -> None:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar=lambda: True)

    async def failing_upsert(*args, **kwargs) -> None:
        raise RuntimeError("database offline")

    monkeypatch.setattr(metrics, "_upsert_workflow_roi", failing_upsert)

    await metrics.update_workflow_roi_daily(
        workflow_id=str(uuid4()),
        org_id=None,
        status=ExecutionStatus.SUCCESS.value,
        db=db,
    )

    db.rollback.assert_awaited_once()


async def test_upsert_workflow_roi_executes_org_and_global_conflict_paths() -> None:
    for org_id in (None, uuid4()):
        db = AsyncMock()

        await metrics._upsert_workflow_roi(
            db,
            metrics.date.today(),
            uuid4(),
            org_id,
            True,
            4,
            12.5,
        )

        db.execute.assert_awaited_once()
