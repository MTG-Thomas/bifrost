# MTG Bifrost Versioning

`MTG-Thomas/bifrost` uses an **independent semver line** owned by Midtown Technology
Group. Version numbers describe **MTG fork behavior**, not upstream release numbers.

## Semver policy

| Component | Meaning on this fork |
|-----------|----------------------|
| **MAJOR** | Breaking platform or operator-facing behavior changes |
| **MINOR** | Backward-compatible features or substantial improvements |
| **PATCH** | Backward-compatible fixes, security patches, small changes |

Pre-release dev builds use `<next-patch>-dev.<commits-since-tag>` (see
[dev-version-format runbook](runbooks/dev-version-format.md)).

## First release baseline

- **First MTG semver tag:** `v1.0.0`
- **Upstream relationship:** documented here and in GitHub Release notes — **not**
  encoded in the version number.
- **Upstream lineage at `v1.0.0`:** includes platform work through upstream
  `v0.9.1` plus MTG fork-specific changes (badges, security policy, CI/registry
  ownership, workspace integration, and related operator tooling).

Future releases increment MTG semver only. When useful, release notes may mention
which upstream tag or commit range the MTG release incorporated.

## Tags and releases

1. Cut semver tags on `MTG-Thomas/bifrost` only (`vMAJOR.MINOR.PATCH`).
2. Do **not** mirror upstream tags into this repo for dev-version computation.
3. Fork-local prerelease tags such as `v0.9.1-mtg.1` are ignored by
   `scripts/compute-dev-version.sh`.
4. Before the first tag, dev versions bootstrap as `1.0.0-dev.N` from commit
   count on `main`.
5. Run `./scripts/release-check.sh vX.Y.Z` before tagging.
6. Push the tag; CI builds signed images and opens a GitHub Release draft.
7. Edit the release notes to include a **Fixed vulnerabilities** section (OpenSSF
   Passing requirement) and an upstream baseline note when relevant.

## Related files

- `scripts/compute-dev-version.sh` — dev version on every `main` build
- `scripts/release-check.sh` — pre-tag checks
- `.claude/skills/bifrost-release/SKILL.md` — operator release flow
- `.claude-plugin/plugin.json` — plugin manifest version (updated at tag time)
