"""Scheduler-owned expiry of MCP operation receipt replay payloads."""

from __future__ import annotations

import logging

from src.services.operation_receipts import cleanup_expired_operation_receipt_payloads

logger = logging.getLogger(__name__)


async def cleanup_operation_receipt_payloads() -> dict[str, int]:
    """Clear expired bounded payloads while preserving hash tombstones."""
    cleaned = await cleanup_expired_operation_receipt_payloads()
    logger.info("Expired MCP operation receipt payloads cleaned", extra={"cleaned": cleaned})
    return {"cleaned": cleaned}


__all__ = ["cleanup_operation_receipt_payloads"]
