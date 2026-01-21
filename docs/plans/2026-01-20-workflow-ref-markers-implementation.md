# Workflow Ref Markers Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace scattered field serializers/validators with `Annotated` type markers for workflow reference transformation during GitHub sync.

**Architecture:** Use `WorkflowRef()` marker with `Annotated` types. Transform via pure functions at sync boundary (not during every model_dump). Auto-generate `_export` metadata from model introspection.

**Tech Stack:** Pydantic v2, Python typing (Annotated, get_origin, get_args)

---

## Task 1: Create refs.py Module with Marker Classes

**Files:**
- Create: `api/src/models/contracts/refs.py`
- Test: `api/tests/unit/models/contracts/test_refs.py`

**Step 1: Create the refs.py module**

```python
"""
Portable reference markers for Pydantic models.

Use with Annotated to mark fields that should be transformed
during GitHub sync (UUID ↔ path::function_name).

Example:
    class FormPublic(BaseModel):
        workflow_id: Annotated[str | None, WorkflowRef()] = None
"""

from dataclasses import dataclass
from typing import Annotated, Any, get_args, get_origin

from pydantic import BaseModel


@dataclass(frozen=True)
class WorkflowRef:
    """Marks a field as a workflow reference (UUID ↔ path::function_name)."""

    pass


def get_workflow_ref_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    """
    Get all field paths marked with WorkflowRef in a model.

    Handles nested models recursively, returning dot-notation paths.
    For lists of models, uses '*' wildcard notation.

    Args:
        model: The Pydantic model class to introspect
        prefix: Current path prefix for recursive calls

    Returns:
        List of field paths like ["workflow_id", "form_schema.fields.*.data_provider_id"]
    """
    paths: list[str] = []

    for field_name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        current_path = f"{prefix}.{field_name}" if prefix else field_name

        if annotation is None:
            continue

        # Unwrap Optional/Union types to get the base type
        origin = get_origin(annotation)

        # Handle Annotated types
        if origin is Annotated:
            args = get_args(annotation)
            base_type = args[0]
            metadata = args[1:]

            # Check if this field has WorkflowRef marker
            if any(isinstance(m, WorkflowRef) for m in metadata):
                paths.append(current_path)
                continue

            # Check if base type is a model (for nested introspection)
            base_origin = get_origin(base_type)
            if base_origin is list:
                inner = get_args(base_type)[0] if get_args(base_type) else None
                if inner and isinstance(inner, type) and issubclass(inner, BaseModel):
                    nested = get_workflow_ref_paths(inner, f"{current_path}.*")
                    paths.extend(nested)
            elif isinstance(base_type, type) and issubclass(base_type, BaseModel):
                nested = get_workflow_ref_paths(base_type, current_path)
                paths.extend(nested)
            continue

        # Handle list[Model] directly (not in Annotated)
        if origin is list:
            inner_args = get_args(annotation)
            inner = inner_args[0] if inner_args else None
            if inner and isinstance(inner, type) and issubclass(inner, BaseModel):
                nested = get_workflow_ref_paths(inner, f"{current_path}.*")
                paths.extend(nested)
            continue

        # Handle nested BaseModel directly
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            nested = get_workflow_ref_paths(annotation, current_path)
            paths.extend(nested)

    return paths


def transform_refs_for_export(
    data: dict[str, Any],
    model: type[BaseModel],
    uuid_to_ref: dict[str, str],
) -> dict[str, Any]:
    """
    Transform all WorkflowRef fields from UUID to portable ref.

    Args:
        data: Dict from model_dump()
        model: The Pydantic model class
        uuid_to_ref: UUID -> "path::function_name" mapping

    Returns:
        Transformed dict with portable refs (new dict, does not mutate input)
    """
    result = data.copy()

    for field_name, field_info in model.model_fields.items():
        if field_name not in result:
            continue

        annotation = field_info.annotation
        value = result[field_name]

        if value is None:
            continue

        origin = get_origin(annotation)

        # Handle Annotated types
        if origin is Annotated:
            args = get_args(annotation)
            base_type = args[0]
            metadata = args[1:]

            # Direct WorkflowRef field
            if any(isinstance(m, WorkflowRef) for m in metadata):
                if isinstance(value, str) and value in uuid_to_ref:
                    result[field_name] = uuid_to_ref[value]
                continue

            # Nested model in Annotated
            base_origin = get_origin(base_type)
            if base_origin is list:
                inner = get_args(base_type)[0] if get_args(base_type) else None
                if inner and isinstance(inner, type) and issubclass(inner, BaseModel):
                    result[field_name] = [
                        transform_refs_for_export(item, inner, uuid_to_ref)
                        if isinstance(item, dict) else item
                        for item in value
                    ]
            elif isinstance(base_type, type) and issubclass(base_type, BaseModel):
                result[field_name] = transform_refs_for_export(value, base_type, uuid_to_ref)
            continue

        # Handle list[Model] directly
        if origin is list and isinstance(value, list):
            inner_args = get_args(annotation)
            inner = inner_args[0] if inner_args else None
            if inner and isinstance(inner, type) and issubclass(inner, BaseModel):
                result[field_name] = [
                    transform_refs_for_export(item, inner, uuid_to_ref)
                    if isinstance(item, dict) else item
                    for item in value
                ]
            continue

        # Handle nested BaseModel directly
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            result[field_name] = transform_refs_for_export(value, annotation, uuid_to_ref)

    return result


def transform_refs_for_import(
    data: dict[str, Any],
    model: type[BaseModel],
    ref_to_uuid: dict[str, str],
) -> dict[str, Any]:
    """
    Transform all WorkflowRef fields from portable ref to UUID.

    Args:
        data: Dict to be passed to model_validate()
        model: The Pydantic model class
        ref_to_uuid: "path::function_name" -> UUID mapping

    Returns:
        Transformed dict with UUIDs (new dict, does not mutate input)
    """
    result = data.copy()

    for field_name, field_info in model.model_fields.items():
        if field_name not in result:
            continue

        annotation = field_info.annotation
        value = result[field_name]

        if value is None:
            continue

        origin = get_origin(annotation)

        # Handle Annotated types
        if origin is Annotated:
            args = get_args(annotation)
            base_type = args[0]
            metadata = args[1:]

            # Direct WorkflowRef field
            if any(isinstance(m, WorkflowRef) for m in metadata):
                if isinstance(value, str) and "::" in value:
                    result[field_name] = ref_to_uuid.get(value, value)
                continue

            # Nested model in Annotated
            base_origin = get_origin(base_type)
            if base_origin is list:
                inner = get_args(base_type)[0] if get_args(base_type) else None
                if inner and isinstance(inner, type) and issubclass(inner, BaseModel):
                    result[field_name] = [
                        transform_refs_for_import(item, inner, ref_to_uuid)
                        if isinstance(item, dict) else item
                        for item in value
                    ]
            elif isinstance(base_type, type) and issubclass(base_type, BaseModel):
                result[field_name] = transform_refs_for_import(value, base_type, ref_to_uuid)
            continue

        # Handle list[Model] directly
        if origin is list and isinstance(value, list):
            inner_args = get_args(annotation)
            inner = inner_args[0] if inner_args else None
            if inner and isinstance(inner, type) and issubclass(inner, BaseModel):
                result[field_name] = [
                    transform_refs_for_import(item, inner, ref_to_uuid)
                    if isinstance(item, dict) else item
                    for item in value
                ]
            continue

        # Handle nested BaseModel directly
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            result[field_name] = transform_refs_for_import(value, annotation, ref_to_uuid)

    return result
```

