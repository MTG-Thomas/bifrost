"""Durable revision counters for MCP catalogs."""

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base

WORKFLOW_CATALOG_NAME = "workflow_tools"


class MCPCatalogRevision(Base):
    """Transactionally advanced revision for one dynamic MCP catalog."""

    __tablename__ = "mcp_catalog_revisions"

    catalog: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
