"""
Knowledge Sources Router

CRUD for knowledge sources and their documents.
Admin-only for source management, role-based access for reading.
Documents are stored via the KnowledgeRepository with embeddings.
"""

import logging
import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser, CurrentSuperuser
from src.core.database import DbSession
from src.core.org_filter import OrgFilterType, resolve_org_filter
from src.models.contracts.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentPublic,
    KnowledgeDocumentSummary,
    KnowledgeDocumentUpdate,
    KnowledgeSourceCreate,
    KnowledgeSourcePublic,
    KnowledgeSourceSummary,
    KnowledgeSourceUpdate,
)
from src.models.orm.knowledge_sources import KnowledgeSource, KnowledgeSourceRole
from src.models.orm.users import Role
from src.models.orm.knowledge import KnowledgeStore
from src.repositories.knowledge import KnowledgeRepository
from src.repositories.knowledge_sources import KnowledgeSourceRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-sources", tags=["Knowledge Sources"])


def generate_namespace(name: str) -> str:
    """Generate a URL-safe namespace key from a name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "default"


def _source_to_public(source: KnowledgeSource) -> KnowledgeSourcePublic:
    """Convert KnowledgeSource ORM to public response."""
    return KnowledgeSourcePublic(
        id=source.id,
        name=source.name,
        namespace=source.namespace,
        description=source.description,
        organization_id=source.organization_id,
        access_level=source.access_level,
        is_active=source.is_active,
        document_count=source.document_count,
        role_ids=[str(r.id) for r in source.roles] if source.roles else [],
        created_by=source.created_by,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


# =============================================================================
# Knowledge Source CRUD
# =============================================================================


@router.get("")
async def list_knowledge_sources(
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(default=None),
    active_only: bool = True,
) -> list[KnowledgeSourceSummary]:
    """List knowledge sources the user has access to."""
    try:
        filter_type, filter_org_id = resolve_org_filter(user, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    is_admin = user.is_superuser or any(
        role in ["Platform Admin", "Platform Owner"] for role in user.roles
    )

    repo = KnowledgeSourceRepository(
        session=db,
        org_id=filter_org_id,
        user_id=user.user_id,
        is_superuser=is_admin,
    )

    if is_admin:
        # Build query with filter type
        query = select(KnowledgeSource).options(selectinload(KnowledgeSource.roles))

        if filter_type == OrgFilterType.GLOBAL_ONLY:
            query = query.where(KnowledgeSource.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            if filter_org_id:
                query = query.where(KnowledgeSource.organization_id == filter_org_id)
        elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL:
            if filter_org_id:
                query = query.where(
                    (KnowledgeSource.organization_id == filter_org_id) |
                    (KnowledgeSource.organization_id.is_(None))
                )
            else:
                query = query.where(KnowledgeSource.organization_id.is_(None))

        if active_only:
            query = query.where(KnowledgeSource.is_active.is_(True))

        query = query.order_by(KnowledgeSource.name)
        result = await db.execute(query)
        sources = list(result.scalars().unique().all())
    else:
        sources = await repo.list(is_active=True) if active_only else await repo.list()

    return [KnowledgeSourceSummary.model_validate(s) for s in sources]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_knowledge_source(
    data: KnowledgeSourceCreate,
    db: DbSession,
    user: CurrentSuperuser,
) -> KnowledgeSourcePublic:
    """Create a new knowledge source (admin only)."""
    namespace = data.namespace or generate_namespace(data.name)

    # Check for namespace uniqueness within org
    existing = await db.execute(
        select(KnowledgeSource).where(
            KnowledgeSource.namespace == namespace,
            KnowledgeSource.organization_id == data.organization_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Knowledge source with namespace '{namespace}' already exists in this scope")

    source = KnowledgeSource(
        name=data.name,
        namespace=namespace,
        description=data.description,
        organization_id=data.organization_id,
        access_level=data.access_level,
        created_by=user.email,
    )
    db.add(source)
    await db.flush()

    # Add role assignments
    if data.role_ids:
        for role_id in data.role_ids:
            try:
                role_uuid = UUID(role_id)
                result = await db.execute(
                    select(Role).where(Role.id == role_uuid).where(Role.is_active.is_(True))
                )
                role = result.scalar_one_or_none()
                if role:
                    db.add(KnowledgeSourceRole(
                        knowledge_source_id=source.id,
                        role_id=role.id,
                        assigned_by=user.email,
                    ))
            except ValueError:
                logger.warning(f"Invalid role ID: {role_id}")

    await db.flush()

    # Reload with relationships
    result = await db.execute(
        select(KnowledgeSource)
        .options(selectinload(KnowledgeSource.roles))
        .where(KnowledgeSource.id == source.id)
    )
    source = result.scalar_one()
    return _source_to_public(source)


@router.get("/{source_id}")
async def get_knowledge_source(
    source_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> KnowledgeSourcePublic:
    """Get a knowledge source by ID."""
    is_admin = user.is_superuser or any(
        role in ["Platform Admin", "Platform Owner"] for role in user.roles
    )

    repo = KnowledgeSourceRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=is_admin,
    )

    # Load with roles
    result = await db.execute(
        select(KnowledgeSource)
        .options(selectinload(KnowledgeSource.roles))
        .where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    # Access check for non-admins
    if not is_admin:
        if not await repo._can_access_entity(source):
            raise HTTPException(404, f"Knowledge source {source_id} not found")

    return _source_to_public(source)


@router.put("/{source_id}")
async def update_knowledge_source(
    source_id: UUID,
    data: KnowledgeSourceUpdate,
    db: DbSession,
    user: CurrentSuperuser,
) -> KnowledgeSourcePublic:
    """Update a knowledge source (admin only)."""
    result = await db.execute(
        select(KnowledgeSource)
        .options(selectinload(KnowledgeSource.roles))
        .where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    if data.name is not None:
        source.name = data.name
    if data.description is not None:
        source.description = data.description
    if data.access_level is not None:
        source.access_level = data.access_level
    if data.is_active is not None:
        source.is_active = data.is_active

    source.updated_at = datetime.utcnow()

    # Update role assignments if provided
    if data.role_ids is not None:
        await db.execute(
            delete(KnowledgeSourceRole).where(
                KnowledgeSourceRole.knowledge_source_id == source_id
            )
        )
        for role_id in data.role_ids:
            try:
                role_uuid = UUID(role_id)
                result = await db.execute(
                    select(Role).where(Role.id == role_uuid).where(Role.is_active.is_(True))
                )
                role = result.scalar_one_or_none()
                if role:
                    db.add(KnowledgeSourceRole(
                        knowledge_source_id=source_id,
                        role_id=role.id,
                        assigned_by=user.email,
                    ))
            except ValueError:
                logger.warning(f"Invalid role ID: {role_id}")

    await db.flush()

    # Reload
    result = await db.execute(
        select(KnowledgeSource)
        .options(selectinload(KnowledgeSource.roles))
        .where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one()
    return _source_to_public(source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_source(
    source_id: UUID,
    db: DbSession,
    user: CurrentSuperuser,
) -> None:
    """Soft delete a knowledge source (admin only)."""
    result = await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    source.is_active = False
    source.updated_at = datetime.utcnow()
    await db.flush()


# =============================================================================
# Role Assignment
# =============================================================================


@router.get("/{source_id}/roles")
async def get_knowledge_source_roles(
    source_id: UUID,
    db: DbSession,
    user: CurrentSuperuser,
) -> dict:
    """Get roles assigned to a knowledge source."""
    result = await db.execute(
        select(KnowledgeSource)
        .options(selectinload(KnowledgeSource.roles))
        .where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    return {"role_ids": [str(r.id) for r in source.roles]}


@router.post("/{source_id}/roles", status_code=status.HTTP_201_CREATED)
async def assign_roles_to_knowledge_source(
    source_id: UUID,
    data: dict,
    db: DbSession,
    user: CurrentSuperuser,
) -> dict:
    """Assign roles to a knowledge source."""
    result = await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    role_ids = data.get("role_ids", [])
    added = []
    for role_id in role_ids:
        try:
            role_uuid = UUID(role_id)
            result = await db.execute(
                select(Role).where(Role.id == role_uuid).where(Role.is_active.is_(True))
            )
            role = result.scalar_one_or_none()
            if role:
                existing = await db.execute(
                    select(KnowledgeSourceRole).where(
                        KnowledgeSourceRole.knowledge_source_id == source_id,
                        KnowledgeSourceRole.role_id == role.id,
                    )
                )
                if not existing.scalar_one_or_none():
                    db.add(KnowledgeSourceRole(
                        knowledge_source_id=source_id,
                        role_id=role.id,
                        assigned_by=user.email,
                    ))
                    added.append(str(role.id))
        except ValueError:
            pass

    await db.flush()
    return {"added_role_ids": added}


@router.delete("/{source_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role_from_knowledge_source(
    source_id: UUID,
    role_id: UUID,
    db: DbSession,
    user: CurrentSuperuser,
) -> None:
    """Remove a role from a knowledge source."""
    await db.execute(
        delete(KnowledgeSourceRole).where(
            KnowledgeSourceRole.knowledge_source_id == source_id,
            KnowledgeSourceRole.role_id == role_id,
        )
    )
    await db.flush()


# =============================================================================
# Document CRUD
# =============================================================================


@router.get("/{source_id}/documents")
async def list_documents(
    source_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[KnowledgeDocumentSummary]:
    """List documents in a knowledge source."""
    is_admin = user.is_superuser or any(
        role in ["Platform Admin", "Platform Owner"] for role in user.roles
    )

    # Verify source exists and user has access
    result = await db.execute(
        select(KnowledgeSource)
        .options(selectinload(KnowledgeSource.roles))
        .where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    if not is_admin:
        repo = KnowledgeSourceRepository(
            session=db,
            org_id=user.organization_id,
            user_id=user.user_id,
            is_superuser=False,
        )
        if not await repo._can_access_entity(source):
            raise HTTPException(404, f"Knowledge source {source_id} not found")

    # Query documents from knowledge_store by namespace
    result = await db.execute(
        select(KnowledgeStore)
        .where(KnowledgeStore.namespace == source.namespace)
        .where(
            (KnowledgeStore.organization_id == source.organization_id) |
            (KnowledgeStore.organization_id.is_(None))
            if source.organization_id
            else KnowledgeStore.organization_id.is_(None)
        )
        .order_by(KnowledgeStore.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    docs = result.scalars().all()

    return [
        KnowledgeDocumentSummary(
            id=str(doc.id),
            namespace=doc.namespace,
            key=doc.key,
            content_preview=doc.content[:200] if doc.content else "",
            metadata=doc.doc_metadata or {},
            created_at=doc.created_at,
        )
        for doc in docs
    ]


@router.post("/{source_id}/documents", status_code=status.HTTP_201_CREATED)
async def create_document(
    source_id: UUID,
    data: KnowledgeDocumentCreate,
    db: DbSession,
    user: CurrentSuperuser,
) -> KnowledgeDocumentPublic:
    """Create a document in a knowledge source with embedding."""
    result = await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    # Generate embedding
    try:
        from src.services.embeddings.factory import get_embedding_client
        client = await get_embedding_client(db)
        embedding = await client.embed(data.content)
    except ValueError as e:
        raise HTTPException(503, f"Embedding service unavailable: {e}")

    # Store via KnowledgeRepository
    knowledge_repo = KnowledgeRepository(
        session=db,
        org_id=source.organization_id,
    )

    doc_id = await knowledge_repo.store(
        content=data.content,
        embedding=embedding,
        namespace=source.namespace,
        key=data.key,
        metadata=data.metadata,
        organization_id=source.organization_id,
        created_by=user.user_id,
    )

    # Increment document count
    source.document_count = (source.document_count or 0) + 1
    source.updated_at = datetime.utcnow()
    await db.flush()

    # Load the created document
    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == UUID(doc_id))
    )
    doc = result.scalar_one()

    return KnowledgeDocumentPublic(
        id=str(doc.id),
        namespace=doc.namespace,
        key=doc.key,
        content=doc.content,
        metadata=doc.doc_metadata or {},
        organization_id=str(doc.organization_id) if doc.organization_id else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("/{source_id}/documents/{doc_id}")
async def get_document(
    source_id: UUID,
    doc_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> KnowledgeDocumentPublic:
    """Get a document by ID."""
    is_admin = user.is_superuser or any(
        role in ["Platform Admin", "Platform Owner"] for role in user.roles
    )

    # Verify source access
    result = await db.execute(
        select(KnowledgeSource)
        .options(selectinload(KnowledgeSource.roles))
        .where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    if not is_admin:
        repo = KnowledgeSourceRepository(
            session=db,
            org_id=user.organization_id,
            user_id=user.user_id,
            is_superuser=False,
        )
        if not await repo._can_access_entity(source):
            raise HTTPException(404, f"Knowledge source {source_id} not found")

    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc or doc.namespace != source.namespace:
        raise HTTPException(404, f"Document {doc_id} not found")

    return KnowledgeDocumentPublic(
        id=str(doc.id),
        namespace=doc.namespace,
        key=doc.key,
        content=doc.content,
        metadata=doc.doc_metadata or {},
        organization_id=str(doc.organization_id) if doc.organization_id else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.put("/{source_id}/documents/{doc_id}")
async def update_document(
    source_id: UUID,
    doc_id: UUID,
    data: KnowledgeDocumentUpdate,
    db: DbSession,
    user: CurrentSuperuser,
) -> KnowledgeDocumentPublic:
    """Update a document and re-embed."""
    result = await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc or doc.namespace != source.namespace:
        raise HTTPException(404, f"Document {doc_id} not found")

    # Re-embed
    try:
        from src.services.embeddings.factory import get_embedding_client
        client = await get_embedding_client(db)
        embedding = await client.embed(data.content)
    except ValueError as e:
        raise HTTPException(503, f"Embedding service unavailable: {e}")

    doc.content = data.content
    doc.embedding = embedding
    if data.metadata is not None:
        doc.doc_metadata = data.metadata
    doc.updated_at = datetime.utcnow()

    await db.flush()

    return KnowledgeDocumentPublic(
        id=str(doc.id),
        namespace=doc.namespace,
        key=doc.key,
        content=doc.content,
        metadata=doc.doc_metadata or {},
        organization_id=str(doc.organization_id) if doc.organization_id else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.delete("/{source_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    source_id: UUID,
    doc_id: UUID,
    db: DbSession,
    user: CurrentSuperuser,
) -> None:
    """Delete a document from a knowledge source."""
    result = await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, f"Knowledge source {source_id} not found")

    result = await db.execute(
        select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc or doc.namespace != source.namespace:
        raise HTTPException(404, f"Document {doc_id} not found")

    await db.execute(
        delete(KnowledgeStore).where(KnowledgeStore.id == doc_id)
    )

    # Decrement document count
    source.document_count = max(0, (source.document_count or 0) - 1)
    source.updated_at = datetime.utcnow()
    await db.flush()
