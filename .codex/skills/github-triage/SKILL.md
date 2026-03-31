---
name: github-triage
description: Triage and prioritize large sets of GitHub pull requests and issues for this repo. Use when the user wants a backlog sorted, needs a prioritized action list, or wants PRs and issues bucketed by urgency, risk, merge readiness, and likely next action.
---

# GitHub Triage

Use this skill when the problem is backlog management, not implementation. The goal is to turn a noisy set of PRs and issues into a short, defensible priority order.

Prefer the GitHub app for PR and issue metadata. Use local `gh` only when the connector does not expose the needed state cleanly.

## Workflow

1. Resolve the scope.
   - Identify the repo.
   - Determine whether the user wants PRs, issues, or both.
   - If needed, narrow by open state, label, assignee, author, or recency.

2. Collect structured data first.
   - Pull PR and issue lists with titles, numbers, state, draft status, labels, assignees, timestamps, and comments.
   - For PRs, also inspect review state, mergeability signals, and failing checks when available.

3. Classify each item.
   - `urgent`: blocking production, customers, security, or active delivery
   - `ready`: actionable now with a clear next step
   - `blocked`: waiting on review, CI, missing context, or external dependency
   - `stale`: old, inactive, superseded, or unclear ownership

4. Score the next action, not just the topic.
   - Prefer items that are both important and unblocked.
   - Separate "high importance but blocked" from "can be closed quickly now".
   - For PRs, distinguish merge-ready work from review-heavy work.
   - For issues, distinguish bugs/incidents from ideas or cleanup.

5. Produce a short queue.
   - Recommend a top tranche, usually 3-10 items.
   - Give each item one reason it ranks where it does and one explicit next action.
   - Call out items that should probably be closed, merged, relabeled, or deferred.

## Default Prioritization Heuristics

- Highest: production risk, customer impact, security, broken CI on active work, merge-ready PRs that unblock other work
- Next: small unblockers, review-ready PRs, issues with clear owners and low ambiguity
- Lower: drafts without momentum, speculative enhancements, stale cleanup, unclear requests without recent activity

## Output Expectations

- Start with the highest-signal queue, not a long inventory.
- For each recommended item, include:
  - PR/issue number and title
  - why it matters now
  - the immediate next action
- Keep the full backlog summary compressed into buckets and counts.

## Rules

- Do not turn triage into deep code review unless the user asks for review on a selected PR.
- Do not rank by recency alone when impact or unblock value says otherwise.
- Do not bury stale or duplicate items; say explicitly when something should be closed or merged.
- If signals are weak, say that the ranking is provisional and identify what missing signal would change it.
