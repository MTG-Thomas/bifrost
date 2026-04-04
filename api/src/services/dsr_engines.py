"""Engine adapter abstraction for desired state resources."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass
class PlanResult:
    summary: str
    summary_json: dict


@dataclass
class ApplyResult:
    result_json: dict


class EngineAdapter(Protocol):
    async def plan(self, resource_kind: str, spec: dict) -> PlanResult: ...
    async def apply(self, resource_kind: str, spec: dict, plan_summary: dict) -> ApplyResult: ...
    async def observe(self, resource_kind: str, spec: dict) -> dict: ...
    async def destroy(self, resource_kind: str, spec: dict) -> ApplyResult: ...


class _BaseAdapter:
    engine_name: str

    async def plan(self, resource_kind: str, spec: dict) -> PlanResult:
        digest = hashlib.sha256(f"{resource_kind}:{spec}".encode()).hexdigest()[:8]
        action = str(spec.get("action", "create")).lower()
        summary_json = {
            "engine": self.engine_name,
            "resource_kind": resource_kind,
            "action": action,
            "changes": spec.get("changes", [action]),
            "fingerprint": digest,
        }
        return PlanResult(
            summary=f"{self.engine_name} plan for {resource_kind} ({action})",
            summary_json=summary_json,
        )

    async def apply(self, resource_kind: str, spec: dict, plan_summary: dict) -> ApplyResult:
        return ApplyResult(
            result_json={
                "engine": self.engine_name,
                "resource_kind": resource_kind,
                "applied": True,
                "outputs": spec.get("outputs", {}),
                "plan_fingerprint": plan_summary.get("fingerprint"),
            }
        )

    async def observe(self, resource_kind: str, spec: dict) -> dict:
        return {
            "engine": self.engine_name,
            "resource_kind": resource_kind,
            "observed": True,
            "spec": spec,
        }

    async def destroy(self, resource_kind: str, spec: dict) -> ApplyResult:
        return ApplyResult(result_json={"engine": self.engine_name, "resource_kind": resource_kind, "destroyed": True})


class OpenTofuAdapter(_BaseAdapter):
    engine_name = "tofu"


class TerraformAdapter(_BaseAdapter):
    engine_name = "terraform"


class PythonSdkAdapter(_BaseAdapter):
    engine_name = "python"


def get_engine_adapter(engine: str) -> EngineAdapter:
    if engine == "tofu":
        return OpenTofuAdapter()
    if engine == "terraform":
        return TerraformAdapter()
    if engine == "python":
        return PythonSdkAdapter()
    raise ValueError(f"Unsupported engine: {engine}")


def classify_risk(summary_json: dict) -> tuple[str, bool]:
    """Simple Phase 1 policy rules."""
    action = str(summary_json.get("action", "")).lower()
    change_blob = " ".join(str(item).lower() for item in summary_json.get("changes", []))

    if "destroy" in action or "destroy" in change_blob:
        return "high", True
    if "security" in change_blob or "network" in change_blob or "modify" in action:
        return "medium", True
    return "low", False
