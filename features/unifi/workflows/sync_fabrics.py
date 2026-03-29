"""
UniFi: Sync Fabrics

Scaffold workflow that syncs UniFi fabrics into Bifrost organizations.

Entity model:
  entity_id   = fabric:<fabric-id>
  entity_name = fabric name

This keeps fabric mappings distinct from site mappings while still using the
same UniFi integration.
"""

from bifrost import integrations, organizations, workflow
from modules.unifi import UniFiClient


@workflow(
    name="UniFi: Sync Fabrics",
    description="Sync UniFi fabrics into Bifrost organizations for policy allocation.",
    category="UniFi",
    tags=["unifi", "sync", "site-manager", "fabric"],
)
async def sync_unifi_fabrics() -> dict:
    from modules.unifi import get_client

    client = await get_client(scope="global")
    try:
        fabrics = await client.list_site_manager_fabrics()
    finally:
        await client.close()

    existing_mappings = await integrations.list_mappings("UniFi") or []
    existing_by_entity = {
        mapping.entity_id: mapping
        for mapping in existing_mappings
        if getattr(mapping, "entity_id", None)
    }

    all_orgs = await organizations.list()
    orgs_by_name = {org.name.lower(): org for org in all_orgs}

    created_orgs = 0
    mapped = 0
    already_mapped = 0
    errors: list[str] = []

    for fabric in fabrics:
        normalized = UniFiClient.normalize_fabric(fabric)
        fabric_id = normalized["id"]
        fabric_name = normalized["name"] or fabric_id

        if not fabric_id:
            errors.append(f"Skipped fabric with no ID: {fabric}")
            continue

        entity_id = f"fabric:{fabric_id}"
        if entity_id in existing_by_entity:
            already_mapped += 1
            continue

        try:
            org = orgs_by_name.get(fabric_name.lower())
            if org is None:
                org = await organizations.create(fabric_name)
                orgs_by_name[fabric_name.lower()] = org
                created_orgs += 1

            await integrations.upsert_mapping(
                "UniFi",
                scope=org.id,
                entity_id=entity_id,
                entity_name=fabric_name,
                config={"fabric_id": fabric_id, "mapping_kind": "fabric"},
            )
            mapped += 1
        except Exception as exc:
            errors.append(f"{fabric_name} ({fabric_id}): {exc}")

    return {
        "total": len(fabrics),
        "mapped": mapped,
        "already_mapped": already_mapped,
        "created_orgs": created_orgs,
        "errors": errors,
    }
