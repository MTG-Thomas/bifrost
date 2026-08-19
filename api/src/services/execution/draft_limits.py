"""Pure worker-boundary limits for immutable Workspace draft executions."""

from __future__ import annotations

import json
from typing import Any


class WorkspaceDraftOutputLimitExceeded(RuntimeError):
    """A draft canary returned more serialized data than its immutable bound."""


def enforce_draft_output_limit(context_data: dict[str, Any], result: Any) -> None:
    """Enforce draft output size before it crosses the worker boundary."""
    if context_data.get("runtime_mode") != "workspace-draft-v1":
        return
    limit = context_data.get("draft_max_output_bytes")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise WorkspaceDraftOutputLimitExceeded(
            "draft canary is missing its hard output bound"
        )
    serialized = json.dumps(
        result,
        default=str,
        ensure_ascii=False,
    ).encode("utf-8")
    if len(serialized) > limit:
        raise WorkspaceDraftOutputLimitExceeded(
            f"draft canary output is {len(serialized)} bytes; limit is {limit}"
        )
