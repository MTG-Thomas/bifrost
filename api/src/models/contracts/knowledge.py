"""
Knowledge source and document contract models for Bifrost.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer


# ==================== KNOWLEDGE SOURCE MODELS ====================


class KnowledgeSourceCreate(BaseModel):
    """Request model for creating a knowledge source."""
    name: str = Field(..., min_length=1, max_length=255)
    namespace: str | None = Field(default=None, max_length=255, description="Namespace key (auto-generated from name if omitted)")
    description: str | None = Field(default=None, max_length=2000)
    organization_id: UUID | None = Field(default=None, description="Organization ID (null = global)")
    access_level: str = Field(default="role_based", description="authenticated or role_based")
    role_ids: list[str] = Field(default_factory=list, description="Role IDs for role_based access")


class KnowledgeSourceUpdate(BaseModel):
    """Request model for updating a knowledge source."""
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    access_level: str | None = None
    is_active: bool | None = None
    role_ids: list[str] | None = None


class KnowledgeSourcePublic(BaseModel):
    """Knowledge source output for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    namespace: str
    description: str | None = None
    organization_id: UUID | None = None
    access_level: str
    is_active: bool
    document_count: int = 0
    role_ids: list[str] = Field(default_factory=list)
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("id", "organization_id")
    def serialize_uuid(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


class KnowledgeSourceSummary(BaseModel):
    """Lightweight knowledge source summary for listings."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    namespace: str
    description: str | None = None
    organization_id: UUID | None = None
    access_level: str
    is_active: bool
    document_count: int = 0
    created_at: datetime

    @field_serializer("id", "organization_id")
    def serialize_uuid(self, v: UUID | None) -> str | None:
        return str(v) if v else None

    @field_serializer("created_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()


# ==================== KNOWLEDGE DOCUMENT MODELS ====================


class KnowledgeDocumentCreate(BaseModel):
    """Request model for creating a knowledge document."""
    content: str = Field(..., min_length=1, max_length=500000, description="Markdown content")
    key: str | None = Field(default=None, max_length=255, description="Optional key for upsert")
    metadata: dict = Field(default_factory=dict)


class KnowledgeDocumentUpdate(BaseModel):
    """Request model for updating a knowledge document."""
    content: str = Field(..., min_length=1, max_length=500000, description="Markdown content")
    metadata: dict | None = None


class KnowledgeDocumentPublic(BaseModel):
    """Knowledge document output for API responses."""

    id: str
    namespace: str
    key: str | None = None
    content: str
    metadata: dict = Field(default_factory=dict)
    organization_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KnowledgeDocumentSummary(BaseModel):
    """Lightweight document summary (no full content)."""

    id: str
    namespace: str
    key: str | None = None
    content_preview: str = Field(default="", description="First ~200 chars of content")
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None = None
