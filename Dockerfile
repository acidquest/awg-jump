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

# ============================================================
# Stage 4 — финальный образ
# ============================================================
FROM python:3.12-slim-bookworm AS final

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    iproute2 \
    ipset \
    iptables \
    iputils-ping \
    net-tools \
    openssh-client \
    procps \
    supervisor \
    tcpdump \
    wireguard-tools \
    && rm -rf /var/lib/apt/lists/*

COPY --from=awg-builder /out/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=awg-tools-builder /out/usr/bin/awg /usr/local/bin/awg
COPY --from=python-builder /opt/venv /opt/venv
COPY --from=frontend-builder /frontend/dist /app/static

COPY backend/ /app/backend/
COPY node/ /app/node/
COPY scripts/ /app/scripts/
COPY supervisord.conf /etc/supervisor/supervisord.conf

RUN chmod +x /usr/local/bin/amneziawg-go /app/scripts/*.sh \
    && mkdir -p /var/log/supervisor /var/run/amneziawg

WORKDIR /app

EXPOSE 51820/udp
EXPOSE 8080/tcp

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
