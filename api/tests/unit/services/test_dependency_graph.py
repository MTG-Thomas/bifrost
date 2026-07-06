from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.services.dependency_graph import (
    _extract_workflows_from_props,
    GraphNode,
    GraphEdge,
    DependencyGraph,
    DependencyGraphService,
)

UUID_A = "12345678-1234-1234-1234-123456789012"
UUID_B = "abcdefab-abcd-abcd-abcd-abcdefabcdef"


class TestExtractWorkflowsFromProps:
    def test_workflowId_at_top_level(self):
        result = _extract_workflows_from_props({"workflowId": UUID_A})
        assert result == {UUID(UUID_A)}

    def test_dataProviderId_at_top_level(self):
        result = _extract_workflows_from_props({"dataProviderId": UUID_A})
        assert result == {UUID(UUID_A)}

    def test_invalid_uuid_returns_empty(self):
        result = _extract_workflows_from_props({"workflowId": "not-a-uuid"})
        assert result == set()

    def test_nested_in_dict(self):
        result = _extract_workflows_from_props({"onClick": {"workflowId": UUID_A}})
        assert result == {UUID(UUID_A)}

    def test_list_of_dicts(self):
        obj = [{"workflowId": UUID_A}, {"workflowId": UUID_B}]
        result = _extract_workflows_from_props(obj)
        assert result == {UUID(UUID_A), UUID(UUID_B)}

    def test_deeply_nested(self):
        obj = {"rowActions": [{"onClick": {"workflowId": UUID_A}}]}
        result = _extract_workflows_from_props(obj)
        assert result == {UUID(UUID_A)}

    def test_none_returns_empty(self):
        assert _extract_workflows_from_props(None) == set()

    def test_string_returns_empty(self):
        assert _extract_workflows_from_props("string") == set()

    def test_int_returns_empty(self):
        assert _extract_workflows_from_props(42) == set()

    def test_empty_dict_returns_empty(self):
        assert _extract_workflows_from_props({}) == set()

    def test_mixed_workflowId_and_dataProviderId(self):
        obj = {
            "button": {"workflowId": UUID_A},
            "table": {"dataProviderId": UUID_B},
        }
        result = _extract_workflows_from_props(obj)
        assert result == {UUID(UUID_A), UUID(UUID_B)}

    def test_duplicate_ids_deduplicated(self):
        obj = [{"workflowId": UUID_A}, {"workflowId": UUID_A}]
        result = _extract_workflows_from_props(obj)
        assert result == {UUID(UUID_A)}

    def test_non_string_workflowId_ignored(self):
        result = _extract_workflows_from_props({"workflowId": 12345})
        assert result == set()

    def test_empty_list_returns_empty(self):
        assert _extract_workflows_from_props([]) == set()


class TestGraphNode:
    def test_constructor(self):
        org_id = uuid4()
        node = GraphNode(id="workflow:123", type="workflow", name="My WF", org_id=org_id)
        assert node.id == "workflow:123"
        assert node.type == "workflow"
        assert node.name == "My WF"
        assert node.org_id == org_id

    def test_to_dict(self):
        org_id = uuid4()
        node = GraphNode(id="form:456", type="form", name="My Form", org_id=org_id)
        assert node.to_dict() == {
            "id": "form:456",
            "type": "form",
            "name": "My Form",
            "org_id": str(org_id),
        }

    def test_to_dict_org_id_none(self):
        node = GraphNode(id="app:789", type="app", name="My App")
        assert node.to_dict()["org_id"] is None


class TestGraphEdge:
    def test_constructor(self):
        edge = GraphEdge(source="workflow:1", target="form:2", relationship="uses")
        assert edge.source == "workflow:1"
        assert edge.target == "form:2"
        assert edge.relationship == "uses"

    def test_to_dict(self):
        edge = GraphEdge(source="a", target="b", relationship="uses")
        assert edge.to_dict() == {
            "source": "a",
            "target": "b",
            "relationship": "uses",
        }


class TestDependencyGraph:
    def test_add_node(self):
        graph = DependencyGraph(root_id="workflow:1")
        node = GraphNode(id="workflow:1", type="workflow", name="WF1")
        graph.add_node(node)
        assert "workflow:1" in graph.nodes
        assert graph.nodes["workflow:1"] is node

    def test_add_node_idempotent(self):
        graph = DependencyGraph(root_id="workflow:1")
        node1 = GraphNode(id="workflow:1", type="workflow", name="First")
        node2 = GraphNode(id="workflow:1", type="workflow", name="Second")
        graph.add_node(node1)
        graph.add_node(node2)
        assert len(graph.nodes) == 1
        assert graph.nodes["workflow:1"].name == "First"

    def test_add_edge(self):
        graph = DependencyGraph(root_id="workflow:1")
        graph.add_edge("workflow:1", "form:2", "uses")
        assert len(graph.edges) == 1
        assert graph.edges[0].source == "workflow:1"
        assert graph.edges[0].target == "form:2"
        assert graph.edges[0].relationship == "uses"

    def test_add_edge_no_duplicate(self):
        graph = DependencyGraph(root_id="workflow:1")
        graph.add_edge("workflow:1", "form:2", "uses")
        graph.add_edge("workflow:1", "form:2", "uses")
        assert len(graph.edges) == 1

    def test_add_edge_different_targets(self):
        graph = DependencyGraph(root_id="workflow:1")
        graph.add_edge("workflow:1", "form:2", "uses")
        graph.add_edge("workflow:1", "form:3", "uses")
        assert len(graph.edges) == 2

    def test_to_dict(self):
        graph = DependencyGraph(root_id="workflow:1")
        org_id = uuid4()
        graph.add_node(GraphNode(id="workflow:1", type="workflow", name="WF1", org_id=org_id))
        graph.add_node(GraphNode(id="form:2", type="form", name="Form1"))
        graph.add_edge("workflow:1", "form:2", "uses")

        result = graph.to_dict()
        assert result["root_id"] == "workflow:1"
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["edges"][0] == {
            "source": "workflow:1",
            "target": "form:2",
            "relationship": "uses",
        }
        node_ids = {n["id"] for n in result["nodes"]}
        assert node_ids == {"workflow:1", "form:2"}

    def test_to_dict_empty_graph(self):
        graph = DependencyGraph(root_id="workflow:1")
        result = graph.to_dict()
        assert result == {"nodes": [], "edges": [], "root_id": "workflow:1"}


class ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class ResultStub:
    def __init__(self, *, rows=(), scalar=None, scalars=()):
        self._rows = list(rows)
        self._scalar = scalar
        self._scalars = list(scalars)

    def all(self):
        return self._rows

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return ScalarRows(self._scalars)


class DbStub:
    def __init__(self, *results):
        self.results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


class TestDependencyGraphService:
    @pytest.mark.asyncio
    async def test_build_workflow_lookup_indexes_ids_names_and_portable_refs(self):
        first_id = uuid4()
        second_id = uuid4()
        db = DbStub(
            ResultStub(
                rows=[
                    (
                        first_id,
                        "create_ticket",
                        "features/tickets/workflows.py",
                        "create_ticket",
                    ),
                    (second_id, "no_path", None, "missing_path"),
                ]
            )
        )

        lookup = await DependencyGraphService(db)._build_workflow_lookup()

        assert lookup[str(first_id)] == first_id
        assert lookup["create_ticket"] == first_id
        assert lookup["features/tickets/workflows.py::create_ticket"] == first_id
        assert lookup[str(second_id)] == second_id
        assert lookup["no_path"] == second_id
        assert "None::missing_path" not in lookup

    @pytest.mark.asyncio
    async def test_app_uses_workflow_matches_uuid_and_portable_refs(self, monkeypatch):
        workflow_id = uuid4()
        app = SimpleNamespace(repo_prefix="apps/helpdesk/")
        db = DbStub(
            ResultStub(rows=[("useWorkflow('ignored')",), ("useWorkflow('portable')",)]),
            ResultStub(rows=[("features/tickets/workflows.py", "create_ticket")]),
        )
        monkeypatch.setattr(
            "src.services.app_dependencies.parse_dependencies",
            lambda content: {"features/tickets/workflows.py::create_ticket"}
            if "portable" in content
            else set(),
        )

        assert (
            await DependencyGraphService(db)._app_uses_workflow(app, workflow_id)
            is True
        )

    @pytest.mark.asyncio
    async def test_form_dependencies_include_uuid_workflows_and_field_providers(self):
        workflow_id = uuid4()
        launch_id = uuid4()
        provider_id = uuid4()
        form = SimpleNamespace(
            workflow_id=str(workflow_id),
            launch_workflow_id=str(launch_id),
            fields=[
                SimpleNamespace(data_provider_id=provider_id),
                SimpleNamespace(data_provider_id=None),
            ],
        )
        db = DbStub(ResultStub(scalar=form))

        deps = await DependencyGraphService(db)._get_dependencies("form", uuid4())

        assert deps == [
            ("workflow", workflow_id, "uses"),
            ("workflow", launch_id, "uses"),
            ("workflow", provider_id, "uses"),
        ]

    @pytest.mark.asyncio
    async def test_form_dependencies_ignore_portable_refs_until_lookup_supported(self):
        provider_id = uuid4()
        form = SimpleNamespace(
            workflow_id="features/tickets/workflows.py::create_ticket",
            launch_workflow_id="not-a-uuid",
            fields=[SimpleNamespace(data_provider_id=provider_id)],
        )
        db = DbStub(ResultStub(scalar=form))

        deps = await DependencyGraphService(db)._get_dependencies("form", uuid4())

        assert deps == [("workflow", provider_id, "uses")]

    @pytest.mark.asyncio
    async def test_agent_dependencies_are_deduplicated(self):
        workflow_id = uuid4()
        db = DbStub(ResultStub(scalars=[workflow_id, workflow_id]))

        deps = await DependencyGraphService(db)._get_dependencies("agent", uuid4())

        assert deps == [("workflow", workflow_id, "uses")]

    @pytest.mark.asyncio
    async def test_build_graph_follows_uses_and_used_by_edges_with_depth_limit(self):
        workflow_id = uuid4()
        form_id = uuid4()
        app_id = uuid4()

        class FakeService(DependencyGraphService):
            async def _fetch_entity_node(self, entity_type, entity_id):
                return GraphNode(
                    id=f"{entity_type}:{entity_id}",
                    type=entity_type,
                    name=f"{entity_type}-{str(entity_id)[:8]}",
                )

            async def _get_dependencies(self, entity_type, entity_id):
                if entity_type == "workflow":
                    return [("form", form_id, "used_by")]
                if entity_type == "form":
                    return [("app", app_id, "uses")]
                msg = f"depth limit should avoid querying {entity_type}"
                raise AssertionError(msg)

        graph = await FakeService(DbStub()).build_graph("workflow", workflow_id, depth=1)

        assert set(graph.nodes) == {
            f"workflow:{workflow_id}",
            f"form:{form_id}",
        }
        assert [edge.to_dict() for edge in graph.edges] == [
            {
                "source": f"form:{form_id}",
                "target": f"workflow:{workflow_id}",
                "relationship": "uses",
            }
        ]
