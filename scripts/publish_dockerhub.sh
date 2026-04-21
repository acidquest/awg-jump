#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./scripts/publish_dockerhub.sh <dockerhub-namespace> <tag> [--latest] [--with-node] [--gateway] [--only-gateway]

Example:
  ./scripts/publish_dockerhub.sh myorg 2026-04-08 --latest
  ./scripts/publish_dockerhub.sh myorg 2026-04-08 --latest --with-node
  ./scripts/publish_dockerhub.sh myorg 2026-04-08 --latest --gateway
  ./scripts/publish_dockerhub.sh myorg 2026-04-08 --latest --only-gateway

By default this script builds and pushes:
  docker.io/<namespace>/awg-jump:<tag>

If --with-node is passed, it also pushes:
  docker.io/<namespace>/awg-node:<tag>

If --gateway is passed, it also pushes:
  docker.io/<namespace>/awg-gateway:<tag>

If --only-gateway is passed, it builds and pushes only:
  docker.io/<namespace>/awg-gateway:<tag>
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
PUSH_GATEWAY=false
ONLY_GATEWAY=false

for arg in "$@"; do
    case "$arg" in
        --latest)
            PUSH_LATEST=true
            ;;
        --with-node)
            PUSH_NODE=true
            ;;
        --gateway)
            PUSH_GATEWAY=true
            ;;
        --only-gateway)
            ONLY_GATEWAY=true
            PUSH_GATEWAY=true
            PUSH_NODE=false
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
IMAGE_NODE="docker.io/${NAMESPACE}/awg-node:${TAG}"
IMAGE_GATEWAY="docker.io/${NAMESPACE}/awg-gateway:${TAG}"

JUMP_TAGS=(-t "$IMAGE_JUMP")
NODE_TAGS=(-t "$IMAGE_NODE")
GATEWAY_TAGS=(-t "$IMAGE_GATEWAY")

if [[ "$PUSH_LATEST" == true ]]; then
    JUMP_TAGS+=(-t "docker.io/${NAMESPACE}/awg-jump:latest")
    if [[ "$PUSH_NODE" == true ]]; then
        NODE_TAGS+=(-t "docker.io/${NAMESPACE}/awg-node:latest")
    fi
    if [[ "$PUSH_GATEWAY" == true ]]; then
        GATEWAY_TAGS+=(-t "docker.io/${NAMESPACE}/awg-gateway:latest")
    fi
fi

if [[ "$ONLY_GATEWAY" != true ]]; then
    echo "Publishing jump image: $IMAGE_JUMP"
    docker buildx build \
        --platform linux/amd64 \
        "${JUMP_TAGS[@]}" \
        --push \
        "$REPO_ROOT"

    if [[ "$PUSH_NODE" == true ]]; then
        echo "Publishing node image: $IMAGE_NODE"
        docker buildx build \
            --platform linux/amd64 \
            "${NODE_TAGS[@]}" \
            --push \
            "$REPO_ROOT/node"
    fi
fi

if [[ "$PUSH_GATEWAY" == true ]]; then
    echo "Publishing gateway image: $IMAGE_GATEWAY"
    docker buildx build \
        --platform linux/amd64 \
        "${GATEWAY_TAGS[@]}" \
        --push \
        -f "$REPO_ROOT/gateway/Dockerfile" \
        "$REPO_ROOT"
fi

echo
echo "Published images:"
if [[ "$ONLY_GATEWAY" != true ]]; then
    printf '  %s\n' "$IMAGE_JUMP"
    if [[ "$PUSH_NODE" == true ]]; then
        printf '  %s\n' "$IMAGE_NODE"
    fi
fi
if [[ "$PUSH_GATEWAY" == true ]]; then
    printf '  %s\n' "$IMAGE_GATEWAY"
fi
