"""
Meraki admin baseline workflows.

These workflows compare all Meraki organizations to a known-good baseline org
and optionally add/update standard Midtown admins to match that baseline.
"""

from __future__ import annotations

import asyncio

from bifrost import config, workflow
from modules.meraki import MerakiClient

MERAKI_ADMIN_INVENTORY_CONCURRENCY = 4
MERAKI_ADMIN_WRITE_DELAY_SECONDS = 0.25
MERAKI_POLICY_CUSTOMER_EXCLUSIONS_KEY = "meraki_customer_org_exclusions_csv"
MERAKI_POLICY_PROCUREMENT_ORGS_KEY = "meraki_procurement_license_org_names_csv"
MERAKI_POLICY_PROCUREMENT_ALLOWED_ADMINS_KEY = (
    "meraki_procurement_license_allowed_admin_emails_csv"
)
DEFAULT_BASELINE_EXCLUDED_ORG_NAMES = [
    "Taylor Computer Solutions",
    "Jacobson Hile Kight",
    "Cynthia L Hovey DDS",
    "Connected Healthcare Systems",
    "MTG Kntlnd Licenses",
    "MTG More Licenses",
    "MTG WAP Licenses",
    "MTGLicense",
]
DEFAULT_PROCUREMENT_LICENSE_ORG_NAMES = [
    "MTG Kntlnd Licenses",
    "MTG More Licenses",
    "MTG WAP Licenses",
    "MTGLicense",
]
DEFAULT_PROCUREMENT_ALLOWED_ADMIN_EMAILS = [
    "thomas@midtowntg.com",
    "doug@midtowntg.com",
    "eric@carbonpeaktech.com",
]


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _matches_all_terms(name: str, terms: list[str]) -> bool:
    normalized_name = name.strip().lower()
    return all(term in normalized_name for term in terms)


def _to_csv(values: list[str]) -> str:
    return ",".join(values)


async def _get_meraki_admin_governance_policy() -> dict[str, list[str] | str]:
    customer_org_exclusions_csv = await config.get(
        MERAKI_POLICY_CUSTOMER_EXCLUSIONS_KEY,
        default=_to_csv(DEFAULT_BASELINE_EXCLUDED_ORG_NAMES),
        scope="global",
    )
    procurement_org_names_csv = await config.get(
        MERAKI_POLICY_PROCUREMENT_ORGS_KEY,
        default=_to_csv(DEFAULT_PROCUREMENT_LICENSE_ORG_NAMES),
        scope="global",
    )
    procurement_allowed_admin_emails_csv = await config.get(
        MERAKI_POLICY_PROCUREMENT_ALLOWED_ADMINS_KEY,
        default=_to_csv(DEFAULT_PROCUREMENT_ALLOWED_ADMIN_EMAILS),
        scope="global",
    )
    return {
        "customer_org_exclusions_csv": str(customer_org_exclusions_csv or ""),
        "customer_org_exclusions": _parse_csv(str(customer_org_exclusions_csv or "")),
        "procurement_org_names_csv": str(procurement_org_names_csv or ""),
        "procurement_org_names": _parse_csv(str(procurement_org_names_csv or "")),
        "procurement_allowed_admin_emails_csv": str(
            procurement_allowed_admin_emails_csv or ""
        ),
        "procurement_allowed_admin_emails": _parse_csv(
            str(procurement_allowed_admin_emails_csv or "")
        ),
    }


async def _effective_baseline_excluded_org_names(
    excluded_org_names_csv: str | None,
) -> set[str]:
    policy = await _get_meraki_admin_governance_policy()
    return set(policy["customer_org_exclusions"]) | set(_parse_csv(excluded_org_names_csv))


def _select_target_org_results(
    inventory: dict[str, dict],
    *,
    target_org_names_csv: str | None,
    org_name_match_all_csv: str | None,
) -> list[dict]:
    explicit_names = set(_parse_csv(target_org_names_csv))
    match_terms = _parse_csv(org_name_match_all_csv)

    selected: list[dict] = []
    for organization_name in sorted(inventory, key=str.lower):
        result = inventory[organization_name]
        normalized_name = result["organization_name"].strip().lower()
        if explicit_names and normalized_name in explicit_names:
            selected.append(result)
            continue
        if match_terms and _matches_all_terms(normalized_name, match_terms):
            selected.append(result)

    return selected


