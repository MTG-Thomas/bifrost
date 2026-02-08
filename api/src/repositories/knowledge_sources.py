"""
Knowledge Source Repository

Repository for KnowledgeSource CRUD with organization scoping and role-based access.
"""

from src.models.orm.knowledge_sources import KnowledgeSource, KnowledgeSourceRole
from src.repositories.org_scoped import OrgScopedRepository


class KnowledgeSourceRepository(OrgScopedRepository[KnowledgeSource]):
    """
    Knowledge source repository using OrgScopedRepository.

    Uses CASCADE scoping (org + global) and role-based access control
    via the knowledge_source_roles junction table.
    """

    model = KnowledgeSource
    role_table = KnowledgeSourceRole
    role_entity_id_column = "knowledge_source_id"
