# Meraki + Oxidized Backup Orchestration Architecture (Draft)

Date: 2026-03-29
Status: Draft proposal for implementation

## Context

Goal: add a Bifrost workflow module that orchestrates Oxidized for network config backups in Meraki-heavy client estates, with change alerting into HaloPSA and per-client export.

## Recommendation Summary

Use a **hybrid model**:

1. **Meraki API** for inventory sync and cloud-side configuration change events.
2. **Oxidized direct polling** for devices reachable via NetBird/private adjacency where CLI config backups are desired.
3. **Bifrost** as lifecycle/orchestration layer for container runtime, schedules, ticketing, and acknowledgement state.

Rationale:
- Meraki API already provides robust inventory and configuration-change records.
- Oxidized excels at versioned config storage/diffing for directly reachable devices.
- Not all Meraki environments expose equivalent direct CLI backup coverage across product lines.

## Existing Repo Patterns To Reuse

- Integration module pattern in `modules/{vendor}.py` (thin API client + `get_client`).
- Workflow pattern in `features/{vendor}/workflows/*.py` with `@workflow` and `@data_provider` decorators.
- Org mapping via `integrations.upsert_mapping(...)` and mapped `entity_id` usage.
- Global/org config persistence through `bifrost.config.get/set`.
- HaloPSA ticket creation through shared tooling in `shared/halopsa/tools/tickets.py`.
- DB-backed schedule/event runtime (schedule sources + event deliveries).

## Proposed New Surfaces

### Modules

- `modules/oxidized.py`
  - Client for Oxidized control endpoints (reload, node list, status) and health checks.
  - Optional helper for local git repository introspection if mounted.

- `modules/meraki_config_audit.py` (or extension within `modules/meraki.py`)
  - Focused methods for:
    - organization device inventory
    - configuration changes feed

### Features

- `features/meraki_oxidized/workflows/`
  - `sync_inventory.py`
  - `reconcile_oxidized_nodes.py`
  - `collect_config_changes.py`
  - `create_change_ticket.py`
  - `acknowledge_change.py`
  - `export_client_configs.py`
  - `lifecycle.py` (provision/deprovision/restart)

### Shared

- `shared/network_backups/` (optional)
  - Diff normalization utilities.
  - Hashing and dedupe helpers for re-alert suppression.

## Per-Client Configuration Contract

Required per client:
- `enabled` (bool)
- `meraki_api_key_akv_ref` (string)
- `meraki_organization_id` (string)
- `backup_schedule_cron` (string)
- `alert_severity` (enum)

Strongly recommended:
- `connectivity_mode` (`meraki_api_only` | `oxidized_direct` | `hybrid`)
- `realert_interval_minutes` (int)
- `oxidized_group_name` (string)
- `netbird_profile` or route-group reference

## Data Model Proposal

- `NetworkBackupProfile`
  - `id`, `org_id`, `enabled`, `backup_cron`, `alert_severity`, `connectivity_mode`, `akv_ref`, `meraki_org_id`

- `NetworkDeviceInventory`
  - `id`, `org_id`, `device_id`, `name`, `model`, `network_id`, `management_ip`, `last_seen_at`, `source_snapshot_at`

- `ConfigVersionRef`
  - `id`, `org_id`, `device_id`, `source` (`oxidized`/`meraki_api`), `version_ref`, `captured_at`

- `ConfigChangeEvent`
  - `id`, `org_id`, `device_id`, `change_hash`, `detected_at`, `before_ref`, `after_ref`, `diff_text`,
    `ack_state`, `ticket_id`, `last_alert_at`, `alert_count`

## Lifecycle Management

### Provision

- Validate client profile and AKV reference.
- Ensure NetBird path/policy prerequisites.
- Render Oxidized config and source DB.
- Create/update runtime workload + persistent storage.

### Reconcile

- Scheduled Meraki inventory sync updates source DB.
- Trigger Oxidized node reload when inventory changed.

### Poll + Detect

- Oxidized polling runs by interval.
- Bifrost collector compares new refs and computes diffs.
- New unacknowledged changes emit ticket workflow.

### Acknowledge / Re-alert

- Operator acknowledges change in Bifrost UI.
- Unacknowledged changes re-alert based on policy.

### Export

- Workflow packages per-client repo/config history for on-demand UI download.

## Connectivity Model

Oxidized direct device polling requires network adjacency. The implementation assumes NetBird overlay/private connectivity is the default model for internal target access.

## Security Model

- Meraki API key is stored as AKV reference per client.
- Device credentials for Oxidized are managed by Bifrost secret storage and rendered at runtime only.
- Avoid writing secrets to long-lived logs and artifact outputs.

## Rollout Plan

1. **Schema + config contracts**
2. **Inventory sync + lifecycle scaffolding**
3. **Oxidized runtime + git output wiring**
4. **Change detection + HaloPSA ticketing**
5. **Acknowledge + re-alert loop**
6. **Export UX and hardening**

## Open Questions

1. Preferred deployment topology: one Oxidized instance per client or shared multi-tenant runtime?
2. Should Meraki API change records be treated as first-class version artifacts when direct config is unavailable?
3. What HaloPSA queue/team/type mapping rules should be defaulted per client?
4. What ack granularity is desired (ticket-level, device-level, change-hash-level)?
5. How should large diffs be transported (inline vs attachment) for ticket payload limits?

