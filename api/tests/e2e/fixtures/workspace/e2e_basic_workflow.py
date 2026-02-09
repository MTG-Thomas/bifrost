"""
E2E Simple Greeting Workflow

Copy of platform/examples/example_basic_workflow.py with renamed workflow for E2E testing.
A basic workflow for testing form submission and execution.
"""

import logging

from bifrost import workflow, context

logger = logging.getLogger(__name__)


@workflow(
    name="e2e_simple_greeting",
    description="E2E simple greeting workflow",
    category="e2e_testing",
    tags=["e2e", "test", "greeting"]
)
async def e2e_simple_greeting(
    name: str,
    greeting_type: str = "Hello",
    include_timestamp: bool = False
) -> dict:
    """
    E2E simple greeting workflow that creates a personalized greeting.

    Args:
        name: Name to greet
        greeting_type: Type of greeting (default: "Hello")
        include_timestamp: Whether to include timestamp

    Returns:
        Dictionary with greeting message
    """
    import datetime

    greeting = f"{greeting_type}, {name}!"

    if include_timestamp:
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        greeting += f" (at {timestamp})"

    logger.info(f"Generated greeting: {greeting}")

    return {
        "greeting": greeting,
        "name": name,
        "greeting_type": greeting_type,
        "org_id": context.org_id
    }
