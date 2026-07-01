# Delivery Lanes

Bifrost needs mature engineering practices, but not every change needs the same
ceremony. Choose the lightest lane that protects the actual risk, then say what
you chose in the PR description.

## Lane 1: Tiny And Internal

Use for docs, copy, README/runbook updates, small style-only UI polish, isolated
test expectation fixes, and low-risk chores with no runtime behavior change.

Expected flow:

- Work in a branch or worktree.
- Run a format, lint, or targeted check only when it is relevant.
- Open a PR with a short rationale.
- Do not boot the dev stack or full test stack unless the change needs it.

Review expectation: one maintainer pass is enough. Extra AI/code reviewers are
optional and should be skipped when they would only restate obvious style nits.

## Lane 2: Product Or API

Use for endpoints, DTOs, user-visible frontend, generated types, workflow-facing
code, manifest serialization, CLI/MCP surfaces, and meaningful behavior changes.

Expected flow:

- Work in a branch or worktree.
- Add or update the narrow tests that cover the changed behavior.
- Run the targeted backend/client checks for the touched area.
- Regenerate OpenAPI TypeScript types when API contracts change.
- Before merge, make sure CI or the equivalent relevant local checks are green.

Review expectation: one human/maintainer review by default. Add a specialist or
AI review only when the diff is broad, subtle, or crosses an unfamiliar boundary.

## Lane 3: Platform Or Live Operations

Use for auth, execution, tenant isolation, migrations, queues, schedules,
deployment behavior, secrets, audit logging, infrastructure, and anything that
can affect live workflow execution or customer data.

Expected flow:

- Work in an isolated worktree.
- Run the relevant tests and type/lint checks from `CLAUDE.md`.
- Capture live or VM-backed evidence when the task depends on deployed behavior.
- Keep read-only inspection separate from live mutation.
- Call out sensitive paths and rollback/verification notes in the PR.

Review expectation: human review is required. Add extra reviewers only for
specific risk ownership, such as security, data model, deployment, or execution
engine behavior. Avoid piling on generic reviewers.

## Lane 4: Spike Or Proof

Use when the goal is to test an idea quickly before deciding whether it should
be productized.

Expected flow:

- Make the experiment explicitly temporary.
- Prefer a disposable branch, worktree, script, or draft PR.
- Prove or disprove the idea with the smallest useful evidence.
- Convert only the proven, useful part into a separate mergeable PR.

Review expectation: do not treat spike code as production-ready just because it
exists in git. Review the follow-up production PR, not the throwaway experiment.

## Reviewer Budget

Code review is for risk reduction, not for creating a queue of overlapping
opinions.

- Default to one accountable reviewer.
- Add a second reviewer only when the change spans ownership boundaries or a
  sensitive path.
- Use AI/code-review bots for broad diffs, unfamiliar code, security-sensitive
  changes, or when the author explicitly wants another pass.
- Skip extra reviewers for small docs, narrow tests, obvious bug fixes, and
  changes already covered by focused tests plus CI.
- If reviews disagree, the author or maintainer should resolve the decision
  explicitly instead of waiting for every reviewer to converge.

## Codex-Authored PRs

Many Bifrost PRs are created by a human prompting Codex, Codex writing the code,
automated code-review agents reviewing it, and Codex stewarding the PR through
feedback. That loop is useful when it finds real defects and still lets changes
move. It fails when every PR becomes a long-running bot arbitration exercise.

Use this default:

- Codex opens the PR with the chosen delivery lane, changed-risk summary, and
  verification already run or intentionally skipped.
- One primary automated reviewer is enough by default. Prefer the reviewer that
  tends to catch the risk in the diff: Bugbot-style review for runtime defects,
  CodeRabbit-style review for broader logic/design review, and static-analysis
  tools for security/compliance gates.
- Codex should address concrete correctness, security, data-loss, auth,
  execution, deployment, and test-coverage findings.
- Codex should summarize or dismiss duplicated, stale, style-only, or
  already-covered findings instead of repeatedly rewriting the PR to satisfy
  every bot suggestion.
- A second automated reviewer is appropriate for Lane 3 changes, broad Lane 2
  changes, unfamiliar subsystems, or when the first review finds a serious bug
  class that deserves a different lens.
- For large sync or merge PRs, scope automated review by path or concern. A bot
  saying "too many files" is a signal to narrow the review, not to keep
  rerunning the same broad pass.
- For Dependabot and base-image PRs, prefer dependency/security evidence and a
  build/runtime smoke over generic code-review commentary.

Stewardship budget:

- Do one focused response pass for the primary automated review.
- Do one follow-up pass after pushed fixes if the reviewer found real defects.
- Stop and ask the maintainer when feedback becomes repetitive, contradicts
  project intent, demands unrelated refactors, or would turn a small PR into a
  larger one.

The goal is to keep enough automated review that the maintainer can trust
Codex-authored platform changes, while preserving enough velocity that people
keep making those changes.
