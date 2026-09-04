#!/usr/bin/env bash
# Compare last-commit timestamps between upstream bifrost and gobifrost.
# Print drift summary and list user-facing files changed since docs were last updated.
# Forks do not publish the upstream website, so they exit successfully before
# looking for a docs checkout.
#
# Used by the bifrost-release skill (Step 3 dev push, Step 1b full release) to
# decide whether to offer a docs-refresh dispatch before pushing or tagging.
#
# Exit codes:
#   0  = clean (docs are at-or-ahead of bifrost, OR no user-facing changes since)
#   1  = drift detected (bifrost has user-facing changes since DOCS_LAST)
#   2  = error (missing repo, etc.)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIFROST_REPO="${BIFROST_REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DOCS_REPO="${DOCS_REPO:-$HOME/GitHub/gobifrost}"

repository_from_origin() {
    local origin_url repository

    origin_url=$(git -C "$BIFROST_REPO" remote get-url origin 2>/dev/null) || return 0
    case "$origin_url" in
        https://github.com/*)
            repository="${origin_url#https://github.com/}"
            ;;
        git@github.com:*)
            repository="${origin_url#git@github.com:}"
            ;;
        ssh://git@github.com/*)
            repository="${origin_url#ssh://git@github.com/}"
            ;;
        *)
            return 0
            ;;
    esac

    repository="${repository%/}"
    printf '%s\n' "${repository%.git}"
}

# User-facing surface area. Add to this list when introducing new dirs that
# affect what's visible in docs/screenshots. Patterns are anchored regex
# fragments matched against `git log --name-only` output.
USER_FACING_PATTERNS=(
    '^api/src/handlers/'
    '^api/shared/models\.py$'
    '^api/bifrost/'                       # CLI surface
    '^api/src/services/mcp_server/tools/' # MCP tools (user-callable)
    '^client/src/'
    '^docs/llm\.txt$'                     # CLI/MCP guidance shipped to users
    '^docs/runbooks/'                     # operational docs visible to users
)

if ! git -C "$BIFROST_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "error: bifrost repo not found at $BIFROST_REPO" >&2
    exit 2
fi

REPOSITORY_ID="${BIFROST_GITHUB_REPOSITORY:-${GITHUB_REPOSITORY:-}}"
if [[ -z "$REPOSITORY_ID" ]]; then
    REPOSITORY_ID=$(repository_from_origin)
fi

if [[ -n "$REPOSITORY_ID" && "${REPOSITORY_ID,,}" != "gobifrost/bifrost" ]]; then
    echo "✓ upstream documentation check waived for fork $REPOSITORY_ID"
    echo "  gobifrost.com is maintained by gobifrost/bifrost and is not a fork release gate."
    exit 0
fi

if ! git -C "$DOCS_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "warn: docs repo not found at $DOCS_REPO" >&2
    echo "      clone it: git clone git@github.com:gobifrost/website.git $DOCS_REPO" >&2
    exit 2
fi

BIFROST_LAST=$(git -C "$BIFROST_REPO" log -1 --format=%cI origin/main)
DOCS_LAST=$(git -C "$DOCS_REPO" log -1 --format=%cI origin/main)
BIFROST_LAST_EPOCH=$(git -C "$BIFROST_REPO" log -1 --format=%ct origin/main)
DOCS_LAST_EPOCH=$(git -C "$DOCS_REPO" log -1 --format=%ct origin/main)

echo "bifrost last main commit:  $BIFROST_LAST"
echo "docs    last main commit:  $DOCS_LAST"

# If docs are at-or-ahead of bifrost, no drift to surface.
if [[ "$DOCS_LAST_EPOCH" -ge "$BIFROST_LAST_EPOCH" ]]; then
    echo
    echo "✓ docs are current (at or ahead of bifrost main)"
    exit 0
fi

# Build a single regex from the surface-area patterns.
SURFACE_RE=$(printf '%s|' "${USER_FACING_PATTERNS[@]}")
SURFACE_RE="${SURFACE_RE%|}"

# List user-facing files touched in commits since DOCS_LAST.
CHANGED_FILES=$(
    git -C "$BIFROST_REPO" log --since="$DOCS_LAST" --name-only --pretty=format: origin/main \
        | grep -E "$SURFACE_RE" \
        | sort -u || true
)

COMMIT_COUNT=$(git -C "$BIFROST_REPO" log --since="$DOCS_LAST" --oneline origin/main | wc -l | tr -d ' ')

echo
if [[ -z "$CHANGED_FILES" ]]; then
    echo "✓ no user-facing surface-area changes since docs were last updated"
    echo "  ($COMMIT_COUNT commits since DOCS_LAST, none touching tracked dirs)"
    exit 0
fi

FILE_COUNT=$(echo "$CHANGED_FILES" | wc -l | tr -d ' ')

echo "⚠ user-facing surface area changed since docs were last updated:"
echo "  $COMMIT_COUNT commits since DOCS_LAST, $FILE_COUNT user-facing files touched"
echo
echo "$CHANGED_FILES" | head -25
if [[ "$FILE_COUNT" -gt 25 ]]; then
    echo "  ... and $((FILE_COUNT - 25)) more"
fi

exit 1