**Step 2: Run pyright to verify no type errors**

Run: `cd api && pyright src/models/contracts/refs.py`
Expected: 0 errors

**Step 3: Create unit tests**

Create `api/tests/unit/models/contracts/test_refs.py`:

```python
"""Tests for WorkflowRef marker and transform functions."""

import pytest
from typing import Annotated

from pydantic import BaseModel, Field

from src.models.contracts.refs import (
    WorkflowRef,
    get_workflow_ref_paths,
    transform_refs_for_export,
    transform_refs_for_import,
)


# Test models
class InnerModel(BaseModel):
    data_provider_id: Annotated[str | None, WorkflowRef()] = None
    name: str = ""


class OuterModel(BaseModel):
    workflow_id: Annotated[str | None, WorkflowRef()] = None
    launch_workflow_id: Annotated[str | None, WorkflowRef()] = None
    nested: InnerModel | None = None
    items: list[InnerModel] = Field(default_factory=list)
    title: str = ""


class ModelWithList(BaseModel):
    tool_ids: Annotated[list[str], WorkflowRef()] = Field(default_factory=list)
    name: str = ""


class TestGetWorkflowRefPaths:
    def test_simple_fields(self):
        """Direct WorkflowRef fields are detected."""
        paths = get_workflow_ref_paths(OuterModel)
        assert "workflow_id" in paths
        assert "launch_workflow_id" in paths

    def test_nested_model(self):
        """Nested WorkflowRef fields use dot notation."""
        paths = get_workflow_ref_paths(OuterModel)
        assert "nested.data_provider_id" in paths

    def test_list_of_models(self):
        """List of models uses wildcard notation."""
        paths = get_workflow_ref_paths(OuterModel)
        assert "items.*.data_provider_id" in paths

    def test_non_ref_fields_excluded(self):
        """Non-WorkflowRef fields are not included."""
        paths = get_workflow_ref_paths(OuterModel)
        assert "title" not in paths
        assert "nested.name" not in paths

    def test_list_field_with_marker(self):
        """List field with WorkflowRef marker is detected."""
        paths = get_workflow_ref_paths(ModelWithList)
        assert "tool_ids" in paths


class TestTransformRefsForExport:
    def test_simple_field(self):
        """UUIDs are transformed to portable refs."""
        data = {"workflow_id": "uuid-123", "title": "Test"}
        uuid_to_ref = {"uuid-123": "workflows/test.py::my_workflow"}
        result = transform_refs_for_export(data, OuterModel, uuid_to_ref)
        assert result["workflow_id"] == "workflows/test.py::my_workflow"
        assert result["title"] == "Test"

    def test_nested_model(self):
        """Nested model fields are transformed."""
        data = {
            "workflow_id": None,
            "nested": {"data_provider_id": "uuid-456", "name": "Inner"},
            "title": "Test",
        }
        uuid_to_ref = {"uuid-456": "workflows/dp.py::provider"}
        result = transform_refs_for_export(data, OuterModel, uuid_to_ref)
        assert result["nested"]["data_provider_id"] == "workflows/dp.py::provider"

    def test_list_of_models(self):
        """List of models has all items transformed."""
        data = {
            "workflow_id": None,
            "items": [
                {"data_provider_id": "uuid-1", "name": "A"},
                {"data_provider_id": "uuid-2", "name": "B"},
            ],
            "title": "Test",
        }
        uuid_to_ref = {
            "uuid-1": "workflows/a.py::func_a",
            "uuid-2": "workflows/b.py::func_b",
        }
        result = transform_refs_for_export(data, OuterModel, uuid_to_ref)
        assert result["items"][0]["data_provider_id"] == "workflows/a.py::func_a"
        assert result["items"][1]["data_provider_id"] == "workflows/b.py::func_b"

    def test_unknown_uuid_unchanged(self):
        """UUIDs not in map are left unchanged."""
        data = {"workflow_id": "unknown-uuid", "title": "Test"}
        uuid_to_ref = {"other-uuid": "workflows/other.py::func"}
        result = transform_refs_for_export(data, OuterModel, uuid_to_ref)
        assert result["workflow_id"] == "unknown-uuid"

    def test_does_not_mutate_input(self):
        """Original data is not modified."""
        data = {"workflow_id": "uuid-123", "title": "Test"}
        uuid_to_ref = {"uuid-123": "workflows/test.py::my_workflow"}
        transform_refs_for_export(data, OuterModel, uuid_to_ref)
        assert data["workflow_id"] == "uuid-123"


class TestTransformRefsForImport:
    def test_simple_field(self):
        """Portable refs are transformed to UUIDs."""
        data = {"workflow_id": "workflows/test.py::my_workflow", "title": "Test"}
        ref_to_uuid = {"workflows/test.py::my_workflow": "uuid-123"}
        result = transform_refs_for_import(data, OuterModel, ref_to_uuid)
        assert result["workflow_id"] == "uuid-123"

    def test_nested_model(self):
        """Nested model refs are transformed."""
        data = {
            "workflow_id": None,
            "nested": {"data_provider_id": "workflows/dp.py::provider", "name": "Inner"},
            "title": "Test",
        }
        ref_to_uuid = {"workflows/dp.py::provider": "uuid-456"}
        result = transform_refs_for_import(data, OuterModel, ref_to_uuid)
        assert result["nested"]["data_provider_id"] == "uuid-456"

    def test_list_of_models(self):
        """List of models has all items transformed."""
        data = {
            "workflow_id": None,
            "items": [
                {"data_provider_id": "workflows/a.py::func_a", "name": "A"},
                {"data_provider_id": "workflows/b.py::func_b", "name": "B"},
            ],
            "title": "Test",
        }
        ref_to_uuid = {
            "workflows/a.py::func_a": "uuid-1",
            "workflows/b.py::func_b": "uuid-2",
        }
        result = transform_refs_for_import(data, OuterModel, ref_to_uuid)
        assert result["items"][0]["data_provider_id"] == "uuid-1"
        assert result["items"][1]["data_provider_id"] == "uuid-2"

    def test_unresolved_ref_unchanged(self):
        """Refs not in map are left unchanged."""
        data = {"workflow_id": "workflows/unknown.py::func", "title": "Test"}
        ref_to_uuid = {"workflows/other.py::func": "uuid-123"}
        result = transform_refs_for_import(data, OuterModel, ref_to_uuid)
        assert result["workflow_id"] == "workflows/unknown.py::func"

    def test_uuid_unchanged(self):
        """Already-UUID values are left unchanged."""
        data = {"workflow_id": "uuid-123", "title": "Test"}
        ref_to_uuid = {"workflows/test.py::func": "uuid-456"}
        result = transform_refs_for_import(data, OuterModel, ref_to_uuid)
        assert result["workflow_id"] == "uuid-123"


class TestRoundTrip:
    def test_export_then_import_restores_uuids(self):
        """Export then import returns original UUIDs."""
        original = {
            "workflow_id": "uuid-123",
            "launch_workflow_id": "uuid-456",
            "nested": {"data_provider_id": "uuid-789", "name": "Nested"},
            "items": [
                {"data_provider_id": "uuid-aaa", "name": "A"},
            ],
            "title": "Test",
        }
        uuid_to_ref = {
            "uuid-123": "workflows/main.py::main",
            "uuid-456": "workflows/launch.py::launch",
            "uuid-789": "workflows/dp.py::provider",
            "uuid-aaa": "workflows/a.py::func_a",
        }
        ref_to_uuid = {v: k for k, v in uuid_to_ref.items()}

        exported = transform_refs_for_export(original, OuterModel, uuid_to_ref)
        imported = transform_refs_for_import(exported, OuterModel, ref_to_uuid)

        assert imported["workflow_id"] == "uuid-123"
        assert imported["launch_workflow_id"] == "uuid-456"
        assert imported["nested"]["data_provider_id"] == "uuid-789"
        assert imported["items"][0]["data_provider_id"] == "uuid-aaa"
```

