from __future__ import annotations

import uuid

from src.models.orm.applications import Application
from src.models.orm.forms import Form
from src.models.orm.workflows import Workflow
from src.services.solutions.dependency_walker import SolutionDependencyWalker, _Seed


class _Repo:
    def __init__(self, files: dict[str, bytes], *, fail_list: bool = False):
        self.files = files
        self.fail_list = fail_list

    async def list(self, prefix: str = "") -> list[str]:
        if self.fail_list:
            raise RuntimeError("object storage unavailable")
        return [path for path in self.files if path.startswith(prefix)]

    async def read(self, path: str) -> bytes:
        try:
            return self.files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _TupleResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, *results):
        self.results = list(results)

    async def execute(self, _stmt):
        if not self.results:
            raise AssertionError("unexpected query")
        return self.results.pop(0)


def _workflow(*, path: str, fn: str = "main", name: str = "wf") -> Workflow:
    return Workflow(
        id=uuid.uuid4(),
        name=name,
        function_name=fn,
        path=path,
        type="workflow",
        is_active=True,
        organization_id=None,
        solution_id=None,
    )


async def test_read_app_sources_returns_sorted_relative_text_sources() -> None:
    app = Application(
        id=uuid.uuid4(),
        name="Portal",
        slug="portal",
        repo_path="apps/portal",
        organization_id=None,
    )
    repo = _Repo(
        {
            "apps/portal/pages/index.tsx": b"export default function Page() {}",
            "apps/portal/_layout.tsx": b"export default function Layout() {}",
            "apps/other/pages/index.tsx": b"ignored",
        }
    )
    walker = SolutionDependencyWalker(db=None, repo=repo)

    sources = await walker._read_app_sources(app)

    assert sources == [
        ("_layout.tsx", "export default function Layout() {}"),
        ("pages/index.tsx", "export default function Page() {}"),
    ]


async def test_read_app_sources_tolerates_list_and_read_failures() -> None:
    app = Application(
        id=uuid.uuid4(),
        name="Portal",
        slug="portal",
        repo_path="apps/portal",
        organization_id=None,
    )
    list_failure = SolutionDependencyWalker(db=None, repo=_Repo({}, fail_list=True))
    assert await list_failure._read_app_sources(app) == []

    missing_read = SolutionDependencyWalker(
        db=None,
        repo=_Repo({"apps/portal/pages/index.tsx": b"ok"}),
    )
    missing_read.repo.files.pop("apps/portal/pages/index.tsx")
    assert await missing_read._read_app_sources(app) == []


async def test_collect_module_closure_prefers_module_file_then_package_init() -> None:
    repo = _Repo(
        {
            "modules/pkg/__init__.py": b"from modules.leaf import value",
            "modules/leaf.py": b"value = 1",
        }
    )
    walker = SolutionDependencyWalker(db=None, repo=repo)
    acc: dict[str, str] = {}

    await walker._collect_module_closure("from modules.pkg import tool", acc)

    assert acc == {
        "modules/pkg/__init__.py": "from modules.leaf import value",
        "modules/leaf.py": "value = 1",
    }


async def test_scope_uses_null_scope_for_global_solution() -> None:
    from src.models.orm.workflows import Workflow

    walker = SolutionDependencyWalker(db=None, repo=_Repo({}))

    global_scope = str(walker._scope(Workflow, None).compile(compile_kwargs={"literal_binds": True}))
    org_scope = str(
        walker._scope(
            Workflow, uuid.UUID("00000000-0000-0000-0000-000000000001")
        ).compile(compile_kwargs={"literal_binds": True})
    )

    assert "organization_id IS NULL" in global_scope
    assert "00000000000000000000000000000001" in org_scope


async def test_agent_tool_workflow_helpers_return_filtered_rows() -> None:
    selected_agent = uuid.uuid4()
    other_agent = uuid.uuid4()
    selected_wf = uuid.uuid4()
    other_wf = uuid.uuid4()
    db = _Db(
        _ScalarResult([selected_wf]),
        _TupleResult(
            [
                (selected_agent, "Selected", selected_wf),
                (other_agent, "Outside", other_wf),
            ]
        ),
    )
    walker = SolutionDependencyWalker(db=db, repo=_Repo({}))

    assert await walker._agent_tool_workflows({selected_agent}) == [selected_wf]
    assert await walker._all_agent_tool_workflows(None, exclude={selected_agent}) == [
        (other_agent, "Outside", other_wf)
    ]


