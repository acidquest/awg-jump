#!/bin/sh
# Генерация self-signed TLS сертификата для nginx.
# Запускается один раз при первом старте контейнера.
# Если сертификат уже существует — пропускаем генерацию.

CERT_DIR=/etc/nginx/certs

if [ -f "$CERT_DIR/server.crt" ]; then
    echo "[generate-cert] Certificate already exists, skipping generation."
    exit 0
fi

mkdir -p "$CERT_DIR"

# Установить openssl если отсутствует (nginx:alpine его не включает)
if ! command -v openssl >/dev/null 2>&1; then
    echo "[generate-cert] Installing openssl..."
    apk add --no-cache openssl
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
