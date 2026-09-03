# Security Policy

## Supported Versions

Bifrost is in active development against `main`. Only `main` is supported
at this time — there are no LTS branches. If you're running a fork or a
historical commit, please rebase onto current `main` before reporting.

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |
| Older   | :x:                |

## Reporting a Vulnerability

**Do not open a public issue for security reports.**

Report privately through GitHub's private vulnerability reporting flow:

### 1. GitHub private vulnerability reporting (preferred)

Go to https://github.com/gobifrost/bifrost/security/advisories/new
and submit a draft advisory. This keeps the report confidential and lets
us discuss, patch, and coordinate disclosure inside GitHub's tooling.

If you cannot use that flow, contact a repository maintainer through a
private channel and do not paste exploit details, credentials, or full PoCs
in public GitHub issues, discussions, or pull requests.

### Response SLA

- **Acknowledgment:** within 48 hours of report
- **Triage + severity assessment:** within 7 days
- **Patch + advisory disclosure:** depends on severity; we'll communicate
  expected timelines after triage

We don't currently run a paid bug bounty. If your report leads to a fix,
we'll credit you in the advisory and the release notes (unless you'd
rather stay anonymous).

## Supply-Chain Practices

The repo runs:

- **Dependabot alerts + security updates** for `pip`, `npm`, `docker`,
  `github-actions` (see `.github/dependabot.yml`)
- **CodeQL** SAST on push, PR, and weekly schedule
  (see `.github/workflows/codeql.yml`)
- **Secret scanning + push protection** at the repo level
- **OpenSSF Scorecard** weekly, results published to
  https://api.securityscorecards.dev/projects/github.com/gobifrost/bifrost

### Dependency update policy

Dependabot acts like unattended upgrades for low-risk maintenance. It checks
weekly, waits 7 days before proposing stable patches and 14 days before stable
minors, groups compatible updates, and merges them after CI passes.

| Update type | Dependabot behavior |
|---|---|
| Security advisory bumps | Open immediately and merge if CI is green |
| Stable patch (`x.y.Z`) | Group after a 7-day cooldown and merge if CI is green |
| Stable minor (`x.Y.z`) | Group after a 14-day cooldown and merge if CI is green |
| Major (`X.y.z`) | No routine PR; upgrade deliberately |
| Prerelease (`alpha`, `beta`, `rc`) | Keep an explicit pin until stable unless we opt in |
| Docker base image | Security PRs only; refresh deliberately |
| GitHub Actions (pinned to SHA) | Stable patch/minor updates merge if CI is green |

Auto-merged PRs still go through the full CI suite (lint, typecheck,
unit, e2e). If a green-CI auto-merge later breaks something, the
behavior is by definition uncovered by tests. The response is to add
the test and fix forward, not to gate the auto-merge harder.

## What's Out of Scope

- Issues in dependencies — report those upstream.
- Issues in self-hosted deployments where the operator misconfigured
  the deployment (open ports, weak passwords, etc.) — these are
  operator concerns, not Bifrost vulnerabilities.
- DoS via deliberate resource exhaustion of a single-tenant deploy.
