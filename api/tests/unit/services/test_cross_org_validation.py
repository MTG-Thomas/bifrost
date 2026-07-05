from uuid import UUID

import pytest

from src.services.cross_org_validation import (
    CrossOrgValidationError,
    validate_workflow_reference,
    validate_workflow_references,
)


ENTITY_ORG_ID = UUID("11111111-1111-1111-1111-111111111111")
WORKFLOW_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_ORG_ID = UUID("33333333-3333-3333-3333-333333333333")


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _SequenceDb:
    def __init__(self, *values):
        self._values = list(values)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self._values.pop(0))


@pytest.mark.asyncio
async def test_global_entity_reference_short_circuits_without_querying_db():
    db = _SequenceDb()

    await validate_workflow_reference(db, WORKFLOW_ID, None, "form")

    assert db.statements == []


@pytest.mark.asyncio
async def test_org_entity_can_reference_same_org_workflow():
    db = _SequenceDb(ENTITY_ORG_ID)

    await validate_workflow_reference(db, WORKFLOW_ID, ENTITY_ORG_ID, "agent")

    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_org_entity_can_reference_global_or_missing_workflow():
    global_workflow_db = _SequenceDb(None, WORKFLOW_ID)

    await validate_workflow_reference(
        global_workflow_db,
        WORKFLOW_ID,
        ENTITY_ORG_ID,
        "form",
    )

    assert len(global_workflow_db.statements) == 2

    missing_workflow_db = _SequenceDb(None, None)

    await validate_workflow_reference(
        missing_workflow_db,
        WORKFLOW_ID,
        ENTITY_ORG_ID,
        "form",
    )

    assert len(missing_workflow_db.statements) == 2


@pytest.mark.asyncio
async def test_cross_org_workflow_reference_raises_clear_error():
    db = _SequenceDb(OTHER_ORG_ID)

    with pytest.raises(CrossOrgValidationError) as exc_info:
        await validate_workflow_reference(db, WORKFLOW_ID, ENTITY_ORG_ID, "form")

    message = str(exc_info.value)
    assert "Cannot reference workflow from a different organization" in message
    assert "form belongs to organization" in message
    assert str(ENTITY_ORG_ID) in message
    assert str(OTHER_ORG_ID) in message


@pytest.mark.asyncio
async def test_validate_workflow_references_checks_each_reference_in_order():
    first_workflow_id = UUID("44444444-4444-4444-4444-444444444444")
    second_workflow_id = UUID("55555555-5555-5555-5555-555555555555")
    db = _SequenceDb(ENTITY_ORG_ID, OTHER_ORG_ID)

    with pytest.raises(CrossOrgValidationError):
        await validate_workflow_references(
            db,
            [first_workflow_id, second_workflow_id],
            ENTITY_ORG_ID,
            "agent",
        )

    assert len(db.statements) == 2