**Step 4: Run the tests**

Run: `./test.sh tests/unit/models/contracts/test_refs.py -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add api/src/models/contracts/refs.py api/tests/unit/models/contracts/test_refs.py
git commit -m "$(cat <<'EOF'
feat(refs): add WorkflowRef marker and transform functions

Introduces a declarative approach to workflow reference transformation:
- WorkflowRef() marker class for Annotated types
- get_workflow_ref_paths() introspects models for ref fields
- transform_refs_for_export() converts UUIDs to portable refs
- transform_refs_for_import() converts portable refs to UUIDs

This replaces scattered @field_serializer/@field_validator decorators
with a single source of truth for which fields need transformation.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add WorkflowRef Markers to FormField Model

**Files:**
- Modify: `api/src/models/contracts/forms.py`

**Step 1: Add import and marker to FormField.data_provider_id**

In `forms.py`, add the import at the top:

```python
from typing import Annotated
from src.models.contracts.refs import WorkflowRef
```

Change `FormField.data_provider_id` from:
```python
data_provider_id: str | None = None
```
to:
```python
data_provider_id: Annotated[str | None, WorkflowRef()] = None
```

**Step 2: Run type check**

Run: `cd api && pyright src/models/contracts/forms.py`
Expected: 0 errors

**Step 3: Run existing form tests**

Run: `./test.sh tests/unit/models/test_forms.py -v`
Expected: All tests pass (marker doesn't affect normal validation)

**Step 4: Commit**

```bash
git add api/src/models/contracts/forms.py
git commit -m "$(cat <<'EOF'
feat(forms): add WorkflowRef marker to FormField.data_provider_id

