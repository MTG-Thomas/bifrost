"""
Workflow Role Service

Manages workflow role assignments, including automatic sync from
forms/apps/agents to workflows.

This service implements Phase 3 of the workflow-role-access plan:
auto-assignment of roles to workflows when entities are saved.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent, AgentRole
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import AppComponent, AppPage
from src.models.orm.forms import Form, FormField, FormRole
from src.models.orm.workflow_roles import WorkflowRole


# =============================================================================
# Workflow ID Extraction Functions
# =============================================================================


def extract_form_workflow_ids(
    form: Form,
    fields: list[FormField] | None = None,
) -> list[UUID]:
    """
    Extract all workflow IDs referenced by a form.

    Extracts from:
    - form.workflow_id (main execution workflow)
    - form.launch_workflow_id (startup/pre-execution workflow)
    - form_fields.data_provider_id (dynamic field data providers)

    Args:
        form: The Form ORM object
        fields: Optional list of fields (uses form.fields if not provided)

    Returns:
        List of workflow UUIDs (deduplicated)
    """
    workflow_ids: set[UUID] = set()

    # Main workflow
    if form.workflow_id:
        try:
            workflow_ids.add(UUID(form.workflow_id))
        except ValueError:
            pass

    # Launch workflow
    if form.launch_workflow_id:
        try:
            workflow_ids.add(UUID(form.launch_workflow_id))
        except ValueError:
            pass

    # Data providers from fields
    form_fields = fields if fields is not None else form.fields
    for field in form_fields:
        if field.data_provider_id:
            workflow_ids.add(field.data_provider_id)

    return list(workflow_ids)


def extract_agent_workflow_ids(agent: Agent) -> list[UUID]:
    """
    Extract all workflow IDs (tools) referenced by an agent.

    Args:
        agent: The Agent ORM object with tools relationship loaded

    Returns:
        List of workflow UUIDs (tool IDs)
    """
    return [tool.id for tool in agent.tools]


def _extract_workflows_from_props(obj: object) -> set[UUID]:
    """
    Recursively find all workflowId and dataProviderId values in a nested dict/list.

    Handles all nested patterns including:
    - props.workflowId
    - props.onClick.workflowId
    - props.rowActions[].onClick.workflowId
    - props.headerActions[].onClick.workflowId
    - props.footerActions[].workflowId
    - Any other nested structure

    Args:
        obj: Nested dict, list, or primitive

    Returns:
        Set of workflow UUIDs found
    """
    workflows: set[UUID] = set()

    if isinstance(obj, dict):
        # Check for workflowId key
        if wf_id := obj.get("workflowId"):
            if isinstance(wf_id, str):
                try:
                    workflows.add(UUID(wf_id))
                except ValueError:
                    pass

        # Check for dataProviderId key
        if dp_id := obj.get("dataProviderId"):
            if isinstance(dp_id, str):
                try:
                    workflows.add(UUID(dp_id))
                except ValueError:
                    pass

        # Recurse into all values
        for value in obj.values():
            workflows.update(_extract_workflows_from_props(value))

    elif isinstance(obj, list):
        for item in obj:
            workflows.update(_extract_workflows_from_props(item))

    return workflows


def extract_app_workflow_ids(
    pages: list[AppPage],
    components: list[AppComponent],
) -> list[UUID]:
    """
    Extract all workflow IDs from app pages and components.

    Extracts from:
    - page.launch_workflow_id
    - page.data_sources[].workflowId
    - page.data_sources[].dataProviderId
    - component.loading_workflows[]
    - component.props (recursively - all workflowId/dataProviderId values)

    Args:
        pages: List of AppPage ORM objects
        components: List of AppComponent ORM objects

    Returns:
        List of workflow UUIDs (deduplicated)
    """
    workflows: set[UUID] = set()

    for page in pages:
        # Page launch workflow
        launch_wf_id = (
            getattr(page, "launch_workflow_id", None)
            if hasattr(page, "launch_workflow_id")
            else page.get("launch_workflow_id") if isinstance(page, dict) else None
        )
        if launch_wf_id:
            if isinstance(launch_wf_id, UUID):
                workflows.add(launch_wf_id)
            else:
                try:
                    workflows.add(UUID(launch_wf_id))
                except (ValueError, TypeError):
                    pass

        # Page data sources
        data_sources = (
            getattr(page, "data_sources", None)
            if hasattr(page, "data_sources")
            else page.get("data_sources") if isinstance(page, dict) else None
        ) or []
        for ds in data_sources:
            if isinstance(ds, dict):
                if wf_id := ds.get("workflowId"):
                    try:
                        workflows.add(UUID(wf_id))
                    except (ValueError, TypeError):
                        pass
                if dp_id := ds.get("dataProviderId"):
                    try:
                        workflows.add(UUID(dp_id))
                    except (ValueError, TypeError):
                        pass

    for comp in components:
        # Component loading_workflows
        loading_wfs = (
            getattr(comp, "loading_workflows", None)
            if hasattr(comp, "loading_workflows")
            else comp.get("loading_workflows") if isinstance(comp, dict) else None
        ) or []
        for wf_id in loading_wfs:
            try:
                workflows.add(UUID(wf_id))
            except (ValueError, TypeError):
                pass

        # Component props (recursive extraction)
        props = (
            getattr(comp, "props", None)
            if hasattr(comp, "props")
            else comp.get("props") if isinstance(comp, dict) else None
        ) or {}
        workflows.update(_extract_workflows_from_props(props))

    return list(workflows)


# =============================================================================
# WorkflowRoleService
# =============================================================================


class WorkflowRoleService:
    """Service for managing workflow role assignments."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_entity_roles_to_workflows(
        self,
        workflow_ids: list[UUID],
        role_ids: list[UUID],
        assigned_by: str,
    ) -> None:
        """
        Bulk assign roles to workflows (additive, upsert pattern).

        For each workflow in workflow_ids, ensure all role_ids are assigned.
        This is ADDITIVE - it never removes existing roles from workflows.
        Uses PostgreSQL ON CONFLICT DO NOTHING for efficiency.

        Args:
            workflow_ids: List of workflow UUIDs to assign roles to
            role_ids: List of role UUIDs to assign to the workflows
            assigned_by: Email of the user performing the assignment
        """
        if not workflow_ids or not role_ids:
            return

        now = datetime.utcnow()

        # Build list of all (workflow_id, role_id) combinations to insert
        values = [
            {
                "workflow_id": wf_id,
                "role_id": role_id,
                "assigned_by": assigned_by,
                "assigned_at": now,
            }
            for wf_id in workflow_ids
            for role_id in role_ids
        ]

        # Use PostgreSQL INSERT ... ON CONFLICT DO NOTHING for efficiency
        stmt = insert(WorkflowRole).values(values)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["workflow_id", "role_id"]
        )

        await self.db.execute(stmt)

    async def get_form_role_ids(self, form_id: UUID) -> list[UUID]:
        """Get all role IDs assigned to a form."""
        result = await self.db.execute(
            select(FormRole.role_id).where(FormRole.form_id == form_id)
        )
        return list(result.scalars().all())

    async def get_agent_role_ids(self, agent_id: UUID) -> list[UUID]:
        """Get all role IDs assigned to an agent."""
        result = await self.db.execute(
            select(AgentRole.role_id).where(AgentRole.agent_id == agent_id)
        )
        return list(result.scalars().all())

    async def get_app_role_ids(self, app_id: UUID) -> list[UUID]:
        """Get all role IDs assigned to an application."""
        result = await self.db.execute(
            select(AppRole.role_id).where(AppRole.app_id == app_id)
        )
        return list(result.scalars().all())


