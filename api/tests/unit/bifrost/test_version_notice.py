from __future__ import annotations

from pathlib import Path

import pytest

from bifrost import _version_notice


def test_version_notice_is_deduped_by_url_and_server_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_version_notice.tempfile, "gettempdir", lambda: str(tmp_path))

    api_url = "https://dev.bifrost.example.com/"

    assert _version_notice.should_notify(api_url, "2026.7.5") is True

    _version_notice.mark_notified(api_url, "2026.7.5")

    assert _version_notice.should_notify("https://dev.bifrost.example.com", "2026.7.5") is False
    assert _version_notice.should_notify(api_url, "2026.7.6") is True
    assert len(list(tmp_path.glob("bifrost-vnotice-*"))) == 1


def test_version_notice_read_and_write_failures_are_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenMarker:
        def exists(self) -> bool:
            return True

        def read_text(self, *args, **kwargs) -> str:
            raise OSError("cannot read marker")

        def write_text(self, *args, **kwargs) -> None:
            raise OSError("cannot write marker")

    monkeypatch.setattr(_version_notice, "_marker_path", lambda api_url: BrokenMarker())

    assert _version_notice.should_notify("https://dev.bifrost.example.com", "2026.7.5") is True
    _version_notice.mark_notified("https://dev.bifrost.example.com", "2026.7.5")
