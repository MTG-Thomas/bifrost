from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from src.scheduler import health


def test_scheduler_heartbeat_reports_missing_fresh_and_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat_path = tmp_path / "scheduler-heartbeat"
    monkeypatch.setattr(health, "HEARTBEAT_PATH", heartbeat_path)

    assert not health.heartbeat_is_fresh(60)

    health.write_heartbeat()
    assert health.heartbeat_is_fresh(60)

    stale = time.time() - 61
    os.utime(heartbeat_path, (stale, stale))
    assert not health.heartbeat_is_fresh(60)


def test_scheduler_heartbeat_creates_private_runtime_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_directory = tmp_path / "runtime"
    heartbeat_path = runtime_directory / "scheduler-heartbeat"
    monkeypatch.setattr(health, "HEARTBEAT_PATH", heartbeat_path)

    health.write_heartbeat()

    assert stat.S_IMODE(runtime_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(heartbeat_path.stat().st_mode) == 0o600


def test_scheduler_heartbeat_rejects_public_runtime_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_directory = tmp_path / "runtime"
    runtime_directory.mkdir(mode=0o700)
    runtime_directory.chmod(0o777)
    monkeypatch.setattr(health, "HEARTBEAT_PATH", runtime_directory / "scheduler-heartbeat")

    with pytest.raises(PermissionError, match="runtime directory is not private"):
        health.write_heartbeat()


def test_scheduler_heartbeat_rejects_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.touch()
    heartbeat_path = tmp_path / "scheduler-heartbeat"
    heartbeat_path.symlink_to(target)
    monkeypatch.setattr(health, "HEARTBEAT_PATH", heartbeat_path)

    with pytest.raises(OSError):
        health.write_heartbeat()

    assert not health.heartbeat_is_fresh(60)
