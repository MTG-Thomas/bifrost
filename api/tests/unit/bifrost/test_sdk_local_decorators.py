import logging

import pytest

from bifrost import decorators


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


def test_tool_decorator_marks_workflow_as_agent_tool() -> None:
    @decorators.tool(name="lookup_user", description="Look up a user")
    def lookup(email: str) -> str:
        return email

    metadata = lookup._executable_metadata
    assert metadata.name == "lookup_user"
    assert metadata.description == "Look up a user"
    assert metadata.type == "tool"
    assert metadata.is_tool is True


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
