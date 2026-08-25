#!/usr/bin/env bash
set -euo pipefail

# Prepare the dependency-heavy images used by required test jobs without
# downloading third-party JavaScript actions during GitHub's job setup phase.
# Reviewed source remains bind-mounted by docker-compose.test.yml.

registry="${REGISTRY:?REGISTRY is required}"
username="${GHCR_USERNAME:?GHCR_USERNAME is required}"
token="${GHCR_TOKEN:?GHCR_TOKEN is required}"
image_tag="${CI_TEST_IMAGE_TAG:?CI_TEST_IMAGE_TAG is required}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

printf '%s' "$token" | docker login "$registry" --username "$username" --password-stdin
trap 'docker logout "$registry" >/dev/null 2>&1 || true' EXIT

prepare_image() {
  local name="$1"
  local changed="$2"
  local remote_image="$3"
  local local_image="$4"
  local context="$5"
  local dockerfile="$6"
  local remote_ref="${registry}/${remote_image}:${image_tag}"
  local pulled=false

  if docker pull "$remote_ref"; then
    pulled=true
  elif [ "$changed" != "true" ]; then
    echo "::warning::Unable to pull $name CI image; building it from the reviewed checkout."
  fi

  if [ "$changed" != "true" ] && [ "$pulled" = "true" ]; then
    docker tag "$remote_ref" "$local_image"
    printf -- '- %s: pulled `%s`\n' "$name" "$remote_ref" >> "$summary"
    return
  fi

  build_args=(
    --file "$dockerfile"
    --tag "$local_image"
  )
  if [ "$pulled" = "true" ]; then
    build_args+=(--cache-from "$remote_ref")
  fi
  DOCKER_BUILDKIT=1 docker build "${build_args[@]}" "$context"
  printf -- '- %s: built `%s` from reviewed source\n' "$name" "$local_image" >> "$summary"
}

{
  echo "### CI test images"
  echo
} >> "$summary"

for requested in "$@"; do
  case "$requested" in
    api)
      prepare_image \
        "API" \
        "${API_IMAGE_CHANGED:?API_IMAGE_CHANGED is required}" \
        "${CI_API_TEST_IMAGE:?CI_API_TEST_IMAGE is required}" \
        "bifrost-test-api-dev:latest" \
        "." \
        "./api/Dockerfile.dev"
      ;;
    client)
      prepare_image \
        "client" \
        "${CLIENT_IMAGE_CHANGED:?CLIENT_IMAGE_CHANGED is required}" \
        "${CI_CLIENT_TEST_IMAGE:?CI_CLIENT_TEST_IMAGE is required}" \
        "bifrost-test-client-dev:latest" \
        "./client" \
        "./client/Dockerfile.dev"
      ;;
    playwright)
      prepare_image \
        "Playwright" \
        "${PLAYWRIGHT_IMAGE_CHANGED:?PLAYWRIGHT_IMAGE_CHANGED is required}" \
        "${CI_PLAYWRIGHT_TEST_IMAGE:?CI_PLAYWRIGHT_TEST_IMAGE is required}" \
        "bifrost-test-client-e2e:latest" \
        "./client" \
        "./client/Dockerfile.playwright"
      ;;
    *)
      echo "Unknown CI test image: $requested" >&2
      exit 2
      ;;
  esac
done
