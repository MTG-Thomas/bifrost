# Contributing to Bifrost

Thanks for contributing. This doc is the friendly front door — it covers the *spirit* of how we work. Tool-neutral agent guidance lives in [`AGENTS.md`](./AGENTS.md), the detailed Bifrost playbook lives in [`CLAUDE.md`](./CLAUDE.md), and Claude-specific workflow skills live under [`.claude/skills/`](./.claude/skills/).

Community and project governance:

- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)
- [GOVERNANCE.md](./GOVERNANCE.md) — roles, culture, backup maintainers, access continuity

We aim for a positive, progress-oriented culture and presume best intent unless
evidence says otherwise; maintainers hold one another accountable to that bar
(see GOVERNANCE.md § Culture and maintainer standards).

## Developer Certificate of Origin (DCO)

By contributing to `MTG-Thomas/bifrost`, you certify that your contribution
complies with the [Developer Certificate of Origin, version 1.1](https://developercertificate.org/):

```text
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.

Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I have the right
    to submit it under the open source license indicated in the file; or

(b) The contribution is based upon previous work that, to the best of my
    knowledge, is covered under an appropriate open source license and I have
    the right under that license to submit that work with modifications,
    whether created in whole or in part by me, under the same open source
    license (unless I am permitted to submit under a different license), as
    indicated in the file; or

(c) The contribution was provided directly to me by some other person who
    certified (a), (b) or (c) and I have not modified it.

(d) I understand and agree that this project and the contribution are public
    and that a record of the contribution (including all personal information I
    submit with it, including my sign-off) is maintained indefinitely and may
    be redistributed consistent with this project or the open source license(s)
    involved.
```

**Sign-off:** append a `Signed-off-by` line to each commit message:

```text
Signed-off-by: Your Name <your.email@example.com>
```

Git can add this automatically: `git commit -s`. Squash merges should retain DCO
sign-off in the final commit message or PR description when GitHub DCO probot is
enabled.

## Before you open a PR

Pick the delivery lane first: [docs/dev/delivery-lanes.md](./docs/dev/delivery-lanes.md).
Small docs and chores should not pay the same process cost as auth, execution,
migrations, or live-ops changes.

- [ ] The PR description names the lane or otherwise explains the verification scope.
- [ ] Tests exist for changed runtime behavior (see **Testing expectations** below).
- [ ] Relevant targeted checks pass for the touched area.
- [ ] If you changed `api/shared/models.py` or API contracts, you re-ran `npm run generate:types` in `client/`.
- [ ] If you touched a sensitive path, you've called it out in the PR description.
- [ ] For product/API or platform/live-ops lanes, the dev stack or CI has exercised the behavior that matters.

## Testing expectations

All work ships with tests. The full matrix of what goes where lives in [`CLAUDE.md` → Testing & Quality](./CLAUDE.md#testing--quality) — read that for specifics. A few high-level notes:

**Backend.** Business logic belongs in `api/tests/unit/`. Anything that crosses an endpoint, queue, or DB boundary belongs in `api/tests/e2e/`. Always run via `./test.sh`, never pytest directly on the host — the Dockerized stack is part of the test.

**React components.** Ship a sibling `*.test.tsx` (vitest) covering the rendered behavior, not the implementation.

**Functional frontend modules.** New or modified `.ts` files under `client/src/lib/**` and `client/src/services/**` that export functions need a sibling `*.test.ts`. Storage adapters, auth helpers, API wrappers, and anything with a cross-tab/cross-window concern need tests that exercise that boundary specifically — regressions that only show up with two tabs open are the kind a future refactor will silently re-introduce. Pure type/constant re-export files and thin third-party SDK wrappers are exempt.

**User-facing features.** Add a happy-path Playwright spec in `client/e2e/`.

## Sensitive paths

Some areas of the codebase need a higher bar — auth, execution engine, multi-tenancy filters, migrations, secrets, manifest round-trip, audit logging.

If your change touches any of those, expect:
- A manual review regardless of PR size.
- A reviewer asking about tests for the specific failure mode, not just "does it compile."
- Higher scrutiny on any code path that could cross tenant boundaries, leak secrets, or skip an audit.

Call it out in the PR description so the reviewer doesn't have to rediscover it.

## Reviewer budget

Use code review to reduce risk, not to collect redundant opinions. Most PRs get
one accountable reviewer. Add a second reviewer when the change crosses an
ownership boundary or touches a sensitive path. Use AI/code-review bots when the
diff is broad, subtle, security-sensitive, or when the author wants another pass.

Skip extra reviewers for small docs, narrow tests, obvious bug fixes, and
low-risk chores already covered by focused checks. If reviewers disagree, the
author or maintainer should resolve the decision explicitly instead of waiting
for every reviewer to converge.

For Codex-authored PRs, automated review is part of the normal authoring loop:
human prompt, Codex implementation, automated review, Codex stewardship. Keep
that loop moving. One primary automated reviewer is the default; add another
only for sensitive paths, broad diffs, unfamiliar subsystems, or targeted second
opinions. Codex should fix concrete defects and record why stale, duplicate,
style-only, or scope-expanding suggestions were not followed.

## How to pick up work

All trackable work — bugs, features, chores, ideas — lives in [GitHub Issues](https://github.com/gobifrost/bifrost/issues). If it's not an issue, it's not on the roadmap.

**Looking for something to pick up?**

- [`help wanted`](https://github.com/gobifrost/bifrost/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) — issues I won't get to soon and would love help on.
- [`good first issue`](https://github.com/gobifrost/bifrost/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) — ramp-up-friendly, smaller scope.
- Unassigned issues without those labels are technically takeable, but I may already have them in my head — leave a comment first to avoid double work.

**Claim an issue** by self-assigning from the issue sidebar. If you change your mind, unassign — no hard feelings.

**Filing a new issue?** Pick the right template from the issue picker:

- **Bug** — something is broken or behaves unexpectedly.
- **Feature** — a new capability or meaningful enhancement. Link a design doc from `docs/superpowers/specs/` if one exists.
- **Chore** — maintenance, tooling, deps, CI, refactor without user-visible change.
- **Blank issue** — rapid capture for ideas or anything that doesn't fit the above.

## Commit & PR style

Conventional commits. The type in the subject should match what actually changed:

- `feat(apps):` — new user-facing capability
- `fix(embed):` — a bug fix, scoped to the affected area
- `refactor(execution):` — internal change, no behavior difference
- `docs(plans):` — plan docs, design notes, READMEs
- `test(forms):` — test-only change
- `chore(deps):` — dependency bumps, tooling

PR titles follow the same convention. Keep them under 70 characters; put the detail in the description. The description is the place to say *why* — a diff shows what changed but rarely why it had to change.

## When a rule doesn't fit

Rules are written against the common case. Sometimes the common case doesn't apply. If you're bending a rule deliberately, say so in the PR description with a one-line rationale ("skipping vitest coverage on `foo.ts` because it only re-exports types"). That turns rule-bending from a silent decision into a visible one, and lets a reviewer push back or agree on the spot.

Open an issue or a draft PR if you're unsure. Standards that survive are the ones where exceptions are documented, not hidden.
