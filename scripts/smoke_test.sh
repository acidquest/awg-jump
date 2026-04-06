#!/bin/bash
set -euo pipefail

COMPOSE=(env ENV_FILE=.env.smoke docker compose --env-file .env.smoke)

cleanup() {
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -f .env.smoke
}

trap cleanup EXIT

cp .env.example .env.smoke
python3 - <<'PYEOF'
from pathlib import Path

path = Path(".env.smoke")
text = path.read_text()
text = text.replace("ADMIN_PASSWORD=changeme_REQUIRED   # ОБЯЗАТЕЛЬНО изменить!", "ADMIN_PASSWORD=test")
text = text.replace("SECRET_KEY=replace-with-random-secret-key-here", "SECRET_KEY=smoke-test-secret-key")
text = text.replace("TLS_COMMON_NAME=localhost", "TLS_COMMON_NAME=127.0.0.1")
text = text.replace("NGINX_HTTPS_PORT=443", "NGINX_HTTPS_PORT=8443")
text = text.replace("NGINX_HTTP_PORT=80", "NGINX_HTTP_PORT=8081")
path.write_text(text)
PYEOF

echo "[smoke] docker compose up -d --build"
"${COMPOSE[@]}" up -d --build

echo "[smoke] sleep 15"
sleep 15

echo "[smoke] curl -f http://localhost:8080/api/system/status"
curl -f http://localhost:8080/api/system/status

echo "[smoke] curl -f -X POST http://localhost:8080/api/auth/login -d '{\"username\":\"admin\",\"password\":\"test\"}'"
curl -f -X POST http://localhost:8080/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"test"}'

echo "[smoke] docker exec awg-jump wg show"
docker exec awg-jump wg show

echo "[smoke] docker compose down"
"${COMPOSE[@]}" down