def _resolve_admin_templates(
    *,
    baseline: dict,
    inventory: dict[str, dict],
    admin_emails: list[str],
) -> dict[str, dict]:
    templates = {
        admin["email"]: admin
        for admin in baseline["admins"]
        if admin.get("email")
    }

    for email in admin_emails:
        if email in templates:
            continue
        for result in inventory.values():
            for admin in result.get("all_admins", result.get("admins", [])):
                if admin.get("email") == email:
                    templates[email] = admin
                    break
            if email in templates:
                break

    missing_templates = [email for email in admin_emails if email not in templates]
    if missing_templates:
        raise RuntimeError(
            "No Meraki admin template could be found for: "
            + ", ".join(sorted(missing_templates))
        )

    return templates


async def _load_org_admin_inventory(
    *,
    baseline_org_name: str,
    include_account_statuses_csv: str,
) -> tuple[MerakiClient, dict[str, dict], dict]:
    from modules.meraki import get_client

    client = await get_client(scope="global")
    organizations = await client.list_organizations()
    include_statuses = set(_parse_csv(include_account_statuses_csv))

    semaphore = asyncio.Semaphore(MERAKI_ADMIN_INVENTORY_CONCURRENCY)

    async def fetch_org_admins(organization: dict) -> dict:
        normalized_org = MerakiClient.normalize_organization(organization)
        org_id = normalized_org["id"]
        org_name = normalized_org["name"] or org_id

        try:
            async with semaphore:
                admins = await client.list_organization_admins(org_id)
        except Exception as exc:
            return {
                "organization_id": org_id,
                "organization_name": org_name,
                "error": str(exc),
            }

        normalized_admins = [
            MerakiClient.normalize_admin(admin)
            for admin in admins
            if isinstance(admin, dict)
        ]

        eligible_admins = [
            admin
            for admin in normalized_admins
            if admin["email"]
            and (
                not include_statuses
                or admin["accountStatus"].lower() in include_statuses
            )
        ]

        return {
            "organization_id": org_id,
            "organization_name": org_name,
            "admins": eligible_admins,
            "all_admins": normalized_admins,
        }

    results = await asyncio.gather(
        *(fetch_org_admins(organization) for organization in organizations)
    )

    inventory = {
        result["organization_name"]: result
        for result in results
        if result.get("organization_name")
    }

    baseline = inventory.get(baseline_org_name)
    if not baseline:
        raise RuntimeError(
            f"Baseline organization '{baseline_org_name}' was not found in Meraki."
        )
    if baseline.get("error"):
        raise RuntimeError(
            f"Baseline organization '{baseline_org_name}' could not be audited: {baseline['error']}"
        )

    return client, inventory, baseline


async def _sleep_after_meraki_write(write_delay_seconds: float) -> None:
    if write_delay_seconds > 0:
        await asyncio.sleep(write_delay_seconds)


@workflow(
    name="Meraki: Get Admin Governance Policy",
    description="Return the configured Meraki admin governance policy used by audit and sync workflows.",
    category="Meraki",
    tags=["meraki", "policy", "config", "admins"],
)
async def get_meraki_admin_governance_policy() -> dict:
    policy = await _get_meraki_admin_governance_policy()
    return {
        **policy,
        "config_keys": {
            "customer_org_exclusions_csv": MERAKI_POLICY_CUSTOMER_EXCLUSIONS_KEY,
            "procurement_org_names_csv": MERAKI_POLICY_PROCUREMENT_ORGS_KEY,
            "procurement_allowed_admin_emails_csv": MERAKI_POLICY_PROCUREMENT_ALLOWED_ADMINS_KEY,
        },
    }


@workflow(
    name="Meraki: Save Admin Governance Policy",
    description="Persist the Meraki admin governance policy used by audit and sync workflows.",
    category="Meraki",
    tags=["meraki", "policy", "config", "admins"],
)
async def save_meraki_admin_governance_policy(
    customer_org_exclusions_csv: str,
    procurement_org_names_csv: str,
    procurement_allowed_admin_emails_csv: str,
) -> dict:
    await config.set(
        MERAKI_POLICY_CUSTOMER_EXCLUSIONS_KEY,
        customer_org_exclusions_csv,
        scope="global",
    )
    await config.set(
        MERAKI_POLICY_PROCUREMENT_ORGS_KEY,
        procurement_org_names_csv,
        scope="global",
    )
    await config.set(
        MERAKI_POLICY_PROCUREMENT_ALLOWED_ADMINS_KEY,
        procurement_allowed_admin_emails_csv,
        scope="global",
    )
    return await get_meraki_admin_governance_policy()