Marks the field for automatic transformation during GitHub sync.
Normal Pydantic validation is unaffected.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add WorkflowRef Markers to FormPublic Model

**Files:**
- Modify: `api/src/models/contracts/forms.py`

**Step 1: Add markers to workflow_id and launch_workflow_id**

Change in `FormPublic`:
```python
workflow_id: str | None = None
launch_workflow_id: str | None = None
```
to:
```python
workflow_id: Annotated[str | None, WorkflowRef()] = None
launch_workflow_id: Annotated[str | None, WorkflowRef()] = None
```

**Step 2: Add Field(exclude=True) for no-export fields**

Change these fields in `FormPublic`:
```python
created_at: datetime | None = None
updated_at: datetime | None = None
organization_id: str | None = None
access_level: FormAccessLevel | None = None
```
to:
```python
created_at: datetime | None = Field(default=None, exclude=True)
updated_at: datetime | None = Field(default=None, exclude=True)
organization_id: str | None = Field(default=None, exclude=True)
access_level: FormAccessLevel | None = Field(default=None, exclude=True)
```

**Step 3: Run type check and tests**

Run: `cd api && pyright src/models/contracts/forms.py`
Expected: 0 errors

Run: `./test.sh tests/unit/models/test_forms.py -v`
Expected: All tests pass

