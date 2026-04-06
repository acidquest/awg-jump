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

CN="${TLS_COMMON_NAME:-localhost}"
echo "[generate-cert] Generating self-signed certificate for CN=$CN (valid 10 years)..."

# Попытка с subjectAltName (современные браузеры требуют SAN)
openssl req -x509 \
    -newkey rsa:4096 \
    -sha256 \
    -days 3650 \
    -nodes \
    -keyout "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.crt" \
    -subj "/C=XX/ST=State/L=City/O=AWG/CN=$CN" \
    -addext "subjectAltName=DNS:$CN,IP:$CN" 2>/dev/null \
|| \
openssl req -x509 \
    -newkey rsa:4096 \
    -sha256 \
    -days 3650 \
    -nodes \
    -keyout "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.crt" \
    -subj "/CN=$CN"

chmod 600 "$CERT_DIR/server.key"

echo "[generate-cert] Certificate generated successfully."
echo "[generate-cert] SHA-256 fingerprint (add to browser exception):"
openssl x509 -in "$CERT_DIR/server.crt" -noout -fingerprint -sha256
