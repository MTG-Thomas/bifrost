"""Durable at-most-once receipts with bounded, expiring replay payloads.

The receipt row is a permanent irreversible tombstone: expiry clears replay
payloads and durable handles, never the hashed uniqueness key. Raw caller
operation IDs and unhashed caller/tenant scope are never persisted. Terminal
payloads are retained for seven days, redacted by key, bounded structurally,
and capped at 64 KiB before they are written.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import pydantic_core
from sqlalchemy import null, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.orm.operation_receipts import OperationReceipt


class OperationReceiptDisposition(str, Enum):
    """How a caller should handle a receipt claim."""

    OWNER = "owner"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STARTED = "started"
    MISMATCH = "mismatch"
    EXPIRED = "expired"


@dataclass(frozen=True)
class OperationReceiptClaim:
    """Detached claim state safe to use after the DB session closes."""

    receipt_id: UUID
    disposition: OperationReceiptDisposition
    owner_token: UUID | None = None
    response: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    durable_handle: dict[str, str] | None = None


RECEIPT_PAYLOAD_RETENTION = timedelta(days=7)
MAX_RECEIPT_PAYLOAD_BYTES = 64 * 1024
MAX_RECEIPT_DEPTH = 8
MAX_RECEIPT_MAPPING_ITEMS = 100
MAX_RECEIPT_SEQUENCE_ITEMS = 100
MAX_RECEIPT_STRING_CHARS = 4096
REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "operation_id",
)


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


def canonical_operation_scope_key(value: Any) -> str:
    """Hash a caller operation scope without retaining its raw identifiers."""
    return canonical_request_fingerprint(value)


def _sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_and_bound(value: Any, *, depth: int = 0) -> Any:
    if depth >= MAX_RECEIPT_DEPTH:
        return "[TRUNCATED:DEPTH]"
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        items = list(value.items())
        for raw_key, item in items[:MAX_RECEIPT_MAPPING_ITEMS]:
            key = str(raw_key)[:200]
            bounded[key] = (
                REDACTED_VALUE
                if _sensitive_key(key)
                else _redact_and_bound(item, depth=depth + 1)
            )
        if len(items) > MAX_RECEIPT_MAPPING_ITEMS:
            bounded["_receipt_truncated_items"] = len(items) - MAX_RECEIPT_MAPPING_ITEMS
        return bounded
    if isinstance(value, (list, tuple)):
        bounded_items = [
            _redact_and_bound(item, depth=depth + 1)
            for item in value[:MAX_RECEIPT_SEQUENCE_ITEMS]
        ]
        if len(value) > MAX_RECEIPT_SEQUENCE_ITEMS:
            bounded_items.append(
                {"_receipt_truncated_items": len(value) - MAX_RECEIPT_SEQUENCE_ITEMS}
            )
        return bounded_items
    if isinstance(value, str) and len(value) > MAX_RECEIPT_STRING_CHARS:
        return value[:MAX_RECEIPT_STRING_CHARS] + "[TRUNCATED]"
    return value


def bounded_replay_envelope(value: dict[str, Any]) -> dict[str, Any]:
    """Return the exact redacted envelope that may be returned and persisted."""
    normalized = pydantic_core.to_jsonable_python(value, fallback=str)
    bounded = _redact_and_bound(normalized)
    if not isinstance(bounded, dict):
        raise TypeError("Receipt replay envelope must be an object")
    serialized = json.dumps(
        bounded, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(serialized) <= MAX_RECEIPT_PAYLOAD_BYTES:
        return bounded

    # Keep the gateway/error identity and durable handle, but make oversized
    # tool output explicit. This reduced envelope is also the first response,
    # so a later replay remains byte-for-byte equivalent at the JSON layer.
    essential_keys = (
        "agent_id",
        "agent_name",
        "tool_ref",
        "tool_name",
        "source",
        "duration_ms",
        "durable_handle",
        "code",
        "message",
        "retryable",
    )
    reduced = {key: bounded[key] for key in essential_keys if key in bounded}
    if "result" in bounded:
        reduced["result"] = {
            "_receipt_payload_truncated": True,
            "max_bytes": MAX_RECEIPT_PAYLOAD_BYTES,
        }
    if "details" in bounded:
        reduced["details"] = {"_receipt_payload_truncated": True}
    reduced["_receipt_payload_truncated"] = True
    return reduced


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + RECEIPT_PAYLOAD_RETENTION


def _claim_from_receipt(
    receipt: OperationReceipt,
    *,
    request_fingerprint: str,
) -> OperationReceiptClaim:
    if receipt.request_fingerprint != request_fingerprint:
        disposition = OperationReceiptDisposition.MISMATCH
    elif (
        receipt.payload_cleared_at is not None
        or receipt.expires_at <= datetime.now(timezone.utc)
    ):
        disposition = OperationReceiptDisposition.EXPIRED
    else:
        disposition = OperationReceiptDisposition(receipt.status)
    return OperationReceiptClaim(
        receipt_id=receipt.id,
        disposition=disposition,
        response=receipt.response,
        error=receipt.error,
        durable_handle=receipt.durable_handle,
    )


async def claim_operation_receipt(
    *,
    namespace: str,
    scope_key: str,
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
                    request_fingerprint=request_fingerprint,
                    status="started",
                    owner_token=owner_token,
                    expires_at=_expires_at(),
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


async def read_operation_response_for_handle(
    handle: dict[str, str],
) -> dict[str, Any] | None:
    """Read unexpired gateway metadata after canonical task authorization."""
    validated = _validate_durable_handle(handle)
    async with get_db_context() as db:
        return (
            await db.execute(
                select(OperationReceipt.response)
                .where(OperationReceipt.durable_handle == validated)
                .where(OperationReceipt.status == "succeeded")
                .where(OperationReceipt.payload_cleared_at.is_(None))
                .where(OperationReceipt.expires_at > datetime.now(timezone.utc))
                .order_by(OperationReceipt.completed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


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
        response=bounded_replay_envelope(response),
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
        error=bounded_replay_envelope(error),
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
        "expires_at": _expires_at(),
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


def _validate_durable_handle(handle: dict[str, str]) -> dict[str, str]:
    kind = handle.get("kind")
    durable_id = handle.get("id")
    if kind not in {"platform-job", "execution", "agent-run"}:
        raise ValueError("Unsupported durable handle kind")
    if not isinstance(durable_id, str) or not durable_id or len(durable_id) > 100:
        raise ValueError("Invalid durable handle id")
    return {"kind": kind, "id": durable_id}


async def record_operation_receipt_handle(
    receipt_id: UUID,
    owner_token: UUID,
    handle: dict[str, str],
) -> None:
    """Persist the minimal recovery handle before the terminal replay write."""
    validated = _validate_durable_handle(handle)
    async with get_db_context() as db:
        recorded_id = (
            await db.execute(
                update(OperationReceipt)
                .where(
                    OperationReceipt.id == receipt_id,
                    OperationReceipt.status == "started",
                    OperationReceipt.owner_token == owner_token,
                )
                .values(durable_handle=validated)
                .returning(OperationReceipt.id)
            )
        ).scalar_one_or_none()
        if recorded_id is None:
            raise OperationReceiptOwnershipError(
                f"Operation receipt {receipt_id} is not owned by this caller"
            )
        await db.commit()


async def reconcile_operation_receipt_success(
    receipt_id: UUID,
    handle: dict[str, str],
    response: dict[str, Any],
) -> bool:
    """Close STARTED from an authorized canonical lifecycle observation."""
    validated = _validate_durable_handle(handle)
    bounded = bounded_replay_envelope(response)
    async with get_db_context() as db:
        reconciled_id = (
            await db.execute(
                update(OperationReceipt)
                .where(
                    OperationReceipt.id == receipt_id,
                    OperationReceipt.status == "started",
                    (
                        OperationReceipt.durable_handle == validated
                    ) | OperationReceipt.durable_handle.is_(None),
                )
                .values(
                    status="succeeded",
                    response=bounded,
                    durable_handle=validated,
                    completed_at=datetime.now(timezone.utc),
                    expires_at=_expires_at(),
                )
                .returning(OperationReceipt.id)
            )
        ).scalar_one_or_none()
        await db.commit()
        return reconciled_id is not None


async def reconcile_operation_receipt_error(
    receipt_id: UUID,
    handle: dict[str, str],
    error: dict[str, Any],
) -> bool:
    """Close STARTED from an authorized failed canonical lifecycle."""
    validated = _validate_durable_handle(handle)
    bounded = bounded_replay_envelope(error)
    async with get_db_context() as db:
        reconciled_id = (
            await db.execute(
                update(OperationReceipt)
                .where(
                    OperationReceipt.id == receipt_id,
                    OperationReceipt.status == "started",
                    (
                        OperationReceipt.durable_handle == validated
                    ) | OperationReceipt.durable_handle.is_(None),
                )
                .values(
                    status="failed",
                    error=bounded,
                    durable_handle=validated,
                    completed_at=datetime.now(timezone.utc),
                    expires_at=_expires_at(),
                )
                .returning(OperationReceipt.id)
            )
        ).scalar_one_or_none()
        await db.commit()
        return reconciled_id is not None


async def resolve_ambiguous_operation_receipt(
    receipt_id: UUID,
    db: AsyncSession,
) -> bool:
    """Stage fail-closed resolution in the caller's audit transaction."""
    error = bounded_replay_envelope({
        "code": "OPERATION_OUTCOME_RESOLVED_UNKNOWN",
        "message": "An administrator resolved this ambiguous operation as failed.",
        "retryable": False,
        "details": {"resolution": "operator_marked_failed"},
    })
    resolved_id = (
        await db.execute(
            update(OperationReceipt)
            .where(
                OperationReceipt.id == receipt_id,
                OperationReceipt.status == "started",
            )
            .values(
                status="failed",
                error=error,
                completed_at=datetime.now(timezone.utc),
                expires_at=_expires_at(),
            )
            .returning(OperationReceipt.id)
        )
    ).scalar_one_or_none()
    return resolved_id is not None


