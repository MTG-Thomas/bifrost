#!/usr/bin/env bash
set -euo pipefail

# Decide whether CI must rebuild the local dev/test images instead of reusing
# the baked GHCR images from main. Runtime source is bind-mounted by
# docker-compose.test.yml; only image-baked inputs need to force a rebuild.

event_name="${GITHUB_EVENT_NAME:-}"
github_output="${GITHUB_OUTPUT:-/dev/null}"
github_step_summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

api_changed=false
client_changed=false
playwright_changed=false

if [ "$event_name" = "merge_group" ]; then
  # Merge queue refs can contain multiple PRs. Rebuild there so dependency or
  # Dockerfile changes from any queued PR are tested against their own image.
  api_changed=true
  client_changed=true
  playwright_changed=true
elif [ "$event_name" = "pull_request" ]; then
  base_ref="${GITHUB_BASE_REF:-main}"
  head_ref="${GITHUB_HEAD_REF:-}"
  if [ -n "$head_ref" ]; then
    git fetch --no-tags --depth=1 origin \
      "$base_ref:refs/remotes/origin/$base_ref" \
      "$head_ref:refs/remotes/origin/$head_ref"
    # Shallow PR tip fetches may not include a usable merge base, so this
    # head_ref path intentionally uses two-dot diff instead of three-dot.
    changed_files="$(git diff --name-only "origin/$base_ref..origin/$head_ref" || true)"
  elif git rev-parse --verify HEAD^2 >/dev/null 2>&1; then
    # actions/checkout uses GitHub's synthetic pull_request merge ref by
    # default. Diff the merge parents directly; origin/base...HEAD can be
    # empty for that synthetic commit and would incorrectly reuse stale images.
    changed_files="$(git diff --name-only HEAD^1...HEAD^2 || true)"
  else
    git fetch --no-tags --depth=1 origin "$base_ref"
    changed_files="$(git diff --name-only "origin/$base_ref...HEAD" || true)"
  fi

  api_pattern='^(api/Dockerfile\.dev|api/_bifrost_workspace_effects\.py|pyproject\.toml|requirements(-pyright)?\.lock|api/src/services/app_compiler/(package(-lock)?\.json|compile\.js|tailwind\.js)|api/src/services/app_bundler/package(-lock)?\.json|api/src/services/sdk_package/(package(-lock)?\.json|build_sdk\.js)|client/src/lib/app-sdk/.*)$'
  client_pattern='^(client/Dockerfile\.dev|client/package(-lock)?\.json)$'
  playwright_pattern='^(client/Dockerfile\.playwright|client/package(-lock)?\.json)$'

  if printf '%s\n' "$changed_files" | grep -Eq "$api_pattern"; then
    api_changed=true
  fi
  if printf '%s\n' "$changed_files" | grep -Eq "$client_pattern"; then
    client_changed=true
  fi
  if printf '%s\n' "$changed_files" | grep -Eq "$playwright_pattern"; then
    playwright_changed=true
  fi
fi

{
  echo "api_changed=$api_changed"
  echo "client_changed=$client_changed"
  echo "playwright_changed=$playwright_changed"
} >> "$github_output"

{
  echo "### CI test image input detection"
  echo ""
  echo "- API image rebuild required: \`$api_changed\`"
  echo "- Client image rebuild required: \`$client_changed\`"
  echo "- Playwright image rebuild required: \`$playwright_changed\`"
} >> "$github_step_summary"
