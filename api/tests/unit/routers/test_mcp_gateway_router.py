import pytest
from fastapi import HTTPException

from src.routers.mcp import _raise_gateway_http_error
from src.services.mcp_server.gateway import GatewayError


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
