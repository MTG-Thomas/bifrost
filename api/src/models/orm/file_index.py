"""
FileIndex ORM model.

Search index for text content in _repo/. Populated via dual-write
whenever files are written to S3. Only indexes text-searchable files
(.py, .yaml, .md, .txt, etc.). No entity routing, no polymorphic references.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class FileIndex(Base):
    """Search index for workspace files in _repo/."""

    __tablename__ = "file_index"

    path: Mapped[str] = mapped_column(String(1000), primary_key=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
