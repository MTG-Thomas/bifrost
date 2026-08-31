"""The /api/version endpoint must report the server's contract_version.

The CLI's contract gate reads ``contract_version`` from this response to decide
compatibility, so the endpoint must surface it and it must equal the server-side
source of truth.
"""

from __future__ import annotations

import concurrent.futures
import time

import pytest

from shared.contract_version import CONTRACT_VERSION


@pytest.mark.asyncio
async def test_version_endpoint_reports_contract_version() -> None:
    from src.routers.version import get_version_info

    result = await get_version_info()

    assert result.contract_version == CONTRACT_VERSION
    # The build version string is still present (unchanged behavior).
    assert isinstance(result.version, str)


def test_sdk_fingerprint_cold_build_is_single_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.routers import version

    calls = 0

    def build(_version: str) -> str:
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return "fingerprint"

    monkeypatch.setattr(version, "_sdk_fingerprint_value", version._SDK_FINGERPRINT_UNSET)
    monkeypatch.setattr(version, "sdk_fingerprint", build)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: version.get_sdk_fingerprint(), range(8)))

    assert results == ["fingerprint"] * 8
    assert calls == 1


def test_sdk_fingerprint_failure_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.routers import version

    calls = 0

    def fail(_version: str) -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("broken toolchain")

    monkeypatch.setattr(version, "_sdk_fingerprint_value", version._SDK_FINGERPRINT_UNSET)
    monkeypatch.setattr(version, "sdk_fingerprint", fail)

    assert version.get_sdk_fingerprint() == "unavailable"
    assert version.get_sdk_fingerprint() == "unavailable"
    assert calls == 1
