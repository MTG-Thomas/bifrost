from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.execution import module_loader
from src.services.execution.module_loader import (
    DataProviderMetadata,
    WorkflowMetadata,
    WorkflowParameter,
)


def _cleanup_modules(*names: str) -> None:
    for name in names:
        sys.modules.pop(name, None)


def test_exec_from_db_sets_module_dunders_and_exports_function() -> None:
    module = module_loader.exec_from_db(
        "VALUE = 41\n\ndef run():\n    return VALUE + 1\n",
        "features/tickets/workflows/run_ticket.py",
        "run",
        workspace_generation="generation-1",
    )

    try:
        assert module.__name__ == "features.tickets.workflows.run_ticket"
        assert module.__file__ == "features/tickets/workflows/run_ticket.py"
        assert module.__package__ == "features.tickets.workflows"
        assert module.__workspace_generation__ == "generation-1"
        assert module.run() == 42
        assert sys.modules["features.tickets.workflows.run_ticket"] is module
    finally:
        _cleanup_modules("features.tickets.workflows.run_ticket")


def test_exec_from_db_raises_import_error_for_runtime_failure() -> None:
    with pytest.raises(ImportError, match="Failed to execute workflow from DB: boom"):
        module_loader.exec_from_db(
            "raise RuntimeError('boom')\n",
            "workflows/bad.py",
            "run",
        )


def test_load_workflow_from_db_returns_workflow_metadata() -> None:
    code = """
from src.services.execution.module_loader import WorkflowMetadata

def run():
    return "ok"

run._executable_metadata = WorkflowMetadata(name="Run Ticket", description="Run")
"""

    func, metadata, error = module_loader.load_workflow_from_db(
        code,
        "workflows/run_ticket.py",
        "run",
    )

    try:
        assert error is None
        assert func is not None
        assert func() == "ok"
        assert isinstance(metadata, WorkflowMetadata)
        assert metadata.name == "Run Ticket"
    finally:
        _cleanup_modules("workflows.run_ticket")


def test_load_workflow_from_db_converts_data_provider_metadata() -> None:
    code = """
from src.services.execution.module_loader import DataProviderMetadata

def options():
    return []

options._executable_metadata = DataProviderMetadata(
    name="Options",
    description="Load options",
    parameters=[],
    timeout_seconds=123,
)
"""

    func, metadata, error = module_loader.load_workflow_from_db(
        code,
        "providers/options.py",
        "options",
    )

    try:
        assert error is None
        assert func is not None
        assert isinstance(metadata, WorkflowMetadata)
        assert metadata.name == "Options"
        assert metadata.type == "data_provider"
        assert metadata.timeout_seconds == 123
    finally:
        _cleanup_modules("providers.options")


def test_load_workflow_from_db_reports_missing_function() -> None:
    func, metadata, error = module_loader.load_workflow_from_db(
        "def other():\n    pass\n",
        "workflows/missing.py",
        "run",
    )

    try:
        assert func is None
        assert metadata is None
        assert error == "Workflow function 'run' not found in code from workflows/missing.py"
    finally:
        _cleanup_modules("workflows.missing")


def test_load_workflow_from_db_wraps_syntax_error() -> None:
    func, metadata, error = module_loader.load_workflow_from_db(
        "def broken(:\n    pass\n",
        "workflows/broken.py",
        "run",
    )

    assert func is None
    assert metadata is None
    assert error is not None
    assert "unexpected error happened outside of your workflow" in error


@dataclass
class LegacyParameter:
    name: str
    type: str
    label: str | None = None
    required: bool = False
    default_value: object | None = None


def test_convert_parameters_preserves_new_and_converts_legacy() -> None:
    existing = WorkflowParameter(name="title", type="string", required=True)
    legacy = LegacyParameter(
        name="count",
        type="int",
        label="Count",
        required=False,
        default_value=5,
    )

    assert module_loader._convert_parameters([existing, legacy]) == [
        existing,
        WorkflowParameter(
            name="count",
            type="int",
            label="Count",
            required=False,
            default_value=5,
        ),
    ]


