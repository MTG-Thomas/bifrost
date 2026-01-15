"""
Dependency Graph Service

BFS-based graph traversal for entity dependency visualization.
Builds a bidirectional dependency graph from workflows, forms, apps, and agents.

This service is query-time (not precomputed) since the dependency canvas
is rarely accessed and complexity is bounded by the depth limit.
"""

from collections import deque
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm import (
    Agent,
    AgentTool,
    Application,
    AppPage,
    AppComponent,
    Form,
    Workflow,
)


def _extract_workflows_from_props(obj: dict | list | str | int | None) -> set[UUID]:
    """
    Recursively find all workflowId and dataProviderId values in a nested dict/list.

    Handles all nested patterns including:
    - props.workflowId
    - props.onClick.workflowId
    - props.rowActions[].onClick.workflowId
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


EntityType = Literal["workflow", "form", "app", "agent"]


class GraphNode:
    """Node in the dependency graph."""

    def __init__(
        self,
        id: str,
        type: EntityType,
        name: str,
        org_id: UUID | None = None,
    ):
        self.id = id
        self.type = type
        self.name = name
        self.org_id = org_id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "org_id": str(self.org_id) if self.org_id else None,
        }


class GraphEdge:
    """Edge in the dependency graph."""

    def __init__(self, source: str, target: str, relationship: str):
        self.source = source
        self.target = target
        self.relationship = relationship

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "relationship": self.relationship,
        }


class DependencyGraph:
    """Result of dependency graph traversal."""

    def __init__(self, root_id: str):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self.root_id = root_id

    def add_node(self, node: GraphNode) -> None:
        """Add a node to the graph if not already present."""
        if node.id not in self.nodes:
            self.nodes[node.id] = node

    def add_edge(self, source: str, target: str, relationship: str) -> None:
        """Add an edge to the graph, avoiding duplicates."""
        # Check for duplicate edges
        for edge in self.edges:
            if edge.source == source and edge.target == target:
                return
        self.edges.append(GraphEdge(source, target, relationship))

    def to_dict(self) -> dict:
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
            "root_id": self.root_id,
        }


class DependencyGraphService:
    """
    Service for building entity dependency graphs.

    Performs BFS traversal from a root entity, following relationships
    in both directions (uses/used_by) up to a configurable depth.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_graph(
        self,
        entity_type: EntityType,
        entity_id: UUID,
        depth: int = 2,
    ) -> DependencyGraph:
        """
        Build a dependency graph starting from the specified entity.

        Args:
            entity_type: Type of the root entity
            entity_id: UUID of the root entity
            depth: Maximum traversal depth (1-5)

        Returns:
            DependencyGraph with nodes and edges
        """
        depth = max(1, min(5, depth))  # Clamp to 1-5

        root_key = f"{entity_type}:{entity_id}"
        graph = DependencyGraph(root_key)

        # BFS queue: (entity_type, entity_id, current_depth)
        queue: deque[tuple[EntityType, UUID, int]] = deque()
        queue.append((entity_type, entity_id, 0))
        visited: set[str] = set()

        while queue:
            current_type, current_id, current_depth = queue.popleft()
            node_key = f"{current_type}:{current_id}"

            if node_key in visited:
                continue
            visited.add(node_key)

            # Fetch entity and add as node
            node = await self._fetch_entity_node(current_type, current_id)
            if node is None:
                continue
            graph.add_node(node)

            # Stop exploring if at max depth
            if current_depth >= depth:
                continue

            # Get dependencies in both directions
            dependencies = await self._get_dependencies(current_type, current_id)

            for dep_type, dep_id, relationship in dependencies:
                dep_key = f"{dep_type}:{dep_id}"

                # Add edge
                if relationship == "uses":
                    graph.add_edge(node_key, dep_key, "uses")
                else:  # used_by
                    graph.add_edge(dep_key, node_key, "uses")

                # Queue for exploration if not visited
                if dep_key not in visited:
                    queue.append((dep_type, dep_id, current_depth + 1))

        return graph

    async def _app_uses_workflow(
        self,
        app: Application,
        workflow_id: UUID,
    ) -> bool:
        """
        Check if an application uses a specific workflow.

        Checks pages (launch_workflow_id, data_sources) and components (props).
        """
        if not app.active_version_id:
            return False

        # Get pages for the active version
        pages_result = await self.db.execute(
            select(AppPage).where(AppPage.version_id == app.active_version_id)
        )
        pages = pages_result.scalars().all()

        for page in pages:
            # Check launch workflow
            if page.launch_workflow_id == workflow_id:
                return True

            # Check data sources
            for ds in page.data_sources or []:
                if ds.get("workflowId") == str(workflow_id):
                    return True
                if ds.get("dataProviderId") == str(workflow_id):
                    return True

        # Get components for those pages
        page_ids = [p.id for p in pages]
        if not page_ids:
            return False

        components_result = await self.db.execute(
            select(AppComponent).where(AppComponent.page_id.in_(page_ids))
        )
        components = components_result.scalars().all()

        for comp in components:
            # Check loading_workflows
            for wf_id in comp.loading_workflows or []:
                try:
                    if UUID(wf_id) == workflow_id:
                        return True
                except ValueError:
                    pass

            # Check props recursively
            if workflow_id in _extract_workflows_from_props(comp.props or {}):
                return True

        return False

    async def _fetch_entity_node(
        self,
        entity_type: EntityType,
        entity_id: UUID,
    ) -> GraphNode | None:
        """Fetch entity details and create a GraphNode."""
        if entity_type == "workflow":
            result = await self.db.execute(
                select(Workflow).where(Workflow.id == entity_id)
            )
            entity = result.scalar_one_or_none()
            if entity:
                return GraphNode(
                    id=f"workflow:{entity_id}",
                    type="workflow",
                    name=entity.name,
                    org_id=entity.organization_id,
                )

        elif entity_type == "form":
            result = await self.db.execute(
                select(Form).where(Form.id == entity_id)
            )
            entity = result.scalar_one_or_none()
            if entity:
                return GraphNode(
                    id=f"form:{entity_id}",
                    type="form",
                    name=entity.name,
                    org_id=entity.organization_id,
                )

        elif entity_type == "app":
            result = await self.db.execute(
                select(Application).where(Application.id == entity_id)
            )
            entity = result.scalar_one_or_none()
            if entity:
                return GraphNode(
                    id=f"app:{entity_id}",
                    type="app",
                    name=entity.name,
                    org_id=entity.organization_id,
                )

        elif entity_type == "agent":
            result = await self.db.execute(
                select(Agent).where(Agent.id == entity_id)
            )
            entity = result.scalar_one_or_none()
            if entity:
                return GraphNode(
                    id=f"agent:{entity_id}",
                    type="agent",
                    name=entity.name,
                    org_id=entity.organization_id,
                )

        return None

    async def _get_dependencies(
        self,
        entity_type: EntityType,
        entity_id: UUID,
    ) -> list[tuple[EntityType, UUID, str]]:
        """
        Get all dependencies for an entity in both directions.

        Returns list of (entity_type, entity_id, relationship) tuples.
        relationship is "uses" (this entity uses target) or "used_by" (target uses this).
        """
        dependencies: list[tuple[EntityType, UUID, str]] = []

        if entity_type == "workflow":
            # Workflows are USED BY forms, apps, and agents
            # Query entities directly for reverse lookups

            # Check forms that reference this workflow
            forms_result = await self.db.execute(
                select(Form.id).where(
                    Form.is_active.is_(True),
                    (
                        (Form.workflow_id == str(entity_id))
                        | (Form.launch_workflow_id == str(entity_id))
                    ),
                )
            )
            for form_id in forms_result.scalars().all():
                dependencies.append(("form", form_id, "used_by"))

            # Check apps that might reference this workflow (via pages/components)
            # This is expensive but dependency graph is rarely called
            apps_result = await self.db.execute(
                select(Application).options(selectinload(Application.active_version))
            )
            for app in apps_result.scalars().all():
                if app.active_version_id:
                    # Check if this app uses the workflow
                    if await self._app_uses_workflow(app, entity_id):
                        dependencies.append(("app", app.id, "used_by"))

            # Check agents directly (via agent_tools)
            result = await self.db.execute(
                select(AgentTool.agent_id).where(
                    AgentTool.workflow_id == entity_id
                )
            )
            agent_ids = result.scalars().all()
            for agent_id in agent_ids:
                dependencies.append(("agent", agent_id, "used_by"))

        elif entity_type == "form":
            # Forms USE workflows
            result = await self.db.execute(
                select(Form)
                .options(selectinload(Form.fields))
                .where(Form.id == entity_id)
            )
            form = result.scalar_one_or_none()
            if form:
                # Main workflow
                if form.workflow_id:
                    try:
                        wf_id = UUID(form.workflow_id)
                        dependencies.append(("workflow", wf_id, "uses"))
                    except ValueError:
                        pass

                # Launch workflow
                if form.launch_workflow_id:
                    try:
                        wf_id = UUID(form.launch_workflow_id)
                        dependencies.append(("workflow", wf_id, "uses"))
                    except ValueError:
                        pass

                # Data provider workflows from fields
                for field in form.fields:
                    if field.data_provider_id:
                        dependencies.append(
                            ("workflow", field.data_provider_id, "uses")
                        )

        elif entity_type == "app":
            # Apps USE workflows (via pages and components)
            result = await self.db.execute(
                select(Application)
                .options(selectinload(Application.active_version))
                .where(Application.id == entity_id)
            )
            app = result.scalar_one_or_none()
            if app and app.active_version_id:
                # Get pages for the active version
                pages_result = await self.db.execute(
                    select(AppPage).where(AppPage.version_id == app.active_version_id)
                )
                pages = pages_result.scalars().all()

                # Get components for those pages
                page_ids = [p.id for p in pages]
                if page_ids:
                    components_result = await self.db.execute(
                        select(AppComponent).where(AppComponent.page_id.in_(page_ids))
                    )
                    components = components_result.scalars().all()
                else:
                    components = []

                # Extract workflows from pages
                for page in pages:
                    if page.launch_workflow_id:
                        dependencies.append(
                            ("workflow", page.launch_workflow_id, "uses")
                        )

                    # Data sources
                    for ds in page.data_sources or []:
                        if wf_id := ds.get("workflowId"):
                            try:
                                dependencies.append(
                                    ("workflow", UUID(wf_id), "uses")
                                )
                            except ValueError:
                                pass
                        if dp_id := ds.get("dataProviderId"):
                            try:
                                dependencies.append(
                                    ("workflow", UUID(dp_id), "uses")
                                )
                            except ValueError:
                                pass

                # Extract workflows from components
                for comp in components:
                    # Loading workflows
                    for wf_id in comp.loading_workflows or []:
                        try:
                            dependencies.append(
                                ("workflow", UUID(wf_id), "uses")
                            )
                        except ValueError:
                            pass

                    # Props (recursive extraction)
                    for wf_id in _extract_workflows_from_props(comp.props or {}):
                        dependencies.append(("workflow", wf_id, "uses"))

        elif entity_type == "agent":
            # Agents USE workflows (via agent_tools)
            result = await self.db.execute(
                select(AgentTool.workflow_id).where(
                    AgentTool.agent_id == entity_id
                )
            )
            workflow_ids = result.scalars().all()
            for wf_id in workflow_ids:
                dependencies.append(("workflow", wf_id, "uses"))

        # Deduplicate dependencies
        seen: set[str] = set()
        unique_deps: list[tuple[EntityType, UUID, str]] = []
        for dep in dependencies:
            key = f"{dep[0]}:{dep[1]}:{dep[2]}"
            if key not in seen:
                seen.add(key)
                unique_deps.append(dep)

        return unique_deps
