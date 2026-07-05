from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.routers.nuclei_scans import (
    BulkStateUpdateRequest,
    FindingInput,
    ScanIngestRequest,
    _occurrence_key,
    _stable_id,
    bulk_update_finding_state,
    get_findings,
    get_scan_history,
    ingest_scan_results,
    router,
)


def test_finding_input_normalizes_severity() -> None:
    finding = FindingInput(
        template_id="cve-2026-test",
        host="https://example.test",
        severity="HIGH",
        matched_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert finding.severity == "high"


def test_finding_input_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError):
        FindingInput(
            template_id="cve-2026-test",
            host="https://example.test",
            severity="urgent",
            matched_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
        )


def test_occurrence_key_and_stable_id_are_deterministic() -> None:
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    finding = FindingInput(
        template_id="cve-2026-test",
        host="https://example.test",
        severity="medium",
        matched_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
    )

    key = _occurrence_key(org_id, finding)

    assert key == (
        "11111111-1111-1111-1111-111111111111:"
        "cve-2026-test:https://example.test:2026-04-26T12:00:00+00:00"
    )
    assert _stable_id(key) == _stable_id(key)


def test_ingest_request_defaults_to_complete_empty_findings() -> None:
    request = ScanIngestRequest(scan_host_device_id="scanner-1")

    assert request.findings == []
    assert request.incomplete is False


def test_ingest_request_accepts_incomplete_flag() -> None:
    request = ScanIngestRequest(scan_host_device_id="scanner-1", incomplete=True)

    assert request.incomplete is True


def test_bulk_state_update_only_allows_lifecycle_states() -> None:
    assert BulkStateUpdateRequest(finding_ids=["finding-1"], state="resolved").state == "resolved"

    with pytest.raises(ValidationError):
        BulkStateUpdateRequest(finding_ids=["finding-1"], state="open")


def test_router_registers_scan_endpoints() -> None:
    paths = {route.path for route in router.routes}

    assert {
        "/api/scans/runs/{org_id}",
        "/api/scans/runs/{org_id}/{run_id}/ingest",
        "/api/scans/history/{org_id}",
        "/api/scans/findings/{org_id}",
        "/api/scans/findings/{org_id}/bulk-state",
    }.issubset(paths)


def _user() -> SimpleNamespace:
    return SimpleNamespace(email="scanner@example.com")


def _result(value: object | None = None, values: list[object] | None = None) -> MagicMock:
    scalars = MagicMock()
    scalars.all.return_value = values or []
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value = scalars
    return result


@pytest.mark.asyncio
async def test_scan_history_returns_empty_when_table_missing() -> None:
    with patch("src.routers.nuclei_scans._get_table", AsyncMock(return_value=None)):
        result = await get_scan_history(
            UUID("11111111-1111-1111-1111-111111111111"),
            _user(),
            AsyncMock(),
        )

    assert result == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_findings_validates_state_and_severity_before_db_lookup() -> None:
    with pytest.raises(HTTPException) as state_exc:
        await get_findings(
            UUID("11111111-1111-1111-1111-111111111111"),
            _user(),
            AsyncMock(),
            state="new",
        )

    with pytest.raises(HTTPException) as severity_exc:
        await get_findings(
            UUID("11111111-1111-1111-1111-111111111111"),
            _user(),
            AsyncMock(),
            severity="urgent",
        )

    assert state_exc.value.status_code == 400
    assert severity_exc.value.status_code == 400


@pytest.mark.asyncio
async def test_findings_filters_state_severity_and_template_tag() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=_result(
            values=[
                SimpleNamespace(
                    data={
                        "finding_id": "keep",
                        "state": "open",
                        "severity": "HIGH",
                        "template_tags": ["exposure"],
                        "matched_at": "2026-07-05T02:00:00Z",
                    }
                ),
                SimpleNamespace(
                    data={
                        "finding_id": "drop",
                        "state": "resolved",
                        "severity": "high",
                        "template_tags": ["exposure"],
                        "matched_at": "2026-07-05T01:00:00Z",
                    }
                ),
            ]
        )
    )

    with patch(
        "src.routers.nuclei_scans._get_table",
        AsyncMock(return_value=SimpleNamespace(id="findings-table")),
    ):
        result = await get_findings(
            UUID("11111111-1111-1111-1111-111111111111"),
            _user(),
            db,
            state="open",
            severity="high",
            template_tag="exposure",
            limit=200,
        )

    assert result["total"] == 1
    assert result["items"][0]["finding_id"] == "keep"


@pytest.mark.asyncio
async def test_bulk_update_finding_state_sets_resolved_timestamp() -> None:
    doc = SimpleNamespace(id="finding-1", data={"state": "open"}, updated_by=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(values=[doc]))
    db.flush = AsyncMock()

    with patch(
        "src.routers.nuclei_scans._get_or_create_table",
        AsyncMock(return_value=SimpleNamespace(id="findings-table")),
    ):
        result = await bulk_update_finding_state(
            UUID("11111111-1111-1111-1111-111111111111"),
            BulkStateUpdateRequest(finding_ids=["finding-1"], state="resolved"),
            _user(),
            db,
        )

    assert result == {"updated": 1, "state": "resolved"}
    assert doc.data["state"] == "resolved"
    assert "resolved_at" in doc.data
    assert doc.updated_by == "scanner@example.com"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_scan_results_realerts_and_resolves_missing_active_findings() -> None:
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    matched_at = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    active_doc = SimpleNamespace(
        id="existing-active",
        data={
            "template_id": "cve-1",
            "host": "https://target.test",
            "state": "acknowledged",
        },
        updated_by=None,
    )
    resolved_candidate = SimpleNamespace(
        id="resolved-candidate",
        data={
            "template_id": "cve-2",
            "host": "https://target.test",
            "state": "open",
        },
        updated_by=None,
    )
    run_doc = SimpleNamespace(id="run-1", data={"scan_started_at": "old"}, updated_by=None)
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(value=run_doc),
            _result(values=[active_doc, resolved_candidate]),
        ]
    )

    with patch(
        "src.routers.nuclei_scans._get_or_create_table",
        AsyncMock(side_effect=[SimpleNamespace(id="runs-table"), SimpleNamespace(id="findings-table")]),
    ):
        result = await ingest_scan_results(
            org_id,
            "run-1",
            ScanIngestRequest(
                scan_host_device_id="scanner-1",
                findings=[
                    FindingInput(
                        template_id="cve-1",
                        host="https://target.test",
                        severity="High",
                        matched_at=matched_at,
                    )
                ],
            ),
            _user(),
            db,
        )

    assert result["realert_suppressed"] == 1
    assert result["resolved"] == 1
    assert result["net_new"] == 0
    assert resolved_candidate.data["state"] == "resolved"
    assert run_doc.data["status"] == "completed"
    db.add.assert_called_once()