# =============================================================================
# Convenience Functions
# =============================================================================


async def sync_form_roles_to_workflows(
    db: AsyncSession,
    form: Form,
    fields: list[FormField] | None = None,
    assigned_by: str = "system",
) -> None:
    """
    Sync form's roles to all workflows referenced by the form.

    This is a convenience wrapper that:
    1. Extracts workflow IDs from the form
    2. Gets the form's role IDs
    3. Assigns those roles to all workflows

    Args:
        db: Database session
        form: The Form ORM object
        fields: Optional list of fields (uses form.fields if not provided)
        assigned_by: Email of the user performing the assignment
    """
    service = WorkflowRoleService(db)

    # Extract workflow IDs from form
    workflow_ids = extract_form_workflow_ids(form, fields)
    if not workflow_ids:
        return

    # Get form's role IDs
    role_ids = await service.get_form_role_ids(form.id)
    if not role_ids:
        return

    # Sync roles to workflows
    await service.sync_entity_roles_to_workflows(
        workflow_ids=workflow_ids,
        role_ids=role_ids,
        assigned_by=assigned_by,
    )


async def sync_agent_roles_to_workflows(
    db: AsyncSession,
    agent: Agent,
    assigned_by: str = "system",
) -> None:
    """
    Sync agent's roles to all workflows (tools) used by the agent.

    This is a convenience wrapper that:
    1. Extracts workflow IDs (tools) from the agent
    2. Gets the agent's role IDs
    3. Assigns those roles to all workflows

    Args:
        db: Database session
        agent: The Agent ORM object with tools relationship loaded
        assigned_by: Email of the user performing the assignment
    """
    service = WorkflowRoleService(db)

    # Extract workflow IDs from agent tools
    workflow_ids = extract_agent_workflow_ids(agent)
    if not workflow_ids:
        return

    # Get agent's role IDs
    role_ids = await service.get_agent_role_ids(agent.id)
    if not role_ids:
        return

    # Sync roles to workflows
    await service.sync_entity_roles_to_workflows(
        workflow_ids=workflow_ids,
        role_ids=role_ids,
        assigned_by=assigned_by,
    )


async def sync_app_roles_to_workflows(
    db: AsyncSession,
    app_id: UUID,
    pages: list[AppPage],
    components: list[AppComponent],
    assigned_by: str = "system",
) -> None:
    """
    Sync app's roles to all workflows referenced by the app's pages and components.

    This is a convenience wrapper that:
    1. Extracts workflow IDs from pages and components
    2. Gets the app's role IDs
    3. Assigns those roles to all workflows

    Args:
        db: Database session
        app_id: The Application UUID
        pages: List of AppPage ORM objects
        components: List of AppComponent ORM objects
        assigned_by: Email of the user performing the assignment
    """
    service = WorkflowRoleService(db)

    # Extract workflow IDs from app
    workflow_ids = extract_app_workflow_ids(pages, components)
    if not workflow_ids:
        return

    # Get app's role IDs
    role_ids = await service.get_app_role_ids(app_id)
    if not role_ids:
        return

    # Sync roles to workflows
    await service.sync_entity_roles_to_workflows(
        workflow_ids=workflow_ids,
        role_ids=role_ids,
        assigned_by=assigned_by,
    )
