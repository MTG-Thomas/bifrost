"""Drift checks for the execution-operations migration inventory."""

from pathlib import Path

from src.jobs.platform.registry import list_platform_job_definitions


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = REPOSITORY_ROOT / "docs" / "architecture" / "execution-operations.md"


def test_inventory_names_every_registered_platform_job() -> None:
    inventory = INVENTORY_PATH.read_text(encoding="utf-8")

    missing = sorted(
        job_type
        for job_type in (
            definition.job_type for definition in list_platform_job_definitions()
        )
        if f"`{job_type}`" not in inventory
    )

    assert missing == [], (
        "docs/architecture/execution-operations.md must inventory every "
        f"registered platform job; missing {missing}"
    )


def test_inventory_names_every_worker_delivery_channel() -> None:
    inventory = INVENTORY_PATH.read_text(encoding="utf-8")
    delivery_channels = {
        "workflow-executions",
        "agent-runs",
        "agent-summarization",
        "agent-summarization-backfill",
        "agent-tuning-chat",
        "package-installations",
    }

    missing = sorted(
        channel for channel in delivery_channels if f"`{channel}`" not in inventory
    )

    assert missing == [], (
        "docs/architecture/execution-operations.md must inventory every "
        f"worker delivery channel; missing {missing}"
    )
