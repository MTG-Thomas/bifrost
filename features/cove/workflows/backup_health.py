"""
Cove backup health monitoring workflows.

Implements scheduled backup status polling, Autotask incident creation with
deduplication, and weekly markdown digest generation.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from hashlib import sha1
from typing import Any

from bifrost import context, integrations, organizations, tables, workflow
from modules.autotask import get_client as get_autotask_client
from modules.cove import get_client as get_cove_client

CONFIG_TABLE = "cove_backup_client_config"
SNAPSHOT_TABLE = "cove_backup_daily_snapshots"
INCIDENT_TABLE = "cove_backup_incidents"
DIGEST_TABLE = "cove_backup_weekly_reports"

FAILURE_STATES = {"failed", "missed", "incomplete"}


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _normalize_status(device: dict[str, Any], *, now: datetime, missed_after_hours: int) -> str:
    raw_status = str(device.get("last_session_status") or "").strip().lower()
    raw_completed = str(device.get("last_completed_session_status") or "").strip().lower()

    if raw_status in {"failed", "error", "aborted", "interrupted"}:
        return "failed"

    if raw_completed in {"failed", "incomplete", "interrupted", "aborted"}:
        return "incomplete"

    errors = str(device.get("last_session_errors_count") or "0").strip()
    if errors.isdigit() and int(errors) > 0:
        return "incomplete"

    latest_success = _parse_dt(device.get("last_successful_session_time") or device.get("last_backup_time"))
    if not latest_success:
        return "missed"

    if latest_success < now - timedelta(hours=missed_after_hours):
        return "missed"

    return "succeeded"


def _incident_anchor(device: dict[str, Any], fallback: str) -> str:
    for field in ("last_completed_session_time", "last_session_time", "last_backup_time"):
        parsed = _parse_dt(device.get(field))
        if parsed:
            return parsed.isoformat()
    return fallback


def _incident_key(org_id: str, device_id: str, status: str, anchor: str) -> str:
    raw = f"cove:{org_id}:{device_id}:{status}:{anchor}"
    return sha1(raw.encode("utf-8")).hexdigest()


async def _get_config(org_id: str) -> dict[str, Any] | None:
    row = await tables.get(CONFIG_TABLE, org_id, scope=org_id)
    return row.data if row else None


async def _load_orgs(enabled_only: bool) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for org in await organizations.list():
        cove_mapping = await integrations.get("Cove Data Protection", scope=org.id)
        if not cove_mapping or not cove_mapping.entity_id:
            continue

        cfg = await _get_config(org.id)
        is_enabled = bool(cfg and cfg.get("enabled", True))
        if enabled_only and not is_enabled:
            continue

        found.append(
            {
                "org_id": org.id,
                "org_name": org.name,
                "cove_partner_id": str(cove_mapping.entity_id),
            }
        )
    return found


@workflow(
    name="Cove Data Protection: Configure Backup Monitoring",
    description="Set per-client Cove backup monitoring configuration.",
    category="Cove Data Protection",
    tags=["cove", "backup", "config"],
)
async def configure_cove_backup_monitoring(
    enabled: bool = True,
    cove_partner_id: str | None = None,
    devices_in_scope: list[str] | None = None,
    failure_ticket_severity: str = "2",
    weekly_report_day: str = "monday",
    weekly_report_hour_utc: int = 13,
) -> dict:
    org_id = context.org_id
    if not org_id:
        raise RuntimeError("This workflow must run in an organization scope.")

    cove_mapping = await integrations.get("Cove Data Protection", scope=org_id)
    if not cove_mapping or not cove_mapping.entity_id:
        raise RuntimeError("Cove Data Protection mapping is required for this org.")

    resolved_partner = cove_partner_id or str(cove_mapping.entity_id)

    data = {
        "enabled": enabled,
        "cove_partner_id": resolved_partner,
        "devices_in_scope": [str(d) for d in (devices_in_scope or [])],
        "failure_ticket_severity": str(failure_ticket_severity),
        "weekly_report_day": weekly_report_day.lower(),
        "weekly_report_hour_utc": int(weekly_report_hour_utc),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    await tables.upsert(CONFIG_TABLE, id=org_id, scope=org_id, data=data)
    return {"org_id": org_id, "config": data}


@workflow(
    name="Cove Data Protection: Poll Backup Health",
    description="Poll nightly Cove backup status and create Autotask incidents for failures.",
    category="Cove Data Protection",
    tags=["cove", "backup", "polling", "autotask"],
)
async def poll_cove_backup_health(
    org_id: str | None = None,
    missed_after_hours: int = 30,
) -> dict:
    now = datetime.now(UTC)
    target_orgs = await _load_orgs(enabled_only=True)
    if org_id:
        target_orgs = [entry for entry in target_orgs if entry["org_id"] == org_id]

    results: list[dict[str, Any]] = []

    for target in target_orgs:
        this_org_id = target["org_id"]
        cfg = await _get_config(this_org_id) or {}
        allowed_devices = {str(d) for d in (cfg.get("devices_in_scope") or [])}

        cove = await get_cove_client(scope=this_org_id)
        autotask = await get_autotask_client(scope=this_org_id)

        org_result: dict[str, Any] = {
            "org_id": this_org_id,
            "org_name": target["org_name"],
            "devices": 0,
            "statuses": {"succeeded": 0, "failed": 0, "missed": 0, "incomplete": 0},
            "tickets_created": [],
            "tickets_reused": [],
            "errors": [],
        }

        try:
            devices = await cove.enumerate_devices(partner_id=int(target["cove_partner_id"]))

            for device in devices:
                device_id = str(device.get("device_id") or device.get("account_id") or "")
                if not device_id:
                    continue
                if allowed_devices and device_id not in allowed_devices:
                    continue

                status = _normalize_status(device, now=now, missed_after_hours=missed_after_hours)
                org_result["devices"] += 1
                org_result["statuses"][status] += 1

                snapshot_id = f"{device_id}:{now.date().isoformat()}"
                snapshot = {
                    "org_id": this_org_id,
                    "device_id": device_id,
                    "device_name": device.get("device_name") or device.get("computer_name") or device_id,
                    "captured_at": now.isoformat(),
                    "status": status,
                    "last_backup_time": device.get("last_backup_time"),
                    "last_successful_session_time": device.get("last_successful_session_time"),
                    "last_session_time": device.get("last_session_time"),
                    "last_completed_session_time": device.get("last_completed_session_time"),
                    "last_session_status": device.get("last_session_status"),
                    "last_completed_session_status": device.get("last_completed_session_status"),
                    "last_session_errors_count": device.get("last_session_errors_count"),
                }
                await tables.upsert(SNAPSHOT_TABLE, id=snapshot_id, scope=this_org_id, data=snapshot)

                if status not in FAILURE_STATES:
                    continue

                anchor = _incident_anchor(device, fallback=now.date().isoformat())
                incident_id = _incident_key(this_org_id, device_id, status, anchor)
                existing = await tables.get(INCIDENT_TABLE, incident_id, scope=this_org_id)
                existing_data = existing.data if existing else {}
                if existing_data.get("state") == "open" and existing_data.get("autotask_ticket_id"):
                    org_result["tickets_reused"].append(existing_data["autotask_ticket_id"])
                    continue

                ticket = await autotask.create_ticket(
                    title=f"Cove backup {status}: {snapshot['device_name']}",
                    description=(
                        f"Device: {snapshot['device_name']} ({device_id})\n"
                        f"Cove partner: {target['cove_partner_id']}\n"
                        f"Status: {status}\n"
                        f"Last backup: {snapshot['last_backup_time']}\n"
                        f"Last successful: {snapshot['last_successful_session_time']}\n"
                        f"Last session status: {snapshot['last_session_status']}\n"
                        f"Captured at: {snapshot['captured_at']}"
                    ),
                    priority=str(cfg.get("failure_ticket_severity") or "2"),
                )
                normalized = {
                    "id": ticket.get("id"),
                    "ticket_number": ticket.get("ticketNumber"),
                    "title": ticket.get("title"),
                }
                org_result["tickets_created"].append(normalized)

                await tables.upsert(
                    INCIDENT_TABLE,
                    id=incident_id,
                    scope=this_org_id,
                    data={
                        "incident_id": incident_id,
                        "org_id": this_org_id,
                        "device_id": device_id,
                        "device_name": snapshot["device_name"],
                        "status": status,
                        "anchor": anchor,
                        "state": "open",
                        "autotask_ticket_id": str(ticket.get("id") or ""),
                        "ticket_number": str(ticket.get("ticketNumber") or ""),
                        "created_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    },
                )

        except Exception as exc:  # noqa: BLE001
            org_result["errors"].append(str(exc))
        finally:
            await cove.close()
            await autotask.close()

        results.append(org_result)

    return {
        "ran_at": now.isoformat(),
        "organizations": results,
        "count": len(results),
    }


@workflow(
    name="Cove Data Protection: Generate Weekly Backup Digest",
    description="Generate per-client weekly markdown digest from backup snapshots.",
    category="Cove Data Protection",
    tags=["cove", "backup", "digest", "reporting"],
)
async def generate_cove_weekly_backup_digest(
    org_id: str | None = None,
    lookback_days: int = 7,
) -> dict:
    now = datetime.now(UTC)
    week_start = (now - timedelta(days=lookback_days)).date()
    prev_start = week_start - timedelta(days=lookback_days)

    target_orgs = await _load_orgs(enabled_only=True)
    if org_id:
        target_orgs = [entry for entry in target_orgs if entry["org_id"] == org_id]

    generated: list[dict[str, Any]] = []

    for target in target_orgs:
        this_org_id = target["org_id"]
        records = await tables.query(SNAPSHOT_TABLE, scope=this_org_id, limit=5000)
        rows = [doc.data for doc in records.documents]

        current_by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
        previous_by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for row in rows:
            captured = _parse_dt(row.get("captured_at"))
            if not captured:
                continue
            device_id = str(row.get("device_id") or "")
            if not device_id:
                continue
            if captured.date() >= week_start:
                current_by_device[device_id].append(row)
            elif prev_start <= captured.date() < week_start:
                previous_by_device[device_id].append(row)

        lines = [
            f"# Weekly Cove Backup Digest — {target['org_name']}",
            "",
            f"Report generated: {now.isoformat()}",
            f"Current window start: {week_start.isoformat()}",
            "",
            "| Device | Last Backup | Current Status | Prior Week | Trend |",
            "|---|---|---|---|---|",
        ]

        summary = {"succeeded": 0, "failed": 0, "missed": 0, "incomplete": 0}

        for device_id, device_rows in sorted(current_by_device.items()):
            sorted_rows = sorted(
                device_rows,
                key=lambda r: _parse_dt(r.get("captured_at")) or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            latest = sorted_rows[0]
            status = str(latest.get("status") or "unknown")
            if status in summary:
                summary[status] += 1

            prior_rows = previous_by_device.get(device_id, [])
            prior_status = prior_rows[0].get("status") if prior_rows else "n/a"

            trend = "unchanged"
            if prior_status == "n/a":
                trend = "new"
            elif prior_status != status:
                if status == "succeeded":
                    trend = "improved"
                elif prior_status == "succeeded":
                    trend = "degraded"
                else:
                    trend = "changed"

            lines.append(
                f"| {latest.get('device_name') or device_id} | {latest.get('last_backup_time') or 'n/a'} "
                f"| {status} | {prior_status} | {trend} |"
            )

        lines.extend(
            [
                "",
                "## Summary",
                f"- Devices: {sum(summary.values())}",
                f"- Succeeded: {summary['succeeded']}",
                f"- Failed: {summary['failed']}",
                f"- Missed: {summary['missed']}",
                f"- Incomplete: {summary['incomplete']}",
            ]
        )

        markdown = "\n".join(lines)
        report_id = f"{week_start.isoformat()}"
        await tables.upsert(
            DIGEST_TABLE,
            id=report_id,
            scope=this_org_id,
            data={
                "org_id": this_org_id,
                "org_name": target["org_name"],
                "week_start": week_start.isoformat(),
                "lookback_days": lookback_days,
                "generated_at": now.isoformat(),
                "summary": summary,
                "markdown": markdown,
            },
        )

        generated.append(
            {
                "org_id": this_org_id,
                "org_name": target["org_name"],
                "week_start": week_start.isoformat(),
                "summary": summary,
                "report_id": report_id,
            }
        )

    return {
        "generated_at": now.isoformat(),
        "reports": generated,
        "count": len(generated),
    }
