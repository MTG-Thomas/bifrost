# 2026-03-29 UniFi integration landscape notes (Site Manager + local API)

## Objective

Scaffold a Bifrost UniFi integration that can:

1. Use Site Manager API for cloud/global inventory and sync.
2. Support local/self-hosted UniFi Network API surfaces for environments where cloud access is restricted.
3. Preserve enough mapping context to allocate both sites and fabrics to customer organizations.

## Current landscape summary

- UniFi Site Manager is the central remote-management plane and explicitly exposes API integration support.
- UniFi Fabrics are now a first-class management grouping above sites, with shared RBAC/identity policy boundaries.
- For self-hosting, UniFi OS Server is now the preferred path over legacy UniFi Network Server when feature parity with UniFi OS concepts (organizations, IdP, Site Magic) is required.
- Legacy UniFi Network Server remains available, but Ubiquiti positions it as an advanced/self-managed path and recommends UniFi-native hosting for most users.

## Bifrost allocation model (recommended)

- Primary mapping key for org-scoped automation: **site ID** (`entity_id`).
- Persist fabric context on the mapping config (`fabric_id`) when available.
- Add a separate fabric sync workflow that can create "fabric:" entity IDs for organizations that want policy-at-fabric granularity.
- Treat local API usage as an optional override surface (via `local_network_base_url`) rather than a replacement for Site Manager in multi-tenant MSP scenarios.

## Self-hosting implications for implementation

- Keep TLS verification configurable (`verify_tls`) for mixed deployments and internal PKI setups.
- Expect manual lifecycle responsibilities (patching/updating and runtime availability) for self-hosted environments.
- Keep endpoint paths configurable in code, because both Site Manager and local Network API surfaces are still evolving.

## Follow-up implementation tasks

1. Validate current production endpoint paths and auth header semantics against the latest developer UI docs.
2. Add fabric-to-site relationship reconciliation workflow once endpoint schema is confirmed.
3. Add integration contract tests that pin expected payload shapes from both cloud and local surfaces.
4. Add local-surface tool workflows (site settings/inventory queries) once mapping strategy is finalized.
