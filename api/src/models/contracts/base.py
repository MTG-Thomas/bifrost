"""
Base models and utilities for Bifrost contracts.
Includes enums, retry policies, and helper functions.
"""

import uuid
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


# ==================== ENUMS ====================


class DataProviderInputMode(str, Enum):
    """Data provider input configuration modes (T005)"""
    STATIC = "static"
    FIELD_REF = "fieldRef"
    EXPRESSION = "expression"


class IntegrationType(str, Enum):
    """Supported integration types"""
    MSGRAPH = "msgraph"
    HALOPSA = "halopsa"


# ==================== MODELS ====================


class ExecutionRetryFailure(str, Enum):
    """Execution-engine failures that may start another attempt."""

    WORKER_LOST = "worker_lost"
    SUBPROCESS_CRASH = "subprocess_crash"


class ExecutionRetryPolicy(BaseModel):
    """Versioned policy for retries after execution-engine failures."""

    version: Literal["execution-retry/v1"] = "execution-retry/v1"
    enabled: bool = False
    max_attempts: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Total attempts, including the initial execution",
    )
    retry_on: list[ExecutionRetryFailure] = Field(
        default_factory=list,
        description="Execution-engine failures eligible for another attempt",
    )


# ==================== HELPER FUNCTIONS ====================


def generate_entity_id() -> str:
    """
    Generate UUID for entity IDs.

    Returns:
        UUID string (e.g., "550e8400-e29b-41d4-a716-446655440000")
    """
    return str(uuid.uuid4())
