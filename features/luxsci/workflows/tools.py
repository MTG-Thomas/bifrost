"""
LuxSci inventory tools.

This integration is modeled as a direct tenant connection, not a reseller-style
org mapper. The initial goal is to inventory a LuxSci tenant cleanly so we can
plan migration away from it.
"""

from __future__ import annotations

from bifrost import tool


@tool(
    name="LuxSci: Get Account Inventory",
    description="Summarize LuxSci account inventory counts for users, domains, and aliases.",
    category="LuxSci",
    tags=["luxsci", "inventory", "migration"],
)
async def get_luxsci_account_inventory() -> dict:
    """Return a migration-oriented summary of LuxSci account inventory."""
    from modules.luxsci import LuxSciClient, get_client

    client = await get_client()
    try:
        users = await client.list_users()
        domains = await client.list_domains()
        aliases = await client.list_aliases()
    finally:
        await client.close()

    normalized_users = [LuxSciClient.normalize_user(user) for user in users]
    normalized_domains = [LuxSciClient.normalize_domain(domain) for domain in domains]
    normalized_aliases = [LuxSciClient.normalize_alias(alias) for alias in aliases]

    enabled_users = [user for user in normalized_users if user["status"] == "enabled"]
    disabled_users = [user for user in normalized_users if user["status"] == "disabled"]

    return {
        "summary": {
            "user_count": len(normalized_users),
            "enabled_user_count": len(enabled_users),
            "disabled_user_count": len(disabled_users),
            "domain_count": len(normalized_domains),
            "alias_count": len(normalized_aliases),
        },
        "domains": normalized_domains,
        "users": normalized_users,
        "aliases": normalized_aliases,
    }


@tool(
    name="LuxSci: List Users",
    description="List LuxSci users for the connected account, with optional status and domain filtering.",
    category="LuxSci",
    tags=["luxsci", "users", "migration"],
)
async def list_luxsci_users(
    status: str | None = None,
    domain: str | None = None,
    younger_than: int | None = None,
    older_than: int | None = None,
) -> dict:
    """Return LuxSci users in a normalized shape."""
    from modules.luxsci import LuxSciClient, get_client

    client = await get_client()
    try:
        users = await client.list_users(
            status=status,
            domain=domain,
            younger_than=younger_than,
            older_than=older_than,
        )
    finally:
        await client.close()

    normalized = [LuxSciClient.normalize_user(user) for user in users]
    return {
        "count": len(normalized),
        "filters": {
            "status": status,
            "domain": domain,
            "younger_than": younger_than,
            "older_than": older_than,
        },
        "users": normalized,
    }


@tool(
    name="LuxSci: List Domains",
    description="List LuxSci domains for the connected account.",
    category="LuxSci",
    tags=["luxsci", "domains", "migration"],
)
async def list_luxsci_domains() -> dict:
    """Return LuxSci domains in a normalized shape."""
    from modules.luxsci import LuxSciClient, get_client

    client = await get_client()
    try:
        domains = await client.list_domains()
    finally:
        await client.close()

    normalized = [LuxSciClient.normalize_domain(domain) for domain in domains]
    return {"count": len(normalized), "domains": normalized}


@tool(
    name="LuxSci: List Aliases",
    description="List LuxSci aliases for the connected account.",
    category="LuxSci",
    tags=["luxsci", "aliases", "migration"],
)
async def list_luxsci_aliases() -> dict:
    """Return LuxSci aliases in a normalized shape."""
    from modules.luxsci import LuxSciClient, get_client

    client = await get_client()
    try:
        aliases = await client.list_aliases()
    finally:
        await client.close()

    normalized = [LuxSciClient.normalize_alias(alias) for alias in aliases]
    return {"count": len(normalized), "aliases": normalized}
