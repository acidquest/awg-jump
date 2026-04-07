#!/bin/bash
set -e

echo "[awg-node] Starting AmneziaWG node..."

# Переключить на legacy iptables (совместимость с ядром)
update-alternatives --set iptables /usr/sbin/iptables-legacy 2>/dev/null || true
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true

# Обязательные переменные
: "${AWG_LISTEN_PORT:=51821}"
: "${AWG_PRIVATE_KEY:?AWG_PRIVATE_KEY is required}"
: "${AWG_ADDRESS:?AWG_ADDRESS is required}"
: "${AWG_PEER_PUBLIC_KEY:?AWG_PEER_PUBLIC_KEY is required}"
: "${AWG_PEER_ALLOWED_IPS:=10.20.0.2/32}"

# Определить режим: kernel module или userspace amneziawg-go
# Проверяем строго amneziawg — стандартный wireguard не поддерживает обфускацию
USE_KERNEL=0
echo "[awg-node] Probing for AmneziaWG kernel module via ip link..."
if ip link add awg_probe type amneziawg 2>/dev/null; then
    ip link delete awg_probe 2>/dev/null || true
    echo "[awg-node] Kernel mode confirmed"
    USE_KERNEL=1
else
    echo "[awg-node] No AmneziaWG kernel module (ip link probe failed), using amneziawg-go userspace"
fi

AWG_PID=0

if [ "$USE_KERNEL" = "1" ]; then
    # ── Kernel mode: создать интерфейс через ip link ──────────────────────
    echo "[awg-node] Creating kernel interface awg0..."
    ip link add awg0 type amneziawg || { echo "[awg-node] ERROR: ip link add awg0 type amneziawg failed"; exit 1; }
else
    # ── Userspace mode: запустить amneziawg-go ────────────────────────────
    # Проверить наличие /dev/net/tun
    if [ ! -c /dev/net/tun ]; then
        echo "[awg-node] ERROR: /dev/net/tun not found. Add 'devices: [/dev/net/tun:/dev/net/tun]' to docker-compose.yml"
        exit 1
    fi

    echo "[awg-node] Starting amneziawg-go daemon..."
    # WG_PROCESS_FOREGROUND=1 — не форкаться в демон, работать на переднем плане.
    # Без этого флага родительский процесс форкает дочерний и завершается (exit 0),
    # что ломает отслеживание PID и проверку сокета.
    export WG_PROCESS_FOREGROUND=1
    amneziawg-go awg0 &
    AWG_PID=$!
    sleep 2

    if [ ! -S /var/run/amneziawg/awg0.sock ]; then
        echo "[awg-node] ERROR: AmneziaWG socket not created"
        exit 1
    fi
fi

# Сформировать конфиг (права 600 — файл содержит приватный ключ)
CONFIG_FILE=$(mktemp)
chmod 600 "$CONFIG_FILE"
cat > "$CONFIG_FILE" << EOF
[Interface]
ListenPort = ${AWG_LISTEN_PORT}
PrivateKey = ${AWG_PRIVATE_KEY}
EOF

# Обфускация — симметричные параметры (нода — сервер, без Jc/Jmin/Jmax)
if [ -n "${AWG_S1}" ]; then
    echo "S1 = ${AWG_S1}" >> "$CONFIG_FILE"
fi
if [ -n "${AWG_S2}" ]; then
    echo "S2 = ${AWG_S2}" >> "$CONFIG_FILE"
fi
if [ -n "${AWG_S3}" ]; then
    echo "S3 = ${AWG_S3}" >> "$CONFIG_FILE"
fi
if [ -n "${AWG_S4}" ]; then
    echo "S4 = ${AWG_S4}" >> "$CONFIG_FILE"
fi
if [ -n "${AWG_H1}" ]; then
    echo "H1 = ${AWG_H1}" >> "$CONFIG_FILE"
fi
if [ -n "${AWG_H2}" ]; then
    echo "H2 = ${AWG_H2}" >> "$CONFIG_FILE"
fi
if [ -n "${AWG_H3}" ]; then
    echo "H3 = ${AWG_H3}" >> "$CONFIG_FILE"
fi
if [ -n "${AWG_H4}" ]; then
    echo "H4 = ${AWG_H4}" >> "$CONFIG_FILE"
fi

cat >> "$CONFIG_FILE" << EOF

[Peer]
PublicKey = ${AWG_PEER_PUBLIC_KEY}
AllowedIPs = ${AWG_PEER_ALLOWED_IPS}
EOF

if [ -n "${AWG_PEER_PRESHARED_KEY}" ]; then
    echo "PresharedKey = ${AWG_PEER_PRESHARED_KEY}" >> "$CONFIG_FILE"
fi

if [ -n "${AWG_PEER_PERSISTENT_KEEPALIVE}" ]; then
    echo "PersistentKeepalive = ${AWG_PEER_PERSISTENT_KEEPALIVE}" >> "$CONFIG_FILE"
fi

# Применить конфиг (awg поддерживает обфускацию-параметры: S1/S2/H1/H2 и т.д.)
echo "[awg-node] Applying AmneziaWG config..."
awg setconf awg0 "$CONFIG_FILE"
rm -f "$CONFIG_FILE"

# Настроить сетевой интерфейс
ip addr add "${AWG_ADDRESS}" dev awg0
ip link set awg0 up

# Явный route до tunnel IP jump-сервера.
# awg setconf не управляет маршрутизацией как wg-quick, поэтому без этого
# пакеты к 10.20.0.2 уходят в default route через eth0.
ip route replace "${AWG_PEER_ALLOWED_IPS}" dev awg0

echo "[awg-node] Interface awg0 is up: ${AWG_ADDRESS}"
awg show awg0

# NAT — пробросить трафик через физический интерфейс
# Берём дефолтный интерфейс динамически (обычно eth0, но может быть другим)
DEFAULT_IFACE=$(ip route show default | awk '/default via/ { print $5 }' | head -1)
if [ -z "$DEFAULT_IFACE" ]; then
    DEFAULT_IFACE="eth0"
    echo "[awg-node] WARNING: could not detect default interface, using eth0"
fi
echo "[awg-node] NAT MASQUERADE on ${DEFAULT_IFACE}"
iptables -t nat -A POSTROUTING -o "${DEFAULT_IFACE}" -j MASQUERADE

echo "[awg-node] Node is ready. Listening on UDP port ${AWG_LISTEN_PORT}"

# Держать контейнер живым, проверять статус каждые 60с
while true; do
    if [ "$USE_KERNEL" = "0" ] && [ "$AWG_PID" -gt 0 ]; then
        if ! kill -0 "$AWG_PID" 2>/dev/null; then
            echo "[awg-node] ERROR: amneziawg-go process died"
            exit 1
        fi
    else
        # Kernel mode: проверяем что интерфейс существует
        if ! ip link show awg0 > /dev/null 2>&1; then
            echo "[awg-node] ERROR: awg0 interface disappeared"
            exit 1
        fi
    fi
    sleep 60
done
