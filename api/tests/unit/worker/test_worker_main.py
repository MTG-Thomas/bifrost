from __future__ import annotations

from src.worker import main as worker_main


def test_run_imports_worker_app_main_and_runs_it(monkeypatch):
    async def fake_main() -> None:
        return None

    captured = []

    def fake_run(coro):
        captured.append(coro)
        coro.close()

    monkeypatch.setattr("src.worker.app.main", fake_main)
    monkeypatch.setattr(worker_main.asyncio, "run", fake_run)

    worker_main.run()

    assert len(captured) == 1
    assert captured[0].cr_code is fake_main.__code__
