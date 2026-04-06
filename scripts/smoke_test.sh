#!/bin/bash
# Smoke test: запуск, базовые проверки, остановка
set -e

COMPOSE="docker-compose"
API="http://localhost:8080"
PASS=0
FAIL=0

green() { echo -e "\033[32m[PASS]\033[0m $*"; }
red()   { echo -e "\033[31m[FAIL]\033[0m $*"; }

check() {
    local desc="$1"; shift
    if "$@" &>/dev/null; then
        green "$desc"
        ((PASS++))
    else
        red "$desc"
        ((FAIL++))
    fi
}

echo "=== AWG Jump Smoke Test ==="

# Поднять стек
echo "[*] Building and starting containers..."
$COMPOSE up -d --build

echo "[*] Waiting 20s for services to start..."
sleep 20

# Проверка: контейнер запущен
check "awg-jump container is running" \
    docker inspect -f '{{.State.Running}}' awg-jump

# Проверка: /api/system/status
check "GET /api/system/status returns 200" \
    curl -sf "${API}/api/system/status" -o /dev/null

# Проверка: логин
TOKEN=$(curl -sf -X POST "${API}/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"changeme"}' \
    | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4 || true)

if [ -n "$TOKEN" ]; then
    green "POST /api/auth/login returns token"
    ((PASS++))
else
    red "POST /api/auth/login — no token"
    ((FAIL++))
fi

# Проверка: wg show внутри контейнера
check "wg show runs inside awg-jump" \
    docker exec awg-jump wg show

# Проверка: amneziawg-go бинарник исполняемый
check "amneziawg-go binary is executable" \
    docker exec awg-jump test -x /usr/local/bin/amneziawg-go

# Остановить стек
echo "[*] Stopping containers..."
$COMPOSE down

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
