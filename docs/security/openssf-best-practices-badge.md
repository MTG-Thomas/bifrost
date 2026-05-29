# OpenSSF Best Practices Badge (MTG fork)

Project **13022**: https://www.bestpractices.dev/en/projects/13022

Upstream earned Passing as [project 12665](https://www.bestpractices.dev/en/projects/12665).
The MTG fork maintains its own `.bestpractices.json` at the repo root and tracks
semver releases from **v1.0.0** ([VERSIONING.md](../VERSIONING.md)).

## Current status

| Tier | Typical progress | Notes |
|------|------------------|-------|
| **Passing** | 100% | Met after v1.0.0 tag, release notes, and JSON refresh ([#298](https://github.com/MTG-Thomas/bifrost/pull/298)) |
| **Baseline tier 1 (OSPS)** | ~65%+ | OSPS answers import on the **baseline-1** form, not Passing or Silver |
| **Silver** | In progress | Governance/docs/coverage rings ([#286](https://github.com/MTG-Thomas/bifrost/issues/286)–[#293](https://github.com/MTG-Thomas/bifrost/issues/293), [#290](https://github.com/MTG-Thomas/bifrost/issues/290)) |

Badge in README:

```markdown
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13022/badge)](https://www.bestpractices.dev/en/projects/13022)
```

## BadgeApp forms (separate pages)

BadgeApp does **not** share one form across tiers. After merging `.bestpractices.json`
to `main`, sign in and run **Save (and continue) 🤖** on each tier you are updating:

| Tier | Edit URL |
|------|----------|
| Passing | https://www.bestpractices.dev/en/projects/13022/passing/edit |
| Baseline tier 1 (OSPS) | https://www.bestpractices.dev/en/projects/13022/baseline-1/edit |
| Silver | https://www.bestpractices.dev/en/projects/13022/silver/edit |

**Common mistake:** Saving only on Passing or Silver does not apply OSPS baseline
answers — use **baseline-1** for those fields. Silver metal criteria (governance,
documentation depth, coverage, signed releases) need matching entries in
`.bestpractices.json` *and* a Silver form save.

## Regenerate answers

```powershell
python .\scripts\generate-bestpractices-json.py
```

Commit the regenerated `.bestpractices.json` to `main` before BadgeApp import.

Governance-related Silver fields are justified from [GOVERNANCE.md](../../GOVERNANCE.md),
[CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md), and [CONTRIBUTING.md](../../CONTRIBUTING.md)
(DCO section).

## Submit answers (pick one path)

### Path A — repo automation (recommended)

1. Merge `.bestpractices.json` on `main` (must be visible to BadgeApp).
2. Sign in at [bestpractices.dev](https://www.bestpractices.dev/).
3. Open the edit page for the tier you are updating (table above).
4. Click **Save (and continue) 🤖** so BadgeApp imports the repo file and runs
   its own detectors.

### Path B — direct API-style submit

1. Sign in to [bestpractices.dev](https://www.bestpractices.dev/).
2. Copy the `_BadgeApp_session` cookie (DevTools → Application → Cookies).
3. Submit locally:

```powershell
$env:BADGE_COOKIE = '<paste cookie value>'
$env:BADGE_LEVEL = 'passing'   # or baseline-1, silver
python .\scripts\submit-openssf-badge.py
```

Cookie lifetime is ~48 hours. `BADGE_LEVEL` defaults to `passing`.

### Path C — automation proposal URLs (review in browser)

BadgeApp can pre-fill the edit form from **query parameters** on the project
edit URL. You stay in the loop: proposals are highlighted; you review and
click **Save** to persist. No cookie script required.

Official spec: [automation-proposals.md](https://github.com/coreinfrastructure/best-practices-badge/blob/main/docs/automation-proposals.md)

**Baseline (OSPS) example** — section chooser, then pick baseline-1:

```text
https://www.bestpractices.dev/en/projects/13022/choose/edit?
  osps_ac_01_01_status=Met&
  osps_ac_01_01_justification=GitHub+org+enforces+2FA
```

**Direct section** (skip chooser):

```text
https://www.bestpractices.dev/en/projects/13022/baseline-1/edit?osps_ac_01_01_status=Met&...
https://www.bestpractices.dev/en/projects/13022/silver/edit?governance_status=Met&...
```

Field names: criterion lowercased, `-` → `_`, plus `_status` / `_justification`.
Status values: `?`, `Unmet`, `N/A`, `Met`.

Proposals only fill **blank/unknown** fields unless you add `overrides=` globs
(for example `overrides=osps_ac_*,governance_*`).

Generate URLs from repo JSON:

```powershell
python .\scripts\generate-badge-proposal-url.py --section silver --from-json --prefix governance_ --prefix dco_ --prefix code_of_conduct_
python .\scripts\generate-badge-proposal-url.py --section baseline-1 --field osps_ac_01_01_status=Met --field "osps_ac_01_01_justification=GitHub org enforces 2FA"
```

### Path D — read-only JSON API (verify scores, not submit)

```powershell
(Invoke-WebRequest "https://www.bestpractices.dev/projects/13022.json" -UseBasicParsing).Content
```

Public project JSON shows tier percentages; authenticated REST PUT exists but
BadgeApp discourages blind writes — prefer Path A/C so humans review in the UI.

## Related issues

- [#289](https://github.com/MTG-Thomas/bifrost/issues/289) — Passing badge (closed)
- [#290](https://github.com/MTG-Thomas/bifrost/issues/290) — governance, CoC, DCO for Silver
- [#294](https://github.com/MTG-Thomas/bifrost/pull/294) — MTG semver / v1.0.0 line
