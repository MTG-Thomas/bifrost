"""Coverage for solution capture helper functions."""

from enum import Enum
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.services.solutions import capture


PROVIDER_ID = UUID("11111111-1111-1111-1111-111111111111")


class _Status(Enum):
    READY = "ready"


def test_enum_value_unwraps_enum_values_and_leaves_scalars():
    assert capture._enum_value(_Status.READY) == "ready"
    assert capture._enum_value("plain") == "plain"
    assert capture._enum_value(None) is None


def test_drop_none_preserves_falsey_non_none_values():
    assert capture._drop_none(
        {
            "missing": None,
            "false": False,
            "zero": 0,
            "empty": "",
            "items": [],
        }
    ) == {
        "false": False,
        "zero": 0,
        "empty": "",
        "items": [],
    }


def test_ignore_spec_matches_cli_secret_cache_and_build_outputs():
    spec = capture._ignore_spec()

    assert spec.match_file(".env")
    assert spec.match_file("__pycache__/workflow.cpython-312.pyc")
    assert spec.match_file("node_modules/react/index.js")
    assert not spec.match_file("workflows/ticket_triage.py")


def test_capture_selector_and_result_defaults_are_independent():
    selectors = capture.SolutionCaptureSelectors(
        workflows=[UUID("22222222-2222-2222-2222-222222222222")],
        tables=[],
        apps=[],
        forms=[],
        agents=[],
        claims=[],
        configs=["api_url"],
    )
    other = capture.SolutionCaptureSelectors(
        workflows=[],
        tables=[],
        apps=[],
        forms=[],
        agents=[],
        claims=[],
        configs=[],
    )
    result = capture.SolutionCaptureResult(
        workflows_captured=3,
        config_declarations_captured=1,
    )

    selectors.events.append(UUID("33333333-3333-3333-3333-333333333333"))

    assert other.events == []
    assert result.workflows_captured == 3
    assert result.config_declarations_captured == 1
    assert result.events_captured == 0


def test_form_field_entry_drops_none_and_stringifies_provider_id():
    field = SimpleNamespace(
        name="ticket_id",
        label="Ticket",
        type="text",
        required=True,
        position=2,
        placeholder=None,
        help_text="Paste the ticket ID",
        default_value=None,
        options=[],
        data_provider_id=PROVIDER_ID,
        data_provider_inputs={"query": "$value"},
        visibility_expression=None,
        validation={"minLength": 1},
        allowed_types=None,
        multiple=False,
        max_size_mb=None,
        content="Markdown help",
        allow_as_query_param=True,
        auto_fill=False,
    )

    assert capture.SolutionCaptureService._form_field_entry(field) == {
        "name": "ticket_id",
        "label": "Ticket",
        "type": "text",
        "required": True,
        "position": 2,
        "help_text": "Paste the ticket ID",
        "options": [],
        "data_provider_id": str(PROVIDER_ID),
        "data_provider_inputs": {"query": "$value"},
        "validation": {"minLength": 1},
        "multiple": False,
        "content": "Markdown help",
        "allow_as_query_param": True,
        "auto_fill": False,
    }


class _Repo:
    def __init__(self, files):
        self.files = files

    async def read(self, path):
        value = self.files[path]
        if isinstance(value, Exception):
            raise value
        return value

    async def list(self, prefix):
        return [path for path in self.files if path.startswith(prefix)]


@pytest.mark.asyncio
async def test_python_files_reads_unique_workflow_sources_and_skips_missing():
    repo = _Repo(
        {
            "workflows/a.py": b"def run():\n    return 'a'\n",
            "workflows/missing.py": FileNotFoundError("gone"),
        }
    )
    service = capture.SolutionCaptureService(db=None, repo=repo)

    files = await service._python_files(
        [
            {"path": "workflows/a.py"},
            {"path": "workflows/a.py"},
            {"path": "workflows/missing.py"},
        ],
        include_imports=False,
    )

    assert files == {"workflows/a.py": "def run():\n    return 'a'\n"}


@pytest.mark.asyncio
async def test_python_files_can_vendor_imported_shared_modules(monkeypatch):
    repo = _Repo({"workflows/a.py": b"from modules.shared import helper\n"})
    service = capture.SolutionCaptureService(db=None, repo=repo)

    async def fake_vendor_shared_deps(files, read_fn, solution_local_roots):
        assert files == {"workflows/a.py": "from modules.shared import helper\n"}
        assert await read_fn("missing.py") is None
        assert solution_local_roots == frozenset({"workflows"})
        return {"modules/shared.py": "def helper():\n    return 1\n"}

    monkeypatch.setattr(capture, "vendor_shared_deps", fake_vendor_shared_deps)

    assert await service._python_files(
        [{"path": "workflows/a.py"}],
        include_imports=True,
    ) == {
        "workflows/a.py": "from modules.shared import helper\n",
        "modules/shared.py": "def helper():\n    return 1\n",
    }


@pytest.mark.asyncio
async def test_app_source_files_filters_ignored_paths_and_splits_binary():
    repo = _Repo(
        {
            "apps/desk/src/App.tsx": b"export function App() { return null }\n",
            "apps/desk/public/logo.png": b"\x89PNG\r\n\x1a\n",
            "apps/desk/node_modules/react/index.js": b"ignored",
            "apps/desk/.env": b"SECRET=value",
            "apps/desk/dist/app.js": b"ignored build output",
        }
    )
    service = capture.SolutionCaptureService(db=None, repo=repo)
    app = SimpleNamespace(repo_prefix="apps/desk/")

    src_files, bin_files = await service._app_source_files(app)

    assert src_files == {"src/App.tsx": "export function App() { return null }\n"}
    assert bin_files == {"public/logo.png": "iVBORw0KGgo="}
