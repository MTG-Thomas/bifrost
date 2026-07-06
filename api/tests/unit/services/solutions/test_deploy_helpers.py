"""Coverage for solution deploy helper functions."""

import asyncio
from uuid import UUID

import pytest

from src.services.solutions import deploy


INSTALL_ID = UUID("11111111-1111-1111-1111-111111111111")
MANIFEST_ID = UUID("22222222-2222-2222-2222-222222222222")
MAPPED_ID = UUID("33333333-3333-3333-3333-333333333333")


def test_solution_entity_id_is_stable_per_install_namespace():
    first = deploy.solution_entity_id(INSTALL_ID, MANIFEST_ID)

    assert first == deploy.solution_entity_id(INSTALL_ID, MANIFEST_ID)
    assert first != deploy.solution_entity_id(
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        MANIFEST_ID,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (str(MANIFEST_ID), str(MAPPED_ID)),
        ("workflows/ticket.py::run", "workflows/ticket.py::run"),
        (str(UUID("44444444-4444-4444-4444-444444444444")), str(UUID("44444444-4444-4444-4444-444444444444"))),
        (None, None),
        (42, 42),
    ],
)
def test_remap_ref_only_translates_bundle_uuid_strings(value, expected):
    assert deploy._remap_ref(value, {MANIFEST_ID: MAPPED_ID}) == expected


@pytest.mark.parametrize(
    ("new", "current", "expected"),
    [
        ("1.9.0", "2.0.0", True),
        ("2.0.0", "2.0.0", False),
        ("2.1.0", "2.0.0", False),
        (None, "2.0.0", False),
        ("not-a-version", "2.0.0", False),
        ("1.0.0", "not-a-version", False),
    ],
)
def test_is_downgrade_only_blocks_ordered_pep440_versions(new, current, expected):
    assert deploy._is_downgrade(new, current) is expected


def test_decode_logo_handles_absent_png_and_invalid_content_type():
    assert deploy._decode_logo("app", None, None) == (None, None)

    data, content_type = deploy._decode_logo("app", "aGVsbG8=", "image/png")
    assert data == b"hello"
    assert content_type == "image/png"

    with pytest.raises(deploy.SolutionDeployConflict, match="not allowed"):
        deploy._decode_logo("app", "aGVsbG8=", "text/html")


def test_decode_logo_rejects_oversized_logo(monkeypatch):
    monkeypatch.setattr(deploy, "_LOGO_MAX_SIZE", 2)

    with pytest.raises(deploy.SolutionDeployConflict, match="exceeds"):
        deploy._decode_logo("app", "aGVsbG8=", "image/png")


@pytest.mark.asyncio
async def test_retry_idempotent_retries_then_succeeds(monkeypatch):
    attempts = 0
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def op():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await deploy._retry_idempotent("publish", INSTALL_ID, op)

    assert attempts == 3
    assert sleeps == [deploy._FINALIZE_BACKOFF_S, deploy._FINALIZE_BACKOFF_S * 2]


@pytest.mark.asyncio
async def test_retry_idempotent_raises_after_all_attempts(monkeypatch):
    attempts = 0

    async def fake_sleep(_seconds):
        return None

    async def op():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("still down")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(deploy.SolutionFinalizeIncomplete) as exc_info:
        await deploy._retry_idempotent("publish", INSTALL_ID, op)

    assert str(INSTALL_ID) in str(exc_info.value)
    assert attempts == deploy._FINALIZE_RETRIES


@pytest.mark.asyncio
async def test_deploy_result_default_finalize_is_awaitable():
    result = deploy.DeployResult(workflows_upserted=2, apps_deleted=1)

    assert result.workflows_upserted == 2
    assert result.apps_deleted == 1
    assert await result.finalize_s3() is None