@workflow(
    name="Meraki: Audit Admins Against Baseline Organization",
    description="Compare Meraki org admins to a baseline organization.",
    category="Meraki",
    tags=["meraki", "audit", "admins", "baseline"],
)
async def audit_meraki_admins_against_baseline(
    baseline_org_name: str = "Midtown Technology Group",
    required_admin_emails_csv: str | None = None,
    extra_valid_admin_emails_csv: str = "eric@carbonpeaktech.com",
    excluded_org_names_csv: str = "",
    include_account_statuses_csv: str = "ok,pending,unverified",
) -> dict:
    client, inventory, baseline = await _load_org_admin_inventory(
        baseline_org_name=baseline_org_name,
        include_account_statuses_csv=include_account_statuses_csv,
    )
    try:
        baseline_templates = {
            admin["email"]: admin
            for admin in baseline["admins"]
        }
        selected_admins = set(_parse_csv(required_admin_emails_csv)) or set(
            baseline_templates
        )
        selected_admins.update(_parse_csv(extra_valid_admin_emails_csv))
        expected_admins = sorted(selected_admins)
        excluded_org_names = await _effective_baseline_excluded_org_names(
            excluded_org_names_csv
        )

        disparities = []
        errors = []
        skipped_excluded = []

        for organization_name in sorted(inventory, key=str.lower):
            result = inventory[organization_name]
            normalized_org_name = result["organization_name"].strip().lower()
            if normalized_org_name in excluded_org_names:
                skipped_excluded.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                    }
                )
                continue
            if result.get("error"):
                errors.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "error": result["error"],
                    }
                )
                continue

            admin_emails = sorted({admin["email"] for admin in result["admins"]})
            current_admins = set(admin_emails)
            missing_admins = sorted(
                email for email in expected_admins if email not in current_admins
            )
            extra_admins = sorted(
                email for email in current_admins if email not in expected_admins
            )

            if missing_admins or extra_admins:
                disparities.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "missing_admins": missing_admins,
                        "extra_admins": extra_admins,
                        "admin_count": len(current_admins),
                    }
                )

        disparities.sort(
            key=lambda item: (
                -len(item["missing_admins"]),
                item["organization_name"].lower(),
            )
        )

        return {
            "baseline_organization": baseline_org_name,
            "baseline_admins": expected_admins,
            "excluded_org_names": sorted(excluded_org_names),
            "skipped_excluded": skipped_excluded,
            "organizations_audited": len(
                [
                    item for item in inventory.values()
                    if not item.get("error")
                    and item["organization_name"].strip().lower() not in excluded_org_names
                ]
            ),
            "organizations_with_disparities": len(disparities),
            "disparities": disparities,
            "organizations_with_errors": errors,
        }
    finally:
        await client.close()


