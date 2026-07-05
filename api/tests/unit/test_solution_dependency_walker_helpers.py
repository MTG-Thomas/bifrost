from __future__ import annotations

import uuid

from src.models.orm.applications import Application
from src.models.orm.forms import Form
from src.models.orm.workflows import Workflow
from src.services.solutions.dependency_walker import SolutionDependencyWalker


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