async def cleanup_expired_operation_receipt_payloads(batch_limit: int = 500) -> int:
    """Clear expired payload/handles while retaining permanent hash tombstones."""
    now = datetime.now(timezone.utc)
    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(OperationReceipt)
                .where(OperationReceipt.status.in_(("succeeded", "failed")))
                .where(OperationReceipt.payload_cleared_at.is_(None))
                .where(OperationReceipt.expires_at <= now)
                .order_by(OperationReceipt.expires_at.asc())
                .limit(batch_limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        row_ids = [row.id for row in rows]
        if row_ids:
            # PostgreSQL JSONB distinguishes SQL NULL from JSON `null`; the
            # tombstone constraint and expiry discriminator require SQL NULL.
            await db.execute(
                update(OperationReceipt)
                .where(OperationReceipt.id.in_(row_ids))
                .values(
                    response=null(),
                    error=null(),
                    durable_handle=null(),
                    payload_cleared_at=now,
                )
            )
        await db.commit()
        return len(rows)


__all__ = [
    "OperationReceiptClaim",
    "OperationReceiptDisposition",
    "OperationReceiptOwnershipError",
    "MAX_RECEIPT_PAYLOAD_BYTES",
    "RECEIPT_PAYLOAD_RETENTION",
    "bounded_replay_envelope",
    "canonical_operation_scope_key",
    "canonical_request_fingerprint",
    "claim_operation_receipt",
    "cleanup_expired_operation_receipt_payloads",
    "complete_operation_receipt_error",
    "complete_operation_receipt_success",
    "record_operation_receipt_handle",
    "read_operation_receipt",
    "read_operation_response_for_handle",
    "reconcile_operation_receipt_error",
    "reconcile_operation_receipt_success",
    "resolve_ambiguous_operation_receipt",
    "wait_for_operation_receipt",
]