@workflow(
    name="Meraki: Sync Admins From Baseline Organization",
    description="Add or update standard Meraki admins from a baseline organization.",
    category="Meraki",
    tags=["meraki", "sync", "admins", "baseline"],
)
async def sync_meraki_admins_from_baseline(
    baseline_org_name: str = "Midtown Technology Group",
    required_admin_emails_csv: str = "",
    target_org_names_csv: str | None = None,
    excluded_org_names_csv: str = "",
    include_account_statuses_csv: str = "ok,pending,unverified",
    write_delay_seconds: float = MERAKI_ADMIN_WRITE_DELAY_SECONDS,
    dry_run: bool = True,
) -> dict:
    client, inventory, baseline = await _load_org_admin_inventory(
        baseline_org_name=baseline_org_name,
        include_account_statuses_csv=include_account_statuses_csv,
    )
    try:
        baseline_templates = {
            admin["email"]: admin
            for admin in baseline["admins"]
        }

        target_admin_emails = _parse_csv(required_admin_emails_csv)
        if not target_admin_emails:
            raise RuntimeError("required_admin_emails_csv must include at least one email.")

        missing_from_baseline = sorted(
            email for email in target_admin_emails if email not in baseline_templates
        )
        if missing_from_baseline:
            raise RuntimeError(
                "These emails are not present in the baseline org: "
                + ", ".join(missing_from_baseline)
            )

        target_org_names = set(_parse_csv(target_org_names_csv))
        excluded_org_names = await _effective_baseline_excluded_org_names(
            excluded_org_names_csv
        )
        created = []
        updated = []
        unchanged = []
        skipped_errors = []
        skipped_excluded = []

        for organization_name in sorted(inventory, key=str.lower):
            result = inventory[organization_name]
            normalized_org_name = result["organization_name"].strip().lower()

            if target_org_names and normalized_org_name not in target_org_names:
                continue
            if normalized_org_name in excluded_org_names:
                skipped_excluded.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                    }
                )
                continue
            if normalized_org_name == baseline_org_name.strip().lower():
                continue

            if result.get("error"):
                skipped_errors.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "error": result["error"],
                    }
                )
                continue

            existing_by_email = {
                admin["email"]: admin
                for admin in result.get("all_admins", result["admins"])
            }

            for email in target_admin_emails:
                template = baseline_templates[email]
                existing = existing_by_email.get(email)
                desired_tags = template["tags"]
                desired_networks = template["networks"]
                desired_name = template["name"]
                desired_access = template["orgAccess"]

                if existing is None:
                    action = {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "email": email,
                        "action": "create",
                    }
                    created.append(action)
                    if not dry_run:
                        await client.create_organization_admin(
                            result["organization_id"],
                            email=email,
                            name=desired_name,
                            org_access=desired_access,
                            tags=desired_tags,
                            networks=desired_networks,
                        )
                        await _sleep_after_meraki_write(write_delay_seconds)
                    continue

                drift = {}
                if existing["name"] != desired_name:
                    drift["name"] = {
                        "current": existing["name"],
                        "desired": desired_name,
                    }
                if existing["orgAccess"] != desired_access:
                    drift["orgAccess"] = {
                        "current": existing["orgAccess"],
                        "desired": desired_access,
                    }
                if sorted(existing["tags"]) != sorted(desired_tags):
                    drift["tags"] = {
                        "current": existing["tags"],
                        "desired": desired_tags,
                    }
                if sorted(existing["networks"]) != sorted(desired_networks):
                    drift["networks"] = {
                        "current": existing["networks"],
                        "desired": desired_networks,
                    }

                if drift:
                    action = {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "email": email,
                        "action": "update",
                        "drift": drift,
                    }
                    updated.append(action)
                    if not dry_run:
                        await client.update_organization_admin(
                            result["organization_id"],
                            admin_id=existing["id"],
                            name=desired_name,
                            org_access=desired_access,
                            tags=desired_tags,
                            networks=desired_networks,
                        )
                        await _sleep_after_meraki_write(write_delay_seconds)
                else:
                    unchanged.append(
                        {
                            "organization_id": result["organization_id"],
                            "organization_name": result["organization_name"],
                            "email": email,
                        }
                    )

        return {
            "baseline_organization": baseline_org_name,
            "target_admin_emails": target_admin_emails,
            "excluded_org_names": sorted(excluded_org_names),
            "dry_run": dry_run,
            "created": created,
            "updated": updated,
            "unchanged_count": len(unchanged),
            "skipped_excluded": skipped_excluded,
            "skipped_errors": skipped_errors,
        }
    finally:
        await client.close()


@workflow(
    name="Meraki: Remove Admin Across Organizations",
    description="Remove a specific Meraki admin email across organizations.",
    category="Meraki",
    tags=["meraki", "cleanup", "admins", "remove"],
)
async def remove_meraki_admin_across_organizations(
    admin_email: str,
    target_org_names_csv: str | None = None,
    excluded_org_names_csv: str = "",
    include_account_statuses_csv: str = "ok,pending,unverified",
    write_delay_seconds: float = MERAKI_ADMIN_WRITE_DELAY_SECONDS,
    dry_run: bool = True,
) -> dict:
    normalized_email = admin_email.strip().lower()
    if not normalized_email:
        raise RuntimeError("admin_email is required.")

    client, inventory, _baseline = await _load_org_admin_inventory(
        baseline_org_name="Midtown Technology Group",
        include_account_statuses_csv=include_account_statuses_csv,
    )
    try:
        target_org_names = set(_parse_csv(target_org_names_csv))
        excluded_org_names = await _effective_baseline_excluded_org_names(
            excluded_org_names_csv
        )
        removed = []
        skipped_missing = []
        skipped_errors = []
        skipped_excluded = []

        for organization_name in sorted(inventory, key=str.lower):
            result = inventory[organization_name]
            normalized_org_name = result["organization_name"].strip().lower()

            if target_org_names and normalized_org_name not in target_org_names:
                continue
            if normalized_org_name in excluded_org_names:
                skipped_excluded.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                    }
                )
                continue
            if result.get("error"):
                skipped_errors.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "error": result["error"],
                    }
                )
                continue

            existing = next(
                (
                    admin
                    for admin in result["admins"]
                    if admin["email"] == normalized_email
                ),
                None,
            )
            if existing is None:
                skipped_missing.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                    }
                )
                continue

            action = {
                "organization_id": result["organization_id"],
                "organization_name": result["organization_name"],
                "email": normalized_email,
                "admin_id": existing["id"],
            }
            removed.append(action)
            if not dry_run:
                await client.delete_organization_admin(
                    result["organization_id"],
                    admin_id=existing["id"],
                )
                await _sleep_after_meraki_write(write_delay_seconds)

        return {
            "admin_email": normalized_email,
            "excluded_org_names": sorted(excluded_org_names),
            "dry_run": dry_run,
            "removed": removed,
            "skipped_missing_count": len(skipped_missing),
            "skipped_excluded": skipped_excluded,
            "skipped_errors": skipped_errors,
        }
    finally:
        await client.close()


