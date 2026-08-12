"""Persistent workspace _repo changesets for concurrent remote editors."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkspaceRepoChangeset(Base):
    __tablename__ = "workspace_repo_changesets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(1000), nullable=False)
    base_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    base_files: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    mutations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    validation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    activated_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    writer_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("platform_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    dirty_generation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authoritative_revision: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    authoritative_files: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    authoritative_base_files: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    activation_backup: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    remote_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commit_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    push_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    closure_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closure_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
