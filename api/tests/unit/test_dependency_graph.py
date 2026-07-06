"""
Unit tests for the DependencyGraphService.
"""

from uuid import uuid4

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.services.dependency_graph import (
    DependencyGraphService,
    DependencyGraph,
    GraphNode,
    GraphEdge,
)


class TestDependencyGraph:
    """Tests for the DependencyGraph data structure."""

    def test_add_node_new(self):
        """Test adding a new node to the graph."""
        graph = DependencyGraph("root:123")
        node = GraphNode("workflow:123", "workflow", "Test Workflow")

        graph.add_node(node)

        assert "workflow:123" in graph.nodes
        assert graph.nodes["workflow:123"].name == "Test Workflow"

    def test_add_node_duplicate(self):
        """Test that duplicate nodes are not added."""
        graph = DependencyGraph("root:123")
        node1 = GraphNode("workflow:123", "workflow", "First")
        node2 = GraphNode("workflow:123", "workflow", "Second")

        graph.add_node(node1)
        graph.add_node(node2)

        assert len(graph.nodes) == 1
        assert graph.nodes["workflow:123"].name == "First"

    def test_add_edge(self):
        """Test adding an edge to the graph."""
        graph = DependencyGraph("root:123")

        graph.add_edge("workflow:123", "form:456", "uses")

        assert len(graph.edges) == 1
        assert graph.edges[0].source == "workflow:123"
        assert graph.edges[0].target == "form:456"
        assert graph.edges[0].relationship == "uses"

    def test_add_edge_duplicate(self):
        """Test that duplicate edges are not added."""
        graph = DependencyGraph("root:123")

        graph.add_edge("workflow:123", "form:456", "uses")
        graph.add_edge("workflow:123", "form:456", "uses")

        assert len(graph.edges) == 1

    def test_to_dict(self):
        """Test serializing graph to dictionary."""
        graph = DependencyGraph("workflow:123")
        graph.add_node(GraphNode("workflow:123", "workflow", "Test Workflow"))
        graph.add_node(GraphNode("form:456", "form", "Test Form"))
        graph.add_edge("form:456", "workflow:123", "uses")

        result = graph.to_dict()

        assert result["root_id"] == "workflow:123"
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1


class TestGraphNode:
    """Tests for the GraphNode class."""

    def test_to_dict_without_org(self):
        """Test serializing node without organization."""
        node = GraphNode("workflow:123", "workflow", "Test Workflow")

        result = node.to_dict()

        assert result["id"] == "workflow:123"
        assert result["type"] == "workflow"
        assert result["name"] == "Test Workflow"
        assert result["org_id"] is None

    def test_to_dict_with_org(self):
        """Test serializing node with organization."""
        org_id = uuid4()
        node = GraphNode("form:456", "form", "Test Form", org_id)

        result = node.to_dict()

        assert result["id"] == "form:456"
        assert result["type"] == "form"
        assert result["name"] == "Test Form"
        assert result["org_id"] == str(org_id)


class TestGraphEdge:
    """Tests for the GraphEdge class."""

    def test_to_dict(self):
        """Test serializing edge to dictionary."""
        edge = GraphEdge("workflow:123", "form:456", "uses")

        result = edge.to_dict()

        assert result["source"] == "workflow:123"
        assert result["target"] == "form:456"
        assert result["relationship"] == "uses"


