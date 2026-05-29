# Governance — MTG-Thomas/bifrost

**Status:** early draft (OpenSSF Silver / issue #290). Expect revision as MTG
operator roles and org policy are confirmed.

This document describes how the Midtown Technology Group (MTG) fork of Bifrost
is maintained, who can make decisions, and how we preserve project continuity.

Related documents:

- [CONTRIBUTING.md](CONTRIBUTING.md) — day-to-day contributor workflow
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — community standards
- [SECURITY.md](SECURITY.md) — vulnerability reporting
- [docs/VERSIONING.md](docs/VERSIONING.md) — MTG semver and releases

## Project scope

`MTG-Thomas/bifrost` is the **platform/runtime** repository for the MTG Bifrost
fork (API, client, workers, CI, and core docs). It is distinct from:

- `MTG-Thomas/bifrost-workspace` — MSP workspace workflows and integrations
- `MTG-Thomas/bifrost-infra` — deployment and cloud infrastructure
- `MTG-Thomas/bifrost-ops` — operator runbooks and team onboarding

Upstream lineage is documented in release notes and [VERSIONING.md](docs/VERSIONING.md);
governance here applies to the MTG fork only.

## Roles and responsibilities

| Role | Who | Responsibilities |
|------|-----|------------------|
| **Organization owners** | GitHub owners of the `MTG-Thomas` org | Org settings, billing, security defaults (2FA, secret scanning), backup org admins |
| **Primary maintainer** | [Thomas Bray](https://github.com/MTG-Thomas) | Day-to-day triage, releases, OpenSSF badge stewardship, CoC enforcement |
| **Backup maintainers** | [Doug Eckhart](https://github.com/MTGDeckhart), [Eric Atlas](https://github.com/eric-midtowntg-com) | Assume stewardship if the primary maintainer is unavailable; same responsibilities as the primary maintainer |
| **Code owners** | See [.github/CODEOWNERS](.github/CODEOWNERS) | Required review on changes under owned paths (currently default `@MTG-Thomas`) |
| **Contributors** | Anyone who opens issues or PRs | Propose changes; follow [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| **Automation** | GitHub Actions, Dependabot, Scorecard, CodeQL | CI gates on `main`; no direct merge without passing checks where configured |

Maintainers are expected to:

- Keep `main` protected and release tags auditable (see [bifrost-release skill](.claude/skills/bifrost-release/SKILL.md)).
- Respond to security reports per [SECURITY.md](SECURITY.md).
- Prefer issues and PRs for substantive decisions (transparent record).

## Decision process

1. **Track work in GitHub Issues** — if it is not an issue, it is not on the roadmap ([CONTRIBUTING.md](CONTRIBUTING.md)).
2. **Implement via pull request** — all changes land through PR review; direct pushes to `main` are disallowed by branch protection.
3. **Review** — at least one approval from a code owner / maintainer; sensitive paths called out in CONTRIBUTING receive extra scrutiny.
4. **Merge** — squash or merge per repo settings after required CI checks pass.
5. **Release** — semver tags on `MTG-Thomas/bifrost` only; human release notes for OpenSSF criteria (see release skill).

Larger or contentious changes should use a design note under `docs/superpowers/specs/` or a dedicated issue thread before large diffs.

## Contributions from unassociated contributors

This project **accepts meaningful contributions from people who are not MTG
employees or org members**. You do not need org membership to:

- Open issues and discuss design
- Submit pull requests
- Pick up [`help wanted`](https://github.com/MTG-Thomas/bifrost/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) or [`good first issue`](https://github.com/MTG-Thomas/bifrost/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) work

External contributors merge through the same PR review path as maintainers.
Credit appears in commit history and release notes when applicable.

## Developer Certificate of Origin

Contributions require DCO sign-off per [CONTRIBUTING.md](CONTRIBUTING.md) (Developer
Certificate of Origin 1.1). Maintainers may reject commits that lack valid
`Signed-off-by` lines or equivalent GitHub DCO integration when enabled.

## Access continuity and bus factor

**Goal:** no single person is the only holder of merge access, org admin access,
or release credentials.

Current mechanisms:

- **Primary maintainer:** [Thomas Bray](https://github.com/MTG-Thomas).
- **Named backup maintainers:** [Doug Eckhart](https://github.com/MTGDeckhart) and [Eric Atlas](https://github.com/eric-midtowntg-com) (Midtown org members with merge access).
- **Multiple org owners** on `MTG-Thomas` (GitHub organization settings).
- **Team-based ownership** via `@MTG-Thomas` in [CODEOWNERS](.github/CODEOWNERS).
- **Branch protection on `main`** — required reviews and CI before merge.
- **Documented operator paths** in [bifrost-ops](https://github.com/MTG-Thomas/bifrost-ops) (Windows onboarding, shared dev VM, release runbooks).

If the primary maintainer leaves or is unavailable:

1. [Doug Eckhart](https://github.com/MTGDeckhart) or [Eric Atlas](https://github.com/eric-midtowntg-com) assumes day-to-day stewardship (issues, PRs, releases).
2. Remaining org owners reassign BadgeApp project ownership and any org-only credentials through GitHub org admin settings (document owner in internal ops notes; not stored in this repo).
3. Emergency merges follow the same PR + review rule except where GitHub org policy allows break-glass admin merge with post-incident review.

**Informal upstream advisor:** [Jack Musick](https://github.com/jackmusick) (original Bifrost creator) is not an MTG fork maintainer but may assist with architectural or release questions in a pinch.

## Security and conduct escalation

- **Vulnerabilities:** [SECURITY.md](SECURITY.md) — private GitHub advisories only.
- **Code of Conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — maintainer contact paths listed there.

## Related automation and evidence

| Concern | Evidence |
|---------|----------|
| CI on every PR | [.github/workflows/ci.yml](.github/workflows/ci.yml) |
| Code review | Branch protection + CODEOWNERS |
| Dependency updates | [.github/dependabot.yml](.github/dependabot.yml) |
| OpenSSF badge | [docs/security/openssf-best-practices-badge.md](docs/security/openssf-best-practices-badge.md) |
| Semver releases | [docs/VERSIONING.md](docs/VERSIONING.md), [v1.0.0 release](https://github.com/MTG-Thomas/bifrost/releases/tag/v1.0.0) |

## Revision history

| Date | Change |
|------|--------|
| 2026-05-29 | Initial draft for OpenSSF Silver #290 |
| 2026-05-29 | Named backup maintainers (Doug Eckhart, Eric Atlas); Jack Musick as informal upstream advisor |
| 2026-05-29 | Thomas Bray as primary maintainer; GitHub profile links for all maintainers |
