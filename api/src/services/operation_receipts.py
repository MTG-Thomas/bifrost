"""Durable at-most-once receipts for synchronous request operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import pydantic_core
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from src.core.database import get_db_context
from src.models.orm.operation_receipts import OperationReceipt


class OperationReceiptDisposition(str, Enum):
    """How a caller should handle a receipt claim."""

    OWNER = "owner"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STARTED = "started"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class OperationReceiptClaim:
    """Detached claim state safe to use after the DB session closes."""

    receipt_id: UUID
    disposition: OperationReceiptDisposition
    owner_token: UUID | None = None
    response: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class OperationReceiptOwnershipError(RuntimeError):
    """Raised when a stale or incorrect owner attempts a terminal write."""


def canonical_request_fingerprint(value: Any) -> str:
    """Return a stable SHA-256 fingerprint for a JSON-compatible request."""
    normalized = pydantic_core.to_jsonable_python(value, fallback=str)
    serialized = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _claim_from_receipt(
    receipt: OperationReceipt,
    *,
    request_fingerprint: str,
) -> OperationReceiptClaim:
    if receipt.request_fingerprint != request_fingerprint:
        disposition = OperationReceiptDisposition.MISMATCH
    else:
        disposition = OperationReceiptDisposition(receipt.status)
    return OperationReceiptClaim(
        receipt_id=receipt.id,
        disposition=disposition,
        response=receipt.response,
        error=receipt.error,
    )


async def claim_operation_receipt(
    *,
    namespace: str,
    scope_key: str,
    operation_id: str,
    scope: dict[str, Any],
    request_fingerprint: str,
) -> OperationReceiptClaim:
    """Create one permanent operation owner or return its existing state.

    The new row is committed before this function returns ownership. An
    abandoned ``started`` row is deliberately never reclaimed: its effect may
    have happened immediately before a process or network failure.
    """
    receipt_id = uuid4()
    owner_token = uuid4()
    async with get_db_context() as db:
        inserted_id = (
            await db.execute(
                insert(OperationReceipt)
                .values(
                    id=receipt_id,
                    namespace=namespace,
                    scope_key=scope_key,
                    operation_id=operation_id,
                    scope=pydantic_core.to_jsonable_python(scope, fallback=str),
                    request_fingerprint=request_fingerprint,
                    status="started",
                    owner_token=owner_token,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        OperationReceipt.namespace,
                        OperationReceipt.scope_key,
                    ]
                )
                .returning(OperationReceipt.id)
            )
        ).scalar_one_or_none()
        await db.commit()
        if inserted_id is not None:
            return OperationReceiptClaim(
                receipt_id=inserted_id,
                disposition=OperationReceiptDisposition.OWNER,
                owner_token=owner_token,
            )

        existing = (
            await db.execute(
                select(OperationReceipt).where(
                    OperationReceipt.namespace == namespace,
                    OperationReceipt.scope_key == scope_key,
                )
            )
        ).scalar_one()
        return _claim_from_receipt(
            existing,
            request_fingerprint=request_fingerprint,
        )


async def read_operation_receipt(
    receipt_id: UUID,
    *,
    request_fingerprint: str,
) -> OperationReceiptClaim:
    """Read current receipt state without ever acquiring ownership."""
    async with get_db_context() as db:
        receipt = await db.get(OperationReceipt, receipt_id)
        if receipt is None:
            raise RuntimeError(f"Operation receipt {receipt_id} disappeared")
        return _claim_from_receipt(
            receipt,
            request_fingerprint=request_fingerprint,
        )


async def wait_for_operation_receipt(
    receipt_id: UUID,
    *,
    request_fingerprint: str,
    timeout_seconds: float = 5.0,
) -> OperationReceiptClaim:
    """Briefly join a concurrent owner, then return its latest durable state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    claim = await read_operation_receipt(
        receipt_id,
        request_fingerprint=request_fingerprint,
    )
    while (
        claim.disposition == OperationReceiptDisposition.STARTED
        and loop.time() < deadline
    ):
        await asyncio.sleep(0.05)
        claim = await read_operation_receipt(
            receipt_id,
            request_fingerprint=request_fingerprint,
        )
    return claim


async def complete_operation_receipt_success(
    receipt_id: UUID,
    owner_token: UUID,
    response: dict[str, Any],
) -> None:
    """Persist a successful response, fenced to the one original owner."""
    await _complete_operation_receipt(
        receipt_id,
        owner_token,
        status="succeeded",
        response=pydantic_core.to_jsonable_python(response, fallback=str),
        error=None,
    )


async def complete_operation_receipt_error(
    receipt_id: UUID,
    owner_token: UUID,
    error: dict[str, Any],
) -> None:
    """Persist a structured terminal error, fenced to the original owner."""
    await _complete_operation_receipt(
        receipt_id,
        owner_token,
        status="failed",
        response=None,
        error=pydantic_core.to_jsonable_python(error, fallback=str),
    )


async def _complete_operation_receipt(
    receipt_id: UUID,
    owner_token: UUID,
    *,
    status: str,
    response: dict[str, Any] | None,
    error: dict[str, Any] | None,
) -> None:
    values: dict[str, Any] = {
        "status": status,
        "completed_at": datetime.now(timezone.utc),
    }
    if response is not None:
        values["response"] = response
    if error is not None:
        values["error"] = error
    async with get_db_context() as db:
        completed_id = (
            await db.execute(
                update(OperationReceipt)
                .where(
                    OperationReceipt.id == receipt_id,
                    OperationReceipt.status == "started",
                    OperationReceipt.owner_token == owner_token,
                )
                .values(**values)
                .returning(OperationReceipt.id)
            )
        ).scalar_one_or_none()
        if completed_id is None:
            raise OperationReceiptOwnershipError(
                f"Operation receipt {receipt_id} is not owned by this caller"
            )
        await db.commit()


__all__ = [
    "OperationReceiptClaim",
    "OperationReceiptDisposition",
    "OperationReceiptOwnershipError",
    "canonical_request_fingerprint",
    "claim_operation_receipt",
    "complete_operation_receipt_error",
    "complete_operation_receipt_success",
    "read_operation_receipt",
    "wait_for_operation_receipt",
]