class TestDependencyGraphService:
    """Tests for the DependencyGraphService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def service(self, mock_db):
        """Create a service instance with mock db."""
        return DependencyGraphService(mock_db)

    @pytest.mark.asyncio
    async def test_build_graph_clamps_depth_min(self, service, mock_db):
        """Test that depth is clamped to minimum of 1."""
        # Mock the entity fetch to return None (entity not found)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        entity_id = uuid4()

        # Depth 0 should be clamped to 1
        graph = await service.build_graph("workflow", entity_id, depth=0)

        # Graph should be empty since entity wasn't found
        assert len(graph.nodes) == 0

    @pytest.mark.asyncio
    async def test_build_graph_clamps_depth_max(self, service, mock_db):
        """Test that depth is clamped to maximum of 5."""
        # Mock the entity fetch to return None (entity not found)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        entity_id = uuid4()

        # Depth 10 should be clamped to 5
        graph = await service.build_graph("workflow", entity_id, depth=10)

        # Graph should be empty since entity wasn't found
        assert len(graph.nodes) == 0

    @pytest.mark.asyncio
    async def test_fetch_entity_node_workflow(self, service, mock_db):
        """Test fetching a workflow entity node."""
        entity_id = uuid4()
        org_id = uuid4()

        # Mock workflow entity
        mock_workflow = MagicMock()
        mock_workflow.id = entity_id
        mock_workflow.name = "Test Workflow"
        mock_workflow.organization_id = org_id

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workflow
        mock_db.execute.return_value = mock_result

        node = await service._fetch_entity_node("workflow", entity_id)

        assert node is not None
        assert node.id == f"workflow:{entity_id}"
        assert node.type == "workflow"
        assert node.name == "Test Workflow"
        assert node.org_id == org_id

    @pytest.mark.asyncio
    async def test_fetch_entity_node_not_found(self, service, mock_db):
        """Test fetching an entity that doesn't exist."""
        entity_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        node = await service._fetch_entity_node("workflow", entity_id)

        assert node is None

    @pytest.mark.asyncio
    async def test_get_dependencies_agent(self, service, mock_db):
        """Test getting dependencies for an agent (uses workflows)."""
        agent_id = uuid4()
        workflow_id = uuid4()

        # Mock agent_tools query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [workflow_id]
        mock_db.execute.return_value = mock_result

        deps = await service._get_dependencies("agent", agent_id)

        assert len(deps) == 1
        assert deps[0] == ("workflow", workflow_id, "uses")

    @pytest.mark.asyncio
    async def test_get_dependencies_deduplicates(self, service, mock_db):
        """Test that duplicate dependencies are removed."""
        agent_id = uuid4()
        workflow_id = uuid4()

        # Return same workflow ID twice
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [workflow_id, workflow_id]
        mock_db.execute.return_value = mock_result

        deps = await service._get_dependencies("agent", agent_id)

        # Should be deduplicated to 1
        assert len(deps) == 1

    @pytest.mark.asyncio
    async def test_build_workflow_lookup_includes_uuid_name_and_portable_ref(
        self,
        service,
        mock_db,
    ):
        """Workflow lookup should accept all supported reference formats."""
        workflow_id = uuid4()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (workflow_id, "Sync Tickets", "workflows/tickets.py", "sync_tickets"),
            (uuid4(), "No Path", None, "missing_path"),
        ]
        mock_db.execute.return_value = mock_result

        lookup = await service._build_workflow_lookup()

        assert lookup[str(workflow_id)] == workflow_id
        assert lookup["Sync Tickets"] == workflow_id
        assert lookup["workflows/tickets.py::sync_tickets"] == workflow_id
        assert "None::missing_path" not in lookup

    @pytest.mark.asyncio
    async def test_build_graph_traverses_uses_and_used_by_edges(self, service):
        """BFS should orient used_by edges back toward the current node."""
        root_id = uuid4()
        form_id = uuid4()
        workflow_2_id = uuid4()

        async def fetch_node(entity_type, entity_id):
            return GraphNode(
                id=f"{entity_type}:{entity_id}",
                type=entity_type,
                name=f"{entity_type}-{entity_id}",
            )

        async def get_dependencies(entity_type, entity_id):
            if entity_id == root_id:
                return [
                    ("form", form_id, "used_by"),
                    ("workflow", workflow_2_id, "uses"),
                ]
            return []

        service._fetch_entity_node = fetch_node  # type: ignore[method-assign]
        service._get_dependencies = get_dependencies  # type: ignore[method-assign]

        graph = await service.build_graph("workflow", root_id, depth=5)
        edge_dicts = [edge.to_dict() for edge in graph.edges]

        assert set(graph.nodes) == {
            f"workflow:{root_id}",
            f"form:{form_id}",
            f"workflow:{workflow_2_id}",
        }
        assert {
            "source": f"form:{form_id}",
            "target": f"workflow:{root_id}",
            "relationship": "uses",
        } in edge_dicts
        assert {
            "source": f"workflow:{root_id}",
            "target": f"workflow:{workflow_2_id}",
            "relationship": "uses",
        } in edge_dicts

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entity_type", ["form", "app", "agent"])
    async def test_fetch_entity_node_supports_all_entity_types(
        self,
        service,
        mock_db,
        entity_type,
    ):
        """Each supported entity type should be converted into a graph node."""
        entity_id = uuid4()
        org_id = uuid4()
        mock_entity = SimpleNamespace(
            id=entity_id,
            name=f"{entity_type} name",
            organization_id=org_id,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entity
        mock_db.execute.return_value = mock_result

        node = await service._fetch_entity_node(entity_type, entity_id)

        assert node is not None
        assert node.id == f"{entity_type}:{entity_id}"
        assert node.type == entity_type
        assert node.name == f"{entity_type} name"
        assert node.org_id == org_id

    @pytest.mark.asyncio
    async def test_get_dependencies_form_collects_workflows_and_skips_bad_refs(
        self,
        service,
        mock_db,
    ):
        """Form dependencies include main, launch, and field data providers."""
        workflow_id = uuid4()
        data_provider_id = uuid4()
        form = SimpleNamespace(
            workflow_id=str(workflow_id),
            launch_workflow_id="not-a-uuid",
            fields=[
                SimpleNamespace(data_provider_id=data_provider_id),
                SimpleNamespace(data_provider_id=None),
            ],
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = form
        mock_db.execute.return_value = mock_result

        deps = await service._get_dependencies("form", uuid4())

        assert deps == [
            ("workflow", workflow_id, "uses"),
            ("workflow", data_provider_id, "uses"),
        ]

    @pytest.mark.asyncio
    async def test_get_dependencies_app_parses_file_refs(
        self,
        service,
        mock_db,
        monkeypatch,
    ):
        """App dependencies should resolve parsed file refs through workflow lookup."""
        app_id = uuid4()
        workflow_id = uuid4()
        app = SimpleNamespace(id=app_id, repo_prefix="apps/helpdesk/")
        app_result = MagicMock()
        app_result.scalar_one_or_none.return_value = app
        file_result = MagicMock()
        file_result.all.return_value = [("call('tickets.sync')",), (None,)]
        mock_db.execute.side_effect = [app_result, file_result]
        monkeypatch.setattr(
            "src.services.app_dependencies.parse_dependencies",
            lambda content: {"tickets.sync"},
        )

        async def lookup():
            return {"tickets.sync": workflow_id}

        service._build_workflow_lookup = lookup  # type: ignore[method-assign]

        assert await service._get_dependencies("app", app_id) == [
            ("workflow", workflow_id, "uses")
        ]

    @pytest.mark.asyncio
    async def test_app_uses_workflow_matches_uuid_and_portable_ref(
        self,
        service,
        mock_db,
        monkeypatch,
    ):
        """Reverse app lookup should match both UUID and portable refs in files."""
        workflow_id = uuid4()
        app = SimpleNamespace(repo_prefix="apps/helpdesk/")
        file_result = MagicMock()
        file_result.all.return_value = [("content",)]
        workflow_meta = MagicMock()
        workflow_meta.one_or_none.return_value = ("workflows/tickets.py", "sync")
        mock_db.execute.side_effect = [file_result, workflow_meta]
        monkeypatch.setattr(
            "src.services.app_dependencies.parse_dependencies",
            lambda content: {"workflows/tickets.py::sync"},
        )

        assert await service._app_uses_workflow(app, workflow_id) is True

    @pytest.mark.asyncio
    async def test_get_dependencies_workflow_collects_reverse_dependencies(
        self,
        service,
        mock_db,
    ):
        """Workflow dependencies should include forms, apps, and agents using it."""
        workflow_id = uuid4()
        form_id = uuid4()
        app_id = uuid4()
        agent_id = uuid4()
        forms_result = MagicMock()
        forms_result.scalars.return_value.all.return_value = [form_id]
        apps_result = MagicMock()
        apps_result.scalars.return_value.all.return_value = [
            SimpleNamespace(id=app_id, repo_prefix="apps/helpdesk/")
        ]
        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = [agent_id]
        mock_db.execute.side_effect = [forms_result, apps_result, agents_result]

        async def app_uses_workflow(app, candidate_workflow_id):
            assert candidate_workflow_id == workflow_id
            return True

        service._app_uses_workflow = app_uses_workflow  # type: ignore[method-assign]

        assert await service._get_dependencies("workflow", workflow_id) == [
            ("form", form_id, "used_by"),
            ("app", app_id, "used_by"),
            ("agent", agent_id, "used_by"),
        ]
