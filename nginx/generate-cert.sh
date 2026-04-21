#!/bin/sh
# Генерация self-signed TLS сертификата.
# Скрипт переиспользуется awg-jump и legacy nginx-контуром.

CERT_DIR="${CERT_DIR:-/data/certs}"

if [ -f "$CERT_DIR/server.crt" ]; then
    echo "[generate-cert] Certificate already exists, skipping generation."
    exit 0
fi

mkdir -p "$CERT_DIR"

if ! command -v openssl >/dev/null 2>&1; then
    echo "[generate-cert] ERROR: openssl is required but not installed."
    exit 1
fi

CN="${TLS_COMMON_NAME:-localhost}"
echo "[generate-cert] Generating self-signed certificate for CN=$CN (valid 10 years)..."

# Определяем тип CN: IP-адрес или DNS-имя — для корректного SubjectAltName
# Проверяем является ли CN IPv4/IPv6 адресом
if echo "$CN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || echo "$CN" | grep -q ':'; then
    SAN="IP:$CN"
else
    SAN="DNS:$CN"
fi

echo "[generate-cert] SubjectAltName: $SAN"

openssl req -x509 \
    -newkey rsa:4096 \
    -sha256 \
    -days 3650 \
    -nodes \
    -keyout "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.crt" \
    -subj "/CN=$CN" \
    -addext "subjectAltName=$SAN"

chmod 600 "$CERT_DIR/server.key"

echo "[generate-cert] Certificate generated successfully."
echo "[generate-cert] SHA-256 fingerprint (add to browser exception):"
openssl x509 -in "$CERT_DIR/server.crt" -noout -fingerprint -sha256