@workflow(
    name="Meraki: Audit Procurement License Organization Admins",
    description="Audit restricted admin coverage for MTG procurement/license Meraki orgs.",
    category="Meraki",
    tags=["meraki", "audit", "admins", "license", "procurement"],
)
async def audit_meraki_procurement_license_admins(
    allowed_admin_emails_csv: str = "",
    target_org_names_csv: str = "",
    org_name_match_all_csv: str = "mtg,license",
    include_account_statuses_csv: str = "ok,pending,unverified",
) -> dict:
    client, inventory, baseline = await _load_org_admin_inventory(
        baseline_org_name="Midtown Technology Group",
        include_account_statuses_csv=include_account_statuses_csv,
    )
    try:
        policy = await _get_meraki_admin_governance_policy()
        allowed_admin_emails = _parse_csv(allowed_admin_emails_csv) or list(
            policy["procurement_allowed_admin_emails"]
        )
        effective_target_org_names_csv = (
            target_org_names_csv or policy["procurement_org_names_csv"]
        )
        if not allowed_admin_emails:
            raise RuntimeError("allowed_admin_emails_csv must include at least one email.")

        targets = _select_target_org_results(
            inventory,
            target_org_names_csv=effective_target_org_names_csv,
            org_name_match_all_csv=org_name_match_all_csv,
        )
        disparities = []
        errors = []

        for result in targets:
            if result.get("error"):
                errors.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "error": result["error"],
                    }
                )
                continue

            current_emails = sorted(
                {
                    admin["email"]
                    for admin in result.get("all_admins", result["admins"])
                    if admin.get("email")
                }
            )
            current_admins = set(current_emails)
            missing_admins = sorted(
                email for email in allowed_admin_emails if email not in current_admins
            )
            extra_admins = sorted(
                email for email in current_admins if email not in allowed_admin_emails
            )

            disparities.append(
                {
                    "organization_id": result["organization_id"],
                    "organization_name": result["organization_name"],
                    "missing_admins": missing_admins,
                    "extra_admins": extra_admins,
                    "admin_count": len(current_admins),
                }
            )

        return {
            "allowed_admin_emails": allowed_admin_emails,
            "target_organizations": [
                {
                    "organization_id": result["organization_id"],
                    "organization_name": result["organization_name"],
                }
                for result in targets
            ],
            "organizations_with_disparities": len(
                [
                    item for item in disparities
                    if item["missing_admins"] or item["extra_admins"]
                ]
            ),
            "disparities": disparities,
            "organizations_with_errors": errors,
            "baseline_organization": baseline["organization_name"],
        }
    finally:
        await client.close()


