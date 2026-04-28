# syntax=docker/dockerfile:1.7

# ============================================================
# Stage 1 — сборка amneziawg-go (userspace демон)
# ============================================================
FROM golang:1.24-alpine AS awg-builder

RUN apk add --no-cache ca-certificates git make

WORKDIR /build
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-go.git .
RUN mkdir -p /out \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/amneziawg-go .

# ============================================================
# Stage 2 — сборка amneziawg-tools (команда awg)
# Нужна для работы с kernel module: awg setconf/syncconf/show/set
# ============================================================
FROM debian:bookworm-slim AS awg-tools-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential pkg-config libmnl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-tools.git .
RUN make -C src -j$(nproc) && make -C src install DESTDIR=/out PREFIX=/usr

# ============================================================
# Stage 2 — Python runtime dependencies
# ============================================================
FROM python:3.12-slim-bookworm AS python-builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /build
COPY backend/requirements.txt .
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ============================================================
# Stage 3 — сборка frontend
# ============================================================
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM debian:bookworm-slim AS cloudflared-builder

ARG CLOUDFLARED_VERSION=2025.4.0

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-amd64" \
    -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared

# ============================================================
# Stage 4 — сборка TeleMT
# ============================================================
FROM rust:1-bookworm AS telemt-builder

ARG TELEMT_VERSION=3.4.8

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    pkg-config \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 --branch "${TELEMT_VERSION}" https://github.com/telemt/telemt.git .
RUN cargo build --release

# ============================================================
# Stage 5 — финальный образ
# ============================================================
FROM python:3.12-slim-bookworm AS final

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    dnsmasq \
    iproute2 \
    ipset \
    iptables \
    stubby \
    iputils-ping \
    net-tools \
    openssl \
    openssh-client \
    procps \
    supervisor \
    tcpdump \
    wireguard-tools \
    && rm -rf /var/lib/apt/lists/*

COPY --from=awg-builder /out/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=awg-tools-builder /out/usr/bin/awg /usr/local/bin/awg
COPY --from=cloudflared-builder /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY --from=telemt-builder /build/target/release/telemt /usr/local/bin/telemt
COPY --from=python-builder /opt/venv /opt/venv
COPY --from=frontend-builder /frontend/dist /app/static

COPY backend/ /app/backend/
COPY node/ /app/node/
COPY nginx/ /app/nginx/
COPY scripts/ /app/scripts/
COPY supervisord.conf /etc/supervisor/supervisord.conf

RUN chmod +x /usr/local/bin/amneziawg-go /usr/local/bin/telemt /app/scripts/*.sh \
    && mkdir -p /var/log/supervisor /var/run/amneziawg \
    # Отключаем системный dnsmasq — управляем вручную через dns_manager.py
    && rm -f /etc/dnsmasq.conf /etc/dnsmasq.d/* \
    && echo "# Managed by AWG dns_manager.py" > /etc/dnsmasq.conf

WORKDIR /app

EXPOSE 443/tcp
EXPOSE 51820/udp
EXPOSE 8080/tcp

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
