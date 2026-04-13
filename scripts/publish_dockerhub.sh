#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/publish_dockerhub.sh <dockerhub-namespace> <tag> [--latest] [--with-node]

Example:
  ./scripts/publish_dockerhub.sh myorg 2026-04-08 --latest
  ./scripts/publish_dockerhub.sh myorg 2026-04-08 --latest --with-node

By default this script builds and pushes two images:
  docker.io/<namespace>/awg-jump:<tag>
  docker.io/<namespace>/awg-jump-nginx:<tag>

If --with-node is passed, it also pushes:
  docker.io/<namespace>/awg-node:<tag>
EOF
}

if [[ $# -lt 2 ]]; then
    usage
    exit 1
fi

NAMESPACE="$1"
TAG="$2"
shift 2

PUSH_LATEST=false
PUSH_NODE=false

for arg in "$@"; do
    case "$arg" in
        --latest)
            PUSH_LATEST=true
            ;;
        --with-node)
            PUSH_NODE=true
            ;;
        *)
            usage
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for cmd in docker; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Missing required command: $cmd" >&2
        exit 1
    fi
done

if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon is not available." >&2
    exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
    echo "docker buildx is required." >&2
    exit 1
fi

IMAGE_JUMP="docker.io/${NAMESPACE}/awg-jump:${TAG}"
IMAGE_NGINX="docker.io/${NAMESPACE}/awg-jump-nginx:${TAG}"
IMAGE_NODE="docker.io/${NAMESPACE}/awg-node:${TAG}"

JUMP_TAGS=(-t "$IMAGE_JUMP")
NGINX_TAGS=(-t "$IMAGE_NGINX")
NODE_TAGS=(-t "$IMAGE_NODE")

if [[ "$PUSH_LATEST" == true ]]; then
    JUMP_TAGS+=(-t "docker.io/${NAMESPACE}/awg-jump:latest")
    NGINX_TAGS+=(-t "docker.io/${NAMESPACE}/awg-jump-nginx:latest")
    if [[ "$PUSH_NODE" == true ]]; then
        NODE_TAGS+=(-t "docker.io/${NAMESPACE}/awg-node:latest")
    fi
fi

echo "Publishing jump image: $IMAGE_JUMP"
docker buildx build \
    --platform linux/amd64 \
    "${JUMP_TAGS[@]}" \
    --push \
    "$REPO_ROOT"

echo "Publishing nginx image: $IMAGE_NGINX"
docker buildx build \
    --platform linux/amd64 \
    "${NGINX_TAGS[@]}" \
    --push \
    -f "$REPO_ROOT/nginx/Dockerfile" \
    "$REPO_ROOT/nginx"

if [[ "$PUSH_NODE" == true ]]; then
    echo "Publishing node image: $IMAGE_NODE"
    docker buildx build \
        --platform linux/amd64 \
        "${NODE_TAGS[@]}" \
        --push \
        "$REPO_ROOT/node"
fi

echo
echo "Published images:"
printf '  %s\n' "$IMAGE_JUMP" "$IMAGE_NGINX"
if [[ "$PUSH_NODE" == true ]]; then
    printf '  %s\n' "$IMAGE_NODE"
fi
