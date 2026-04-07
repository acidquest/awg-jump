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

# Запустить TUN демон
echo "[awg-node] Starting amneziawg-go daemon..."
amneziawg-go awg0 &
AWG_PID=$!
sleep 2

# Проверить что сокет создан
if [ ! -S /var/run/wireguard/awg0.sock ]; then
    echo "[awg-node] ERROR: WireGuard socket not created"
    exit 1
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

# Применить конфиг
echo "[awg-node] Applying WireGuard config..."
wg setconf awg0 "$CONFIG_FILE"
rm -f "$CONFIG_FILE"

# Настроить сетевой интерфейс
ip addr add "${AWG_ADDRESS}" dev awg0
ip link set awg0 up

echo "[awg-node] Interface awg0 is up: ${AWG_ADDRESS}"
wg show awg0

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

# Держать контейнер живым, пересматривать статус каждые 60с
while true; do
    if ! kill -0 "$AWG_PID" 2>/dev/null; then
        echo "[awg-node] ERROR: amneziawg-go process died"
        exit 1
    fi
    sleep 60
done
