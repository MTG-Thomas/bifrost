"""
Manifest parser for .bifrost/metadata.yaml.

Provides Pydantic models and functions for reading, writing, and validating
the workspace manifest. The manifest declares all platform entities,
their file paths, UUIDs, org bindings, roles, and runtime config.

Stateless — no DB or S3 dependency.
"""

from __future__ import annotations

import logging

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================


class ManifestOrganization(BaseModel):
    """Organization entry in manifest."""
    id: str
    name: str


class ManifestRole(BaseModel):
    """Role entry in manifest."""
    id: str
    name: str
    organization_id: str | None = None


class ManifestWorkflow(BaseModel):
    """Workflow entry in manifest."""
    id: str
    path: str
    function_name: str
    type: str = "workflow"  # workflow | tool | data_provider
    organization_id: str | None = None
    roles: list[str] = Field(default_factory=list)  # Role UUIDs
    access_level: str = "role_based"
    endpoint_enabled: bool = False
    timeout_seconds: int = 1800
    public_endpoint: bool = False
    # Additional optional config
    category: str = "General"
    tags: list[str] = Field(default_factory=list)


class ManifestForm(BaseModel):
    """Form entry in manifest."""
    id: str
    path: str
    organization_id: str | None = None
    roles: list[str] = Field(default_factory=list)


class ManifestAgent(BaseModel):
    """Agent entry in manifest."""
    id: str
    path: str
    organization_id: str | None = None
    roles: list[str] = Field(default_factory=list)


class ManifestApp(BaseModel):
    """App entry in manifest."""
    id: str
    path: str
    organization_id: str | None = None
    roles: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    """The complete workspace manifest."""
    organizations: list[ManifestOrganization] = Field(default_factory=list)
    roles: list[ManifestRole] = Field(default_factory=list)
    workflows: dict[str, ManifestWorkflow] = Field(default_factory=dict)
    forms: dict[str, ManifestForm] = Field(default_factory=dict)
    agents: dict[str, ManifestAgent] = Field(default_factory=dict)
    apps: dict[str, ManifestApp] = Field(default_factory=dict)


# =============================================================================
# Parse / Serialize
# =============================================================================


def parse_manifest(yaml_str: str) -> Manifest:
    """Parse a YAML string into a Manifest object."""
    if not yaml_str or not yaml_str.strip():
        return Manifest()

    data = yaml.safe_load(yaml_str)
    if not data or not isinstance(data, dict):
        return Manifest()

    return Manifest(**data)


def serialize_manifest(manifest: Manifest) -> str:
    """Serialize a Manifest object to a YAML string."""
    data = manifest.model_dump(mode="json", exclude_none=False)
    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


# =============================================================================
# Validation
# =============================================================================


def validate_manifest(manifest: Manifest) -> list[str]:
    """
    Validate cross-references within the manifest.

    Checks:
    - All organization_id references point to declared organizations
    - All role references point to declared roles

    Returns a list of human-readable error strings. Empty list = valid.
    """
    errors: list[str] = []

    org_ids = {org.id for org in manifest.organizations}
    role_ids = {role.id for role in manifest.roles}

    # Check organization references
    for name, wf in manifest.workflows.items():
        if wf.organization_id and wf.organization_id not in org_ids:
            errors.append(f"Workflow '{name}' references unknown organization: {wf.organization_id}")
        for role_id in wf.roles:
            if role_id not in role_ids:
                errors.append(f"Workflow '{name}' references unknown role: {role_id}")

    for name, form in manifest.forms.items():
        if form.organization_id and form.organization_id not in org_ids:
            errors.append(f"Form '{name}' references unknown organization: {form.organization_id}")
        for role_id in form.roles:
            if role_id not in role_ids:
                errors.append(f"Form '{name}' references unknown role: {role_id}")

    for name, agent in manifest.agents.items():
        if agent.organization_id and agent.organization_id not in org_ids:
            errors.append(f"Agent '{name}' references unknown organization: {agent.organization_id}")
        for role_id in agent.roles:
            if role_id not in role_ids:
                errors.append(f"Agent '{name}' references unknown role: {role_id}")

    for name, app in manifest.apps.items():
        if app.organization_id and app.organization_id not in org_ids:
            errors.append(f"App '{name}' references unknown organization: {app.organization_id}")
        for role_id in app.roles:
            if role_id not in role_ids:
                errors.append(f"App '{name}' references unknown role: {role_id}")

    return errors


# =============================================================================
# Utilities
# =============================================================================


def get_all_entity_ids(manifest: Manifest) -> set[str]:
    """Get all entity UUIDs declared in the manifest."""
    ids: set[str] = set()
    for wf in manifest.workflows.values():
        ids.add(wf.id)
    for form in manifest.forms.values():
        ids.add(form.id)
    for agent in manifest.agents.values():
        ids.add(agent.id)
    for app in manifest.apps.values():
        ids.add(app.id)
    return ids


def get_all_paths(manifest: Manifest) -> set[str]:
    """Get all file paths declared in the manifest."""
    paths: set[str] = set()
    for wf in manifest.workflows.values():
        paths.add(wf.path)
    for form in manifest.forms.values():
        paths.add(form.path)
    for agent in manifest.agents.values():
        paths.add(agent.path)
    for app in manifest.apps.values():
        paths.add(app.path)
    return paths