def test_convert_workflow_metadata_maps_legacy_fields() -> None:
    legacy = SimpleNamespace(
        name="Sync Tickets",
        description="Sync tickets from PSA",
        category="PSA",
        tags=["ticket"],
        tool=True,
        timeout_seconds=60,
        source_file_path="workflows/sync.py",
        parameters=[LegacyParameter(name="dry_run", type="bool")],
        function="callable-marker",
        execution_mode="async",
        retry_policy={"max": 2},
        schedule="0 * * * *",
        endpoint_enabled=True,
        allowed_methods=["POST", "GET"],
        disable_global_key=True,
        public_endpoint=True,
        tool_description="Sync tickets",
        time_saved=15,
        value=25.5,
    )

    converted = module_loader._convert_workflow_metadata(legacy)

    assert converted.name == "Sync Tickets"
    assert converted.type == "tool"
    assert converted.category == "PSA"
    assert converted.tags == ["ticket"]
    assert converted.timeout_seconds == 60
    assert converted.source_file_path == "workflows/sync.py"
    assert converted.parameters == [WorkflowParameter(name="dry_run", type="bool")]
    assert converted.function == "callable-marker"
    assert converted.execution_mode == "async"
    assert converted.retry_policy == {"max": 2}
    assert converted.schedule == "0 * * * *"
    assert converted.endpoint_enabled is True
    assert converted.allowed_methods == ["POST", "GET"]
    assert converted.disable_global_key is True
    assert converted.public_endpoint is True
    assert converted.tool_description == "Sync tickets"
    assert converted.time_saved == 15
    assert converted.value == 25.5


def test_convert_data_provider_metadata_maps_legacy_fields() -> None:
    legacy = SimpleNamespace(
        name="Statuses",
        description="Ticket statuses",
        category="PSA",
        tags=["ticket"],
        timeout_seconds=20,
        source_file_path="providers/statuses.py",
        parameters=[LegacyParameter(name="client_id", type="string")],
        function="callable-marker",
        cache_ttl_seconds=900,
        source="workspace",
    )

    converted = module_loader._convert_data_provider_metadata(legacy)

    assert converted == DataProviderMetadata(
        name="Statuses",
        description="Ticket statuses",
        category="PSA",
        tags=["ticket"],
        type="data_provider",
        timeout_seconds=20,
        source_file_path="providers/statuses.py",
        parameters=[WorkflowParameter(name="client_id", type="string")],
        function="callable-marker",
        cache_ttl_seconds=900,
        source="workspace",
    )


def test_scan_all_workflows_filters_private_packages_and_data_providers(tmp_path) -> None:
    (tmp_path / "visible.py").write_text(
        "from src.services.execution.module_loader import WorkflowMetadata, DataProviderMetadata\n"
        "def workflow_func():\n"
        "    return 'workflow'\n"
        "workflow_func._executable_metadata = WorkflowMetadata(name='Visible', description='')\n"
        "def provider_func():\n"
        "    return []\n"
        "provider_func._executable_metadata = DataProviderMetadata(name='Provider', description='')\n"
    )
    (tmp_path / "_private.py").write_text(
        "raise RuntimeError('private files should be skipped')\n"
    )
    packages = tmp_path / ".packages"
    packages.mkdir()
    (packages / "ignored.py").write_text("raise RuntimeError('packages should be skipped')\n")

    with patch("src.services.execution.module_loader.get_workspace_paths", return_value=[tmp_path]):
        workflows = module_loader.scan_all_workflows()

    _cleanup_modules("visible")
    assert [workflow.name for workflow in workflows] == ["Visible"]


def test_load_workflow_finds_by_metadata_name(tmp_path) -> None:
    (tmp_path / "workflow_file.py").write_text(
        "from src.services.execution.module_loader import WorkflowMetadata\n"
        "def run():\n"
        "    return 42\n"
        "run._executable_metadata = WorkflowMetadata(name='Answer', description='')\n"
    )

    with patch("src.services.execution.module_loader.get_workspace_paths", return_value=[tmp_path]):
        loaded = module_loader.load_workflow("Answer")

    try:
        assert loaded is not None
        func, metadata = loaded
        assert func() == 42
        assert metadata.name == "Answer"
    finally:
        _cleanup_modules("workflow_file")


def test_scan_all_data_providers_and_load_data_provider(tmp_path) -> None:
    (tmp_path / "providers.py").write_text(
        "from src.services.execution.module_loader import WorkflowMetadata, DataProviderMetadata\n"
        "def workflow_func():\n"
        "    return 'workflow'\n"
        "workflow_func._executable_metadata = WorkflowMetadata(name='Workflow', description='')\n"
        "def statuses():\n"
        "    return ['open']\n"
        "statuses._executable_metadata = DataProviderMetadata(name='Statuses', description='')\n"
    )

    with patch("src.services.execution.module_loader.get_workspace_paths", return_value=[tmp_path]):
        providers = module_loader.scan_all_data_providers()
        loaded = module_loader.load_data_provider("Statuses")

    try:
        assert [provider.name for provider in providers] == ["Statuses"]
        assert loaded is not None
        func, metadata = loaded
        assert func() == ["open"]
        assert metadata.name == "Statuses"
    finally:
        _cleanup_modules("providers")
