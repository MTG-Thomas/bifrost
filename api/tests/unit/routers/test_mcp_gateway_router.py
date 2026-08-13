from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.mcp import MCPOperationReceiptResolutionRequest
from src.routers.mcp import (
    _raise_gateway_http_error,
    resolve_gateway_operation_receipt,
)
from src.services.mcp_server.gateway import GatewayError
from src.services.operation_receipts import (
    OperationReceiptDisposition,
    canonical_operation_scope_key,
    canonical_request_fingerprint,
    claim_operation_receipt,
)


@pytest.mark.parametrize(
    "code",
    ["OPERATION_ID_REUSED", "OPERATION_IN_PROGRESS_OR_UNKNOWN"],
)
def test_operation_receipt_conflicts_map_to_http_409(code: str) -> None:
    error = GatewayError(code, "Operation conflict", retryable=True)

    with pytest.raises(HTTPException) as exc_info:
        _raise_gateway_http_error(error)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == error.as_dict()


@pytest.mark.asyncio
async def test_admin_ambiguous_resolution_is_fail_closed_and_audited() -> None:
    receipt_id = uuid4()
    db = AsyncMock()
    request = MCPOperationReceiptResolutionRequest(
        resolution="failed_unknown",
        reason="Canonical provider logs show no safe replay path",
    )
    with (
        patch(
            "src.services.operation_receipts.resolve_ambiguous_operation_receipt",
            new=AsyncMock(return_value=True),
        ) as resolve,
        patch("src.services.audit.emit_audit", new=AsyncMock()) as audit,
    ):
        response = await resolve_gateway_operation_receipt(
            receipt_id,
            request,
            SimpleNamespace(is_superuser=True),  # type: ignore[arg-type]
            db,
        )

    assert response.status == "failed"
    resolve.assert_awaited_once_with(receipt_id, db)
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["strict"] is True
    details = audit.await_args.kwargs["details"]
    assert details["resolution"] == "failed_unknown"
    assert "Canonical provider" not in str(details)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_ambiguous_resolution(
    async_session_factory,
) -> None:
    kwargs = {
        "namespace": "test.router-resolution.v1",
        "scope_key": canonical_operation_scope_key({"caller": str(uuid4())}),
        "request_fingerprint": canonical_request_fingerprint({"arguments": {}}),
    }
    owner = await claim_operation_receipt(**kwargs)
    request = MCPOperationReceiptResolutionRequest(
        resolution="failed_unknown",
        reason="Investigated but canonical outcome remains unknowable",
    )

    async with async_session_factory() as db:
        with (
            patch(
                "src.services.audit.emit_audit",
                new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
            ),
            pytest.raises(RuntimeError, match="audit unavailable"),
        ):
            await resolve_gateway_operation_receipt(
                owner.receipt_id,
                request,
                SimpleNamespace(is_superuser=True),  # type: ignore[arg-type]
                db,
            )

    retry = await claim_operation_receipt(**kwargs)
    assert retry.disposition == OperationReceiptDisposition.STARTED
