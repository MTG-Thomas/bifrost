from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from src.services import operation_receipts as receipt_service
from src.services.operation_receipts import (
    OperationReceiptDisposition,
    OperationReceiptOwnershipError,
    canonical_request_fingerprint,
    claim_operation_receipt,
    complete_operation_receipt_error,
    complete_operation_receipt_success,
)


@pytest.fixture(autouse=True)
def use_null_pool_sessions(async_session_factory, monkeypatch: pytest.MonkeyPatch):
    """Keep service-created sessions safe across function-scoped event loops."""

    @asynccontextmanager
    async def test_db_context():
        async with async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(receipt_service, "get_db_context", test_db_context)


def _claim_kwargs() -> dict:
    return {
        "namespace": "test.operation.v1",
        "scope_key": str(uuid4()),
        "operation_id": "caller-operation",
        "scope": {"caller": str(uuid4())},
        "request_fingerprint": canonical_request_fingerprint(
            {"arguments": {"value": 1}, "task_requested": False}
        ),
    }


def test_canonical_request_fingerprint_ignores_mapping_order() -> None:
    first = canonical_request_fingerprint(
        {"arguments": {"alpha": 1, "beta": [2, 3]}, "task_requested": False}
    )
    second = canonical_request_fingerprint(
        {"task_requested": False, "arguments": {"beta": [2, 3], "alpha": 1}}
    )
    task_mode = canonical_request_fingerprint(
        {"arguments": {"alpha": 1, "beta": [2, 3]}, "task_requested": True}
    )

    assert first == second
    assert first != task_mode


@pytest.mark.asyncio
async def test_concurrent_claims_have_exactly_one_owner() -> None:
    kwargs = _claim_kwargs()

    claims = await asyncio.gather(
        *(claim_operation_receipt(**kwargs) for _ in range(5))
    )

    assert sum(
        claim.disposition == OperationReceiptDisposition.OWNER for claim in claims
    ) == 1
    assert {
        claim.disposition
        for claim in claims
        if claim.disposition != OperationReceiptDisposition.OWNER
    } == {OperationReceiptDisposition.STARTED}
    assert len({claim.receipt_id for claim in claims}) == 1


@pytest.mark.asyncio
async def test_success_is_permanently_replayed_and_mismatch_is_rejected() -> None:
    kwargs = _claim_kwargs()
    owner = await claim_operation_receipt(**kwargs)
    assert owner.owner_token is not None
    response = {"result": {"created": True}, "duration_ms": 12}

    await complete_operation_receipt_success(
        owner.receipt_id,
        owner.owner_token,
        response,
    )

    replay = await claim_operation_receipt(**kwargs)
    mismatch = await claim_operation_receipt(
        **{
            **kwargs,
            "request_fingerprint": canonical_request_fingerprint(
                {"arguments": {"value": 2}, "task_requested": False}
            ),
        }
    )
    assert replay.disposition == OperationReceiptDisposition.SUCCEEDED
    assert replay.response == response
    assert mismatch.disposition == OperationReceiptDisposition.MISMATCH


@pytest.mark.asyncio
async def test_error_is_replayed_and_started_receipt_is_never_reclaimed() -> None:
    error_kwargs = _claim_kwargs()
    error_owner = await claim_operation_receipt(**error_kwargs)
    assert error_owner.owner_token is not None
    error = {
        "code": "INVALID_ARGUMENTS",
        "message": "Bad input",
        "retryable": True,
        "details": {"issues": [{"path": "/value"}]},
    }
    await complete_operation_receipt_error(
        error_owner.receipt_id,
        error_owner.owner_token,
        error,
    )

    failed = await claim_operation_receipt(**error_kwargs)
    assert failed.disposition == OperationReceiptDisposition.FAILED
    assert failed.error == error

    started_kwargs = _claim_kwargs()
    started_owner = await claim_operation_receipt(**started_kwargs)
    retry = await claim_operation_receipt(**started_kwargs)
    assert started_owner.disposition == OperationReceiptDisposition.OWNER
    assert retry.disposition == OperationReceiptDisposition.STARTED
    assert retry.owner_token is None
    assert retry.receipt_id == started_owner.receipt_id


@pytest.mark.asyncio
async def test_terminal_write_is_fenced_to_original_owner() -> None:
    owner = await claim_operation_receipt(**_claim_kwargs())

    with pytest.raises(OperationReceiptOwnershipError):
        await complete_operation_receipt_success(
            owner.receipt_id,
            uuid4(),
            {"result": "wrong owner"},
        )

    assert owner.owner_token is not None
    await complete_operation_receipt_success(
        owner.receipt_id,
        owner.owner_token,
        {"result": "original owner"},
    )
