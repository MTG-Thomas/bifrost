#!/usr/bin/env bash
# Compute a monotonic semver dev version for the current commit.
#
# Format: <next-patch>-dev.<HEAD-committer-epoch>
# Example: latest tag v1.0.0, HEAD committed at epoch 1788062400
#          -> 1.0.1-dev.1788062400
#
# The committer timestamp is part of the commit object, so an exact merge
# candidate and the same commit on main compute the same version. Unlike a
# commits-since-tag count, it also cannot move backwards merely because a PR
# was rebased or squash-merged with fewer commits in its resulting history.
#
# MTG fork: uses tags on MTG-Thomas/bifrost only. Before the first stable
# release, bootstraps to 1.0.0-dev.<commit-count>.
# Fork-local prerelease tags such as `v0.9.1-mtg.1` are intentionally ignored.
set -euo pipefail

target_ref=HEAD
additional_commits=0
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || "$1" != "--next-after" ]]; then
    echo "usage: $0 [--next-after <git-commit>]" >&2
    exit 2
  fi
  target_ref="$2"
  additional_commits=1
fi

if ! git rev-parse --verify "${target_ref}^{commit}" >/dev/null 2>&1; then
  echo "compute-dev-version: '$target_ref' is not a commit" >&2
  exit 2
fi

# Planned first MTG semver release when no stable tag exists yet.
MTG_BOOTSTRAP_MAJOR=1
MTG_BOOTSTRAP_MINOR=0
MTG_BOOTSTRAP_PATCH=0

last_tag=$(git describe \
  --tags \
  --abbrev=0 \
  --match "v[0-9]*.[0-9]*.[0-9]*" \
  --exclude "v*-*" \
  "$target_ref" \
  2>/dev/null || true)

if [[ -z "$last_tag" ]]; then
  commits=$(git rev-list --count "$target_ref")
  commits=$((commits + additional_commits))
  printf '%s.%s.%s-dev.%s\n' \
    "$MTG_BOOTSTRAP_MAJOR" "$MTG_BOOTSTRAP_MINOR" "$MTG_BOOTSTRAP_PATCH" "$commits"
  exit 0
fi

if ! [[ "$last_tag" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "compute-dev-version: latest tag '$last_tag' is not vMAJOR.MINOR.PATCH" >&2
  exit 1
fi

major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
patch="${BASH_REMATCH[3]}"
next_patch=$((patch + 1))

if [[ "$additional_commits" -eq 1 ]]; then
  # Candidate publication predicts the single merge commit after its parent.
  # Keep this mode stable across the PR build and main-push promotion paths.
  sequence=$(git rev-list "${last_tag}..${target_ref}" --count)
  sequence=$((sequence + additional_commits))
else
  sequence=$(git show -s --format=%ct "$target_ref" 2>/dev/null || true)
fi
if ! [[ "$sequence" =~ ^[1-9][0-9]*$ ]]; then
  echo "compute-dev-version: could not compute a positive version sequence" >&2
  exit 1
fi

printf '%s.%s.%s-dev.%s\n' "$major" "$minor" "$next_patch" "$sequence"