**Step 4: Commit**

```bash
git add api/src/models/contracts/forms.py
git commit -m "$(cat <<'EOF'
feat(forms): add WorkflowRef markers and Field(exclude=True) to FormPublic

- workflow_id and launch_workflow_id marked with WorkflowRef()
- created_at, updated_at, organization_id, access_level use exclude=True

This replaces the manual exclude={...} set in serialization.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add WorkflowRef Markers to AgentPublic Model

**Files:**
- Modify: `api/src/models/contracts/agents.py`

**Step 1: Add import and marker to tool_ids**

In `agents.py`, add the import:

```python
from typing import Annotated
from src.models.contracts.refs import WorkflowRef
```

Change `AgentPublic.tool_ids` from:
```python
tool_ids: list[str] = Field(default_factory=list)
```
to:
```python
tool_ids: Annotated[list[str], WorkflowRef()] = Field(default_factory=list)
```

**Step 2: Add Field(exclude=True) for no-export fields**

Change these fields in `AgentPublic`:
```python
created_at: datetime | None = None
updated_at: datetime | None = None
organization_id: str | None = None
access_level: AgentAccessLevel | None = None
created_by: str | None = None
is_system: bool = False
```
to:
```python
created_at: datetime | None = Field(default=None, exclude=True)
updated_at: datetime | None = Field(default=None, exclude=True)
organization_id: str | None = Field(default=None, exclude=True)
access_level: AgentAccessLevel | None = Field(default=None, exclude=True)
created_by: str | None = Field(default=None, exclude=True)
is_system: bool = Field(default=False, exclude=True)
```

**Step 3: Run type check and tests**

Run: `cd api && pyright src/models/contracts/agents.py`
Expected: 0 errors

Run: `./test.sh tests/unit/models/test_agents.py -v`
Expected: All tests pass

**Step 4: Commit**

```bash
git add api/src/models/contracts/agents.py
git commit -m "$(cat <<'EOF'
feat(agents): add WorkflowRef marker and Field(exclude=True) to AgentPublic

