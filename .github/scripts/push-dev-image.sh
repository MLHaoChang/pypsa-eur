#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

# workflow_dispatch can select a branch; only master may publish its image.
if [[ "$GITHUB_REF" != "refs/heads/master" ]]; then
    echo "Environment images must be published from master." >&2
    exit 1
fi

image="ghcr.io/${IMAGE_NAME:?}"
current="$image:${GITHUB_SHA:?}"
cached="$image:${hash_last_changed:?}"

if [[ "$hash_last_changed" == "$GITHUB_SHA" || "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]] ||
    ! docker manifest inspect "$cached" >/dev/null 2>&1; then
    # Forks and newly created registries have no previous environment tag.
    # Link the package to this repository so its workflow token can publish.
    docker build . --file docker/dev-env/Dockerfile \
        --label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" \
        --tag "$current"
    # Keep the environment revision available for later unchanged commits.
    if [[ "$cached" != "$current" ]]; then
        docker tag "$current" "$cached"
        docker push "$cached"
    fi
else
    docker pull "$cached"
    docker tag "$cached" "$current"
fi

docker push "$current"
docker tag "$current" "$image:latest"
docker push "$image:latest"
