#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

require_cmd() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Missing required command: $cmd" >&2
        exit 1
    fi
}

prompt() {
    local var_name="$1"
    local prompt_text="$2"
    local default_value="${3:-}"
    local value=""

    if [[ -n "$default_value" ]]; then
        read -r -p "$prompt_text [$default_value]: " value
        value="${value:-$default_value}"
    else
        while [[ -z "$value" ]]; do
            read -r -p "$prompt_text: " value
        done
    fi

    printf -v "$var_name" '%s' "$value"
}

replace_env_value() {
    local file="$1"
    local key="$2"
    local value="$3"
    python3 - "$file" "$key" "$value" <<'PYEOF'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

lines = path.read_text().splitlines()
needle = f"{key}="
updated = False
for idx, line in enumerate(lines):
    stripped = line.lstrip()
    if stripped.startswith(needle):
        lines[idx] = f"{key}={value}"
        updated = True
        break

if not updated:
    lines.append(f"{key}={value}")

path.write_text("\n".join(lines) + "\n")
PYEOF
}

replace_compose_root() {
    local file="$1"
    local remote_dir="$2"
    python3 - "$file" "$remote_dir" <<'PYEOF'
from pathlib import Path
import sys

path = Path(sys.argv[1])
remote_dir = sys.argv[2].rstrip("/")
content = path.read_text()
path.write_text(content.replace("/opt/awg-jump", remote_dir))
PYEOF
}

require_cmd ssh
require_cmd scp
require_cmd tar
require_cmd python3

HOST="${1:-}"
SSH_USER="${2:-}"
SSH_PORT="${3:-}"
REMOTE_DIR="${4:-}"

if [[ -z "$HOST" ]]; then
    prompt HOST "Remote node IP or hostname"
fi
if [[ -z "$SSH_USER" ]]; then
    prompt SSH_USER "SSH username" "root"
fi
if [[ -z "$SSH_PORT" ]]; then
    prompt SSH_PORT "SSH port" "22"
fi
if [[ -z "$REMOTE_DIR" ]]; then
    prompt REMOTE_DIR "Remote deploy directory" "/opt/awg-jump"
fi

prompt DOCKER_NAMESPACE "Docker Hub namespace" "your-dockerhub-namespace"
prompt IMAGE_TAG "Docker image tag" "latest"

IMAGE_JUMP="docker.io/${DOCKER_NAMESPACE}/awg-jump:${IMAGE_TAG}"

REMOTE_ROOT_PREFIX=""
if [[ "$SSH_USER" != "root" ]]; then
    REMOTE_ROOT_PREFIX="sudo "
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cp "$REPO_ROOT/deploy/docker-compose.images.yml" "$TMP_DIR/docker-compose.yml"
cp "$REPO_ROOT/nginx/docker-compose.yml" "$TMP_DIR/docker-compose.nginx.yml"
cp "$REPO_ROOT/.env.ru.example" "$TMP_DIR/.env.ru.example"
cp "$REPO_ROOT/.env.en.example" "$TMP_DIR/.env.en.example"
cp "$REPO_ROOT/.env.ru.example" "$TMP_DIR/.env"
mkdir -p "$TMP_DIR/nginx"
cp "$REPO_ROOT/nginx/nginx.conf" "$TMP_DIR/nginx/nginx.conf"

replace_env_value "$TMP_DIR/.env" "TLS_COMMON_NAME" "$HOST"
replace_env_value "$TMP_DIR/.env" "SERVER_HOST" "$HOST"
replace_env_value "$TMP_DIR/.env" "AWG_JUMP_IMAGE" "$IMAGE_JUMP"
replace_compose_root "$TMP_DIR/docker-compose.nginx.yml" "$REMOTE_DIR"

cat >"$TMP_DIR/REMOTE_BOOTSTRAP.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

REMOTE_DIR="$1"

if [[ "$EUID" -ne 0 ]]; then
    echo "Run remote bootstrap as root." >&2
    exit 1
fi

install_docker_apt() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl

    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sh
    fi

    if ! docker compose version >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq docker-compose-plugin || true
    fi
}

install_docker_dnf() {
    dnf install -y ca-certificates curl

    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sh
    fi
}

if command -v apt-get >/dev/null 2>&1; then
    install_docker_apt
elif command -v dnf >/dev/null 2>&1; then
    install_docker_dnf
else
    echo "Unsupported Linux distribution: no apt-get or dnf found." >&2
    exit 1
fi

systemctl enable --now docker

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin is not available after installation." >&2
    exit 1
fi

mkdir -p "$REMOTE_DIR"
mkdir -p "$REMOTE_DIR/data/certs" "$REMOTE_DIR/data/backups" "$REMOTE_DIR/data/geoip" "$REMOTE_DIR/data/wg_configs"
mkdir -p "$REMOTE_DIR/nginx"
mkdir -p /var/log/nginx

if [[ ! -c /dev/net/tun ]]; then
    mkdir -p /dev/net
    mknod /dev/net/tun c 10 200 || true
    chmod 666 /dev/net/tun || true
fi
EOF

echo "Uploading deploy bundle"
tar -C "$TMP_DIR" -czf "$TMP_DIR/bundle.tgz" \
    docker-compose.yml \
    docker-compose.nginx.yml \
    .env \
    .env.ru.example \
    .env.en.example \
    nginx/nginx.conf \
    REMOTE_BOOTSTRAP.sh
scp -P "$SSH_PORT" "$TMP_DIR/bundle.tgz" "${SSH_USER}@${HOST}:/tmp/awg-jump-bootstrap.tgz"

echo "Installing Docker and unpacking files on remote host"
ssh -p "$SSH_PORT" "${SSH_USER}@${HOST}" "tar -xzf /tmp/awg-jump-bootstrap.tgz -C /tmp && ${REMOTE_ROOT_PREFIX}bash /tmp/REMOTE_BOOTSTRAP.sh '$REMOTE_DIR' && ${REMOTE_ROOT_PREFIX}tar -xzf /tmp/awg-jump-bootstrap.tgz -C '$REMOTE_DIR' docker-compose.yml docker-compose.nginx.yml .env .env.ru.example .env.en.example nginx/nginx.conf && rm -f /tmp/awg-jump-bootstrap.tgz /tmp/REMOTE_BOOTSTRAP.sh"

cat <<EOF

Bootstrap complete.

Remote directory: ${REMOTE_DIR}

Files created:
  ${REMOTE_DIR}/docker-compose.yml
  ${REMOTE_DIR}/docker-compose.nginx.yml
  ${REMOTE_DIR}/.env
  ${REMOTE_DIR}/.env.ru.example
  ${REMOTE_DIR}/.env.en.example
  ${REMOTE_DIR}/nginx/nginx.conf

Next steps on the node:
  1. Edit ${REMOTE_DIR}/.env and set at least ADMIN_PASSWORD and SECRET_KEY.
  2. Verify AWG_JUMP_IMAGE in ${REMOTE_DIR}/.env.
  3. Start the stack:
     cd ${REMOTE_DIR}
     docker compose -f docker-compose.yml -f docker-compose.nginx.yml pull
     docker compose -f docker-compose.yml -f docker-compose.nginx.yml up -d
EOF
