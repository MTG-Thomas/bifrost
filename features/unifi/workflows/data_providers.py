"""UniFi data providers for Bifrost org mapping."""

from bifrost import data_provider
from modules.unifi import UniFiClient


@data_provider(
    name="UniFi: List Sites",
    description="Returns UniFi sites from Site Manager for org mapping picker.",
    category="UniFi",
    tags=["unifi", "data-provider", "site-manager"],
)
async def list_unifi_sites() -> list[dict]:
    from modules.unifi import get_client

    client = await get_client(scope="global")
    try:
        sites = await client.list_site_manager_sites()
    finally:
        await client.close()

    options: list[dict[str, str]] = []
    for site in sites:
        normalized = UniFiClient.normalize_site(site)
        if normalized["id"] and normalized["name"]:
            options.append({"value": normalized["id"], "label": normalized["name"]})

    return sorted(options, key=lambda item: item["label"].lower())


@data_provider(
    name="UniFi: List Fabrics",
    description="Returns UniFi fabrics from Site Manager for mapping policy pickers.",
    category="UniFi",
    tags=["unifi", "data-provider", "fabric", "site-manager"],
)
async def list_unifi_fabrics() -> list[dict]:
    from modules.unifi import get_client

    client = await get_client(scope="global")
    try:
        fabrics = await client.list_site_manager_fabrics()
    finally:
        await client.close()

    options: list[dict[str, str]] = []
    for fabric in fabrics:
        normalized = UniFiClient.normalize_fabric(fabric)
        if normalized["id"] and normalized["name"]:
            options.append({"value": normalized["id"], "label": normalized["name"]})

    return sorted(options, key=lambda item: item["label"].lower())
