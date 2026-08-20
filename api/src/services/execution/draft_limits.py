"""Pure worker-boundary limits for immutable Workspace draft executions."""

from __future__ import annotations

import json
from typing import Any


class WorkspaceDraftOutputLimitExceeded(RuntimeError):
    """An immutable Workspace runtime exceeded its output bound."""


def enforce_draft_output_limit(context_data: dict[str, Any], result: Any) -> None:
    """Enforce output size before immutable results cross the worker boundary."""
    runtime_mode = context_data.get("runtime_mode")
    if runtime_mode not in {"workspace-canary-v1", "workspace-release-v1"}:
        return
    limit = context_data.get("runtime_max_output_bytes")
    if limit is None and runtime_mode == "workspace-canary-v1":
        limit = context_data.get("draft_max_output_bytes")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise WorkspaceDraftOutputLimitExceeded(
            "immutable Workspace runtime is missing its hard output bound"
        )
    serialized = json.dumps(
        result,
        default=str,
        ensure_ascii=False,
    ).encode("utf-8")
    if len(serialized) > limit:
        raise WorkspaceDraftOutputLimitExceeded(
            f"immutable Workspace runtime output is {len(serialized)} bytes; "
            f"limit is {limit}"
        )
