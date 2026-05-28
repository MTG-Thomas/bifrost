# OpenSSF Best Practices Badge (MTG fork)

Project **13022**: https://www.bestpractices.dev/en/projects/13022

Upstream earned Passing as [project 12665](https://www.bestpractices.dev/en/projects/12665).
The MTG fork reuses that evidence via `.bestpractices.json` at the repo root.

## Submit answers (pick one path)

### Path A — repo automation (recommended)

1. Merge `.bestpractices.json` on `main` (must be visible to BadgeApp).
2. Sign in at [bestpractices.dev](https://www.bestpractices.dev/).
3. Open the Passing edit page:
   https://www.bestpractices.dev/en/projects/13022/passing/edit
4. Click **Save (and continue) 🤖** so BadgeApp imports the repo file and runs
   its own detectors.

Regenerate the file after repo changes:

```powershell
python .\scripts\generate-bestpractices-json.py
```

### Path B — direct API-style submit

1. Sign in to [bestpractices.dev](https://www.bestpractices.dev/).
2. Copy the `_BadgeApp_session` cookie (DevTools → Application → Cookies).
3. Submit locally:

```powershell
$env:BADGE_COOKIE = '<paste cookie value>'
python .\scripts\submit-openssf-badge.py
```

Cookie lifetime is ~48 hours.

## Known gap before 100% Passing

`MTG-Thomas/bifrost` has **no semver git tags or GitHub Releases yet**. Several
Passing criteria (`version_tags`, `version_unique`, `release_notes`,
`release_notes_vulns`) need at least one tagged release with notes. The release
workflow and `bifrost-release` skill are ready; cut the first fork tag, then
re-run generation/submission.

## After Passing

Add to `README.md`:

```markdown
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13022/badge)](https://www.bestpractices.dev/en/projects/13022)
```