@workflow(
    name="Meraki: Sync Procurement License Organization Admins",
    description="Enforce a restricted admin roster on MTG procurement/license Meraki orgs.",
    category="Meraki",
    tags=["meraki", "sync", "admins", "license", "procurement"],
)
async def sync_meraki_procurement_license_admins(
    allowed_admin_emails_csv: str = "",
    target_org_names_csv: str = "",
    org_name_match_all_csv: str = "mtg,license",
    include_account_statuses_csv: str = "ok,pending,unverified",
    write_delay_seconds: float = MERAKI_ADMIN_WRITE_DELAY_SECONDS,
    dry_run: bool = True,
) -> dict:
    client, inventory, baseline = await _load_org_admin_inventory(
        baseline_org_name="Midtown Technology Group",
        include_account_statuses_csv=include_account_statuses_csv,
    )
    try:
        policy = await _get_meraki_admin_governance_policy()
        allowed_admin_emails = _parse_csv(allowed_admin_emails_csv) or list(
            policy["procurement_allowed_admin_emails"]
        )
        effective_target_org_names_csv = (
            target_org_names_csv or policy["procurement_org_names_csv"]
        )
        if not allowed_admin_emails:
            raise RuntimeError("allowed_admin_emails_csv must include at least one email.")

        templates = _resolve_admin_templates(
            baseline=baseline,
            inventory=inventory,
            admin_emails=allowed_admin_emails,
        )

        targets = _select_target_org_results(
            inventory,
            target_org_names_csv=effective_target_org_names_csv,
            org_name_match_all_csv=org_name_match_all_csv,
        )

        created = []
        updated = []
        removed = []
        unchanged = []
        skipped_errors = []

        for result in targets:
            if result.get("error"):
                skipped_errors.append(
                    {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "error": result["error"],
                    }
                )
                continue

            all_admins = result.get("all_admins", result["admins"])
            existing_by_email = {
                admin["email"]: admin
                for admin in all_admins
                if admin.get("email")
            }
            current_admins = set(existing_by_email)
            allowed_set = set(allowed_admin_emails)

            for email in allowed_admin_emails:
                template = templates[email]
                existing = existing_by_email.get(email)
                desired_name = template["name"]
                desired_access = template["orgAccess"]
                desired_tags = template["tags"]
                desired_networks = template["networks"]

                if existing is None:
                    action = {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "email": email,
                        "action": "create",
                    }
                    created.append(action)
                    if not dry_run:
                        await client.create_organization_admin(
                            result["organization_id"],
                            email=email,
                            name=desired_name,
                            org_access=desired_access,
                            tags=desired_tags,
                            networks=desired_networks,
                        )
                        await _sleep_after_meraki_write(write_delay_seconds)
                    continue

                drift = {}
                if existing["name"] != desired_name:
                    drift["name"] = {
                        "current": existing["name"],
                        "desired": desired_name,
                    }
                if existing["orgAccess"] != desired_access:
                    drift["orgAccess"] = {
                        "current": existing["orgAccess"],
                        "desired": desired_access,
                    }
                if sorted(existing["tags"]) != sorted(desired_tags):
                    drift["tags"] = {
                        "current": existing["tags"],
                        "desired": desired_tags,
                    }
                if sorted(existing["networks"]) != sorted(desired_networks):
                    drift["networks"] = {
                        "current": existing["networks"],
                        "desired": desired_networks,
                    }

                if drift:
                    action = {
                        "organization_id": result["organization_id"],
                        "organization_name": result["organization_name"],
                        "email": email,
                        "action": "update",
                        "drift": drift,
                    }
                    updated.append(action)
                    if not dry_run:
                        await client.update_organization_admin(
                            result["organization_id"],
                            admin_id=existing["id"],
                            name=desired_name,
                            org_access=desired_access,
                            tags=desired_tags,
                            networks=desired_networks,
                        )
                        await _sleep_after_meraki_write(write_delay_seconds)
                else:
                    unchanged.append(
                        {
                            "organization_id": result["organization_id"],
                            "organization_name": result["organization_name"],
                            "email": email,
                        }
                    )

            for email in sorted(current_admins - allowed_set):
                existing = existing_by_email[email]
                action = {
                    "organization_id": result["organization_id"],
                    "organization_name": result["organization_name"],
                    "email": email,
                    "admin_id": existing["id"],
                }
                removed.append(action)
                if not dry_run:
                    await client.delete_organization_admin(
                        result["organization_id"],
                        admin_id=existing["id"],
                    )
                    await _sleep_after_meraki_write(write_delay_seconds)

        return {
            "allowed_admin_emails": allowed_admin_emails,
            "target_organizations": [
                {
                    "organization_id": result["organization_id"],
                    "organization_name": result["organization_name"],
                }
                for result in targets
            ],
            "dry_run": dry_run,
            "created": created,
            "updated": updated,
            "removed": removed,
            "unchanged_count": len(unchanged),
            "skipped_errors": skipped_errors,
        }
    finally:
        await client.close()
