"""
Organization MCP Tools

Tools for listing, creating, and getting organizations.
All organization tools are restricted (platform-admin only).
"""

import logging
import re
from typing import Any
from uuid import UUID, uuid4

from mcp.types import CallToolResult
from sqlalchemy import select

from src.core.database import get_db_context
from src.models.orm.organizations import Organization
from src.services.mcp_server.tool_decorator import system_tool
from src.services.mcp_server.tool_registry import ToolCategory
from src.services.mcp_server.tool_result import error_result, success_result

logger = logging.getLogger(__name__)


@system_tool(
    id="list_organizations",
    name="List Organizations",
    description="List all organizations in the platform.",
    category=ToolCategory.ORGANIZATION,
    default_enabled_for_coding_agent=True,
    is_restricted=True,
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def list_organizations(context: Any) -> CallToolResult:
    """List all organizations.

    Platform admin only. Returns id, name, domain, is_active for each org.
    """
    logger.info("MCP list_organizations called")

    try:
        async with get_db_context() as db:
            query = select(Organization).order_by(Organization.name)
            result = await db.execute(query)
            orgs = result.scalars().all()

            orgs_data = [
                {
                    "id": str(org.id),
                    "name": org.name,
                    "domain": org.domain,
                    "is_active": org.is_active,
                }
                for org in orgs
            ]

            display_text = f"Found {len(orgs_data)} organization(s)"
            return success_result(display_text, {"organizations": orgs_data, "count": len(orgs_data)})

    except Exception as e:
        logger.exception(f"Error listing organizations via MCP: {e}")
        return error_result(f"Error listing organizations: {str(e)}")


@system_tool(
    id="get_organization",
    name="Get Organization",
    description="Get organization details by ID or domain.",
    category=ToolCategory.ORGANIZATION,
    default_enabled_for_coding_agent=True,
    is_restricted=True,
    input_schema={
        "type": "object",
        "properties": {
            "organization_id": {
                "type": "string",
                "description": "Organization UUID",
            },
            "domain": {
                "type": "string",
                "description": "Organization domain (alternative to ID)",
            },
        },
        "required": [],
    },
)
async def get_organization(
    context: Any,
    organization_id: str | None = None,
    domain: str | None = None,
) -> CallToolResult:
    """Get organization details by ID or domain.

    Platform admin only. Must provide at least one of organization_id or domain.
    """
    logger.info(f"MCP get_organization called with id={organization_id}, domain={domain}")

    if not organization_id and not domain:
        return error_result("Either organization_id or domain is required")

    try:
        async with get_db_context() as db:
            query = select(Organization)

            if organization_id:
                try:
                    query = query.where(Organization.id == UUID(organization_id))
                except ValueError:
                    return error_result(f"Invalid organization_id format: {organization_id}")
            else:
                query = query.where(Organization.domain == domain)

            result = await db.execute(query)
            org = result.scalar_one_or_none()

            if not org:
                identifier = organization_id or domain
                return error_result(f"Organization not found: {identifier}")

            org_data = {
                "id": str(org.id),
                "name": org.name,
                "domain": org.domain,
                "is_active": org.is_active,
                "settings": org.settings,
                "created_at": org.created_at.isoformat() if org.created_at else None,
                "created_by": org.created_by,
                "updated_at": org.updated_at.isoformat() if org.updated_at else None,
            }

            display_text = f"Organization: {org.name}"
            return success_result(display_text, org_data)

    except Exception as e:
        logger.exception(f"Error getting organization via MCP: {e}")
        return error_result(f"Error getting organization: {str(e)}")


@system_tool(
    id="create_organization",
    name="Create Organization",
    description="Create a new organization.",
    category=ToolCategory.ORGANIZATION,
    default_enabled_for_coding_agent=False,
    is_restricted=True,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Organization name (required)",
            },
            "domain": {
                "type": "string",
                "description": "Organization domain (optional, auto-generated from name if not provided)",
            },
        },
        "required": ["name"],
    },
)
async def create_organization(
    context: Any,
    name: str,
    domain: str | None = None,
) -> CallToolResult:
    """Create a new organization.

    Platform admin only.

    Args:
        context: MCP context with user permissions
        name: Organization name (required)
        domain: Organization domain (optional, auto-generated from name if not provided)

    Returns:
        CallToolResult with created organization details
    """
    logger.info(f"MCP create_organization called with name={name}")

    if not name:
        return error_result("name is required")

    if len(name) > 255:
        return error_result("name must be 255 characters or less")

    # Generate domain from name if not provided
    if not domain:
        # Convert to lowercase, replace spaces/special chars with hyphens
        domain = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    if len(domain) > 255:
        return error_result("domain must be 255 characters or less")

    try:
        async with get_db_context() as db:
            # Check for duplicate domain
            existing_query = select(Organization).where(Organization.domain == domain)
            existing_result = await db.execute(existing_query)
            if existing_result.scalar_one_or_none():
                return error_result(f"Organization with domain '{domain}' already exists")

            # Create organization
            org = Organization(
                id=uuid4(),
                name=name,
                domain=domain,
                is_active=True,
                settings={},
                created_by=context.user_email,
            )

            db.add(org)
            await db.commit()

            logger.info(f"Created organization {org.id}: {org.name}")

            display_text = f"Created organization: {org.name}"
            return success_result(display_text, {
                "success": True,
                "id": str(org.id),
                "name": org.name,
                "domain": org.domain,
                "is_active": org.is_active,
            })

    except Exception as e:
        logger.exception(f"Error creating organization via MCP: {e}")
        return error_result(f"Error creating organization: {str(e)}")
