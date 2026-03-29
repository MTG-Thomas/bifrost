"""
UniFi: Sync Sites

Syncs UniFi Site Manager sites into Bifrost organizations and creates
IntegrationMappings for org-scoped workflows.

Entity model:
  entity_id   = UniFi site ID
  entity_name = site name

Mapping config:
  fabric_id   = Optional UniFi fabric ID associated with the site.
"""

from bifrost import integrations, organizations, workflow
from modules.unifi import UniFiClient


@workflow(
    name="UniFi: Sync Sites",
    description="Sync UniFi Site Manager sites into Bifrost organizations.",
    category="UniFi",
    tags=["unifi", "sync", "site-manager", "sites"],
)
async def sync_unifi_sites() -> dict:
    from modules.unifi import get_client

    client = await get_client(scope="global")
    try:
        sites = await client.list_site_manager_sites()
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

    for site in sites:
        normalized = UniFiClient.normalize_site(site)
        site_id = normalized["id"]
        site_name = normalized["name"] or site_id
        fabric_id = normalized.get("fabric_id", "")

        if not site_id:
            errors.append(f"Skipped site with no ID: {site}")
            continue

        if site_id in existing_by_entity:
            already_mapped += 1
            continue

        try:
            org = orgs_by_name.get(site_name.lower())
            if org is None:
                org = await organizations.create(site_name)
                orgs_by_name[site_name.lower()] = org
                created_orgs += 1

            await integrations.upsert_mapping(
                "UniFi",
                scope=org.id,
                entity_id=site_id,
                entity_name=site_name,
                config={"fabric_id": fabric_id} if fabric_id else None,
            )
            mapped += 1
        except Exception as exc:
            errors.append(f"{site_name} ({site_id}): {exc}")

    return {
        "total": len(sites),
        "mapped": mapped,
        "already_mapped": already_mapped,
        "created_orgs": created_orgs,
        "errors": errors,
    }
