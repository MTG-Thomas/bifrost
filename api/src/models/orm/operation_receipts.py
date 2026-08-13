"""Durable, request-scoped idempotency receipts."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class OperationReceipt(Base):
    """One permanent receipt for an at-most-once operation."""

    __tablename__ = "operation_receipts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="started"
    )
    owner_token: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=uuid4
    )
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "uq_operation_receipts_namespace_scope",
            "namespace",
            "scope_key",
            unique=True,
        ),
        CheckConstraint(
            "status IN ('started', 'succeeded', 'failed')",
            name="ck_operation_receipts_status",
        ),
        CheckConstraint(
            "(status = 'started' AND response IS NULL AND error IS NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'succeeded' AND response IS NOT NULL AND error IS NULL "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND response IS NULL AND error IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_operation_receipts_terminal_payload",
        ),
    )
