#!/bin/bash
set -euo pipefail

echo "[awg-node] Starting AmneziaWG node..."

# Нода должна маршрутизировать трафик из awg0 в uplink.
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true

# Переключить на legacy iptables (совместимость с ядром)
update-alternatives --set iptables /usr/sbin/iptables-legacy 2>/dev/null || true
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true

# Обязательные переменные
: "${AWG_LISTEN_PORT:=51821}"
: "${AWG_PRIVATE_KEY:?AWG_PRIVATE_KEY is required}"
: "${AWG_ADDRESS:?AWG_ADDRESS is required}"
AWG_CONFIG_PATH="${AWG_CONFIG_PATH:-/etc/awg-node/awg0.conf}"
CONFIG_FILE=""
DEFAULT_IFACE=""

cleanup() {
    if [ -n "${CONFIG_FILE:-}" ] && [ -f "${CONFIG_FILE:-}" ]; then
        rm -f "$CONFIG_FILE"
    fi

    if [ -n "${DEFAULT_IFACE:-}" ]; then
        iptables -t nat -C POSTROUTING -o "${DEFAULT_IFACE}" -j MASQUERADE 2>/dev/null && \
        iptables -t nat -D POSTROUTING -o "${DEFAULT_IFACE}" -j MASQUERADE || true
        iptables -C FORWARD -i awg0 -o "${DEFAULT_IFACE}" -j ACCEPT 2>/dev/null && \
        iptables -D FORWARD -i awg0 -o "${DEFAULT_IFACE}" -j ACCEPT || true
        iptables -C FORWARD -i "${DEFAULT_IFACE}" -o awg0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null && \
        iptables -D FORWARD -i "${DEFAULT_IFACE}" -o awg0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT || true
        iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -i awg0 -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null && \
        iptables -t mangle -D FORWARD -p tcp --tcp-flags SYN,RST SYN -i awg0 -j TCPMSS --clamp-mss-to-pmtu || true
    fi

    ip link show awg0 >/dev/null 2>&1 && ip link delete awg0 >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

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

if ip link show awg0 >/dev/null 2>&1; then
    echo "[awg-node] Removing stale awg0 before startup..."
    ip link delete awg0 || { echo "[awg-node] ERROR: failed to delete stale awg0"; exit 1; }
fi

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
if [ -f "$AWG_CONFIG_PATH" ]; then
    cp "$AWG_CONFIG_PATH" "$CONFIG_FILE"
else
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
fi

# Применить конфиг (awg поддерживает обфускацию-параметры: S1/S2/H1/H2 и т.д.)
echo "[awg-node] Applying AmneziaWG config..."
awg setconf awg0 "$CONFIG_FILE"

# Настроить сетевой интерфейс
ip addr flush dev awg0
ip addr add "${AWG_ADDRESS}" dev awg0
ip link set awg0 up
ip link set dev awg0 mtu 1300

# Route до каждого peer tunnel IP из конфига.
awk '
  BEGIN { in_peer = 0 }
  /^\[Peer\]/ { in_peer = 1; next }
  /^\[/ { in_peer = 0 }
  in_peer && /^AllowedIPs[[:space:]]*=/ {
    sub(/^[^=]*=[[:space:]]*/, "", $0)
    split($0, parts, ",")
    for (i in parts) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", parts[i])
      if (parts[i] != "") print parts[i]
    }
  }
' "$CONFIG_FILE" | while read -r allowed; do
  ip route replace "$allowed" dev awg0
done

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
iptables -t nat -C POSTROUTING -o "${DEFAULT_IFACE}" -j MASQUERADE 2>/dev/null || \
iptables -t nat -A POSTROUTING -o "${DEFAULT_IFACE}" -j MASQUERADE

# Разрешить форвардинг трафика между awg0 и uplink.
# На многих хостах policy FORWARD = DROP (например, из-за Docker), поэтому
# одного MASQUERADE недостаточно: пакеты из awg0 режутся до выхода в eth0.
echo "[awg-node] FORWARD allow awg0 <-> ${DEFAULT_IFACE}"
iptables -C FORWARD -i awg0 -o "${DEFAULT_IFACE}" -j ACCEPT 2>/dev/null || \
iptables -A FORWARD -i awg0 -o "${DEFAULT_IFACE}" -j ACCEPT
iptables -C FORWARD -i "${DEFAULT_IFACE}" -o awg0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
iptables -A FORWARD -i "${DEFAULT_IFACE}" -o awg0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -i awg0 -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -i awg0 -j TCPMSS --clamp-mss-to-pmtu

rm -f "$CONFIG_FILE"
CONFIG_FILE=""

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
