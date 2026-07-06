import pytest

from src.jobs.schedulers import worker_metrics_cleanup


class _FakeResult:
    rowcount = 7


class _FakeSession:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.executed = None
        self.commits = 0

    async def execute(self, stmt):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.executed = stmt
        return _FakeResult()

    async def commit(self):
        self.commits += 1


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


def _factory_for(session):
    return lambda: _SessionContext(session)


@pytest.mark.asyncio
async def test_cleanup_old_worker_metrics_deletes_rows_older_than_retention(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(
        worker_metrics_cleanup,
        "get_session_factory",
        lambda: _factory_for(session),
    )

    result = await worker_metrics_cleanup.cleanup_old_worker_metrics()

    assert result == {"rows_deleted": 7}
    assert session.commits == 1
    compiled = str(
        session.executed.compile(compile_kwargs={"literal_binds": True})
    )
    assert "DELETE FROM worker_metrics" in compiled
    assert "worker_metrics.timestamp <" in compiled


@pytest.mark.asyncio
async def test_cleanup_old_worker_metrics_reports_error_without_commit(monkeypatch):
    session = _FakeSession(fail=True)
    monkeypatch.setattr(
        worker_metrics_cleanup,
        "get_session_factory",
        lambda: _factory_for(session),
    )

    result = await worker_metrics_cleanup.cleanup_old_worker_metrics()

    assert result == {"rows_deleted": 0, "error": "database unavailable"}
    assert session.commits == 0