- tool_ids marked with WorkflowRef() for list transformation
- No-export fields use exclude=True

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Update Form Indexer to Use New Transform Functions

**Files:**
- Modify: `api/src/services/file_storage/indexers/form.py`

**Step 1: Update imports**

Add:
```python
from src.models.contracts.refs import (
    get_workflow_ref_paths,
    transform_refs_for_export,
    transform_refs_for_import,
)
```

**Step 2: Update _serialize_form_to_json**

Replace the function body:

```python
def _serialize_form_to_json(
    form: Form,
    workflow_map: dict[str, str] | None = None
) -> bytes:
    """
    Serialize a Form to JSON bytes using Pydantic model_dump.

    Uses FormPublic.model_dump() with exclude=True fields auto-excluded.
    Transforms workflow refs via transform_refs_for_export().

    Args:
        form: Form ORM instance with fields relationship loaded
        workflow_map: Optional mapping of workflow UUID → portable ref.
                      If provided, workflow references are transformed.

    Returns:
        JSON serialized as UTF-8 bytes
    """
    form_public = FormPublic.model_validate(form)

    # model_dump respects Field(exclude=True) automatically
    form_data = form_public.model_dump(mode="json", exclude_none=True)

    # Transform refs if we have a workflow map
    if workflow_map:
        form_data = transform_refs_for_export(form_data, FormPublic, workflow_map)
        form_data["_export"] = {
            "workflow_refs": get_workflow_ref_paths(FormPublic),
            "version": "1.0",
        }

    return json.dumps(form_data, indent=2).encode("utf-8")
```

**Step 3: Update index_form to use transform_refs_for_import**

Find the section that handles `_export` metadata (around line 150-160) and update:

```python
# Check for portable refs from export and resolve them to UUIDs
export_meta = form_data.pop("_export", None)
if export_meta and "workflow_refs" in export_meta:
    from src.services.file_storage.ref_translation import build_ref_to_uuid_map
    ref_to_uuid = await build_ref_to_uuid_map(self.db)
    form_data = transform_refs_for_import(form_data, FormPublic, ref_to_uuid)
```

**Step 4: Run type check**

Run: `cd api && pyright src/services/file_storage/indexers/form.py`
Expected: 0 errors

**Step 5: Run form indexer tests**

Run: `./test.sh tests/unit/services/file_storage/ -v -k form`
Expected: All tests pass

**Step 6: Commit**

