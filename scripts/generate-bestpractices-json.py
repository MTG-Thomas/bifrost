#!/usr/bin/env python3
"""Generate .bestpractices.json for OpenSSF BadgeApp automation."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

REPO = "MTG-Thomas/bifrost"
BASE = f"https://github.com/{REPO}"
UPSTREAM_JSON = "https://www.bestpractices.dev/projects/12665.json"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".bestpractices.json"

SUBSTITUTIONS = (
    ("github.com/jackmusick/bifrost", f"github.com/{REPO}"),
    ("ghcr.io/jackmusick/", "ghcr.io/mtg-thomas/"),
    (
        "api.securityscorecards.dev/projects/github.com/jackmusick/bifrost",
        f"api.securityscorecards.dev/projects/github.com/{REPO}",
    ),
)


def adapt(text: str | None) -> str | None:
    if not text:
        return text
    for old, new in SUBSTITUTIONS:
        text = text.replace(old, new)
    text = re.sub(
        r" or direct email to jackmmusick@gmail\.com\.?",
        ".",
        text,
    )
    text = re.sub(r" and email to jackmmusick@gmail\.com", "", text)
    text = re.sub(r" or email to jackmmusick@gmail\.com", "", text)
    text = re.sub(
        r"two private channels: GitHub private vulnerability advisory at "
        rf"{re.escape(BASE)}/security/advisories/new \(preferred\) and email to jackmmusick@gmail\.com\.",
        f"private reporting via GitHub private vulnerability advisory at {BASE}/security/advisories/new.",
        text,
    )
    return text


def main() -> None:
    with urllib.request.urlopen(UPSTREAM_JSON) as response:
        upstream = json.load(response)

    skip = {
        "id",
        "user_id",
        "created_at",
        "updated_at",
        "achieved_passing_at",
        "lost_passing_at",
        "last_reminder_at",
        "lock_version",
        "badge_percentage_0",
        "badge_percentage_1",
        "badge_percentage_2",
        "disabled_reminders",
        "homepage_url_status",
        "homepage_url_justification",
    }

    output: dict[str, str] = {
        "name": "Bifrost Integrations",
        "description": adapt(
            upstream.get("description")
            or (
                "Bifrost Integrations is an open-source automation platform "
                "for Integration Services."
            )
        )
        or "",
        "homepage_url": BASE,
        "repo_url": BASE,
        "license": "AGPL-3.0",
        "implementation_languages": upstream.get("implementation_languages")
        or "Python, TypeScript, Shell, JavaScript",
    }

    for key, value in upstream.items():
        if key in skip or key in output:
            continue
        if key.endswith("_status") and value in {"Met", "N/A"}:
            output[key] = value
            justification_key = key.replace("_status", "_justification")
            justification = upstream.get(justification_key)
            if justification:
                output[justification_key] = adapt(justification) or ""

    overrides = {
        "repo_interim_justification": (
            f"Active development on main; commits land continuously. See {BASE}/commits/main"
        ),
        "maintained_justification": (
            f"Active development on main with regular merges, Dependabot updates, "
            f"and CI on every PR. See {BASE}/commits/main"
        ),
        "report_responses_justification": (
            f"Contributors open bug-fix PRs on {BASE}; maintainers review and merge "
            f"or respond. See {BASE}/pulls"
        ),
        "enhancement_responses_justification": (
            f"Enhancement requests filed as GitHub Issues on {BASE} receive "
            f"maintainer engagement. See {BASE}/issues"
        ),
        "vulnerability_report_process_justification": (
            f"{BASE}/blob/main/SECURITY.md documents private reporting via GitHub "
            f"private vulnerability advisory at {BASE}/security/advisories/new."
        ),
        "vulnerability_report_private_justification": (
            f"{BASE}/blob/main/SECURITY.md mandates private reporting via "
            f"{BASE}/security/advisories/new. SECURITY.md explicitly states "
            '"Do not open a public issue for security reports."'
        ),
        "dynamic_analysis_justification": (
            "Playwright E2E tests run on every PR (./test.sh client e2e) against "
            f"the running React+FastAPI stack. Backend e2e (./test.sh e2e) exercises "
            f"the API with PostgreSQL/Redis/RabbitMQ containers. See {BASE}/blob/main/test.sh"
        ),
        "dynamic_analysis_fixed_justification": (
            f"Medium+ findings from CI/CodeQL/Scorecard are triaged via {BASE}/security "
            "and fixed in follow-up PRs."
        ),
        "version_tags_justification": (
            f"First MTG semver tag v1.0.0 published at {BASE}/releases/tag/v1.0.0. "
            f"CI builds signed release artifacts on every v* tag "
            f"({BASE}/blob/main/.github/workflows/ci.yml)."
        ),
        "version_unique_justification": (
            "Release workflow requires unique semver tags (v1.2.3). "
            f"v1.0.0 is the first MTG fork tag at {BASE}/tags."
        ),
        "version_semver_justification": (
            f"MTG-owned semver line documented in {BASE}/blob/main/docs/VERSIONING.md; "
            f"first tag v1.0.0 at {BASE}/tags."
        ),
        "release_notes_justification": (
            f"v1.0.0 release notes include a themed change summary and upstream "
            f"baseline at {BASE}/releases/tag/v1.0.0."
        ),
        "release_notes_vulns_justification": (
            f"v1.0.0 release notes include a Fixed CVEs section at "
            f"{BASE}/releases/tag/v1.0.0."
        ),
        "no_leaked_credentials_justification": (
            "GitHub secret-scanning + push protection are enabled on "
            f"{BASE} (verified via gh api repos/MTG-Thomas/bifrost)."
        ),
        "test_continuous_integration_justification": (
            f".github/workflows/ci.yml on {BASE} runs lint + unit + e2e on every "
            "push and PR to main."
        ),
        "static_analysis_often_justification": (
            f"CodeQL runs on every push to main, every PR, and weekly cron "
            f"({BASE}/blob/main/.github/workflows/codeql.yml)."
        ),
        "dco_justification": (
            f"Developer Certificate of Origin 1.1 and Signed-off-by requirements "
            f"are documented in {BASE}/blob/main/CONTRIBUTING.md."
        ),
        "governance_justification": (
            f"MTG fork governance (roles, decisions, continuity) is documented in "
            f"{BASE}/blob/main/GOVERNANCE.md."
        ),
        "code_of_conduct_justification": (
            f"Contributor Covenant 2.1 is published at "
            f"{BASE}/blob/main/CODE_OF_CONDUCT.md with enforcement contacts."
        ),
        "roles_responsibilities_justification": (
            f"Maintainer, contributor, and code-owner roles are defined in "
            f"{BASE}/blob/main/GOVERNANCE.md and {BASE}/blob/main/.github/CODEOWNERS."
        ),
        "access_continuity_justification": (
            f"Backup maintainers Doug Eckhart and Eric Atlas (@MTG-Thomas org members) "
            f"and succession steps are documented in {BASE}/blob/main/GOVERNANCE.md."
        ),
        "bus_factor_justification": (
            f"Multiple @MTG-Thomas maintainers with named backups (Doug Eckhart, Eric Atlas) "
            f"and org-owner paths are documented in {BASE}/blob/main/GOVERNANCE.md."
        ),
        "contributors_unassociated_justification": (
            f"External contributors are explicitly welcome via PR/issue flow; see "
            f"{BASE}/blob/main/GOVERNANCE.md and {BASE}/blob/main/CONTRIBUTING.md."
        ),
    }

    release_pending_statuses: set[str] = set()

    for key, value in overrides.items():
        status_key = key.replace("_justification", "_status")
        output[status_key] = "Unmet" if status_key in release_pending_statuses else "Met"
        output[key] = value

    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} ({len(output)} fields)")


if __name__ == "__main__":
    main()