async def test_reverse_refs_reports_outside_form_app_and_config_consumers() -> None:
    selected_wf = _workflow(path="workflows/selected.py", name="selected")
    outside_wf = _workflow(path="workflows/outside.py", name="outside")
    table_id = uuid.uuid4()
    app_id = uuid.uuid4()
    form_id = uuid.uuid4()
    app = Application(
        id=app_id,
        name="Outside App",
        slug="outside-app",
        repo_path="apps/outside",
        organization_id=None,
    )
    form = Form(
        id=form_id,
        name="Outside Form",
        organization_id=None,
        created_by="test",
        workflow_id=str(selected_wf.id),
    )
    table = type("TableObj", (), {"id": table_id, "name": "orders"})()
    db = _Db(_TupleResult([]))
    walker = SolutionDependencyWalker(
        db=db,
        repo=_Repo(
            {
                "workflows/outside.py": (
                    b'await tables.get("orders")\n'
                    b'api_key = await config.get("API_KEY")\n'
                ),
                "apps/outside/App.tsx": (
                    b'useWorkflow("selected");\n'
                    b'useTable("orders");\n'
                ),
            }
        ),
    )

    refs = await walker._reverse_refs(
        _Seed(tables={table_id}, configs={"API_KEY"}),
        {selected_wf.id},
        None,
        {selected_wf.id: selected_wf, outside_wf.id: outside_wf},
        {f"{selected_wf.path}::{selected_wf.function_name}": selected_wf},
        {table_id: table},
        {"orders": table},
        {form.id: form},
        {app.id: app},
    )

    observed = {
        (
            ref.referencer_kind,
            ref.referencer_name,
            ref.target_kind,
            ref.target_name,
        )
        for ref in refs
    }
    assert observed == {
        ("workflow", "outside", "table", "orders"),
        ("workflow", "outside", "config", "API_KEY"),
        ("form", "Outside Form", "workflow", "selected"),
        ("app", "Outside App", "workflow", "selected"),
        ("app", "Outside App", "table", "orders"),
    }


def test_resolve_workflow_ref_supports_path_refs_and_bare_names() -> None:
    by_path = _workflow(path="workflows/report.py", fn="run", name="report")
    by_name = _workflow(path="workflows/sync.py", name="sync_orders")
    by_id = {by_path.id: by_path, by_name.id: by_name}
    by_pathfn = {f"{by_path.path}::{by_path.function_name}": by_path}

    assert (
        SolutionDependencyWalker._resolve_workflow_ref(
            "workflows/report.py::run", by_id, by_pathfn
        )
        is by_path
    )
    assert (
        SolutionDependencyWalker._resolve_workflow_ref(
            "sync_orders", by_id, by_pathfn
        )
        is by_name
    )
    assert (
        SolutionDependencyWalker._resolve_workflow_ref(
            "missing", by_id, by_pathfn
        )
        is None
    )


def test_form_workflows_resolves_ids_path_fallback_and_dedupes() -> None:
    handler = _workflow(path="workflows/handler.py", fn="run", name="handler")
    launch = _workflow(path="workflows/launch.py", fn="main", name="launch")
    by_id = {handler.id: handler, launch.id: launch}
    by_pathfn = {f"{handler.path}::{handler.function_name}": handler}

    duplicate_form = Form(
        id=uuid.uuid4(),
        name="Intake",
        organization_id=None,
        created_by="test",
        workflow_id=str(handler.id),
        launch_workflow_id=str(handler.id),
    )
    assert SolutionDependencyWalker._form_workflows(
        duplicate_form, by_id, by_pathfn
    ) == [handler]

    portable_form = Form(
        id=uuid.uuid4(),
        name="Portable",
        organization_id=None,
        created_by="test",
        workflow_id="workflows/handler.py::run",
        launch_workflow_id=str(launch.id),
    )
    assert SolutionDependencyWalker._form_workflows(
        portable_form, by_id, by_pathfn
    ) == [handler, launch]

    fallback_form = Form(
        id=uuid.uuid4(),
        name="Fallback",
        organization_id=None,
        created_by="test",
        workflow_path="workflows/handler.py",
        workflow_function_name="run",
    )
    assert SolutionDependencyWalker._form_workflows(
        fallback_form, by_id, by_pathfn
    ) == [handler]