```bash
git add api/src/services/file_storage/indexers/form.py
git commit -m "$(cat <<'EOF'
refactor(form-indexer): use refs.py transform functions

Replaces:
- Manual exclude={...} set with Field(exclude=True)
- Context passing with pure transform functions
- Manual _export.workflow_refs list with auto-generated from model

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Update Agent Indexer to Use New Transform Functions

**Files:**
- Modify: `api/src/services/file_storage/indexers/agent.py`

**Step 1: Update imports**

Add:
```python
from src.models.contracts.refs import (
    get_workflow_ref_paths,
    transform_refs_for_export,
    transform_refs_for_import,
)
```

**Step 2: Update _serialize_agent_to_json**

Replace the function body:

```python
def _serialize_agent_to_json(
    agent: Agent,
    workflow_map: dict[str, str] | None = None
) -> bytes:
    """
    Serialize an Agent to JSON bytes using Pydantic model_dump.

    Uses AgentPublic.model_dump() with exclude=True fields auto-excluded.
    Transforms workflow refs via transform_refs_for_export().

    Args:
        agent: Agent ORM instance with tools relationship loaded
        workflow_map: Optional mapping of workflow UUID → portable ref.
                      If provided, tool_ids are transformed.

    Returns:
        JSON serialized as UTF-8 bytes
    """
    agent_public = AgentPublic.model_validate(agent)

    # model_dump respects Field(exclude=True) automatically
    agent_data = agent_public.model_dump(mode="json", exclude_none=True)

    # Remove empty arrays to match import format
    for key in ["delegated_agent_ids", "role_ids", "knowledge_sources", "system_tools"]:
        if key in agent_data and agent_data[key] == []:
            del agent_data[key]

    # Transform refs if we have a workflow map
    if workflow_map:
        agent_data = transform_refs_for_export(agent_data, AgentPublic, workflow_map)
        agent_data["_export"] = {
            "workflow_refs": get_workflow_ref_paths(AgentPublic),
            "version": "1.0",
        }

    return json.dumps(agent_data, indent=2).encode("utf-8")
```

**Step 3: Update index_agent to use transform_refs_for_import**

Find the section that handles `_export` metadata and update:

```python
# Check for portable refs from export and resolve them to UUIDs
export_meta = agent_data.pop("_export", None)
if export_meta and "workflow_refs" in export_meta:
    from src.services.file_storage.ref_translation import build_ref_to_uuid_map
    ref_to_uuid = await build_ref_to_uuid_map(self.db)
    agent_data = transform_refs_for_import(agent_data, AgentPublic, ref_to_uuid)
```

**Step 4: Run type check**

Run: `cd api && pyright src/services/file_storage/indexers/agent.py`
Expected: 0 errors

**Step 5: Run agent indexer tests**

Run: `./test.sh tests/unit/services/file_storage/ -v -k agent`
Expected: All tests pass

**Step 6: Commit**

```bash
git add api/src/services/file_storage/indexers/agent.py
git commit -m "$(cat <<'EOF'
refactor(agent-indexer): use refs.py transform functions

Same pattern as form indexer - uses pure transform functions
instead of context passing and manual field lists.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Remove Old Decorators from Forms

**Files:**
- Modify: `api/src/models/contracts/forms.py`

**Step 1: Remove @field_serializer decorators**

Remove these decorators and their methods from FormField, FormCreate, FormUpdate, FormPublic:
- `@field_serializer("data_provider_id")`
- `@field_serializer("workflow_id", "launch_workflow_id")`

**Step 2: Remove @field_validator decorators**

Remove these decorators and their methods:
- `@field_validator("data_provider_id", mode="before")`
- `@field_validator("workflow_id", "launch_workflow_id", mode="before")`
- `@field_validator("form_schema", mode="before")` that forwards context

**Step 3: Remove ValidationInfo import if no longer needed**

If no validators remain that use `info`, remove:
```python
from pydantic import ValidationInfo
```

**Step 4: Run type check and tests**

Run: `cd api && pyright src/models/contracts/forms.py`
Expected: 0 errors

Run: `./test.sh tests/unit/models/test_forms.py -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add api/src/models/contracts/forms.py
git commit -m "$(cat <<'EOF'
refactor(forms): remove old field_serializer/field_validator decorators

These are replaced by WorkflowRef markers and transform functions.
No more context threading needed.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Remove Old Decorators from Agents

**Files:**
- Modify: `api/src/models/contracts/agents.py`

**Step 1: Remove @field_serializer decorator**

Remove:
- `@field_serializer("tool_ids")`

**Step 2: Remove @field_validator decorator**

Remove:
- `@field_validator("tool_ids", mode="before")`

**Step 3: Run type check and tests**

Run: `cd api && pyright src/models/contracts/agents.py`
Expected: 0 errors

Run: `./test.sh tests/unit/models/test_agents.py -v`
Expected: All tests pass

**Step 4: Commit**

```bash
git add api/src/models/contracts/agents.py
git commit -m "$(cat <<'EOF'
refactor(agents): remove old field_serializer/field_validator decorators

