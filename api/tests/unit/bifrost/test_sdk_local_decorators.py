import logging

import pytest
from bifrost import decorators
from shared.workspace_effects import WorkflowBounds, WorkflowEffect

pytestmark = pytest.mark.unit


def test_workflow_metadata_keeps_tool_fields_in_sync() -> None:
    from_flag = decorators.WorkflowMetadata(name="flagged", is_tool=True)
    from_type = decorators.WorkflowMetadata(name="typed", type="tool")

    assert from_flag.type == "tool"
    assert from_flag.is_tool is True
    assert from_type.type == "tool"
    assert from_type.is_tool is True


def test_workflow_decorator_attaches_docstring_metadata_and_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bifrost.decorators"):

        @decorators.workflow(category="Ops", tags=["daily"], timeout=30)
        async def daily_check(customer: str) -> dict:
            """Run the daily customer check."""
            return {"customer": customer}

    metadata = daily_check._executable_metadata
    assert metadata.name == "daily_check"
    assert metadata.description == "Run the daily customer check."
    assert metadata.category == "Ops"
    assert metadata.tags == ["daily"]
    assert metadata.type == "workflow"
    assert metadata.is_tool is False
    assert "Unknown @workflow parameters ignored: timeout" in caplog.text


def test_workflow_decorator_supports_direct_application() -> None:
    @decorators.workflow
    def direct_workflow() -> None:
        pass

    metadata = direct_workflow._executable_metadata
    assert metadata.name == "direct_workflow"
    assert metadata.description == ""
    assert metadata.type == "workflow"


def test_workflow_decorator_attaches_typed_promotion_declarations() -> None:
    @decorators.workflow(
        effects=[
            {"kind": "integration.read", "target": "Microsoft Graph"},
            WorkflowEffect(kind="bifrost.read", target="organizations"),
        ],
        enforced_bounds={"max_records_read": 250, "max_pages": 5},
        requested_bounds=WorkflowBounds(max_duration_seconds=45),
    )
    def bounded_read() -> None:
        pass

    metadata = bounded_read._executable_metadata
    assert metadata.effects == (
        WorkflowEffect(kind="integration.read", target="Microsoft Graph"),
        WorkflowEffect(kind="bifrost.read", target="organizations"),
    )
    assert metadata.enforced_bounds == WorkflowBounds(
        max_records_read=250,
        max_pages=5,
    )
    assert metadata.requested_bounds == WorkflowBounds(max_duration_seconds=45)


def test_workflow_decorator_rejects_malformed_promotion_declarations() -> None:
    with pytest.raises(ValueError, match="requires an integration target"):

        @decorators.workflow(effects=[{"kind": "integration.write"}])
        def malformed_effect() -> None:
            pass

    with pytest.raises(ValueError, match="unknown fields"):

        @decorators.workflow(requested_bounds={"timeout": 30})
        def malformed_bounds() -> None:
            pass


def test_tool_decorator_marks_workflow_as_agent_tool() -> None:
    @decorators.tool(name="lookup_user", description="Look up a user")
    def lookup(email: str) -> str:
        return email

    metadata = lookup._executable_metadata
    assert metadata.name == "lookup_user"
    assert metadata.description == "Look up a user"
    assert metadata.type == "tool"
    assert metadata.is_tool is True


def test_tool_decorator_forwards_promotion_declarations() -> None:
    @decorators.tool(
        effects=[{"kind": "bifrost.read"}],
        requested_bounds={"max_output_bytes": 4096},
    )
    def bounded_tool() -> None:
        pass

    metadata = bounded_tool._executable_metadata
    assert metadata.effects == (WorkflowEffect(kind="bifrost.read"),)
    assert metadata.requested_bounds == WorkflowBounds(max_output_bytes=4096)


def test_data_provider_decorator_attaches_provider_metadata_and_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bifrost.decorators"):

        @decorators.data_provider(name="departments", cache_ttl=60)
        def department_options() -> list[str]:
            """Department options."""
            return ["Engineering"]

    metadata = department_options._executable_metadata
    assert metadata.name == "departments"
    assert metadata.description == "Department options."
    assert metadata.category == "General"
    assert metadata.tags == []
    assert metadata.type == "data_provider"
    assert "Unknown @data_provider parameters ignored: cache_ttl" in caplog.text


def test_data_provider_decorator_supports_direct_application() -> None:
    @decorators.data_provider
    def direct_provider() -> list[str]:
        return ["A"]

    metadata = direct_provider._executable_metadata
    assert metadata.name == "direct_provider"
    assert metadata.description == ""
    assert metadata.type == "data_provider"


def test_data_provider_decorator_attaches_promotion_declarations() -> None:
    @decorators.data_provider(
        effects=[],
        enforced_bounds={"max_records_read": 100},
    )
    def bounded_provider() -> list[str]:
        return []

    metadata = bounded_provider._executable_metadata
    assert metadata.effects == ()
    assert metadata.enforced_bounds == WorkflowBounds(max_records_read=100)