Replaced by WorkflowRef marker and transform functions.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Clean Up ref_translation.py

**Files:**
- Modify: `api/src/services/file_storage/ref_translation.py`

**Step 1: Remove unused functions**

Remove these functions (now handled by refs.py):
- `transform_path_refs_to_uuids()`
- `resolve_workflow_ref()`
- `transform_workflow_refs()`
- `add_export_metadata()`
- `extract_export_metadata()`

Keep these functions (still needed):
- `build_workflow_ref_map()`
- `build_ref_to_uuid_map()`
- `transform_app_source_uuids_to_refs()`
- `transform_app_source_refs_to_uuids()`
- Helper functions they depend on

**Step 2: Remove unused imports and models**

Clean up any imports that are no longer needed after removing the functions.

**Step 3: Run type check**

Run: `cd api && pyright src/services/file_storage/ref_translation.py`
Expected: 0 errors

**Step 4: Run all ref_translation tests**

Run: `./test.sh tests/ -v -k ref_translation`
Expected: All tests pass

**Step 5: Commit**

```bash
git add api/src/services/file_storage/ref_translation.py
git commit -m "$(cat <<'EOF'
refactor(ref-translation): remove functions replaced by refs.py

Removed:
- transform_path_refs_to_uuids()
- resolve_workflow_ref()
- transform_workflow_refs()
- add_export_metadata()
- extract_export_metadata()

Kept (still needed for app source transformation):
- build_workflow_ref_map()
- build_ref_to_uuid_map()
- transform_app_source_*()

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Run Full Test Suite and Verification

**Files:**
- None (verification only)

**Step 1: Run pyright on all modified files**

Run: `cd api && pyright`
Expected: 0 errors

**Step 2: Run ruff check**

Run: `cd api && ruff check .`
Expected: 0 errors

**Step 3: Run unit tests**

Run: `./test.sh tests/unit/ -v`
Expected: All tests pass

**Step 4: Run integration tests**

Run: `./test.sh tests/integration/ -v`
Expected: All tests pass

**Step 5: Run E2E portable refs tests**

Run: `./test.sh --e2e tests/e2e/api/test_portable_refs_sync.py -v`
Expected: All tests pass

**Step 6: Run full E2E suite**

Run: `./test.sh --e2e`
Expected: All tests pass

**Step 7: Final commit (if any fixes needed)**

If any fixes were needed, commit them with appropriate message.

---

## Summary

After completing all tasks:

**What's New:**
- `api/src/models/contracts/refs.py` - WorkflowRef marker, get_workflow_ref_paths, transform functions
- `api/tests/unit/models/contracts/test_refs.py` - Unit tests for refs module

**What's Changed:**
- `FormField.data_provider_id` - Has `WorkflowRef()` marker
- `FormPublic.workflow_id`, `launch_workflow_id` - Have `WorkflowRef()` markers
- `FormPublic` no-export fields - Use `Field(exclude=True)`
- `AgentPublic.tool_ids` - Has `WorkflowRef()` marker
- `AgentPublic` no-export fields - Use `Field(exclude=True)`
- Form indexer - Uses new transform functions
- Agent indexer - Uses new transform functions

**What's Removed:**
- All `@field_serializer` for workflow refs
- All `@field_validator` for workflow refs
- Context forwarding through validators
- Manual `exclude={...}` sets in serialization
- `resolve_workflow_ref()`, `transform_path_refs_to_uuids()`, etc.

**Benefits:**
- Adding a new workflow ref field: Just add `Annotated[..., WorkflowRef()]`
- `_export.workflow_refs` auto-generated from model
- No context threading needed
- Single source of truth for which fields are refs
